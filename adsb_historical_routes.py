#!/usr/bin/env python3
"""
Enhanced Flight Route Splitter v6
Major improvements:
- Skip zero-altitude ground operation tracks
- Aggressive cross-file segment joining
- Better incomplete segment handling
- Use route time data for validation
- More lenient airport matching
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from math import radians, cos, sin, asin, sqrt
import argparse
import sys
import os
import gc
import glob
import json
import bisect
import sqlite3
import subprocess
import tempfile
import time
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Any, List, Tuple, Dict, Optional, Sequence, Set
import csv
import re

# The vendored GeoPackage writer lives next to this script (shared with
# company_route_extractor); make it importable however this file is loaded.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gpkg_writer  # noqa: E402

TOOL_VERSION = "1.4.0"


@dataclass(frozen=True)
class ConfidencePreset:
    """Tunable knobs for flight detection and joining aggressiveness."""
    name: str
    airport_radius_km: float
    max_join_gap_hours: float
    max_join_distance_km: float
    route_time_tolerance: float
    route_time_rescue: bool
    direction_aware_rescue: bool
    min_segment_points: int = 20
    min_flight_altitude_m: float = 300.0
    # Multi-hop joining: let a merged segment keep absorbing further adjacent
    # fragments in one pass, so flights split into 3+ pieces can fully reassemble.
    multi_hop_join: bool = False
    # Geometry (corridor) endpoint recovery: infer a missing origin/destination from
    # the route-network great-circle corridor containing the airborne endpoint. This
    # is turn-robust (uses cross-track corridor distance, not instantaneous heading)
    # and only ever *fills* a missing endpoint — it never filters a matched flight.
    geometry_rescue: bool = False
    corridor_cross_track_km: float = 80.0
    corridor_along_slack: float = 1.15


PRESETS: Dict[str, ConfidencePreset] = {
    "strict": ConfidencePreset(
        name="strict",
        airport_radius_km=20,
        max_join_gap_hours=2.0,
        max_join_distance_km=100,
        route_time_tolerance=0.25,
        route_time_rescue=False,
        direction_aware_rescue=False,
    ),
    "balanced": ConfidencePreset(
        name="balanced",
        airport_radius_km=50,
        max_join_gap_hours=3.0,
        max_join_distance_km=200,
        route_time_tolerance=0.40,
        route_time_rescue=True,
        direction_aware_rescue=True,
        multi_hop_join=True,
        geometry_rescue=True,
        corridor_cross_track_km=70.0,
    ),
    "permissive": ConfidencePreset(
        name="permissive",
        airport_radius_km=100,
        max_join_gap_hours=4.0,
        max_join_distance_km=500,
        route_time_tolerance=0.60,
        route_time_rescue=True,
        direction_aware_rescue=True,
        multi_hop_join=True,
        geometry_rescue=True,
        corridor_cross_track_km=120.0,
    ),
}


def resolve_preset(args) -> ConfidencePreset:
    """Apply CLI overrides on top of the named preset, returning a (possibly renamed) ConfidencePreset."""
    base = PRESETS[args.confidence]
    overrides = {}
    if getattr(args, "airport_radius_km", None) is not None:
        overrides["airport_radius_km"] = args.airport_radius_km
    if getattr(args, "max_join_gap_hours", None) is not None:
        overrides["max_join_gap_hours"] = args.max_join_gap_hours
    if getattr(args, "max_join_distance_km", None) is not None:
        overrides["max_join_distance_km"] = args.max_join_distance_km
    if getattr(args, "route_time_tolerance", None) is not None:
        overrides["route_time_tolerance"] = args.route_time_tolerance
    if getattr(args, "corridor_cross_track_km", None) is not None:
        overrides["corridor_cross_track_km"] = args.corridor_cross_track_km
    rtr = getattr(args, "route_time_rescue", None)
    if rtr is not None:
        overrides["route_time_rescue"] = (rtr == "on")
    if not overrides:
        return base
    return replace(base, name="custom", **overrides)


class Airport:
    """Represents an airport with location data"""
    def __init__(self, code: str, lat: float, lon: float, elevation: float):
        self.code = code
        self.lat = lat
        self.lon = lon
        self.elevation = elevation  # in feet
    
    def __repr__(self):
        return f"Airport({self.code}, {self.lat:.4f}, {self.lon:.4f}, elev={self.elevation:.0f}ft)"


class Route:
    """Represents a known route between two airports with average flight time"""
    def __init__(self, origin: str, destination: str, avg_time: float):
        self.origin = origin
        self.destination = destination
        self.avg_time_min = avg_time  # in minutes
    
    def __repr__(self):
        return f"Route({self.origin}->{self.destination}, {self.avg_time_min:.1f}min)"


class TrackPoint:
    """Represents a single point in a flight track"""
    def __init__(self, timestamp: str, lon: float, lat: float, alt: float, source: str = "ADSB"):
        self.timestamp = timestamp
        self.lon = lon
        self.lat = lat
        self.alt = alt  # in meters
        self.datetime = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        self.groundspeed = None
        self.vertical_speed = None
        # Origin of this fix: "ADSB" (ADS-B Exchange KML), "POS" (ACARS position report),
        # or "FIREHOSE" (ADS-B firehose feed). Used to colour the output and to treat
        # source-filled gaps as real (not inferred).
        self.source = source
    
    def __repr__(self):
        return f"TrackPoint({self.timestamp}, {self.lat:.4f}, {self.lon:.4f}, {self.alt:.0f}m)"


class FlightSegment:
    """Represents a complete flight from origin to destination"""
    def __init__(self, aircraft_id: str, registration: str, points: List[TrackPoint], 
                 origin: Optional[Airport], destination: Optional[Airport],
                 style_color: str, style_width: str, style_opacity: str = "ff",
                 source_file: str = ""):
        self.aircraft_id = aircraft_id
        self.registration = registration
        self.points = points
        self.origin = origin
        self.destination = destination
        self.style_color = style_color
        self.style_width = style_width
        self.style_opacity = style_opacity
        self.source_file = source_file
        self.is_complete = (origin is not None and destination is not None)
        self.has_gaps = False
        self.gap_duration_min = 0
        self.max_altitude = 0
        # Where each endpoint came from (flown_routes v3 ``*_source``): an airport given at
        # detection was observed; a rescue or the evidence stage overwrites these. The
        # candidates are the airports a route-time / geometry rescue accepted, best first.
        self.orig_source = "observed" if origin is not None else None
        self.dest_source = "observed" if destination is not None else None
        self.orig_candidates: List[str] = []
        self.dest_candidates: List[str] = []
        self.total_distance = 0
        
        if points:
            self.takeoff_time = points[0].datetime
            self.landing_time = points[-1].datetime
            self.flight_duration = (self.landing_time - self.takeoff_time).total_seconds() / 60
            self.takeoff_date_str = points[0].datetime.strftime('%Y-%m-%d')
            self.max_altitude = max(p.alt for p in points)
            self.total_distance = self.calculate_total_distance()
        else:
            self.takeoff_time = None
            self.landing_time = None
            self.flight_duration = 0
            self.takeoff_date_str = "UNKNOWN"
    
    def calculate_total_distance(self) -> float:
        """Calculate total distance traveled in km"""
        total = 0
        for i in range(1, len(self.points)):
            total += haversine_distance(
                self.points[i-1].lat, self.points[i-1].lon,
                self.points[i].lat, self.points[i].lon
            )
        return total
    
    def is_valid_flight(self, min_altitude_m: float = 1000, min_duration_min: float = 15, 
                        min_distance_km: float = 20) -> bool:
        """Check if this is a valid flight vs ground operations"""
        # Must have both airports
        if not self.origin or not self.destination:
            return False
            
        # Same airport segments are not valid flights
        if self.origin.code == self.destination.code:
            return False
        
        # Must exceed minimum thresholds
        if self.max_altitude < min_altitude_m:
            return False
        if self.flight_duration < min_duration_min:
            return False
        if self.total_distance < min_distance_km:
            return False
        
        return True
    
    def get_segment_name(self) -> str:
        """Generate segment name in format: AIRCRAFTID-ORIG-DEST-DATE"""
        orig = self.origin.code if self.origin else "UNKN"
        dest = self.destination.code if self.destination else "UNKN"
        return f"{self.aircraft_id}-{orig}-{dest}-{self.takeoff_date_str}"
    
    def get_route_folder(self) -> str:
        """Generate route folder name in format: ORIG-DEST"""
        orig = self.origin.code if self.origin else "UNKN"
        dest = self.destination.code if self.destination else "UNKN"
        return f"{orig}-{dest}"
    
    def sample_points(self, sample_minutes: float) -> List[TrackPoint]:
        """Sample points at specified interval to reduce data size"""
        if not self.points or sample_minutes <= 0:
            return self.points
        
        sampled = [self.points[0]]
        last_sampled_time = self.points[0].datetime
        
        for point in self.points[1:-1]:
            time_diff = (point.datetime - last_sampled_time).total_seconds() / 60
            if time_diff >= sample_minutes:
                sampled.append(point)
                last_sampled_time = point.datetime
        
        if len(self.points) > 1:
            sampled.append(self.points[-1])
        
        return sampled
    
    def __repr__(self):
        orig = self.origin.code if self.origin else "UNKN"
        dest = self.destination.code if self.destination else "UNKN"
        status = "complete" if self.is_complete else "incomplete"
        return f"FlightSegment({self.aircraft_id}, {orig}->{dest}, {len(self.points)}pts, {self.flight_duration:.1f}min, {status})"


def extract_aircraft_id(filename: str) -> str:
    """Extract aircraft ID from filename"""
    basename = os.path.basename(filename)
    match = re.match(r'^([a-zA-Z0-9]+)_', basename)
    if match:
        return match.group(1).upper()
    return "UNKNOWN"


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance between two points on Earth (in kilometers)"""
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    return c * 6371


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing in degrees (0-360, clockwise from north) from point 1 to point 2."""
    from math import atan2, cos, degrees, radians, sin
    rlat1, rlat2 = radians(lat1), radians(lat2)
    dlon = radians(lon2 - lon1)
    x = sin(dlon) * cos(rlat2)
    y = cos(rlat1) * sin(rlat2) - sin(rlat1) * cos(rlat2) * cos(dlon)
    return (degrees(atan2(x, y)) + 360.0) % 360.0


def angular_diff(a: float, b: float) -> float:
    """Smallest angular difference between two bearings in degrees (0-180)."""
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d


def cross_track_km(plat: float, plon: float,
                   alat: float, alon: float,
                   blat: float, blon: float) -> float:
    """Signed perpendicular distance (km) of point P from the great-circle path A->B.

    A value near zero means P sits on the A->B corridor. This is the turn-robust
    primitive behind geometry endpoint recovery: because it measures distance from the
    *corridor* rather than heading, an aircraft that turns, doglegs around weather, or
    flies a SID/STAR still registers as "on the route" as long as it stays near the
    line between the two airports. Callers use abs(); the sign only encodes which side.
    """
    R = 6371.0
    d_ap = haversine_distance(alat, alon, plat, plon) / R  # angular distance, radians
    if d_ap == 0.0:
        return 0.0
    brg_ap = radians(bearing(alat, alon, plat, plon))
    brg_ab = radians(bearing(alat, alon, blat, blon))
    return asin(max(-1.0, min(1.0, sin(d_ap) * sin(brg_ap - brg_ab)))) * R


def mean_bearing(points: List["TrackPoint"], window_minutes: float = 10.0) -> float:
    """Average bearing over the last `window_minutes` of a track. Uses vector mean to avoid wrap-around bias."""
    from math import atan2, cos, degrees, radians, sin

    if len(points) < 2:
        return 0.0

    last_time = points[-1].datetime
    cutoff = last_time - timedelta(minutes=window_minutes)
    window = [p for p in points if p.datetime >= cutoff]
    if len(window) < 2:
        window = points[-min(10, len(points)):]
    if len(window) < 2:
        return 0.0

    sum_x = 0.0
    sum_y = 0.0
    for i in range(len(window) - 1):
        b = bearing(window[i].lat, window[i].lon, window[i + 1].lat, window[i + 1].lon)
        sum_x += sin(radians(b))
        sum_y += cos(radians(b))
    return (degrees(atan2(sum_x, sum_y)) + 360.0) % 360.0


def calculate_groundspeed(p1: TrackPoint, p2: TrackPoint) -> float:
    """Calculate groundspeed between two points (in knots)"""
    distance_km = haversine_distance(p1.lat, p1.lon, p2.lat, p2.lon)
    time_hours = (p2.datetime - p1.datetime).total_seconds() / 3600
    if time_hours > 0:
        speed_kmh = distance_km / time_hours
        return speed_kmh * 0.539957
    return 0


def load_airports_csv(filepath: str) -> List[Airport]:
    """Load airports from CSV file"""
    airports = []
    
    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    code = row['airport']
                    lat = float(row['latitude'])
                    lon = float(row['longitude'])
                    elevation = float(row['elevation_ft'])
                    airports.append(Airport(code, lat, lon, elevation))
                except (ValueError, KeyError) as e:
                    print(f"Warning: Error parsing airport row: {e}")
                    continue
    except Exception as e:
        print(f"Error loading airports file: {e}")
        return []
    
    print(f"Loaded {len(airports)} airports")
    return airports


def load_routes_csv(filepath: str) -> List[Route]:
    """Load routes with average flight times from CSV file"""
    routes = []
    
    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    origin = row['origin']
                    destination = row['destination']
                    avg_time = float(row['avg_enroute_min'])
                    routes.append(Route(origin, destination, avg_time))
                except (ValueError, KeyError) as e:
                    print(f"Warning: Error parsing route row: {e}")
                    continue
    except Exception as e:
        print(f"Error loading routes file: {e}")
        return []
    
    print(f"Loaded {len(routes)} route definitions")
    return routes


# Height above an airport (ft per km of distance from it) that an endpoint may add on top
# of the plain altitude allowance and still be treated as climbing out of / descending
# into it: a 3-degree path, tan(3 deg) * 3280.84 ft/km.
ENDPOINT_PATH_FT_PER_KM = 171.9


def find_nearest_airport(lat: float, lon: float, alt_meters: float, airports: List[Airport],
                        max_distance_km: float = 15.0, max_alt_diff_ft: float = 3000.0,
                        lenient: bool = False) -> Optional[Airport]:
    """
    Find the nearest airport within distance and altitude constraints
    lenient mode: used for takeoff/landing detection, more forgiving on altitude

    In lenient mode a farther airport may not "steal" an endpoint from a closer one. The
    altitude allowance grows with airport elevation, so a climbing aircraft first seen
    16 km from MMSM (elev 7,369 ft) at 12,100 ft fails MMSM's allowance but passes the
    larger one of MMTO (elev 8,466 ft), 89 km away, and was labelled MMTO. When the
    geometrically closest airport in range was rejected only on altitude, but the height
    above it fits a 3-degree climb/descent path from it, the closest airport wins. This
    only re-labels endpoints that already matched; it never matches a new one.
    """
    nearest = None
    min_distance = float('inf')
    closest = None
    closest_distance = float('inf')
    alt_feet = alt_meters * 3.28084  # Convert meters to feet

    for airport in airports:
        distance = haversine_distance(lat, lon, airport.lat, airport.lon)
        alt_diff = abs(alt_feet - airport.elevation)

        if distance <= max_distance_km and distance < closest_distance:
            closest = airport
            closest_distance = distance

        if distance <= max_distance_km and distance < min_distance:
            # More lenient altitude check for low altitudes near airports
            if lenient:
                # If we're low and close, it's probably the airport
                if alt_meters < 500 and distance < 5.0:
                    min_distance = distance
                    nearest = airport
                    continue

            # Standard altitude check with adaptive threshold
            adjusted_alt_threshold = max_alt_diff_ft + (airport.elevation * 0.15 if airport.elevation > 3000 else 0)
            if alt_diff <= adjusted_alt_threshold:
                min_distance = distance
                nearest = airport

    if lenient and nearest is not None and closest is not nearest:
        threshold = max_alt_diff_ft + (closest.elevation * 0.15 if closest.elevation > 3000 else 0)
        height_above = alt_feet - closest.elevation
        if -threshold <= height_above <= threshold + ENDPOINT_PATH_FT_PER_KM * closest_distance:
            return closest

    return nearest


def track_has_useful_altitude(track_points: List[TrackPoint]) -> bool:
    """Check if track has useful altitude data (not all zeros)"""
    if not track_points:
        return False
    
    # Check if more than 90% of points have zero altitude
    zero_count = sum(1 for p in track_points if p.alt == 0.0)
    return zero_count < len(track_points) * 0.9


def parse_kml_tracks(kml_file: str, aircraft_id: str) -> List[Tuple[List[TrackPoint], str, str, str, str]]:
    """
    Parse KML file and extract flight tracks
    Returns: list of (track_points, registration, style_color, style_width, style_opacity)
    Only returns tracks with useful altitude data
    """
    tree = ET.parse(kml_file)
    root = tree.getroot()
    
    ns = {
        'kml': 'http://www.opengis.net/kml/2.2',
        'gx': 'http://www.google.com/kml/ext/2.2'
    }
    
    all_tracks = []
    
    def extract_registration_from_name(name: str) -> str:
        """Extract registration from various name formats"""
        if not name:
            return aircraft_id
        
        parts = name.split()
        if parts:
            reg = parts[0].upper()
            if reg and reg[0].isalpha():
                return reg
        return aircraft_id
    
    def find_tracks_recursive(element, current_registration=None):
        """Recursively search for tracks in folders"""
        name_elem = element.find('kml:name', ns)
        if name_elem is not None and name_elem.text:
            potential_reg = extract_registration_from_name(name_elem.text)
            if potential_reg:
                current_registration = potential_reg
        
        if not current_registration:
            current_registration = aircraft_id
        
        for placemark in element.findall('kml:Placemark', ns):
            pm_name = placemark.find('kml:name', ns)
            if pm_name is not None and pm_name.text:
                reg = extract_registration_from_name(pm_name.text)
                if reg:
                    current_registration = reg
            
            # Extract style information
            style = placemark.find('kml:Style', ns)
            style_color = 'ff0000ff'
            style_width = '2'
            style_opacity = 'ff'
            
            if style is not None:
                line_style = style.find('kml:LineStyle', ns)
                if line_style is not None:
                    color_elem = line_style.find('kml:color', ns)
                    width_elem = line_style.find('kml:width', ns)
                    if color_elem is not None and color_elem.text:
                        style_color = color_elem.text
                        if len(style_color) >= 2:
                            style_opacity = style_color[:2]
                    if width_elem is not None and width_elem.text:
                        style_width = width_elem.text
            
            track = placemark.find('.//gx:Track', ns)
            if track is None:
                continue
            
            whens = track.findall('kml:when', ns)
            coords = track.findall('gx:coord', ns)
            
            if len(whens) != len(coords):
                continue
            
            track_points = []
            for when, coord in zip(whens, coords):
                if when.text and coord.text:
                    timestamp = when.text
                    parts = coord.text.strip().split()
                    if len(parts) >= 3:
                        try:
                            lon = float(parts[0])
                            lat = float(parts[1])
                            alt = float(parts[2])
                            track_points.append(TrackPoint(timestamp, lon, lat, alt))
                        except ValueError:
                            continue
            
            # ONLY ADD TRACKS WITH USEFUL ALTITUDE DATA
            if track_points and track_has_useful_altitude(track_points):
                # Calculate speeds
                for i in range(1, len(track_points)):
                    track_points[i].groundspeed = calculate_groundspeed(
                        track_points[i-1], track_points[i]
                    )
                all_tracks.append(
                    (track_points, current_registration, style_color, style_width, style_opacity)
                )
        
        for folder in element.findall('kml:Folder', ns):
            find_tracks_recursive(folder, current_registration)
    
    document = root.find('.//kml:Document', ns)
    if document is not None:
        find_tracks_recursive(document)
    else:
        find_tracks_recursive(root)
    
    return all_tracks


# ---------------------------------------------------------------------------
# Optional extra position sources: ACARS position reports ("POS") and the
# ADS-B firehose ("FIREHOSE"). Both are merged into the per-aircraft ADS-B
# track before detection so real fixes fill coverage gaps.
# ---------------------------------------------------------------------------

def load_tail_to_hex(path: str) -> Dict[str, str]:
    """Load a `tail_number,icao_hex` CSV into {normalized_tail: HEX}.

    Tail is normalized to A-Z0-9 (so `CC-DIK` and `CCDIK` both match). ACARS reports carry a
    tail number; ADS-B/firehose carry the ICAO hex; this bridges them.
    """
    mapping: Dict[str, str] = {}
    if not path or not os.path.exists(path):
        return mapping
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            tail = re.sub(r"[^A-Z0-9]", "", (row.get("tail_number") or "").upper())
            hexc = (row.get("icao_hex") or "").strip().upper()
            if tail and hexc:
                mapping[tail] = hexc
    return mapping


def _parse_pos_latlon(freetext: str):
    """Extract (lat, lon) from an ACARS freetext like `S 26.085 W 70.677` (S/W are negative)."""
    m = re.search(r"([NS])\s*([\d.]+)\s+([EW])\s*([\d.]+)", freetext)
    if not m:
        return None
    lat = float(m.group(2)) * (-1 if m.group(1) == "S" else 1)
    lon = float(m.group(4)) * (-1 if m.group(3) == "W" else 1)
    return lat, lon


# Raw ACARS (ARINC 620) Airbus-style position report, e.g.
#   FI JJ3634/AN PR-MYV
#   -  POSS23156W046116,S23190W046136,003708,216,UBNOV,005054,UMLAM,M16,32722,204/TS003708,...
# = present position S23°15.6' W046°11.6' (DDMM.m), last waypoint (name or lat/lon),
# time HHMMSS (UTC), flight level, next waypoint, ETA, ... The day is not in the report.
_ACARS_POS_RE = re.compile(
    r"^-\s*POS([NS])(\d{2})(\d{3})([EW])(\d{3})(\d{3}),[^,\r\n]*,(\d{6}),(\d{1,3}),", re.M)
_ACARS_REG_RE = re.compile(r"/AN\s+\.?([A-Z0-9-]+)")
_RECEIVED_RE = re.compile(r"-(\d{14})\d*\.txt$")
_RAW_FILE_TAIL_RE = re.compile(r"^([A-Z0-9]+)_raw_")


def _acars_received_time(msg: dict) -> Optional[datetime]:
    """When the message reached the ground: the S3 key's ``-YYYYMMDDHHMMSSmmm.txt`` stamp,
    else the SQS ``SentTimestamp`` (epoch ms). Naive UTC."""
    attrs = msg.get("message_attributes") or {}
    for key in ((attrs.get("bucket_path") or {}).get("StringValue"), msg.get("uri")):
        m = _RECEIVED_RE.search(key or "")
        if m:
            return datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    sent = (msg.get("attributes") or {}).get("SentTimestamp")
    try:
        return datetime.utcfromtimestamp(int(sent) / 1000.0)
    except (TypeError, ValueError):
        return None


def _parse_acars_pos(msg: dict, body: str):
    """``(tail, timestamp, lat, lon, alt_ft)`` from a raw ACARS POS message, or None.

    The report carries only HHMMSS; the date is taken from the receive time, stepping back
    a day when the report time would be more than an hour after it (sent just after
    midnight). The trailing date field is not used: its order differs between fleets.
    """
    m = _ACARS_POS_RE.search(body)
    reg = _ACARS_REG_RE.search(body)
    received = _acars_received_time(msg)
    if not (m and reg and received):
        return None
    lat = int(m.group(2)) + int(m.group(3)) / 600.0
    lon = int(m.group(5)) + int(m.group(6)) / 600.0
    if lat > 90 or lon > 180:
        return None
    hhmmss = m.group(7)
    try:
        t = received.replace(hour=int(hhmmss[:2]), minute=int(hhmmss[2:4]),
                             second=int(hhmmss[4:]), microsecond=0)
    except ValueError:
        return None
    if t > received + timedelta(hours=1):
        t -= timedelta(days=1)
    tail = re.sub(r"[^A-Z0-9]", "", reg.group(1).upper())
    return (tail, t.strftime("%Y-%m-%dT%H:%M:%SZ"),
            -lat if m.group(1) == "S" else lat, -lon if m.group(4) == "W" else lon,
            int(m.group(8)) * 100.0)


def load_pos_points(folder: str, tail_to_hex: Dict[str, str]) -> Dict[Tuple[str, str], List["TrackPoint"]]:
    """Parse ACARS POS files into {(hex, 'YYYY-MM-DD'): [TrackPoint(source='POS'), ...]}.

    Each file is an SQS message. Two body formats are read:

    * ``<Airline>_Position=[{...}]`` (URL-encoded); each report has `tail_number`,
      `created_at` (date), and a `freetext` encoding `HHMMSS, alt_ft, ?, heading, lat/lon`.
      Position time = created_at date + freetext HHMMSS (UTC).
    * A raw ACARS ``POS`` report (``FI .../AN <reg>`` + ``-  POS<lat><lon>,...``); see
      :func:`_parse_acars_pos`. Files named ``<TAIL>_raw_*.json`` for tails not in the map
      are skipped unread.
    """
    idx: Dict[Tuple[str, str], List["TrackPoint"]] = defaultdict(list)
    if not folder or not os.path.isdir(folder):
        return idx
    for fp in glob.glob(os.path.join(folder, "*.json")):
        mt = _RAW_FILE_TAIL_RE.match(os.path.basename(fp))
        if mt and mt.group(1) not in tail_to_hex:
            continue
        try:
            msg = json.load(open(fp))
            body = msg.get("body", "")
        except Exception:
            continue
        body = urllib.parse.unquote_plus(body)
        m = re.match(r"^\w+_Position=(\[.*\])\s*$", body, re.S)
        if not m:
            report = _parse_acars_pos(msg, body)
            hexc = tail_to_hex.get(report[0]) if report else None
            if hexc:
                tail, ts, lat, lon, alt_ft = report
                try:
                    tp = TrackPoint(ts, lon, lat, alt_ft * 0.3048, source="POS")
                except Exception:
                    continue
                idx[(hexc, ts[:10])].append(tp)
            continue
        try:
            reports = json.loads(m.group(1))
        except Exception:
            continue
        for r in reports:
            tail = re.sub(r"[^A-Z0-9]", "", (r.get("tail_number") or "").upper())
            hexc = tail_to_hex.get(tail)
            if not hexc:
                continue
            date = (r.get("created_at") or "")[:10]
            ft = r.get("freetext") or ""
            parts = [p.strip() for p in ft.lstrip("-").strip().split(",")]
            ll = _parse_pos_latlon(ft)
            if not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) and len(parts) >= 2
                    and re.fullmatch(r"\d{6}", parts[0]) and ll):
                continue
            hh, mi, ss = parts[0][:2], parts[0][2:4], parts[0][4:6]
            try:
                alt_m = float(parts[1]) * 0.3048
            except (ValueError, IndexError):
                alt_m = 0.0
            try:
                tp = TrackPoint(f"{date}T{hh}:{mi}:{ss}Z", ll[1], ll[0], alt_m, source="POS")
            except Exception:
                continue
            idx[(hexc, date)].append(tp)
    return idx


def load_firehose_points(folder: str) -> Dict[Tuple[str, str], List["TrackPoint"]]:
    """Parse ADS-B firehose files into {(hex, 'YYYY-MM-DD'): [TrackPoint(source='FIREHOSE')]}.

    Each file is one report with `icao` (hex), Unix `timestamp`, `latitude`, `longitude`,
    and `altitude` (feet).
    """
    idx: Dict[Tuple[str, str], List["TrackPoint"]] = defaultdict(list)
    if not folder or not os.path.isdir(folder):
        return idx
    for fp in glob.glob(os.path.join(folder, "*.json")):
        try:
            d = json.load(open(fp))
        except Exception:
            continue
        hexc = (d.get("icao") or "").strip().upper()
        ts, lat, lon, alt = d.get("timestamp"), d.get("latitude"), d.get("longitude"), d.get("altitude")
        if not (hexc and ts is not None and lat is not None and lon is not None):
            continue
        try:
            dt = datetime.utcfromtimestamp(int(ts))
            alt_m = float(alt) * 0.3048 if alt not in (None, "") else 0.0
            tp = TrackPoint(dt.strftime("%Y-%m-%dT%H:%M:%SZ"), float(lon), float(lat), alt_m, source="FIREHOSE")
        except Exception:
            continue
        idx[(hexc, dt.strftime("%Y-%m-%d"))].append(tp)
    return idx


def load_position_sources(pos_folder: Optional[str], firehose_folder: Optional[str],
                          tail_icao_path: Optional[str]) -> Dict[Tuple[str, str], List["TrackPoint"]]:
    """Load and combine ACARS POS + firehose fixes, keyed by (hex, 'YYYY-MM-DD')."""
    tail_to_hex = load_tail_to_hex(tail_icao_path) if tail_icao_path else {}
    combined: Dict[Tuple[str, str], List["TrackPoint"]] = defaultdict(list)
    if pos_folder:
        if not tail_to_hex:
            print("  Warning: POS folder given but no tail<->hex map; ACARS reports cannot be keyed to hex.")
        for k, v in load_pos_points(pos_folder, tail_to_hex).items():
            combined[k].extend(v)
    if firehose_folder:
        for k, v in load_firehose_points(firehose_folder).items():
            combined[k].extend(v)
    return combined


# An extra fix that would need a faster ground speed than this from the previous accepted
# point, or to the next ADS-B point, is not this aircraft at that time (e.g. a POS report of
# another flight) and is dropped. Airliners stay well under it even in a strong jet stream.
MAX_FIX_SPEED_KMH = 1300.0


def _too_fast(a: Optional["TrackPoint"], b: Optional["TrackPoint"]) -> bool:
    if a is None or b is None:
        return False
    km = haversine_distance(a.lat, a.lon, b.lat, b.lon)
    secs = abs((b.datetime - a.datetime).total_seconds())
    if secs == 0:
        return km > 1.0
    return km / secs * 3600.0 > MAX_FIX_SPEED_KMH


def merge_track_with_sources(adsb_points: List["TrackPoint"], extra_points: List["TrackPoint"],
                             dedup_seconds: float = 30.0,
                             min_fill_gap_seconds: float = 120.0,
                             stats: Optional[Dict[str, int]] = None) -> List["TrackPoint"]:
    """Merge ADS-B points with extra (POS/firehose) fixes, sorted by time.

    ADS-B is authoritative where present. An extra fix is kept only when it adds real
    coverage, so dense ADS-B stretches aren't fragmented by redundant POS/firehose points:

    * dropped if within ``dedup_seconds`` of an ADS-B point (already covered);
    * between two ADS-B points, kept only if that ADS-B gap is >= ``min_fill_gap_seconds``
      (a genuine gap worth filling, not sub-minute jitter);
    * beyond either ADS-B track end, kept (extends coverage where ADS-B never saw it);
    * dropped if another extra fix has the same time (feeds deliver some reports twice);
    * dropped if reaching it from the previous accepted point, or the next ADS-B point from
      it, needs more than ``MAX_FIX_SPEED_KMH``: it cannot be this aircraft at that time.

    ``stats`` (optional) counts ``duplicate`` and ``implausible`` drops.
    """
    if not extra_points:
        return sorted(adsb_points, key=lambda p: p.datetime)
    adsb_sorted = sorted(adsb_points, key=lambda p: p.datetime)
    adsb_times = [p.datetime for p in adsb_sorted]
    kept = []
    for e in extra_points:
        i = bisect.bisect_left(adsb_times, e.datetime)
        prev_t = adsb_times[i - 1] if i - 1 >= 0 else None
        next_t = adsb_times[i] if i < len(adsb_times) else None
        d_prev = abs((e.datetime - prev_t).total_seconds()) if prev_t else None
        d_next = abs((next_t - e.datetime).total_seconds()) if next_t else None
        if (d_prev is not None and d_prev <= dedup_seconds) or \
           (d_next is not None and d_next <= dedup_seconds):
            continue  # redundant with existing ADS-B
        if prev_t and next_t:
            if (next_t - prev_t).total_seconds() >= min_fill_gap_seconds:
                kept.append(e)  # fills a genuine ADS-B gap
        else:
            kept.append(e)      # extends beyond an ADS-B track end
    accepted: List["TrackPoint"] = []
    for e in sorted(kept, key=lambda p: p.datetime):
        if accepted and e.datetime == accepted[-1].datetime:
            if stats is not None:
                stats["duplicate"] = stats.get("duplicate", 0) + 1
            continue
        i = bisect.bisect_left(adsb_times, e.datetime)
        prev = adsb_sorted[i - 1] if i > 0 else None
        if accepted and (prev is None or accepted[-1].datetime > prev.datetime):
            prev = accepted[-1]
        nxt = adsb_sorted[i] if i < len(adsb_sorted) else None
        if _too_fast(prev, e) or _too_fast(e, nxt):
            if stats is not None:
                stats["implausible"] = stats.get("implausible", 0) + 1
            continue
        accepted.append(e)
    return sorted(adsb_sorted + accepted, key=lambda p: p.datetime)


def enrich_segments_with_sources(segments: List["FlightSegment"],
                                 pos_index: Dict[Tuple[str, str], List["TrackPoint"]],
                                 min_fill_gap_seconds: float = 300.0,
                                 stats: Optional[Dict[str, int]] = None) -> Tuple[int, int]:
    """Fill each detected flight's interior ADS-B gaps with real POS/firehose fixes.

    For every segment, gathers extra fixes for its aircraft (hex) that fall strictly inside
    the flight's own [takeoff, landing] window and merges them in (ADS-B authoritative;
    only gaps >= ``min_fill_gap_seconds`` are filled). Because the window is bounded by the
    flight's own first/last ADS-B fix, origin/destination and timing are unchanged -- this
    only densifies flights already detected from ADS-B. Mutates segments in place; returns
    (flights_enriched, fixes_added); ``stats`` collects the merge's drop counts.
    """
    flights_enriched = fixes_added = 0
    if not pos_index:
        return (0, 0)
    for seg in segments:
        if not seg.points or seg.takeoff_time is None or seg.landing_time is None:
            continue
        t0, t1 = seg.takeoff_time, seg.landing_time
        dates = {t0.strftime("%Y-%m-%d"), t1.strftime("%Y-%m-%d")}  # cover a midnight crossing
        extras = [e for d in dates for e in pos_index.get((seg.aircraft_id, d), [])
                  if t0 <= e.datetime <= t1]
        if not extras:
            continue
        merged = merge_track_with_sources(seg.points, extras,
                                          min_fill_gap_seconds=min_fill_gap_seconds, stats=stats)
        added = len(merged) - len(seg.points)
        if added <= 0:
            continue
        seg.points = merged
        seg.max_altitude = max(p.alt for p in merged)
        seg.total_distance = seg.calculate_total_distance()
        flights_enriched += 1
        fixes_added += added
    return (flights_enriched, fixes_added)


def detect_flight_segments_with_preset(track_points: List["TrackPoint"], airports: List["Airport"],
                                        preset: ConfidencePreset,
                                        max_gap_minutes: float
                                        ) -> List[Tuple[List["TrackPoint"], bool, Optional["Airport"], Optional["Airport"]]]:
    """Like detect_flight_segments, but uses preset.airport_radius_km and preset.min_segment_points."""
    if not track_points or len(track_points) < preset.min_segment_points:
        return []

    segments = []
    current_segment = []
    segment_has_gap = False
    last_point = None

    for point in track_points:
        if last_point:
            time_gap = (point.datetime - last_point.datetime).total_seconds() / 60
            if time_gap > max_gap_minutes:
                if len(current_segment) >= preset.min_segment_points:
                    origin = find_nearest_airport(
                        current_segment[0].lat, current_segment[0].lon, current_segment[0].alt,
                        airports, max_distance_km=preset.airport_radius_km,
                        max_alt_diff_ft=3000.0, lenient=True
                    )
                    destination = find_nearest_airport(
                        current_segment[-1].lat, current_segment[-1].lon, current_segment[-1].alt,
                        airports, max_distance_km=preset.airport_radius_km,
                        max_alt_diff_ft=3000.0, lenient=True
                    )
                    segments.append((current_segment, segment_has_gap, origin, destination))
                current_segment = [point]
                segment_has_gap = False
            else:
                current_segment.append(point)
        else:
            current_segment = [point]
        last_point = point

    if len(current_segment) >= preset.min_segment_points:
        origin = find_nearest_airport(
            current_segment[0].lat, current_segment[0].lon, current_segment[0].alt,
            airports, max_distance_km=preset.airport_radius_km,
            max_alt_diff_ft=3000.0, lenient=True
        )
        destination = find_nearest_airport(
            current_segment[-1].lat, current_segment[-1].lon, current_segment[-1].alt,
            airports, max_distance_km=preset.airport_radius_km,
            max_alt_diff_ft=3000.0, lenient=True
        )
        segments.append((current_segment, segment_has_gap, origin, destination))

    return segments


def detect_flight_segments(track_points: List[TrackPoint], airports: List[Airport],
                          max_gap_minutes: float) -> List[Tuple[List[TrackPoint], bool, Optional[Airport], Optional[Airport]]]:
    """
    Detect flight segments with improved airport detection
    Returns list of (points, has_gap, origin, destination) tuples
    """
    if not track_points or len(track_points) < 20:
        return []
    
    segments = []
    current_segment = []
    segment_has_gap = False
    last_point = None
    
    MIN_FLIGHT_ALTITUDE = 300  # meters - lowered threshold
    MIN_SEGMENT_POINTS = 20
    
    for point in track_points:
        if last_point:
            time_gap = (point.datetime - last_point.datetime).total_seconds() / 60
            
            # Check for large gap
            if time_gap > max_gap_minutes:
                # Save current segment if long enough
                if len(current_segment) >= MIN_SEGMENT_POINTS:
                    # Find airports for this segment
                    origin = find_nearest_airport(
                        current_segment[0].lat, current_segment[0].lon, current_segment[0].alt,
                        airports, max_distance_km=20.0, max_alt_diff_ft=3000.0, lenient=True
                    )
                    destination = find_nearest_airport(
                        current_segment[-1].lat, current_segment[-1].lon, current_segment[-1].alt,
                        airports, max_distance_km=20.0, max_alt_diff_ft=3000.0, lenient=True
                    )
                    segments.append((current_segment, segment_has_gap, origin, destination))
                
                # Start new segment
                current_segment = [point]
                segment_has_gap = False
            else:
                current_segment.append(point)
        else:
            current_segment = [point]
        
        last_point = point
    
    # Add final segment
    if len(current_segment) >= MIN_SEGMENT_POINTS:
        origin = find_nearest_airport(
            current_segment[0].lat, current_segment[0].lon, current_segment[0].alt,
            airports, max_distance_km=20.0, max_alt_diff_ft=3000.0, lenient=True
        )
        destination = find_nearest_airport(
            current_segment[-1].lat, current_segment[-1].lon, current_segment[-1].alt,
            airports, max_distance_km=20.0, max_alt_diff_ft=3000.0, lenient=True
        )
        segments.append((current_segment, segment_has_gap, origin, destination))
    
    return segments


def match_route_by_time(origin_code: str, flight_duration: float,
                       routes_dict: Dict[Tuple[str, str], float],
                       tolerance: float = 0.4) -> Optional[str]:
    """Match destination airport based on origin and flight time"""
    best_match = None
    best_diff = float('inf')

    for (orig, dest), avg_time in routes_dict.items():
        if orig == origin_code:
            time_diff = abs(flight_duration - avg_time)
            relative_diff = time_diff / avg_time

            if relative_diff < tolerance and time_diff < best_diff:
                best_diff = time_diff
                best_match = dest

    return best_match


def match_route_by_time_with_bearing(segment: "FlightSegment",
                                      routes_dict: Dict[Tuple[str, str], float],
                                      airports_dict: Dict[str, "Airport"],
                                      tolerance: float = 0.4,
                                      bearing_tolerance_deg: float = 45.0,
                                      max_cross_track_km: Optional[float] = None,
                                      along_slack: float = 1.15,
                                      ranked: Optional[List[str]] = None) -> Optional[str]:
    """Match a missing destination by scheduled enroute time + direction from the origin.

    Requires the origin->candidate bearing to align with the segment's mean cruise bearing.
    When ``max_cross_track_km`` is given, it ALSO requires the segment's airborne endpoint to
    lie within that cross-track distance of the origin->candidate great circle. The mean-bearing
    check alone cannot separate destinations that share a bearing from the origin (SBMO/SBSV/SBIL
    all lie NE of SBGR); the corridor check uses the endpoint's actual position, so a track
    truncated ~65 km short of SBMO is not mis-assigned to a same-bearing but far-off Salvador
    merely because its clipped (short) duration happens to match the shorter route. Rejected
    candidates fall through to corridor geometry, which infers the endpoint by direction.

    With the corridor gate on, the airborne endpoint must also not lie beyond the candidate
    (``along_slack``, as in corridor geometry). Cross-track alone accepts a point on the
    great circle's extension: VIV MMSM departures last seen near Mazatlan, already 450 km
    past Leon, were labelled MMSM->MMLO because the short MMLO block time fit best.

    ``ranked`` (optional) receives every destination that passed, closest time first.
    """
    if not segment.origin or not segment.points:
        return None

    seg_brg = mean_bearing(segment.points, window_minutes=15.0)
    last = segment.points[-1]
    best_match = None
    best_diff = float("inf")
    passed = []

    for (orig, dest), avg_time in routes_dict.items():
        if orig != segment.origin.code:
            continue
        if dest not in airports_dict:
            continue
        time_diff = abs(segment.flight_duration - avg_time)
        relative_diff = time_diff / avg_time
        if relative_diff >= tolerance:
            continue

        cand = airports_dict[dest]
        cand_brg = bearing(segment.origin.lat, segment.origin.lon, cand.lat, cand.lon)
        if angular_diff(cand_brg, seg_brg) > bearing_tolerance_deg:
            continue

        if max_cross_track_km is not None:
            xt = abs(cross_track_km(last.lat, last.lon,
                                    segment.origin.lat, segment.origin.lon, cand.lat, cand.lon))
            if xt > max_cross_track_km:
                continue
            if (haversine_distance(segment.origin.lat, segment.origin.lon, last.lat, last.lon)
                    > haversine_distance(segment.origin.lat, segment.origin.lon,
                                         cand.lat, cand.lon) * along_slack):
                continue

        passed.append((time_diff, dest))
        if time_diff < best_diff:
            best_diff = time_diff
            best_match = dest

    if ranked is not None:
        ranked[:] = [d for _, d in sorted(passed, key=lambda x: x[0])]
    return best_match


def infer_endpoint_by_corridor(segment: "FlightSegment",
                               routes_dict: Dict[Tuple[str, str], float],
                               airports_dict: Dict[str, "Airport"],
                               max_cross_track_km: float,
                               along_slack: float = 1.15,
                               ranked: Optional[List[str]] = None
                               ) -> Tuple[Optional[str], Optional[str]]:
    """Infer a single missing endpoint from route-network geometry (turn-robust).

    Applies only when exactly one endpoint is known (the aircraft departed from or landed
    at a definite airport) and the other end is airborne (ADS-B coverage began or ended
    mid-cruise). Candidates are restricted to the airline's actual route network, then the
    route-neighbour whose great-circle *corridor* best contains the airborne endpoint wins.

    Unlike route-time rescue, this needs no duration match, so it recovers truncated tracks
    whose clipped duration no longer resembles the scheduled enroute time. Unlike a heading
    test, it tolerates turns/deviations because it measures corridor cross-track distance.

    Returns (missing_airport_code, 'origin'|'dest'), or (None, None) if nothing qualifies.
    ``ranked`` (optional) receives every airport inside the corridor, closest first.
    """
    if not segment.points:
        return (None, None)
    p0 = segment.points[0]
    pn = segment.points[-1]

    def best_corridor_match(known_ap: "Airport", airborne, candidate_codes) -> Optional[str]:
        best_code, best_xt = None, float("inf")
        passed = []
        for code in candidate_codes:
            cand = airports_dict.get(code)
            if cand is None:
                continue
            # Forward hemisphere: the airborne end must lie roughly toward the candidate
            # from the known airport (rejects the airport "behind" the aircraft).
            if angular_diff(bearing(known_ap.lat, known_ap.lon, airborne.lat, airborne.lon),
                            bearing(known_ap.lat, known_ap.lon, cand.lat, cand.lon)) > 90.0:
                continue
            # The airborne end should fall between the two airports (track truncates
            # before reaching the far airport), allowing a little slack past it.
            d_known_air = haversine_distance(known_ap.lat, known_ap.lon, airborne.lat, airborne.lon)
            d_known_cand = haversine_distance(known_ap.lat, known_ap.lon, cand.lat, cand.lon)
            if d_known_air > d_known_cand * along_slack:
                continue
            xt = abs(cross_track_km(airborne.lat, airborne.lon,
                                    known_ap.lat, known_ap.lon, cand.lat, cand.lon))
            if xt <= max_cross_track_km:
                passed.append((xt, code))
            if xt <= max_cross_track_km and xt < best_xt:
                best_xt, best_code = xt, code
        if ranked is not None:
            ranked[:] = [c for _, c in sorted(passed, key=lambda x: x[0])]
        return best_code

    # Origin known, destination missing: airborne end is the last point.
    if segment.origin and not segment.destination:
        candidates = [dest for (orig, dest) in routes_dict if orig == segment.origin.code]
        match = best_corridor_match(segment.origin, pn, candidates)
        return (match, "dest") if match else (None, None)

    # Destination known, origin missing: airborne end is the first point.
    if segment.destination and not segment.origin:
        candidates = [orig for (orig, dest) in routes_dict if dest == segment.destination.code]
        match = best_corridor_match(segment.destination, p0, candidates)
        return (match, "origin") if match else (None, None)

    return (None, None)


def head_bearing(points: List["TrackPoint"], window_minutes: float = 10.0) -> float:
    """Average bearing over the first `window_minutes` of a track (cf. ``mean_bearing``)."""
    if not points:
        return 0.0
    cutoff = points[0].datetime + timedelta(minutes=window_minutes)
    window = [p for p in points if p.datetime <= cutoff]
    if len(window) < 2:
        window = points[:min(10, len(points))]
    return mean_bearing(window, window_minutes=window_minutes)


# A gap across which the track reverses (> TURNAROUND_MIN_REVERSAL_DEG) while covering
# ground slower than this has the aircraft on the ground somewhere, not in flight. The
# floor matches the 200 km/h lower bound of Case 5's plausible-cruise band.
TURNAROUND_MIN_REVERSAL_DEG = 120.0
TURNAROUND_MAX_GAP_SPEED_KMH = 200.0


def is_turnaround(current: "FlightSegment", next_seg: "FlightSegment", time_gap_h: float) -> bool:
    """True when ``next_seg`` is a return leg, not the continuation of ``current``."""
    if len(current.points) < 2 or len(next_seg.points) < 2:
        return False
    a, b = current.points[-1], next_seg.points[0]
    gap_km = haversine_distance(a.lat, a.lon, b.lat, b.lon)
    if gap_km / max(time_gap_h, 0.01) >= TURNAROUND_MAX_GAP_SPEED_KMH:
        return False
    return angular_diff(mean_bearing(current.points, window_minutes=10.0),
                        head_bearing(next_seg.points, window_minutes=10.0)) > TURNAROUND_MIN_REVERSAL_DEG


def assert_join_invariants(current: "FlightSegment", next_seg: "FlightSegment") -> None:
    """Enforce that any join attempt is between two segments of the same aircraft, in chronological order.

    Equal boundary timestamps (next.takeoff == current.landing) are allowed: ADS-B Exchange's KML
    placemark splitter often shares the boundary instant between adjacent placemarks. Only true
    chronological inversion (next.takeoff < current.landing) indicates data corruption.

    These conditions are guaranteed by the upstream partition-by-aircraft + sort-by-time logic;
    the assertions exist to fail loudly if a future refactor breaks that contract.
    """
    if current.aircraft_id != next_seg.aircraft_id:
        raise AssertionError(
            f"Cross-aircraft join blocked: {current.aircraft_id} vs {next_seg.aircraft_id}"
        )
    if next_seg.takeoff_time < current.landing_time:
        raise AssertionError(
            f"Non-chronological join blocked: next.takeoff={next_seg.takeoff_time} is before current.landing={current.landing_time}"
        )


def try_recover_endpoint(segment: "FlightSegment",
                         routes_dict: Dict[Tuple[str, str], float],
                         airports_dict: Dict[str, "Airport"],
                         rt_rescue: bool, geom_rescue: bool,
                         route_tol: float, corridor_xt: float, corridor_slack: float,
                         dir_aware: bool, corridor_gate: bool = False) -> str:
    """Fill a single missing origin/destination on ``segment`` in place, if possible.

    Tries the precise signals first and only guesses geometrically as a fallback:
      1. route-time (+ bearing) matching — needs the segment duration to resemble the
         scheduled enroute time, so it only works on reasonably complete tracks;
      2. corridor geometry (``geom_rescue``) — needs only one known endpoint plus the
         route network, so it recovers truncated tracks whose clipped duration no longer
         matches, and is turn-robust (see ``infer_endpoint_by_corridor``).

    Returns the rescue_method used ("route_time_forward" / "route_time_reverse" /
    "geometry_forward" / "geometry_reverse"), or "" if nothing matched. Only ever *fills*
    a missing endpoint; a segment that already has both airports is returned untouched.
    The filled end's ``*_source`` becomes ``route_time`` / ``geometry`` and its
    ``*_candidates`` every airport that rescue accepted, best first.
    """
    # Corridor gate for route-time: only trust a time+bearing match if the airborne endpoint
    # actually lies in the corridor toward that airport. Enabled for the geometry-aware presets
    # at every rescue stage (including before joining); strict/legacy keep their original
    # ungated behavior. Independent of geom_rescue so the gate can apply even where geometry
    # inference is deliberately withheld (top-of-loop, so joins still get first claim).
    mct = corridor_xt if corridor_gate else None

    # Origin known, destination missing
    if segment.origin and not segment.destination:
        if rt_rescue:
            ranked: List[str] = []
            dest_code = match_route_by_time_with_bearing(
                segment, routes_dict, airports_dict, tolerance=route_tol, max_cross_track_km=mct,
                along_slack=corridor_slack, ranked=ranked
            )
            if dest_code and dest_code in airports_dict:
                segment.destination = airports_dict[dest_code]
                segment.is_complete = True
                segment.dest_source, segment.dest_candidates = "route_time", ranked
                return "route_time_forward"
        if geom_rescue:
            ranked = []
            code, which = infer_endpoint_by_corridor(
                segment, routes_dict, airports_dict, corridor_xt, corridor_slack, ranked=ranked
            )
            if which == "dest" and code in airports_dict:
                segment.destination = airports_dict[code]
                segment.is_complete = True
                segment.dest_source, segment.dest_candidates = "geometry", ranked
                return "geometry_forward"

    # Destination known, origin missing
    elif segment.destination and not segment.origin:
        if rt_rescue:
            first = segment.points[0] if segment.points else None
            seg_brg = mean_bearing(segment.points, window_minutes=15.0) if segment.points else 0.0
            best_orig, best_xt = None, float("inf")
            passed = []
            for (orig, dest), avg_time in routes_dict.items():
                if dest != segment.destination.code or orig not in airports_dict:
                    continue
                if abs(segment.flight_duration - avg_time) >= avg_time * route_tol:
                    continue
                cand = airports_dict[orig]
                if dir_aware:
                    cand_brg = bearing(cand.lat, cand.lon,
                                       segment.destination.lat, segment.destination.lon)
                    if angular_diff(cand_brg, seg_brg) > 45.0:
                        continue
                # Corridor gate + best-fit selection: among time/bearing-consistent origins,
                # take the one whose corridor the airborne origin-end actually sits in.
                if mct is not None and first is not None:
                    xt = abs(cross_track_km(first.lat, first.lon, cand.lat, cand.lon,
                                            segment.destination.lat, segment.destination.lon))
                    if xt > mct:
                        continue
                    # ...and must not lie beyond the candidate origin (see the forward case)
                    dest_ap = segment.destination
                    if (haversine_distance(dest_ap.lat, dest_ap.lon, first.lat, first.lon)
                            > haversine_distance(dest_ap.lat, dest_ap.lon, cand.lat, cand.lon)
                            * corridor_slack):
                        continue
                    passed.append((xt, orig))
                    if xt < best_xt:
                        best_xt, best_orig = xt, orig
                else:
                    passed.append((0.0, orig))
                    if best_orig is None:
                        best_orig = orig  # legacy: first time+bearing match
            if best_orig:
                segment.origin = airports_dict[best_orig]
                segment.is_complete = True
                segment.orig_source = "route_time"
                segment.orig_candidates = [o for _, o in sorted(passed, key=lambda x: x[0])]
                return "route_time_reverse"
        if geom_rescue:
            ranked = []
            code, which = infer_endpoint_by_corridor(
                segment, routes_dict, airports_dict, corridor_xt, corridor_slack, ranked=ranked
            )
            if which == "origin" and code in airports_dict:
                segment.origin = airports_dict[code]
                segment.is_complete = True
                segment.orig_source, segment.orig_candidates = "geometry", ranked
                return "geometry_reverse"

    return ""


def combine_segments_intelligently(segments: List["FlightSegment"], airports: List["Airport"],
                                   routes_dict: Dict[Tuple[str, str], float],
                                   preset: ConfidencePreset = None,
                                   max_join_gap_hours: float = None,
                                   recorder: Optional["DiagnosticsRecorder"] = None,
                                   raw_idx_by_segment: Optional[Dict[int, int]] = None,
                                   ) -> List["FlightSegment"]:
    """Intelligently combine segments using the preset's join knobs.

    Backward-compat: if ``preset`` is None, uses the legacy hardcoded values.
    The optional ``recorder`` and ``raw_idx_by_segment`` enable diagnostics CSV output.
    """
    if not segments:
        return []

    if preset is None:
        # Legacy behavior preserved for any callers that don't pass a preset
        eff_gap = max_join_gap_hours if max_join_gap_hours is not None else 2.0
        eff_dist = 100.0
        eff_route_tol = 0.4
        rt_rescue = True
        dir_aware = False
        multi_hop = False
        geom_rescue = False
        corridor_xt = 80.0
        corridor_slack = 1.15
    else:
        eff_gap = preset.max_join_gap_hours
        eff_dist = preset.max_join_distance_km
        eff_route_tol = preset.route_time_tolerance
        rt_rescue = preset.route_time_rescue
        dir_aware = preset.direction_aware_rescue
        multi_hop = preset.multi_hop_join
        geom_rescue = preset.geometry_rescue
        corridor_xt = preset.corridor_cross_track_km
        corridor_slack = preset.corridor_along_slack

    segments.sort(key=lambda s: s.takeoff_time if s.takeoff_time else datetime.min)
    airports_dict = {a.code: a for a in airports}
    combined = []
    i = 0

    def raw_idx(seg):
        return raw_idx_by_segment.get(id(seg), -1) if raw_idx_by_segment else -1

    def record_outcome(seg, disposition, drop_reason="", joined_with=None, rescue_method="none"):
        if recorder is not None:
            return recorder.record_outcome(
                seg, segment_idx=raw_idx(seg), disposition=disposition,
                drop_reason=drop_reason, joined_with=joined_with or [],
                rescue_method=rescue_method, airports=airports
            )
        return None

    def keep(seg, disposition, rescue_method, n_segments, absorbed, final_row):
        """Accept ``seg`` as a flight; tie its diagnostics rows (absorbed + own) to it."""
        seg.disposition = disposition
        seg.rescue_method = rescue_method
        seg.joined_segment_count = n_segments
        combined.append(seg)
        for row in absorbed + [final_row]:
            if row is not None:
                row["_flight"] = seg
        if final_row is not None:
            final_row["_flight_row"] = True

    while i < len(segments):
        current = segments[i]
        joined_idxs = []
        absorbed_rows = []
        rescue_method = "none"

        if current.is_complete and current.is_valid_flight():
            keep(current, "kept_complete", "none", 1, [], record_outcome(current, "kept_complete"))
            i += 1
            continue

        # Precise recovery first: route-time (+ bearing) for a single missing endpoint.
        # Geometry inference is deliberately withheld here so real-track joining (below)
        # gets first claim on any fragment; the geometric guess only runs at last chance.
        rm = try_recover_endpoint(
            current, routes_dict, airports_dict,
            rt_rescue=rt_rescue, geom_rescue=False,
            route_tol=eff_route_tol, corridor_xt=corridor_xt,
            corridor_slack=corridor_slack, dir_aware=dir_aware,
            corridor_gate=geom_rescue,
        )
        if rm:
            rescue_method = rm
            print(f"        Enhanced {current.origin.code} -> {current.destination.code} via {rm}")
        dest_inferred = rm.endswith("_forward")

        # Join with subsequent segment(s). When multi_hop is enabled the merged segment
        # keeps absorbing further adjacent fragments in this same pass, so a flight split
        # into 3+ pieces reassembles fully; every hop is still gated by the Cases below,
        # and after each hop we re-run route-time recovery on the now-fuller track.
        base_idx = raw_idx(current)
        while i + 1 < len(segments):
            next_seg = segments[i + 1]
            if current.aircraft_id != next_seg.aircraft_id:
                break
            assert_join_invariants(current, next_seg)
            time_gap_h = (next_seg.takeoff_time - current.landing_time).total_seconds() / 3600
            if time_gap_h >= eff_gap:
                break

            should_join = False
            by_bearing = False  # Case 5's direction test passed (rescue_method bearing_join)

            # Case 1: both endpoints incomplete and close in space
            if not current.destination and not next_seg.origin:
                if current.points and next_seg.points:
                    dist = haversine_distance(
                        current.points[-1].lat, current.points[-1].lon,
                        next_seg.points[0].lat, next_seg.points[0].lon
                    )
                    if dist < eff_dist:
                        should_join = True

            # Case 2: both at altitude, very short gap
            if (current.max_altitude > 3000 and next_seg.max_altitude > 3000 and
                    time_gap_h < 0.5):
                if current.points and next_seg.points:
                    dist = haversine_distance(
                        current.points[-1].lat, current.points[-1].lon,
                        next_seg.points[0].lat, next_seg.points[0].lon
                    )
                    if dist < eff_dist:
                        should_join = True

            # Case 3: dest matches next origin → these are sequential flights, DO NOT JOIN
            if (current.destination and next_seg.origin and
                    current.destination.code == next_seg.origin.code):
                should_join = False

            # Case 4: both already complete → DO NOT JOIN
            if current.is_complete and next_seg.is_complete:
                should_join = False

            # Case 5: direction-aware mid-cruise join
            if (dir_aware and not current.destination and not next_seg.origin and
                    current.points and next_seg.points):
                seg_brg = mean_bearing(current.points, window_minutes=10.0)
                gap_brg = bearing(current.points[-1].lat, current.points[-1].lon,
                                  next_seg.points[0].lat, next_seg.points[0].lon)
                if angular_diff(seg_brg, gap_brg) <= 30.0:
                    # Sanity-check: gap distance ≈ cruise-speed × time (reject extreme outliers)
                    gap_dist = haversine_distance(
                        current.points[-1].lat, current.points[-1].lon,
                        next_seg.points[0].lat, next_seg.points[0].lon
                    )
                    implied_speed = gap_dist / max(time_gap_h, 0.01)
                    if 200 <= implied_speed <= 1100:  # km/h, plausible cruise band
                        by_bearing = True
                        should_join = True
                        if rescue_method == "none":
                            rescue_method = "bearing_join"

            # Case 6: turnaround → DO NOT JOIN. The aircraft went out and came back across a
            # coverage hole at the far airport (e.g. MMSM->MMVA->MMSM with no coverage at
            # MMVA): the heading reverses and the gap shows no progress below cruise speed.
            # Joining those fragments produced one "flight" whose labels came from two
            # different legs (VIV MMTO->MMSM, ~5x the great-circle distance).
            if should_join and is_turnaround(current, next_seg, time_gap_h):
                should_join = False

            if not should_join:
                break

            print(f"        Joining segments: {current} + {next_seg}")
            new_points = current.points + next_seg.points
            # A destination inferred for the shorter fragment is stale once the track
            # continues past it: drop it and let recovery re-run on the merged track.
            carried_dest = None if dest_inferred else current.destination
            if dest_inferred:
                dest_inferred = False
                if rescue_method.endswith("_forward"):
                    rescue_method = "none"
            new_segment = FlightSegment(
                current.aircraft_id,
                current.registration,
                new_points,
                current.origin or next_seg.origin,
                next_seg.destination or carried_dest,
                current.style_color,
                current.style_width,
                current.style_opacity,
                current.source_file,
            )
            new_segment.has_gaps = current.has_gaps or next_seg.has_gaps
            # Each end keeps the provenance of the fragment it came from; an end reached
            # only across a bearing join is as good as that join.
            if current.origin is not None:
                new_segment.orig_source = current.orig_source
                new_segment.orig_candidates = current.orig_candidates
            elif next_seg.origin is not None:
                new_segment.orig_source = next_seg.orig_source
                new_segment.orig_candidates = next_seg.orig_candidates
            if next_seg.destination is not None:
                new_segment.dest_source = ("bearing_join" if by_bearing else next_seg.dest_source)
                new_segment.dest_candidates = next_seg.dest_candidates
            elif carried_dest is not None:
                new_segment.dest_source = current.dest_source
                new_segment.dest_candidates = current.dest_candidates
            joined_idxs.append(raw_idx(next_seg))
            # Record the consumed `next_seg` outcome before overwriting `current`
            absorbed_rows.append(record_outcome(next_seg, "kept_joined", joined_with=[base_idx],
                                                rescue_method=rescue_method))
            current = new_segment
            i += 1

            # The fuller track may now match route-time where a fragment didn't; refresh
            # completeness so Cases 3/4 can correctly stop the chain at a real boundary.
            if not (current.is_complete and current.is_valid_flight()):
                rm2 = try_recover_endpoint(
                    current, routes_dict, airports_dict,
                    rt_rescue=rt_rescue, geom_rescue=False,
                    route_tol=eff_route_tol, corridor_xt=corridor_xt,
                    corridor_slack=corridor_slack, dir_aware=dir_aware,
                    corridor_gate=geom_rescue,
                )
                if rm2 and rescue_method in ("none", "bearing_join"):
                    rescue_method = rm2
                if rm2.endswith("_forward"):
                    dest_inferred = True

            if not multi_hop:
                break

        joined_with_final = ([base_idx] + joined_idxs) if joined_idxs else []

        if current.is_valid_flight():
            disp = "kept_rescued" if rescue_method != "none" else (
                "kept_joined" if joined_idxs else "kept_complete"
            )
            row = record_outcome(current, disp, joined_with=joined_with_final,
                                 rescue_method=rescue_method)
            keep(current, disp, rescue_method, 1 + len(joined_idxs), absorbed_rows, row)
        else:
            # Last chance: looser route-time tolerance plus geometry-corridor inference,
            # now applied to the fully reassembled track and to BOTH directions (a truncated
            # arrival with a known destination but airborne origin is recovered here too).
            rm3 = try_recover_endpoint(
                current, routes_dict, airports_dict,
                rt_rescue=rt_rescue, geom_rescue=geom_rescue,
                route_tol=min(eff_route_tol + 0.1, 0.7), corridor_xt=corridor_xt,
                corridor_slack=corridor_slack, dir_aware=dir_aware,
                corridor_gate=geom_rescue,
            )
            if rm3 and current.is_valid_flight():
                print(f"        Rescued segment: {current.origin.code} -> {current.destination.code} via {rm3}")
                row = record_outcome(current, "kept_rescued",
                                     joined_with=joined_with_final, rescue_method=rm3)
                keep(current, "kept_rescued", rm3, 1 + len(joined_idxs), absorbed_rows, row)
                i += 1
                continue
            record_outcome(current, "dropped_unjoined",
                           drop_reason="no_airport_match" if not current.origin or not current.destination else "invalid",
                           joined_with=joined_with_final)

        i += 1

    return combined


# ---------------------------------------------------------------------------
# Endpoint evidence (flown_routes v3). Each endpoint carries where it came from
# (``*_source``); see docs/GEOPACKAGE.md §7 for the evidence order and the values.
# ---------------------------------------------------------------------------

# Ends seen at the airport: by ADS-B at detection, or by a POS fix after --pos-extend-ends.
OBSERVED_SOURCES = ("observed", "pos_observed")
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def end_confidence(source: Optional[str], candidates: Sequence[str] = ()) -> str:
    """``high`` / ``medium`` / ``low`` for one endpoint (request F6).

    high: observed, or a planned leg matched on flight number (rule 1); medium: seen by
    POS, rotation from an observed neighbour, or a planned leg matched on tail and one
    observed end (rule 2); low: everything inferred (route time, geometry, bearing join,
    chained rotation), and any end a rescue had more than one airport to choose from.
    """
    src = source or "unresolved"
    if src == "observed" or (src.startswith("planned:") and src.endswith(":fn")):
        level = "high"
    elif src in ("pos_observed", "rotation") or (src.startswith("planned:")
                                                and src.endswith(":tail")):
        level = "medium"
    else:
        level = "low"
    if len(candidates) > 1:
        level = "low"
    return level


def endpoint_confidence(orig_source: Optional[str], dest_source: Optional[str],
                        orig_candidates: Sequence[str] = (),
                        dest_candidates: Sequence[str] = ()) -> str:
    """A flight's ``endpoint_confidence``: the lower of its two ends."""
    return min(end_confidence(orig_source, orig_candidates),
               end_confidence(dest_source, dest_candidates), key=_CONFIDENCE_RANK.get)


class DiagnosticsRecorder:
    """Buffers per-segment diagnostic rows and writes them as a CSV sidecar to the output KML."""

    COLUMNS = [
        "phase", "preset", "aircraft_id", "registration", "source_file",
        "segment_idx", "takeoff_time", "landing_time", "duration_min",
        "num_points", "max_alt_m", "total_distance_km", "mean_bearing_deg",
        "origin_code", "dest_code", "origin_dist_km", "dest_dist_km",
        "nearest_airport_start_code", "nearest_airport_start_km",
        "nearest_airport_end_code", "nearest_airport_end_km",
        "disposition", "drop_reason", "joined_with_segment_idx", "rescue_method",
        # Filled by link_flights() for the final row of each kept flight and for every
        # fragment absorbed into it; empty for dropped segments.
        "flight_id", "od_gc_km", "od_distance_ratio", "suspect_od",
    ]

    def __init__(self, output_kml_path: str, preset_name: str):
        self.set_output(output_kml_path)
        self.preset_name = preset_name
        self.rows: List[Dict[str, str]] = []

    def set_output(self, output_kml_path: str) -> None:
        self.csv_path = output_kml_path + ".diagnostics.csv"

    def _nearest_within(self, lat: float, lon: float, airports: List["Airport"], max_km: float = 1000.0):
        """Return (airport, distance_km) for the closest airport.

        The airport reference is None when nothing is within max_km, but the returned distance is always the true minimum distance to any airport in the list (or inf if airports is empty), so callers can record it in diagnostics regardless of the radius cap.
        """
        nearest_overall = None
        min_d = float("inf")
        for a in airports:
            d = haversine_distance(lat, lon, a.lat, a.lon)
            if d < min_d:
                min_d = d
                nearest_overall = a
        if nearest_overall is None:
            return (None, float("inf"))
        return (nearest_overall if min_d <= max_km else None, min_d)

    def _build_row(self, phase: str, segment: "FlightSegment", segment_idx: int, airports: List["Airport"]) -> Dict[str, str]:
        first = segment.points[0] if segment.points else None
        last = segment.points[-1] if segment.points else None

        if first is None:
            ns_a, ns_km = (None, float("inf"))
            ne_a, ne_km = (None, float("inf"))
            mb = 0.0
        else:
            ns_a, ns_km = self._nearest_within(first.lat, first.lon, airports)
            ne_a, ne_km = self._nearest_within(last.lat, last.lon, airports)
            mb = mean_bearing(segment.points, window_minutes=10.0)

        origin_dist = ""
        dest_dist = ""
        if first and segment.origin:
            origin_dist = f"{haversine_distance(first.lat, first.lon, segment.origin.lat, segment.origin.lon):.2f}"
        if last and segment.destination:
            dest_dist = f"{haversine_distance(last.lat, last.lon, segment.destination.lat, segment.destination.lon):.2f}"

        return {
            "phase": phase,
            "preset": self.preset_name,
            "aircraft_id": segment.aircraft_id,
            "registration": segment.registration,
            "source_file": segment.source_file,
            "segment_idx": str(segment_idx),
            "takeoff_time": segment.takeoff_time.isoformat() if segment.takeoff_time else "",
            "landing_time": segment.landing_time.isoformat() if segment.landing_time else "",
            "duration_min": f"{segment.flight_duration:.2f}",
            "num_points": str(len(segment.points)),
            "max_alt_m": f"{segment.max_altitude:.0f}",
            "total_distance_km": f"{segment.total_distance:.2f}",
            "mean_bearing_deg": f"{mb:.1f}",
            "origin_code": segment.origin.code if segment.origin else "",
            "dest_code": segment.destination.code if segment.destination else "",
            "origin_dist_km": origin_dist,
            "dest_dist_km": dest_dist,
            "nearest_airport_start_code": ns_a.code if ns_a else "",
            "nearest_airport_start_km": f"{ns_km:.2f}" if ns_km != float("inf") else "",
            "nearest_airport_end_code": ne_a.code if ne_a else "",
            "nearest_airport_end_km": f"{ne_km:.2f}" if ne_km != float("inf") else "",
            "disposition": "",
            "drop_reason": "",
            "joined_with_segment_idx": "",
            "rescue_method": "",
            "flight_id": "",
            "od_gc_km": "",
            "od_distance_ratio": "",
            "suspect_od": "",
        }

    def record_raw(self, segment: "FlightSegment", segment_idx: int, airports: List["Airport"]) -> None:
        self.rows.append(self._build_row("raw", segment, segment_idx, airports))

    def record_outcome(self, segment: "FlightSegment", segment_idx: int, disposition: str,
                       drop_reason: str = "", joined_with: Optional[List[int]] = None,
                       rescue_method: str = "none", airports: Optional[List["Airport"]] = None
                       ) -> Dict[str, str]:
        row = self._build_row("final", segment, segment_idx, airports or [])
        row["disposition"] = disposition
        row["drop_reason"] = drop_reason
        row["joined_with_segment_idx"] = ",".join(str(i) for i in (joined_with or []))
        row["rescue_method"] = rescue_method
        self.rows.append(row)
        return row

    def link_flights(self) -> None:
        """Copy each kept flight's id and O/D check onto the rows tied to it (``_flight``).

        The absorbed ``kept_joined`` fragments get the flight_id only; the O/D check
        describes the whole flight and goes on its own final row.
        """
        for row in self.rows:
            seg = row.get("_flight")
            if seg is None:
                continue
            row["flight_id"] = seg.flight_id
            if row.get("_flight_row"):
                row["od_gc_km"] = f"{seg.od_gc_km:.2f}" if seg.od_gc_km is not None else ""
                row["od_distance_ratio"] = (f"{seg.od_distance_ratio:.3f}"
                                            if seg.od_distance_ratio is not None else "")
                row["suspect_od"] = "1" if seg.suspect_od else "0"

    def drop_flights(self, segments: List["FlightSegment"], reason: str) -> None:
        """Re-label the rows tied to kept ``segments`` that are being excluded afterwards
        (``--takeoff-from`` / ``--takeoff-to``) as ``dropped_date_range``."""
        ids = {id(s) for s in segments}
        for row in self.rows:
            seg = row.get("_flight")
            if seg is not None and id(seg) in ids:
                row["disposition"] = "dropped_date_range"
                row["drop_reason"] = reason
                del row["_flight"]
                row.pop("_flight_row", None)

    def write_csv(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.csv_path)), exist_ok=True)
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in self.rows:
                writer.writerow(row)
        print(f"Diagnostics written to: {self.csv_path}")


# --- Altitude color palette (ADS-B Exchange / tar1090 style) -----------------
# (altitude_ft, hue_deg); fixed saturation 85%, lightness 50%.
ADSBEX_ALT_STOPS = [
    (0, 20), (2000, 40), (4000, 55), (6000, 70), (8000, 100),
    (10000, 140), (20000, 190), (30000, 240), (35000, 275), (40000, 300),
]


def hsl_to_rgb(h, s, l):
    """HSL (h in [0,360), s/l in [0,100]) -> (r, g, b) ints 0-255."""
    h = h % 360.0
    s /= 100.0
    l /= 100.0
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    mm = l - c / 2
    if h < 60:    rp, gp, bp = c, x, 0
    elif h < 120: rp, gp, bp = x, c, 0
    elif h < 180: rp, gp, bp = 0, c, x
    elif h < 240: rp, gp, bp = 0, x, c
    elif h < 300: rp, gp, bp = x, 0, c
    else:         rp, gp, bp = c, 0, x
    return (round((rp + mm) * 255), round((gp + mm) * 255), round((bp + mm) * 255))


def alt_hue(alt_ft, stops=ADSBEX_ALT_STOPS):
    """Interpolate the palette hue (deg) for an altitude in feet; clamped to the ends."""
    if alt_ft <= stops[0][0]:
        return float(stops[0][1])
    if alt_ft >= stops[-1][0]:
        return float(stops[-1][1])
    for (a0, h0), (a1, h1) in zip(stops, stops[1:]):
        if a0 <= alt_ft <= a1:
            t = (alt_ft - a0) / (a1 - a0) if a1 > a0 else 0.0
            return h0 + t * (h1 - h0)
    return float(stops[-1][1])


def rgb_to_kml(r, g, b, a=255):
    """(r,g,b[,a]) 0-255 -> KML AABBGGRR hex string."""
    return f"{a:02x}{b:02x}{g:02x}{r:02x}"


def alt_band_index(alt_m, bands):
    """Altitude in meters -> band index in [0, bands) over 0..top-of-palette (ft)."""
    top_ft = ADSBEX_ALT_STOPS[-1][0]
    alt_ft = max(0.0, alt_m / 0.3048)
    return min(bands - 1, int(min(alt_ft, top_ft) / top_ft * bands))


def alt_band_color(band, bands):
    """Band index -> banded ADS-B-Exchange KML color (AABBGGRR) at the band center."""
    top_ft = ADSBEX_ALT_STOPS[-1][0]
    center_ft = (band + 0.5) / bands * top_ft
    r, g, b = hsl_to_rgb(alt_hue(center_ft), 85, 50)
    return rgb_to_kml(r, g, b)


def create_output_kml(flight_segments: List[FlightSegment], output_file: str,
                     group_by: str = "destination",
                     sample_minutes: float = 2.0,
                     override_color: Optional[str] = None,
                     override_width: Optional[str] = None,
                     override_opacity: Optional[int] = None,
                     show_labels: bool = False,
                     show_icons: bool = False,
                     extend_to_ground: bool = False,
                     stitch_gaps: bool = False,
                     inferred_color: str = "ff888888",
                     stitch_gap_min: float = 5.0,
                     mark_sources: bool = False,
                     pos_color: str = "ff00a5ff",
                     firehose_color: str = "ff00ff00",
                     color_mode: str = "source",
                     altitude_bands: int = 24):
    """Create output KML file with organized folder structure.

    When ``stitch_gaps`` is True, each flight track is split at coverage gaps (an
    inter-point time delta > ``stitch_gap_min`` minutes) into continuous real-data
    placemarks (normal color) plus a straight-line connector placemark per gap, drawn in
    ``inferred_color`` (KML AABBGGRR) to mark the inferred/interpolated portion.

    When ``mark_sources`` is True (position-source integration), each flight track is also
    split at source changes and the stretches drawn in distinct colours -- ADS-B in the
    segment colour, ACARS position reports in ``pos_color``, ADS-B firehose in
    ``firehose_color`` (all KML AABBGGRR) -- so gap-filling real data is visibly marked and
    is *not* rendered as an inferred connector. Default (both False) keeps the original
    single-track-per-flight output unchanged.

    ``color_mode`` selects the line-colouring scheme: ``"source"`` (default) uses the
    behaviour above; ``"altitude"`` instead splits each track into ``altitude_bands`` bands
    over the ADS-B Exchange altitude gradient (see ``ADSBEX_ALT_STOPS``/``alt_band_color``)
    and colours each run by its band. ``color_mode="altitude"`` takes precedence over
    source-based coloring and labeling -- when active, ``mark_sources``-style source
    colours/labels (e.g. ``" [pos]"``) are not applied, even if ``mark_sources`` is True.
    ``altitude_bands`` (default 24) sets the number of bands and must be >= 1.
    """
    
    # Define namespace constants
    KML_NS = 'http://www.opengis.net/kml/2.2'
    GX_NS = 'http://www.google.com/kml/ext/2.2'
    
    # Register namespaces to avoid abbreviations in output
    ET.register_namespace('', KML_NS)
    ET.register_namespace('gx', GX_NS)
    
    # Create root element with explicit namespace
    kml = ET.Element(f'{{{KML_NS}}}kml')
    
    document = ET.SubElement(kml, f'{{{KML_NS}}}Document')
    name_elem = ET.SubElement(document, f'{{{KML_NS}}}name')
    # Document title = output filename stem + grouping-mode suffix
    # (e.g. voi_routes_2026_feb-mar.kml grouped by destination -> "voi_routes_2026_feb-mar_destination")
    name_elem.text = f'{os.path.splitext(os.path.basename(output_file))[0]}_{group_by}'
    
    desc_elem = ET.SubElement(document, f'{{{KML_NS}}}description')
    desc_elem.text = f'Flight segments sampled at {sample_minutes} minute intervals'
    
    routes_by_group = defaultdict(lambda: defaultdict(list))
    
    for segment in flight_segments:
        if group_by == "origin":
            group_code = segment.origin.code if segment.origin else "UNKNOWN"
        else:
            group_code = segment.destination.code if segment.destination else "UNKNOWN"
        
        route_key = segment.get_route_folder()
        routes_by_group[group_code][route_key].append(segment)
    
    total_segments = 0
    total_points_original = 0
    total_points_sampled = 0
    total_connectors = 0

    def _segment_line_color(seg):
        if override_opacity is not None:
            opacity_hex = format(int(override_opacity * 255 / 100), '02x')
            if override_color:
                return opacity_hex + override_color[2:] if len(override_color) >= 8 else opacity_hex + override_color
            return opacity_hex + seg.style_color[2:] if len(seg.style_color) >= 2 else opacity_hex + seg.style_color
        if override_color:
            return override_color
        return seg.style_color

    def _segment_description(seg, sampled):
        d = (f"Aircraft ID: {seg.aircraft_id}\n"
             f"Registration: {seg.registration}\n"
             f"Route: {seg.origin.code} -> {seg.destination.code}\n"
             f"Date: {seg.takeoff_date_str}\n"
             f"Duration: {seg.flight_duration:.1f} minutes\n"
             f"Max altitude: {seg.max_altitude:.0f}m ({seg.max_altitude*3.28084:.0f}ft)\n"
             f"Distance: {seg.total_distance:.1f} km\n"
             f"Points: {len(sampled)} (sampled from {len(seg.points)})\n")
        if seg.has_gaps:
            d += "Note: Contains data gaps\n"
        return d

    def _runs_and_gaps(pts, gap_min, split_gaps, key=lambda p: p.source):
        """Split into runs (each a same-key contiguous stretch) plus real coverage gaps.
        `key(point)` decides the grouping — data source (default) or altitude band."""
        runs, connectors = [], []
        cur = [pts[0]]
        for prev, p in zip(pts, pts[1:]):
            is_gap = split_gaps and (p.datetime - prev.datetime).total_seconds() / 60.0 > gap_min
            if is_gap:
                runs.append((cur, key(cur[0])))
                connectors.append((prev, p))
                cur = [p]
            elif key(p) != key(prev):
                runs.append((cur + [p], key(cur[0])))
                cur = [p]
            else:
                cur.append(p)
        runs.append((cur, key(cur[0])))
        return runs, connectors

    def _add_track(parent, pts, color_text, width_text, name, desc_text):
        pm = ET.SubElement(parent, f'{{{KML_NS}}}Placemark')
        if name:
            ET.SubElement(pm, f'{{{KML_NS}}}name').text = name
        if desc_text:
            ET.SubElement(pm, f'{{{KML_NS}}}description').text = desc_text
        style = ET.SubElement(pm, f'{{{KML_NS}}}Style')
        line_style = ET.SubElement(style, f'{{{KML_NS}}}LineStyle')
        ET.SubElement(line_style, f'{{{KML_NS}}}color').text = color_text
        ET.SubElement(line_style, f'{{{KML_NS}}}width').text = width_text
        icon_style = ET.SubElement(style, f'{{{KML_NS}}}IconStyle')
        if show_icons:
            icon = ET.SubElement(icon_style, f'{{{KML_NS}}}Icon')
            ET.SubElement(icon, f'{{{KML_NS}}}href').text = 'http://maps.google.com/mapfiles/kml/shapes/airports.png'
        else:
            ET.SubElement(icon_style, f'{{{KML_NS}}}scale').text = '0'
        track = ET.SubElement(pm, f'{{{GX_NS}}}Track')
        ET.SubElement(track, f'{{{KML_NS}}}altitudeMode').text = 'absolute'
        ET.SubElement(track, f'{{{KML_NS}}}extrude').text = '1' if extend_to_ground else '0'
        for point in pts:
            ET.SubElement(track, f'{{{KML_NS}}}when').text = point.timestamp
        for point in pts:
            ET.SubElement(track, f'{{{GX_NS}}}coord').text = f"{point.lon} {point.lat} {point.alt}"

    for group_code in sorted(routes_by_group.keys()):
        group_folder = ET.SubElement(document, f'{{{KML_NS}}}Folder')
        group_name = ET.SubElement(group_folder, f'{{{KML_NS}}}name')
        group_name.text = group_code
        
        for route_key in sorted(routes_by_group[group_code].keys()):
            route_folder = ET.SubElement(group_folder, f'{{{KML_NS}}}Folder')
            route_name_elem = ET.SubElement(route_folder, f'{{{KML_NS}}}name')
            route_name_elem.text = route_key
            
            segments = routes_by_group[group_code][route_key]
            segments.sort(key=lambda s: s.takeoff_time if s.takeoff_time else datetime.min)
            
            for segment in segments:
                total_segments += 1
                total_points_original += len(segment.points)
                
                sampled_points = segment.sample_points(sample_minutes)
                total_points_sampled += len(sampled_points)
                
                seg_color = _segment_line_color(segment)
                seg_width = override_width if override_width else segment.style_width
                seg_name = segment.get_segment_name() if show_labels else None
                seg_desc = _segment_description(segment, sampled_points)

                alt_mode = (color_mode == "altitude")
                if (stitch_gaps or mark_sources or alt_mode) and len(sampled_points) >= 2:
                    if alt_mode:
                        keyf = lambda p: alt_band_index(p.alt, altitude_bands)
                        colorf = lambda k: alt_band_color(k, altitude_bands)
                    else:
                        src_color = {"ADSB": seg_color, "POS": pos_color, "FIREHOSE": firehose_color}
                        keyf = lambda p: p.source
                        colorf = lambda k: (src_color.get(k, seg_color) if mark_sources else seg_color)
                    runs, connectors = _runs_and_gaps(sampled_points, stitch_gap_min, stitch_gaps, key=keyf)
                    for run, k in runs:
                        if len(run) >= 2:
                            nm = seg_name
                            if seg_name and mark_sources and not alt_mode and k != "ADSB":
                                nm = seg_name + f" [{str(k).lower()}]"
                            _add_track(route_folder, run, colorf(k), seg_width, nm, seg_desc)
                    for a, b in connectors:
                        total_connectors += 1
                        _add_track(route_folder, [a, b], inferred_color, seg_width,
                                   (seg_name + " [inferred]") if seg_name else None,
                                   "Inferred gap connector (straight-line fill across a coverage "
                                   "gap; interpolated, not measured data)")
                else:
                    _add_track(route_folder, sampled_points, seg_color, seg_width, seg_name, seg_desc)
    
    tree = ET.ElementTree(kml)
    ET.indent(tree, space='  ')
    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
    tree.write(output_file, encoding='utf-8', xml_declaration=True)
    
    print(f"\nOutput written to: {output_file}")
    print(f"Data reduction: {total_points_original} points -> {total_points_sampled} points")
    print(f"Compression ratio: {total_points_sampled/max(total_points_original,1)*100:.1f}%")
    if stitch_gaps and total_connectors:
        print(f"Gap stitching: {total_connectors} inferred connector(s) drawn in {inferred_color}")

    return total_segments


# ---------------------------------------------------------------------------
# GeoPackage output: schema `flown_routes` v1
# (docs/superpowers/specs/2026-10-27-route-gpkg-output-spec.md §3-§4, §6, §12)
#
# One `flights` row per kept flight (sampled display line), one `tracks_full` row per
# flight (every point, M = Unix epoch seconds); both are MULTILINESTRINGs split at the
# date line (v2), optional `track_points` (per point, with
# source and recomputed motion), `diagnostics` (the CSV rows + flight_id) and `meta`.
# KML is rebuilt from the file by reading the flights back into FlightSegments and
# handing them to create_output_kml, so both KML paths share one renderer.
# ---------------------------------------------------------------------------

FLOWN_SCHEMA_NAME = "flown_routes"
FLOWN_SCHEMA_VERSION = "3"
# Versions load_flown_gpkg reads. v1 stored LINESTRINGs; v2 splits lines at the date line;
# v3 adds the endpoint-evidence columns (and POS-only flights) to v2.
FLOWN_READABLE_VERSIONS = ("1", "2", "3")
SUSPECT_OD_RATIO = 2.5

FLIGHTS_COLUMNS = [
    ("flight_id", "TEXT"), ("airline", "TEXT"), ("aircraft_id", "TEXT"),
    ("registration", "TEXT"), ("callsign", "TEXT"),
    ("origin", "TEXT"), ("dest", "TEXT"), ("city_pair", "TEXT"), ("group_key", "TEXT"),
    ("takeoff_time", "DATETIME"), ("landing_time", "DATETIME"), ("flight_date", "DATE"),
    ("duration_min", "REAL"), ("distance_km", "REAL"), ("max_alt_ft", "REAL"),
    ("n_points_full", "INTEGER"), ("n_points_display", "INTEGER"),
    ("has_gaps", "INTEGER"), ("gap_duration_min", "REAL"),
    ("disposition", "TEXT"), ("rescue_method", "TEXT"), ("joined_segment_count", "INTEGER"),
    ("sources", "TEXT"), ("source_file", "TEXT"), ("preset", "TEXT"),
    ("style_color", "TEXT"), ("style_width", "REAL"), ("style_opacity", "REAL"),
    ("od_gc_km", "REAL"), ("od_distance_ratio", "REAL"), ("suspect_od", "INTEGER"),
    # v3: endpoint evidence
    ("flight_number", "TEXT"), ("flight_number_source", "TEXT"),
    ("flight_number_conflict", "INTEGER"),
    ("orig_source", "TEXT"), ("dest_source", "TEXT"),
    ("orig_free", "TEXT"), ("dest_free", "TEXT"),
    ("orig_free_source", "TEXT"), ("dest_free_source", "TEXT"),
    ("orig_candidates", "TEXT"), ("dest_candidates", "TEXT"),
    ("endpoint_confidence", "TEXT"), ("endpoint_conflict", "INTEGER"),
    ("planned_leg_key", "TEXT"), ("n_pos_fixes", "INTEGER"),
]
TRACKS_FULL_COLUMNS = [
    ("flight_id", "TEXT"), ("city_pair", "TEXT"), ("group_key", "TEXT"),
    ("origin", "TEXT"), ("dest", "TEXT"), ("flight_date", "DATE"),
]
TRACK_POINTS_COLUMNS = [
    ("flight_id", "TEXT"), ("city_pair", "TEXT"), ("group_key", "TEXT"),
    ("origin", "TEXT"), ("dest", "TEXT"), ("seq", "INTEGER"), ("t", "DATETIME"),
    ("alt_ft", "REAL"), ("gs_kt", "REAL"), ("vs_fpm", "REAL"), ("track_deg", "REAL"),
    ("source", "TEXT"),
]
DIAGNOSTICS_TYPES = {
    "segment_idx": "INTEGER", "takeoff_time": "DATETIME", "landing_time": "DATETIME",
    "duration_min": "REAL", "num_points": "INTEGER", "max_alt_m": "REAL",
    "total_distance_km": "REAL", "mean_bearing_deg": "REAL", "origin_dist_km": "REAL",
    "dest_dist_km": "REAL", "nearest_airport_start_km": "REAL",
    "nearest_airport_end_km": "REAL", "od_gc_km": "REAL", "od_distance_ratio": "REAL",
    "suspect_od": "INTEGER",
}
SOURCE_ORDER = ["ADSB", "POS", "FIREHOSE"]

# create_output_kml keyword arguments recorded in meta.kml_params, so gpkg_to_kml
# reproduces the build's KML by default.
KML_PARAM_KEYS = [
    "group_by", "sample_minutes", "override_color", "override_width", "override_opacity",
    "show_labels", "show_icons", "extend_to_ground", "stitch_gaps", "inferred_color",
    "stitch_gap_min", "mark_sources", "pos_color", "firehose_color", "color_mode",
    "altitude_bands",
]


def annotate_flights(segments: List[FlightSegment], gap_min: float,
                     suspect_ratio: float = SUSPECT_OD_RATIO) -> int:
    """Give each kept flight its id, measured coverage gaps and O/D plausibility check.

    * ``flight_id`` = ``{aircraft_id}_{takeoff:%Y%m%dT%H%M%SZ}``, stable across reruns of
      the same data. A repeat (two flights of one aircraft taking off in the same second)
      gets a ``_2``/``_3`` suffix; the number of repeats is returned.
    * ``gap_count`` / ``gap_total_min``: inter-point gaps longer than ``gap_min`` minutes in
      the full-resolution (post-enrichment) track. (``FlightSegment.has_gaps`` is left as
      is: detection never sets it, and it drives the KML description.)
    * ``od_gc_km``: great circle between the labelled airports; ``od_distance_ratio``:
      flown distance / great circle; ``suspect_od``: ratio > ``suspect_ratio``.
    """
    seen: Dict[str, int] = {}
    repeats = 0
    for seg in segments:
        base = f"{seg.aircraft_id}_{seg.takeoff_time:%Y%m%dT%H%M%SZ}"
        n = seen.get(base, 0) + 1
        seen[base] = n
        if n > 1:
            repeats += 1
        seg.flight_id = base if n == 1 else f"{base}_{n}"

        gaps = [(b.datetime - a.datetime).total_seconds() / 60.0
                for a, b in zip(seg.points, seg.points[1:])]
        long_gaps = [g for g in gaps if g > gap_min]
        seg.gap_count = len(long_gaps)
        seg.gap_total_min = sum(long_gaps)

        gc_km = haversine_distance(seg.origin.lat, seg.origin.lon,
                                   seg.destination.lat, seg.destination.lon)
        seg.od_gc_km = gc_km
        seg.od_distance_ratio = seg.total_distance / gc_km if gc_km > 0 else None
        seg.suspect_od = (seg.od_distance_ratio is not None
                          and seg.od_distance_ratio > suspect_ratio)
    return repeats


def resolve_output_path(output: str, date_from: str, date_to: str) -> str:
    """Fill ``{from}`` / ``{to}`` in --output with the first/last flight dates."""
    return output.replace("{from}", date_from).replace("{to}", date_to)


def kml_output_path(output: str) -> str:
    """--output may name the GeoPackage (``x.gpkg``); everything else keys off ``x.kml``."""
    root, ext = os.path.splitext(output)
    return root + ".kml" if ext.lower() == ".gpkg" else output


_FILE_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def filter_kml_files_by_date(files: List[str], takeoff_from: Optional[str],
                             takeoff_to: Optional[str]) -> Tuple[List[str], int]:
    """Skip daily KML files (``<hex>_YYYY-MM-DD_...kml``) that cannot hold a flight taking
    off inside [takeoff_from, takeoff_to].

    One extra day is kept on each side: the day before completes flights already airborne
    at midnight (so their tail end is not mistaken for a new flight), and the day after
    completes flights that land after midnight. Files without a date in the name are kept.
    """
    lo = ((datetime.strptime(takeoff_from, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
          if takeoff_from else None)
    hi = ((datetime.strptime(takeoff_to, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
          if takeoff_to else None)
    kept = []
    for f in files:
        m = _FILE_DATE_RE.search(os.path.basename(f))
        if m and ((lo and m.group(1) < lo) or (hi and m.group(1) > hi)):
            continue
        kept.append(f)
    return kept, len(files) - len(kept)


def gpkg_path_for(kml_path: str) -> str:
    """``<output>.gpkg`` next to ``<output>.kml``."""
    root, ext = os.path.splitext(kml_path)
    return (root if ext.lower() == ".kml" else kml_path) + ".gpkg"


def _group_key(seg: FlightSegment, group_by: str) -> str:
    # Same rule as create_output_kml's top-level folders.
    if group_by == "origin":
        return seg.origin.code if seg.origin else "UNKNOWN"
    return seg.destination.code if seg.destination else "UNKNOWN"


def _epoch(dt: datetime) -> float:
    return dt.timestamp()


def _point_motion(points: List[TrackPoint]):
    """Yield (gs_kt, vs_fpm, track_deg) per point from the previous point; None for the
    first point and wherever consecutive points share a timestamp."""
    prev = None
    for p in points:
        if prev is None:
            yield None, None, None
        else:
            dt_s = (p.datetime - prev.datetime).total_seconds()
            if dt_s <= 0:
                yield None, None, None
            else:
                gs = haversine_distance(prev.lat, prev.lon, p.lat, p.lon) / (dt_s / 3600.0) * 0.539957
                vs = (p.alt - prev.alt) * 3.28084 / (dt_s / 60.0)
                trk = (bearing(prev.lat, prev.lon, p.lat, p.lon)
                       if (p.lat, p.lon) != (prev.lat, prev.lon) else None)
                yield gs, vs, trk
        prev = p


def _style_number(text: str) -> Optional[float]:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _diag_value(col: str, value: str):
    if value is None or value == "":
        return None
    typ = DIAGNOSTICS_TYPES.get(col)
    if typ == "DATETIME":
        return gpkg_writer.format_datetime(datetime.fromisoformat(value))
    if typ == "INTEGER":
        return int(value)
    if typ == "REAL":
        return float(value)
    return value


def split_at_antimeridian(coords: Sequence[Tuple[float, ...]]) -> List[List[Tuple[float, ...]]]:
    """Parts of a ``(lon, lat, ...)`` line, cut where it crosses the date line.

    A leg whose longitudes differ by more than 180° (e.g. 170° to -170°) is taken the short
    way: it ends one part at ±180 and starts the next at ∓180. The crossing latitude, and
    any further ordinates (Z, M), are interpolated linearly in lon/lat. A line that does not
    cross is one part with exactly its coordinates. Same rule as company_route_extractor.
    """
    parts: List[List[Tuple[float, ...]]] = [[]]
    prev: Optional[Tuple[float, ...]] = None
    for c in coords:
        if prev is not None and abs(c[0] - prev[0]) > 180.0:
            edge = 180.0 if prev[0] > 0 else -180.0
            lon = c[0] + 2 * edge                      # the next vertex, unwrapped
            t = (edge - prev[0]) / (lon - prev[0])
            rest = tuple(a + t * (b - a) for a, b in zip(prev[1:], c[1:]))
            parts[-1].append((edge,) + rest)
            parts.append([(-edge,) + rest])
        parts[-1].append(tuple(c))
        prev = c
    return parts


def join_antimeridian_parts(parts: List[List[Tuple[float, ...]]]) -> List[Tuple[float, ...]]:
    """Inverse of split_at_antimeridian: the original vertices, without the ±180 ones."""
    if len(parts) == 1:
        return parts[0]
    verts: List[Tuple[float, ...]] = []
    last = len(parts) - 1
    for i, part in enumerate(parts):
        verts.extend(part[1 if i > 0 else 0:len(part) - 1 if i < last else len(part)])
    return verts


def _encode_split_line(coords: List[Tuple[float, ...]], has_m: bool):
    return gpkg_writer.encode_multilinestring(split_at_antimeridian(coords),
                                              has_z=True, has_m=has_m)


def _evidence_values(seg: FlightSegment) -> Tuple[Any, ...]:
    """The v3 ``flights`` columns of one flight, in FLIGHTS_COLUMNS order."""
    osrc = seg.orig_source or "unresolved"
    dsrc = seg.dest_source or "unresolved"
    free_o = getattr(seg, "orig_free", None) or (seg.origin.code, osrc)
    free_d = getattr(seg, "dest_free", None) or (seg.destination.code, dsrc)
    return (
        getattr(seg, "flight_number", None), getattr(seg, "flight_number_source", None),
        1 if getattr(seg, "flight_number_conflict", False) else 0,
        osrc, dsrc, free_o[0], free_d[0], free_o[1], free_d[1],
        "|".join(seg.orig_candidates) or None, "|".join(seg.dest_candidates) or None,
        endpoint_confidence(osrc, dsrc, seg.orig_candidates, seg.dest_candidates),
        1 if getattr(seg, "endpoint_conflict", False) else 0,
        getattr(seg, "planned_leg_key", None),
        sum(1 for p in seg.points if p.source == "POS"),
    )


def write_flown_gpkg(path: str, segments: List[FlightSegment], *, airline: str,
                     preset_name: str, group_by: str, sample_minutes: float,
                     include_points: bool, diagnostics_rows: List[Dict[str, Any]],
                     meta: Dict[str, str]) -> Dict[str, int]:
    """Write the ``flown_routes`` v3 GeoPackage for ``segments`` (annotated, in output order)."""
    w = gpkg_writer.GeoPackageWriter(path)
    try:
        w.add_feature_table("flights", "MULTILINESTRING", FLIGHTS_COLUMNS, z=True,
                            description="One row per kept flight; geometry is the sampled "
                                        "display line (Z metres), split at the date line.")
        w.add_feature_table("tracks_full", "MULTILINESTRING", TRACKS_FULL_COLUMNS, z=True,
                            m=True, description="Every point of each flight; M = Unix epoch "
                                                "seconds; split at the date line.")
        if include_points:
            w.add_feature_table("track_points", "POINT", TRACK_POINTS_COLUMNS, z=True,
                                description="Every point of each flight, with source and motion.")
        diag_cols = [(c, DIAGNOSTICS_TYPES.get(c, "TEXT")) for c in DiagnosticsRecorder.COLUMNS]
        w.add_attribute_table("diagnostics", diag_cols,
                              description="Per-segment detection/join outcomes (CSV sidecar rows).")
        w.add_attribute_table("meta", [("key", "TEXT"), ("value", "TEXT")],
                              description="flown_routes schema metadata")

        def flight_rows():
            for seg in segments:
                display = seg.sample_points(sample_minutes)
                blob, bbox = _encode_split_line([(p.lon, p.lat, p.alt) for p in display],
                                                has_m=False)
                present = {p.source for p in seg.points}
                yield blob, bbox, (
                    seg.flight_id, airline,
                    seg.aircraft_id if getattr(seg, "hex_known", True) else None,
                    seg.registration, None,
                    seg.origin.code, seg.destination.code, seg.get_route_folder(),
                    _group_key(seg, group_by),
                    gpkg_writer.format_datetime(seg.takeoff_time),
                    gpkg_writer.format_datetime(seg.landing_time),
                    seg.takeoff_date_str,
                    seg.flight_duration, seg.total_distance, seg.max_altitude * 3.28084,
                    len(seg.points), len(display),
                    1 if seg.gap_count else 0, seg.gap_total_min,
                    seg.disposition,
                    None if seg.rescue_method in (None, "", "none") else seg.rescue_method,
                    seg.joined_segment_count,
                    ",".join([s for s in SOURCE_ORDER if s in present]
                             + sorted(present - set(SOURCE_ORDER))),
                    seg.source_file, preset_name,
                    seg.style_color, _style_number(seg.style_width),
                    int(seg.style_opacity, 16) / 255.0 if re.fullmatch(r"[0-9a-fA-F]{2}",
                                                                       seg.style_opacity or "")
                    else None,
                    seg.od_gc_km, seg.od_distance_ratio, 1 if seg.suspect_od else 0,
                ) + _evidence_values(seg)

        def track_rows():
            for seg in segments:
                blob, bbox = _encode_split_line(
                    [(p.lon, p.lat, p.alt, _epoch(p.datetime)) for p in seg.points], has_m=True)
                yield blob, bbox, (seg.flight_id, seg.get_route_folder(),
                                   _group_key(seg, group_by), seg.origin.code,
                                   seg.destination.code, seg.takeoff_date_str)

        def point_rows():
            for seg in segments:
                cp, gk = seg.get_route_folder(), _group_key(seg, group_by)
                o, d = seg.origin.code, seg.destination.code
                for seq, (p, (gs, vs, trk)) in enumerate(zip(seg.points, _point_motion(seg.points))):
                    blob, bbox = gpkg_writer.encode_point(p.lon, p.lat, p.alt)
                    yield blob, bbox, (seg.flight_id, cp, gk, o, d, seq,
                                       gpkg_writer.format_datetime(p.datetime),
                                       p.alt * 3.28084, gs, vs, trk, p.source)

        counts = {"flights": w.insert_features("flights", flight_rows()),
                  "tracks_full": w.insert_features("tracks_full", track_rows())}
        if include_points:
            counts["track_points"] = w.insert_features("track_points", point_rows())
        counts["diagnostics"] = w.insert_rows(
            "diagnostics",
            ([_diag_value(c, row.get(c, "")) for c in DiagnosticsRecorder.COLUMNS]
             for row in diagnostics_rows))
        counts["meta"] = w.insert_rows("meta", sorted(meta.items()))

        for col in ("city_pair", "group_key", "dest", "origin", "flight_date",
                    "registration", "aircraft_id"):
            w.add_index("flights", [col])
        w.add_index("flights", ["flight_id"], unique=True)
        w.add_index("tracks_full", ["flight_id"])
        w.add_index("tracks_full", ["city_pair"])
        if include_points:
            w.add_index("track_points", ["flight_id", "seq"])
            w.add_index("track_points", ["city_pair"])
        w.add_index("diagnostics", ["flight_id"])
        w.close()
    except BaseException:
        w.abort()
        raise
    return counts


def _timestamp_from_epoch(m: float, source: str) -> str:
    """Rebuild a ``<when>`` string from an M value (ms precision).

    POS and firehose fixes were stamped to the whole second (``...:SSZ``) when loaded, so
    those keep that form; ADS-B KML timestamps carry milliseconds (``...:SS.fffZ``).
    """
    ms = int(round(m * 1000.0))
    secs, frac = divmod(ms, 1000)
    base = datetime.fromtimestamp(secs, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    if frac == 0 and source in ("POS", "FIREHOSE"):
        return base + "Z"
    return f"{base}.{frac:03d}Z"


def _style_width_text(width: Optional[float]) -> str:
    if width is None:
        return "2"
    return str(int(width)) if float(width).is_integer() else repr(float(width))


_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class StoredFlight(FlightSegment):
    """A flight read back from a ``flown_routes`` GeoPackage, for create_output_kml.

    Keeps the raw ``tracks_full`` vertices and builds TrackPoints only for the vertices
    the KML draws (rebuilding ~3M points per VIV run cost more than the KML itself).
    ``points`` is the vertex list: the renderer only takes its length. Every value the
    renderer prints is exact: times are whole milliseconds, ``max_altitude`` is the max Z,
    ``total_distance`` is the stored ``distance_km`` (the builder's own double), and
    ``sample_points`` repeats FlightSegment's arithmetic on integer milliseconds.
    """

    def __init__(self, flight_id: str, aircraft_id: str, registration: str,
                 origin: str, dest: str, style_color: str, style_width: str,
                 style_opacity: str, source_file: str, verts: List[Tuple[float, ...]],
                 sources: Optional[List[str]], distance_km: float):
        self.flight_id = flight_id
        self.aircraft_id = aircraft_id
        self.registration = registration
        # Code-only airports: the KML renderer uses nothing else from them.
        self.origin = Airport(origin, 0.0, 0.0, 0.0)
        self.destination = Airport(dest, 0.0, 0.0, 0.0)
        self.style_color = style_color
        self.style_width = style_width
        self.style_opacity = style_opacity
        self.source_file = source_file
        self.is_complete = True
        # The builder never sets FlightSegment.has_gaps (detection always reports False),
        # so the KML never carries the gap note; flights.has_gaps holds measured gaps.
        self.has_gaps = False
        self.gap_duration_min = 0
        self.points = verts
        self._sources = sources
        self._ms = [int(round(v[3] * 1000.0)) for v in verts]
        self.takeoff_time = _UNIX_EPOCH + timedelta(milliseconds=self._ms[0])
        self.landing_time = _UNIX_EPOCH + timedelta(milliseconds=self._ms[-1])
        self.flight_duration = (self.landing_time - self.takeoff_time).total_seconds() / 60
        self.takeoff_date_str = self.takeoff_time.strftime('%Y-%m-%d')
        self.max_altitude = max(v[2] for v in verts)
        self.total_distance = distance_km

    def _point(self, i: int) -> TrackPoint:
        x, y, z, m = self.points[i]
        source = self._sources[i] if self._sources else "ADSB"
        return TrackPoint(_timestamp_from_epoch(m, source), x, y, z, source=source)

    def sample_points(self, sample_minutes: float) -> List[TrackPoint]:
        ms = self._ms
        if not ms or sample_minutes <= 0:
            return [self._point(i) for i in range(len(ms))]
        keep = [0]
        last = ms[0]
        for i in range(1, len(ms) - 1):
            # == (point.datetime - last).total_seconds() / 60, bit for bit
            if (ms[i] - last) * 1000 / 10**6 / 60 >= sample_minutes:
                keep.append(i)
                last = ms[i]
        if len(ms) > 1:
            keep.append(len(ms) - 1)
        return [self._point(i) for i in keep]


def load_flown_gpkg(path: str) -> Tuple[Dict[str, str], List[FlightSegment]]:
    """Read a ``flown_routes`` v1-v3 GeoPackage back as StoredFlights, in written order.

    Points come from ``tracks_full`` (and per-point ``source`` from ``track_points`` when
    present; otherwise every point is ADS-B).
    """
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        if meta.get("schema_name") != FLOWN_SCHEMA_NAME:
            raise ValueError(f"{path} is not a {FLOWN_SCHEMA_NAME} GeoPackage")
        if meta.get("schema_version") not in FLOWN_READABLE_VERSIONS:
            raise ValueError(f"{path}: unsupported {FLOWN_SCHEMA_NAME} schema version "
                             f"{meta.get('schema_version')}")
        sources: Dict[str, List[str]] = defaultdict(list)
        has_points = conn.execute("SELECT 1 FROM gpkg_contents WHERE table_name = "
                                  "'track_points'").fetchone() is not None
        if has_points:
            for fid, src in conn.execute("SELECT flight_id, source FROM track_points "
                                         "ORDER BY flight_id, seq"):
                sources[fid].append(src)
        tracks = dict(conn.execute("SELECT flight_id, geom FROM tracks_full"))
        segments = []
        for (flight_id, aircraft_id, registration, origin, dest, color, width, opacity,
             source_file, distance_km) in conn.execute(
                "SELECT flight_id, aircraft_id, registration, origin, dest, style_color, "
                "style_width, style_opacity, source_file, distance_km FROM flights "
                "ORDER BY fid").fetchall():
            kind, _, _, verts = gpkg_writer.decode_geometry(tracks[flight_id])
            if kind == "MULTILINESTRING":
                verts = join_antimeridian_parts(verts)
            srcs = sources.get(flight_id)
            if srcs is not None and len(srcs) != len(verts):
                raise ValueError(f"{flight_id}: track_points/tracks_full point counts differ")
            segments.append(StoredFlight(
                flight_id, aircraft_id, registration, origin, dest, color,
                _style_width_text(width),
                format(int(round(opacity * 255)), "02x") if opacity is not None else "ff",
                source_file or "", verts, srcs, distance_km))
        return meta, segments
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# QGIS project (.qgz). ~/Git/qgis owns the project (navdb chart layers, basemaps, styles);
# --qgz / gpkg_to_qgz.py only run its `prepare` in the QGIS Python that its scripts/env.sh
# sets up. The project links to the GeoPackages; it copies no data.
# ---------------------------------------------------------------------------

QGIS_REPO = os.path.expanduser(os.environ.get("QGIS_REPO", "~/Git/qgis"))
QGIS_HANDOFF = "~/Git/kml/company_route_extractor/docs/handoff/qgis_prepare_partial_inputs.md"


def qgz_path_for(gpkg_path: str) -> str:
    """``<base>.qgz`` next to ``<base>.gpkg``."""
    return os.path.splitext(gpkg_path)[0] + ".qgz"


def validate_qgz_options(qgz: bool, output_format: str, navdb: Optional[str],
                         company: Optional[str]) -> Optional[str]:
    """The reason --qgz cannot run, checked before the build; None when it can."""
    if not qgz:
        return None
    if output_format == "kml":
        return "--qgz needs the GeoPackage: use --format both or gpkg"
    if not navdb:
        return "--qgz needs --navdb (the navdb_<AIRAC>.gpkg for the chart layers)"
    if not os.path.isfile(navdb):
        return f"navdb GeoPackage does not exist: {navdb}"
    if company and not os.path.isfile(company):
        return f"company GeoPackage does not exist: {company}"
    return None


def build_qgz(flown_gpkg: str, qgz: str, navdb: str, airline: str, qgis_repo: str,
              company: Optional[str] = None) -> int:
    """Build the QGIS project for a flown_routes GeoPackage with ~/Git/qgis ``prepare``.

    Passes ``--flown`` and, when given, ``--company`` (the full Route Explorer project:
    flights matched to company routes). Returns 0, or 1 with the reason on stderr; the
    GeoPackage is never touched.
    """
    if not os.path.isfile(os.path.join(qgis_repo, "scripts", "env.sh")):
        print(f"error: .qgz not built: no QGIS viewer repo at {qgis_repo} "
              "(scripts/env.sh missing; set --qgis-repo)", file=sys.stderr)
        return 1
    out_dir = os.path.dirname(os.path.abspath(qgz))
    os.makedirs(out_dir, exist_ok=True)
    args = ["--airline", airline.upper(), "--navdb", os.path.abspath(navdb),
            "--flown", os.path.abspath(flown_gpkg)]
    if company:
        args += ["--company", os.path.abspath(company)]
    args += ["--out", out_dir, "--project-name", os.path.splitext(os.path.basename(qgz))[0]]
    # stdout/stderr PIPE + universal_newlines, not capture_output/text: Python 3.6.
    proc = subprocess.run(
        ["bash", "-c", 'source scripts/env.sh && exec "$QGIS_PY" -m prepare "$@"',
         "prepare"] + args,
        cwd=qgis_repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, check=False)
    if proc.returncode != 0 or not os.path.isfile(qgz):
        tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-5:])
        print(f"error: .qgz not built: qgis prepare exited {proc.returncode}\n{tail}\n"
              f"It needs a prepare that takes --project-name and flown_routes v2; "
              f"see {QGIS_HANDOFF}.", file=sys.stderr)
        return 1
    links = os.path.basename(flown_gpkg) + (f", {os.path.basename(company)}" if company else "")
    print(f"Wrote {os.path.getsize(qgz):,} bytes to {qgz} (QGIS project; links to "
          f"{links} and {os.path.basename(navdb)})")
    return 0


def add_qgz_args(parser: argparse.ArgumentParser, with_switch: bool = True) -> None:
    """--qgz / --navdb / --company / --qgis-repo, shared with gpkg_to_qgz.py."""
    if with_switch:
        parser.add_argument('--qgz', action='store_true',
                            help='Also write <output base>.qgz next to the GeoPackage: a QGIS '
                                 'project linking it with the navdb chart layers, basemaps and '
                                 'styles. Built by ~/Git/qgis prepare in QGIS\'s Python')
    parser.add_argument('--navdb', default=None,
                        help='navdb_<AIRAC>.gpkg for the chart layers (airways, fixes, FIRs, '
                             'procedures). Linked, not copied. Required with --qgz')
    parser.add_argument('--company', default=None,
                        help='Optional company_routes GeoPackage. With it the project is the '
                             'full Route Explorer (flights matched to company routes); without '
                             'it, flown routes only')
    parser.add_argument('--qgis-repo', default=QGIS_REPO, dest='qgis_repo',
                        help='Checkout of the QGIS viewer repo whose prepare builds the '
                             'project (default: $QGIS_REPO, else ~/Git/qgis)')


def export_gpkg_to_kml(gpkg_path: str, kml_path: str, **overrides) -> int:
    """Write KML from a ``flown_routes`` GeoPackage; returns the number of flights.

    Rendering options default to the build's (``meta.kml_params``); any keyword in
    ``KML_PARAM_KEYS`` passed with a non-None value overrides it.
    """
    meta, segments = load_flown_gpkg(gpkg_path)
    params = json.loads(meta.get("kml_params", "{}"))
    for k, v in overrides.items():
        if k not in KML_PARAM_KEYS:
            raise TypeError(f"unknown KML option {k}")
        if v is not None:
            params[k] = v
    return create_output_kml(segments, kml_path, **params)


def process_kml_files(kml_files: List[str], airports: List["Airport"], routes: List["Route"],
                     output_file: str, preset: ConfidencePreset,
                     group_by: str = "destination",
                     sample_minutes: float = 2.0,
                     max_gap_minutes: float = 20.0,
                     override_color: Optional[str] = None,
                     override_width: Optional[str] = None,
                     override_opacity: Optional[int] = None,
                     show_labels: bool = False,
                     show_icons: bool = False,
                     extend_to_ground: bool = False,
                     stitch_gaps: bool = False,
                     inferred_color: str = "ff888888",
                     stitch_gap_min: float = 5.0,
                     integrate_pos: bool = False,
                     pos_folder: Optional[str] = None,
                     firehose_folder: Optional[str] = None,
                     tail_icao_path: Optional[str] = None,
                     pos_color: str = "ff00a5ff",
                     firehose_color: str = "ff00ff00",
                     color_mode: str = "source",
                     altitude_bands: int = 24,
                     output_format: str = "both",
                     gpkg_points: bool = False,
                     legacy_kml: bool = False,
                     airline: str = "",
                     suspect_od_ratio: float = SUSPECT_OD_RATIO,
                     takeoff_from: Optional[str] = None,
                     takeoff_to: Optional[str] = None,
                     argv: Optional[List[str]] = None):
    """Main processing pipeline driven by a ConfidencePreset.

    Builds the flights once, then writes them per ``output_format`` (spec §12):
    ``both`` = ``<output>.gpkg`` + ``<output>.kml``, ``gpkg`` = GeoPackage only, ``kml`` =
    KML only. KML is rendered from the GeoPackage (a temporary one for ``kml``) unless
    ``legacy_kml``, which uses the in-memory flights directly. ``{from}``/``{to}`` in
    ``output_file`` become the first/last flight dates; a ``.gpkg`` ``output_file`` names the
    GeoPackage. ``takeoff_from``/``takeoff_to`` (``YYYY-MM-DD``, inclusive) keep only flights
    whose UTC takeoff date is in range. The diagnostics CSV is always written.
    """
    output_file = kml_output_path(output_file)
    routes_dict = {(r.origin, r.destination): r.avg_time_min for r in routes}
    recorder = DiagnosticsRecorder(output_file, preset_name=preset.name)

    all_segments: List[FlightSegment] = []
    raw_idx_by_segment: Dict[int, int] = {}
    next_idx = 0

    # Optional extra position sources (ACARS POS + ADS-B firehose), keyed by (hex, date).
    pos_index: Dict[Tuple[str, str], List[TrackPoint]] = {}
    if integrate_pos:
        print("\nLoading extra position sources (this reads many small files)...")
        pos_index = load_position_sources(pos_folder, firehose_folder, tail_icao_path)
        print(f"  Loaded {sum(len(v) for v in pos_index.values())} extra fixes "
              f"across {len(pos_index)} aircraft-days")

    kml_files = sorted(kml_files)

    for input_file in kml_files:
        aircraft_id = extract_aircraft_id(input_file)
        print(f"\nProcessing: {os.path.basename(input_file)} (Aircraft: {aircraft_id})")

        all_tracks = parse_kml_tracks(input_file, aircraft_id)
        print(f"Found {len(all_tracks)} useful tracks")

        for track_points, registration, style_color, style_width, style_opacity in all_tracks:
            if not track_points:
                continue
            print(f"  Track with {len(track_points)} points (Reg: {registration})")

            raw_segments = detect_flight_segments_with_preset(
                track_points, airports, preset, max_gap_minutes=max_gap_minutes
            )
            print(f"    Detected {len(raw_segments)} raw segments")

            for segment_points, has_gap, origin, destination in raw_segments:
                if len(segment_points) < preset.min_segment_points:
                    continue
                segment = FlightSegment(
                    aircraft_id, registration, segment_points,
                    origin, destination,
                    style_color, style_width, style_opacity,
                    os.path.basename(input_file)
                )
                segment.has_gaps = has_gap

                raw_idx_by_segment[id(segment)] = next_idx
                recorder.record_raw(segment, segment_idx=next_idx, airports=airports)
                next_idx += 1

                all_segments.append(segment)
                orig = origin.code if origin else "NONE"
                dest = destination.code if destination else "NONE"
                print(f"      {orig} -> {dest}: {len(segment_points)} pts, "
                      f"{segment.flight_duration:.1f} min, max alt {segment.max_altitude:.0f}m")

    print(f"\n\nTotal raw segments collected: {len(all_segments)}")

    print("\nCombining segments by aircraft...")
    segments_by_aircraft = defaultdict(list)
    for seg in all_segments:
        segments_by_aircraft[seg.aircraft_id].append(seg)

    final_segments: List[FlightSegment] = []
    for aircraft_id, segments in segments_by_aircraft.items():
        print(f"\n  Aircraft {aircraft_id}: {len(segments)} raw segments")
        combined = combine_segments_intelligently(
            segments, airports, routes_dict,
            preset=preset, recorder=recorder,
            raw_idx_by_segment=raw_idx_by_segment
        )
        complete_valid = [s for s in combined if s.is_complete and s.is_valid_flight()]
        print(f"    Final valid flights: {len(complete_valid)}")
        final_segments.extend(complete_valid)

    print(f"\n\nTOTAL VALID COMPLETE FLIGHTS: {len(final_segments)}")

    # Keep only flights whose UTC takeoff date is inside --takeoff-from/--takeoff-to.
    if takeoff_from or takeoff_to:
        lo, hi = takeoff_from or "0000-00-00", takeoff_to or "9999-99-99"
        out_of_range = [s for s in final_segments if not lo <= s.takeoff_date_str <= hi]
        if out_of_range:
            recorder.drop_flights(out_of_range, f"takeoff outside {lo}..{hi}")
            dropped = {id(s) for s in out_of_range}
            final_segments = [s for s in final_segments if id(s) not in dropped]
        print(f"Takeoff-date filter {takeoff_from or '(any)'} .. {takeoff_to or '(any)'}: "
              f"dropped {len(out_of_range)} flight(s), {len(final_segments)} remain")

    # Enrich detected flights with real POS/firehose fixes that fall inside each flight's
    # own time window, filling in-flight ADS-B coverage gaps with measured data (not
    # straight-line inference). Detection/route-matching above is unchanged -- ADS-B alone
    # decides the flights; the extra sources only densify the ones already found.
    if integrate_pos and pos_index:
        merge_stats: Dict[str, int] = {}
        n_flights_enriched, n_fixes_added = enrich_segments_with_sources(
            final_segments, pos_index, min_fill_gap_seconds=stitch_gap_min * 60.0,
            stats=merge_stats)
        print(f"\nPosition-source integration: filled {n_fixes_added} real fix(es) into "
              f"{n_flights_enriched} of {len(final_segments)} flights "
              f"(gaps >= {stitch_gap_min:.0f} min, within each flight's own time window)")
        if merge_stats:
            print(f"  Dropped {merge_stats.get('duplicate', 0)} duplicate fix(es) and "
                  f"{merge_stats.get('implausible', 0)} implausible fix(es) (would need "
                  f"> {MAX_FIX_SPEED_KMH:.0f} km/h to or from the ADS-B track)")

    # Flight identity, measured gaps and the origin/destination plausibility check.
    repeats = annotate_flights(final_segments, gap_min=stitch_gap_min,
                               suspect_ratio=suspect_od_ratio)
    if repeats:
        print(f"\nWarning: {repeats} flight id(s) repeated (same aircraft, same takeoff "
              f"second); suffixed _2, _3, ...")
    suspect = [s for s in final_segments if s.suspect_od]
    recorder.link_flights()

    # Name outputs from the actual first/last flight (UTC takeoff) dates.
    flight_dates = sorted(s.takeoff_date_str for s in final_segments)
    date_from = flight_dates[0] if flight_dates else ""
    date_to = flight_dates[-1] if flight_dates else ""
    output_file = resolve_output_path(output_file, date_from or "nodata", date_to or "nodata")
    recorder.set_output(output_file)
    gpkg_path = gpkg_path_for(output_file)
    write_kml = output_format in ("both", "kml")
    write_gpkg = output_format in ("both", "gpkg") or (write_kml and not legacy_kml)
    keep_gpkg = output_format in ("both", "gpkg")

    route_counts = defaultdict(int)
    for seg in final_segments:
        route_counts[f"{seg.origin.code}-{seg.destination.code}"] += 1
    n_raw_segments = len(all_segments)

    kml_params = dict(
        group_by=group_by, sample_minutes=sample_minutes,
        override_color=override_color, override_width=override_width,
        override_opacity=override_opacity, show_labels=show_labels, show_icons=show_icons,
        extend_to_ground=extend_to_ground, stitch_gaps=stitch_gaps,
        inferred_color=inferred_color, stitch_gap_min=stitch_gap_min,
        mark_sources=integrate_pos, pos_color=pos_color, firehose_color=firehose_color,
        color_mode=color_mode, altitude_bands=altitude_bands,
    )

    gpkg_counts: Dict[str, int] = {}
    gpkg_seconds = 0.0
    if write_gpkg:
        if not keep_gpkg:
            fd, gpkg_path = tempfile.mkstemp(suffix=".gpkg",
                                             dir=os.path.dirname(os.path.abspath(output_file)))
            os.close(fd)
        include_points = gpkg_points or integrate_pos  # per-point source needed (spec §4.3)
        if not airline:
            print("\nWarning: --airline not given; the GeoPackage's airline fields are empty.")
        meta = {
            "schema_name": FLOWN_SCHEMA_NAME, "schema_version": FLOWN_SCHEMA_VERSION,
            "tool": "adsb_historical_routes", "tool_version": TOOL_VERSION,
            "created_utc": gpkg_writer.utc_now_str(), "airline": airline,
            "date_from": date_from, "date_to": date_to,
            "argv": json.dumps(argv if argv is not None else sys.argv[1:]),
            "source_count": str(len(kml_files)),
            "preset": preset.name, "group_by": group_by,
            "sample_minutes": str(sample_minutes), "stitch_gap_min": str(stitch_gap_min),
            "integrate_pos": "1" if integrate_pos else "0",
            "track_points": "1" if include_points else "0",
            "kml_params": json.dumps(kml_params),
            "flight_count": str(len(final_segments)),
            "suspect_od_ratio": str(suspect_od_ratio),
            "suspect_od_count": str(len(suspect)),
        }
        print(f"\nWriting GeoPackage{' (temporary, for KML)' if not keep_gpkg else ''}...")
        t0 = time.time()
        gpkg_counts = write_flown_gpkg(
            gpkg_path, final_segments, airline=airline, preset_name=preset.name,
            group_by=group_by, sample_minutes=sample_minutes, include_points=include_points,
            diagnostics_rows=recorder.rows, meta=meta)
        gpkg_seconds = time.time() - t0
        if keep_gpkg:
            print(f"GeoPackage written to: {gpkg_path}")
            print("  " + ", ".join(f"{k}: {v}" for k, v in gpkg_counts.items())
                  + f" ({gpkg_seconds:.1f}s)")

    total_flights = len(final_segments)
    if write_kml:
        print("\nCreating output KML...")
        if legacy_kml:
            total_flights = create_output_kml(final_segments, output_file, **kml_params)
        else:
            # Everything the KML needs is now in the GeoPackage: release the in-memory
            # tracks before reading it back, so peak memory stays near a single copy.
            for seg in all_segments:
                seg.points = None
            for seg in final_segments:
                seg.points = None
            all_segments = segments_by_aircraft = pos_index = None
            try:
                total_flights = export_gpkg_to_kml(gpkg_path, output_file)
            finally:
                if not keep_gpkg and os.path.exists(gpkg_path):
                    os.remove(gpkg_path)

    recorder.write_csv()

    print("\n" + "=" * 60)
    print("FLIGHT PARSING SUMMARY")
    print("=" * 60)
    print(f"Confidence preset: {preset.name}")
    print(f"Total valid complete flights: {total_flights}")
    print(f"Match rate: {total_flights}/{n_raw_segments} = "
          f"{total_flights / max(n_raw_segments, 1) * 100:.1f}%")
    print(f"Flight dates: {date_from} -> {date_to}")
    print(f"Suspect O/D (flown > {suspect_od_ratio:g}x great circle): {len(suspect)} "
          f"({len(suspect) / max(total_flights, 1) * 100:.1f}%)")

    print("\nFlights by route:")
    for route, count in sorted(route_counts.items()):
        print(f"  {route}: {count} flights")
    print("=" * 60)
    return gpkg_path if keep_gpkg else None


def main():
    # This batch tool accumulates millions of TrackPoint objects that all stay live
    # until the run ends. Python's periodic gen-2 GC would repeatedly scan them, causing
    # multi-second stalls that grow as the run progresses (observed as ~30-45s freezes
    # every few thousand files on large folders). No reference cycles are created here,
    # so plain refcounting frees everything — disabling the cyclic collector is safe and
    # removes the stalls.
    gc.disable()

    parser = argparse.ArgumentParser(
        description='Enhanced Flight Route Splitter v6 - Improved segment joining and altitude handling',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument('--kml-files', nargs='+', 
                            help='Specific KML files to process')
    input_group.add_argument('--kml-folder', 
                            help='Folder containing KML files to process')
    
    parser.add_argument('--airports', required=True,
                       help='CSV file containing airport data')
    parser.add_argument('--routes', required=True,
                       help='CSV file containing route times')
    parser.add_argument('--output', required=True,
                       help='Output KML file name; the GeoPackage is written next to it as '
                            '<output>.gpkg. {from} and {to} are replaced by the first and last '
                            'flight dates (e.g. viv_flown_{from}_{to}.kml). A name ending in '
                            '.gpkg names the GeoPackage itself (the KML/CSV use the same stem)')
    parser.add_argument('--takeoff-from', default=None, dest='takeoff_from', metavar='YYYY-MM-DD',
                       help='Keep only flights whose UTC takeoff date is on/after this date. '
                            'Daily KML files more than a day outside the range are skipped')
    parser.add_argument('--takeoff-to', default=None, dest='takeoff_to', metavar='YYYY-MM-DD',
                       help='Keep only flights whose UTC takeoff date is on/before this date')

    # Output selection (GeoPackage spec §12)
    parser.add_argument('--format', choices=['both', 'gpkg', 'kml'], default=None,
                       dest='output_format',
                       help='Outputs to write: both (default) = <output>.gpkg + <output>.kml; '
                            'gpkg = GeoPackage only; kml = KML only')
    parser.add_argument('--gpkg', action='store_true',
                       help='Deprecated alias for --format both')
    parser.add_argument('--gpkg-points', action='store_true', dest='gpkg_points',
                       help='Also write the per-point track_points layer (always on with '
                            '--integrate-pos)')
    parser.add_argument('--legacy-kml', action='store_true', dest='legacy_kml',
                       help='Write KML with the direct (pre-GeoPackage) writer instead of '
                            'rendering it from the GeoPackage. Kept for one release.')
    parser.add_argument('--airline', default='',
                       help='Airline code recorded in the GeoPackage (flights.airline, meta)')
    parser.add_argument('--suspect-od-ratio', type=float, default=SUSPECT_OD_RATIO,
                       dest='suspect_od_ratio',
                       help='Flag a flight as suspect_od when its flown distance exceeds this '
                            'multiple of the great circle between its airports (default 2.5)')
    add_qgz_args(parser)
    
    parser.add_argument('--sample', type=float, default=2.0,
                       help='Point sampling interval in minutes (default: 2)')
    
    parser.add_argument('--maxgap', type=float, default=20.0,
                       help='Maximum gap time allowed in flight segment in minutes (default: 20)')
    
    parser.add_argument('--group', choices=['origin', 'destination'], default='destination',
                       help='Group flights by origin or destination airport (default: destination)')
    
    # Style overrides
    parser.add_argument('--color', 
                       help='Override line color (KML format, e.g., ff0000ff for blue)')
    parser.add_argument('--width', 
                       help='Override line width (e.g., 2)')
    parser.add_argument('--opacity', type=int, metavar='0-100',
                       help='Override line opacity as percentage: 0-100 (default: use original)')
    
    # Display options
    parser.add_argument('--labels', choices=['true', 'false'], default='false',
                       help='Show flight labels/names (default: false)')
    parser.add_argument('--icons', choices=['true', 'false'], default='false',
                       help='Show airport icons (default: false)')
    parser.add_argument('--ground', choices=['true', 'false'], default='false',
                       help='Extend flight paths to ground (default: false)')

    # Gap stitching (off by default; opting in changes each flight into multiple placemarks)
    parser.add_argument('--stitch-gaps', choices=['true', 'false'], default='false',
                       dest='stitch_gaps',
                       help='Draw straight-line connectors across coverage gaps within a flight, '
                            'in a distinct colour, to mark inferred/interpolated data (default: false). '
                            'When true, each flight splits into real-data placemarks + gap connectors.')
    parser.add_argument('--inferred-color', default='ff888888', dest='inferred_color',
                       help='KML colour (AABBGGRR) for inferred gap connectors (default: ff888888, gray)')
    parser.add_argument('--stitch-gap-min', type=float, default=5.0, dest='stitch_gap_min',
                       help='Minutes between consecutive points above which a gap connector is drawn '
                            '(default: 5.0)')

    # Extra position sources: ACARS position reports (POS) and the ADS-B firehose
    parser.add_argument('--integrate-pos', choices=['true', 'false'], default='false',
                       dest='integrate_pos',
                       help='Merge extra position sources (ACARS POS and/or ADS-B firehose) into each '
                            'aircraft track before detection, so real fixes fill coverage gaps '
                            '(default: false). Provide --pos-folder / --firehose-folder / --tail-icao.')
    parser.add_argument('--pos-folder', default=None, dest='pos_folder',
                       help='Folder of ACARS position-report JSON files (keyed to hex via --tail-icao).')
    parser.add_argument('--firehose-folder', default=None, dest='firehose_folder',
                       help='Folder of ADS-B firehose position JSON files (carry ICAO hex directly).')
    parser.add_argument('--tail-icao', default=None, dest='tail_icao',
                       help='CSV mapping tail_number,icao_hex (required to key ACARS POS reports to hex).')
    parser.add_argument('--pos-color', default='ff00a5ff', dest='pos_color',
                       help='KML AABBGGRR colour for ACARS POS-sourced track portions (default: ff00a5ff, orange).')
    parser.add_argument('--firehose-color', default='ff00ff00', dest='firehose_color',
                       help='KML AABBGGRR colour for firehose-sourced track portions (default: ff00ff00, green).')

    parser.add_argument('--color-mode', choices=['source', 'altitude'], default='source',
                        dest='color_mode',
                        help='Line coloring: source (per data source, default) or altitude '
                             '(ADS-B Exchange altitude gradient).')
    parser.add_argument('--altitude-palette', choices=['adsbex'], default='adsbex',
                        dest='altitude_palette',
                        help='Altitude palette (default adsbex: ADS-B Exchange hue ramp).')
    parser.add_argument('--altitude-bands', type=int, default=24, dest='altitude_bands',
                        help='Number of altitude color bands (default 24).')

    # Confidence preset and per-knob overrides
    parser.add_argument('--confidence', choices=['strict', 'balanced', 'permissive'],
                       default='balanced',
                       help='Detection/joining aggressiveness preset (default: balanced)')
    parser.add_argument('--airport-radius-km', type=float, default=None,
                       dest='airport_radius_km',
                       help='Override airport-match radius in km')
    parser.add_argument('--max-join-gap-hours', type=float, default=None,
                       dest='max_join_gap_hours',
                       help='Override max gap (hours) for cross-file joining')
    parser.add_argument('--max-join-distance-km', type=float, default=None,
                       dest='max_join_distance_km',
                       help='Override max spatial gap (km) for cross-file joining')
    parser.add_argument('--route-time-tolerance', type=float, default=None,
                       dest='route_time_tolerance',
                       help='Override route-time matching tolerance (e.g., 0.30 = ±30%%)')
    parser.add_argument('--corridor-cross-track-km', type=float, default=None,
                       dest='corridor_cross_track_km',
                       help='Override corridor cross-track gate (km) for endpoint recovery. '
                            'Lower = stricter (less mis-attribution, fewer recoveries); '
                            'higher = looser. Preset defaults: balanced 70, permissive 120.')
    parser.add_argument('--route-time-rescue', choices=['on', 'off'], default=None,
                       dest='route_time_rescue',
                       help='Override route-time rescue (on/off)')

    args = parser.parse_args()
    
    # Convert string arguments to boolean
    show_labels = args.labels == 'true'
    show_icons = args.icons == 'true'
    extend_to_ground = args.ground == 'true'
    stitch_gaps = args.stitch_gaps == 'true'
    integrate_pos = args.integrate_pos == 'true'
    
    # Validate opacity if provided
    if args.opacity is not None and (args.opacity < 0 or args.opacity > 100):
        print("Error: Opacity must be between 0 and 100")
        sys.exit(1)

    # Validate altitude-bands (guards against ZeroDivisionError / nonsensical bands in
    # alt_band_color, which divides by `bands`)
    if args.altitude_bands < 1:
        print("Error: --altitude-bands must be >= 1")
        sys.exit(1)

    output_format = args.output_format or 'both'
    if args.gpkg:
        if output_format != 'both':
            print("Error: --gpkg (alias for --format both) conflicts with "
                  f"--format {output_format}")
            sys.exit(1)
        print("Note: --gpkg is deprecated; GeoPackage output is the default (--format both).")
    if args.suspect_od_ratio <= 0:
        print("Error: --suspect-od-ratio must be > 0")
        sys.exit(1)
    qgz_error = validate_qgz_options(args.qgz, output_format, args.navdb, args.company)
    if qgz_error:
        print(f"Error: {qgz_error}")
        sys.exit(1)

    kml_files = []
    if args.kml_files:
        for f in args.kml_files:
            if not os.path.exists(f):
                print(f"Error: KML file not found: {f}")
                sys.exit(1)
        kml_files = args.kml_files
    else:
        pattern = os.path.join(args.kml_folder, "*.kml")
        kml_files = glob.glob(pattern)
        if not kml_files:
            print(f"Error: No KML files found in {args.kml_folder}")
            sys.exit(1)

    for name in ('takeoff_from', 'takeoff_to'):
        value = getattr(args, name)
        if value is not None:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                print(f"Error: --{name.replace('_', '-')} must be YYYY-MM-DD, got {value!r}")
                sys.exit(1)
    if args.takeoff_from and args.takeoff_to and args.takeoff_from > args.takeoff_to:
        print("Error: --takeoff-from is after --takeoff-to")
        sys.exit(1)
    if args.takeoff_from or args.takeoff_to:
        kml_files, n_skipped = filter_kml_files_by_date(kml_files, args.takeoff_from,
                                                        args.takeoff_to)
        print(f"Takeoff dates {args.takeoff_from or '(any)'} .. {args.takeoff_to or '(any)'}: "
              f"skipping {n_skipped} KML file(s) dated outside the range")
        if not kml_files:
            print("Error: No KML files left inside the takeoff date range")
            sys.exit(1)

    if not os.path.exists(args.airports):
        print(f"Error: Airports file not found: {args.airports}")
        sys.exit(1)
    
    if not os.path.exists(args.routes):
        print(f"Error: Routes file not found: {args.routes}")
        sys.exit(1)

    # Create the output directory up-front so a bad or unmounted output path
    # fails immediately, rather than after all processing (KML/CSV are only
    # written at the very end). Guards against losing a long run to Errno 2.
    out_dir = os.path.dirname(os.path.abspath(args.output))
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as e:
        print(f"Error: Cannot create output directory {out_dir}: {e}")
        sys.exit(1)
    if not os.access(out_dir, os.W_OK):
        print(f"Error: Output directory is not writable: {out_dir}")
        sys.exit(1)

    print(f"Loading airports from: {args.airports}")
    airports = load_airports_csv(args.airports)
    
    if not airports:
        print("Error: No airports loaded!")
        sys.exit(1)
    
    print(f"Loading routes from: {args.routes}")
    routes = load_routes_csv(args.routes)
    
    print(f"\nConfiguration:")
    print(f"  Files to process: {len(kml_files)}")
    print(f"  Sample interval: {args.sample} minutes")
    print(f"  Max gap allowed: {args.maxgap} minutes")
    print(f"  Grouping: by {args.group}")
    print(f"  Output format: {output_format}"
          f"{' (legacy KML writer)' if args.legacy_kml and output_format != 'gpkg' else ''}")
    print(f"  Zero-altitude track filtering: ENABLED")
    print(f"  Cross-file segment joining: ENABLED")
    print(f"  Route time matching: ENABLED")
    
    preset = resolve_preset(args)
    print(f"  Confidence preset: {preset.name}")
    print(f"    airport_radius_km={preset.airport_radius_km}, "
          f"max_join_gap_hours={preset.max_join_gap_hours}, "
          f"max_join_distance_km={preset.max_join_distance_km}, "
          f"route_time_tolerance={preset.route_time_tolerance}, "
          f"route_time_rescue={preset.route_time_rescue}, "
          f"direction_aware_rescue={preset.direction_aware_rescue}, "
          f"multi_hop_join={preset.multi_hop_join}, "
          f"geometry_rescue={preset.geometry_rescue}, "
          f"corridor_cross_track_km={preset.corridor_cross_track_km}")

    gpkg_path = process_kml_files(
        kml_files, airports, routes, args.output,
        preset=preset,
        group_by=args.group,
        sample_minutes=args.sample,
        max_gap_minutes=args.maxgap,
        override_color=args.color,
        override_width=args.width,
        override_opacity=args.opacity,
        show_labels=show_labels,
        show_icons=show_icons,
        extend_to_ground=extend_to_ground,
        stitch_gaps=stitch_gaps,
        inferred_color=args.inferred_color,
        stitch_gap_min=args.stitch_gap_min,
        integrate_pos=integrate_pos,
        pos_folder=args.pos_folder,
        firehose_folder=args.firehose_folder,
        tail_icao_path=args.tail_icao,
        pos_color=args.pos_color,
        firehose_color=args.firehose_color,
        color_mode=args.color_mode,
        altitude_bands=args.altitude_bands,
        output_format=output_format,
        gpkg_points=args.gpkg_points,
        legacy_kml=args.legacy_kml,
        airline=args.airline,
        suspect_od_ratio=args.suspect_od_ratio,
        takeoff_from=args.takeoff_from,
        takeoff_to=args.takeoff_to,
        argv=sys.argv[1:],
    )

    rc = 0
    if args.qgz:
        print("\nBuilding the QGIS project (~/Git/qgis prepare)...")
        rc = build_qgz(gpkg_path, qgz_path_for(gpkg_path), args.navdb, args.airline,
                       args.qgis_repo, company=args.company)

    print("\nProcessing complete!" if rc == 0
          else "\nProcessing complete; the GeoPackage is written but the .qgz is not.")

    # All output files are already written and closed above. On large runs the process
    # otherwise spends tens of seconds freeing the millions of accumulated point objects
    # during interpreter shutdown — work that produces nothing. Flush our streams and exit
    # immediately so the OS reclaims the memory at once instead.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)


if __name__ == '__main__':
    main()
