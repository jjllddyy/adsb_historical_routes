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
from datetime import datetime, timedelta
from math import radians, cos, sin, asin, sqrt
import argparse
import sys
import os
import gc
import glob
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import List, Tuple, Dict, Optional, Set
import csv
import re


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
    def __init__(self, timestamp: str, lon: float, lat: float, alt: float):
        self.timestamp = timestamp
        self.lon = lon
        self.lat = lat
        self.alt = alt  # in meters
        self.datetime = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        self.groundspeed = None
        self.vertical_speed = None
    
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


def find_nearest_airport(lat: float, lon: float, alt_meters: float, airports: List[Airport], 
                        max_distance_km: float = 15.0, max_alt_diff_ft: float = 3000.0,
                        lenient: bool = False) -> Optional[Airport]:
    """
    Find the nearest airport within distance and altitude constraints
    lenient mode: used for takeoff/landing detection, more forgiving on altitude
    """
    nearest = None
    min_distance = float('inf')
    alt_feet = alt_meters * 3.28084  # Convert meters to feet
    
    for airport in airports:
        distance = haversine_distance(lat, lon, airport.lat, airport.lon)
        alt_diff = abs(alt_feet - airport.elevation)
        
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
                                      max_cross_track_km: Optional[float] = None) -> Optional[str]:
    """Match a missing destination by scheduled enroute time + direction from the origin.

    Requires the origin->candidate bearing to align with the segment's mean cruise bearing.
    When ``max_cross_track_km`` is given, it ALSO requires the segment's airborne endpoint to
    lie within that cross-track distance of the origin->candidate great circle. The mean-bearing
    check alone cannot separate destinations that share a bearing from the origin (SBMO/SBSV/SBIL
    all lie NE of SBGR); the corridor check uses the endpoint's actual position, so a track
    truncated ~65 km short of SBMO is not mis-assigned to a same-bearing but far-off Salvador
    merely because its clipped (short) duration happens to match the shorter route. Rejected
    candidates fall through to corridor geometry, which infers the endpoint by direction.
    """
    if not segment.origin or not segment.points:
        return None

    seg_brg = mean_bearing(segment.points, window_minutes=15.0)
    last = segment.points[-1]
    best_match = None
    best_diff = float("inf")

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

        if time_diff < best_diff:
            best_diff = time_diff
            best_match = dest

    return best_match


def infer_endpoint_by_corridor(segment: "FlightSegment",
                               routes_dict: Dict[Tuple[str, str], float],
                               airports_dict: Dict[str, "Airport"],
                               max_cross_track_km: float,
                               along_slack: float = 1.15
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
    """
    if not segment.points:
        return (None, None)
    p0 = segment.points[0]
    pn = segment.points[-1]

    def best_corridor_match(known_ap: "Airport", airborne, candidate_codes) -> Optional[str]:
        best_code, best_xt = None, float("inf")
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
            if xt <= max_cross_track_km and xt < best_xt:
                best_xt, best_code = xt, code
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
            dest_code = match_route_by_time_with_bearing(
                segment, routes_dict, airports_dict, tolerance=route_tol, max_cross_track_km=mct
            )
            if dest_code and dest_code in airports_dict:
                segment.destination = airports_dict[dest_code]
                segment.is_complete = True
                return "route_time_forward"
        if geom_rescue:
            code, which = infer_endpoint_by_corridor(
                segment, routes_dict, airports_dict, corridor_xt, corridor_slack
            )
            if which == "dest" and code in airports_dict:
                segment.destination = airports_dict[code]
                segment.is_complete = True
                return "geometry_forward"

    # Destination known, origin missing
    elif segment.destination and not segment.origin:
        if rt_rescue:
            first = segment.points[0] if segment.points else None
            seg_brg = mean_bearing(segment.points, window_minutes=15.0) if segment.points else 0.0
            best_orig, best_xt = None, float("inf")
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
                    if xt < best_xt:
                        best_xt, best_orig = xt, orig
                elif best_orig is None:
                    best_orig = orig  # legacy: first time+bearing match
            if best_orig:
                segment.origin = airports_dict[best_orig]
                segment.is_complete = True
                return "route_time_reverse"
        if geom_rescue:
            code, which = infer_endpoint_by_corridor(
                segment, routes_dict, airports_dict, corridor_xt, corridor_slack
            )
            if which == "origin" and code in airports_dict:
                segment.origin = airports_dict[code]
                segment.is_complete = True
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
            recorder.record_outcome(
                seg, segment_idx=raw_idx(seg), disposition=disposition,
                drop_reason=drop_reason, joined_with=joined_with or [],
                rescue_method=rescue_method, airports=airports
            )

    while i < len(segments):
        current = segments[i]
        joined_idxs = []
        rescue_method = "none"

        if current.is_complete and current.is_valid_flight():
            combined.append(current)
            record_outcome(current, "kept_complete")
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
                        should_join = True
                        if rescue_method == "none":
                            rescue_method = "bearing_join"

            if not should_join:
                break

            print(f"        Joining segments: {current} + {next_seg}")
            new_points = current.points + next_seg.points
            new_segment = FlightSegment(
                current.aircraft_id,
                current.registration,
                new_points,
                current.origin or next_seg.origin,
                next_seg.destination or current.destination,
                current.style_color,
                current.style_width,
                current.style_opacity,
                current.source_file,
            )
            new_segment.has_gaps = current.has_gaps or next_seg.has_gaps
            joined_idxs.append(raw_idx(next_seg))
            # Record the consumed `next_seg` outcome before overwriting `current`
            record_outcome(next_seg, "kept_joined", joined_with=[base_idx],
                           rescue_method=rescue_method)
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

            if not multi_hop:
                break

        joined_with_final = ([base_idx] + joined_idxs) if joined_idxs else []

        if current.is_valid_flight():
            disp = "kept_rescued" if rescue_method != "none" else (
                "kept_joined" if joined_idxs else "kept_complete"
            )
            combined.append(current)
            record_outcome(current, disp, joined_with=joined_with_final, rescue_method=rescue_method)
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
                combined.append(current)
                record_outcome(current, "kept_rescued",
                               joined_with=joined_with_final, rescue_method=rm3)
                i += 1
                continue
            record_outcome(current, "dropped_unjoined",
                           drop_reason="no_airport_match" if not current.origin or not current.destination else "invalid",
                           joined_with=joined_with_final)

        i += 1

    return combined


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
    ]

    def __init__(self, output_kml_path: str, preset_name: str):
        self.csv_path = output_kml_path + ".diagnostics.csv"
        self.preset_name = preset_name
        self.rows: List[Dict[str, str]] = []

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
        }

    def record_raw(self, segment: "FlightSegment", segment_idx: int, airports: List["Airport"]) -> None:
        self.rows.append(self._build_row("raw", segment, segment_idx, airports))

    def record_outcome(self, segment: "FlightSegment", segment_idx: int, disposition: str,
                       drop_reason: str = "", joined_with: Optional[List[int]] = None,
                       rescue_method: str = "none", airports: Optional[List["Airport"]] = None) -> None:
        row = self._build_row("final", segment, segment_idx, airports or [])
        row["disposition"] = disposition
        row["drop_reason"] = drop_reason
        row["joined_with_segment_idx"] = ",".join(str(i) for i in (joined_with or []))
        row["rescue_method"] = rescue_method
        self.rows.append(row)

    def write_csv(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.csv_path)), exist_ok=True)
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.COLUMNS)
            writer.writeheader()
            for row in self.rows:
                writer.writerow(row)
        print(f"Diagnostics written to: {self.csv_path}")


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
                     stitch_gap_min: float = 5.0):
    """Create output KML file with organized folder structure.

    When ``stitch_gaps`` is True, each flight track is split at coverage gaps (an
    inter-point time delta > ``stitch_gap_min`` minutes) into continuous real-data
    placemarks (normal color) plus a straight-line connector placemark per gap, drawn in
    ``inferred_color`` (KML AABBGGRR) to mark the inferred/interpolated portion. Default
    (False) keeps the original single-track-per-flight output unchanged.
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

    def _split_runs_and_gaps(pts, gap_min):
        """Split points into continuous runs plus the (prev, next) pairs bracketing each gap."""
        runs, connectors = [], []
        cur = [pts[0]]
        for prev, p in zip(pts, pts[1:]):
            if (p.datetime - prev.datetime).total_seconds() / 60.0 > gap_min:
                runs.append(cur)
                connectors.append((prev, p))
                cur = [p]
            else:
                cur.append(p)
        runs.append(cur)
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

                if stitch_gaps and len(sampled_points) >= 2:
                    # Real data in the segment's colour, split at coverage gaps; each gap
                    # bridged by a straight-line connector placemark in the inferred colour.
                    runs, connectors = _split_runs_and_gaps(sampled_points, stitch_gap_min)
                    for run in runs:
                        if len(run) >= 2:
                            _add_track(route_folder, run, seg_color, seg_width, seg_name, seg_desc)
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
                     stitch_gap_min: float = 5.0):
    """Main processing pipeline driven by a ConfidencePreset, emitting both KML and a diagnostics CSV."""
    routes_dict = {(r.origin, r.destination): r.avg_time_min for r in routes}
    recorder = DiagnosticsRecorder(output_file, preset_name=preset.name)

    all_segments: List[FlightSegment] = []
    raw_idx_by_segment: Dict[int, int] = {}
    next_idx = 0

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

    print("\nCreating output KML...")
    total_flights = create_output_kml(
        final_segments, output_file, group_by,
        sample_minutes,
        override_color, override_width, override_opacity,
        show_labels, show_icons, extend_to_ground,
        stitch_gaps, inferred_color, stitch_gap_min
    )

    recorder.write_csv()

    print("\n" + "=" * 60)
    print("FLIGHT PARSING SUMMARY")
    print("=" * 60)
    print(f"Confidence preset: {preset.name}")
    print(f"Total valid complete flights: {total_flights}")
    print(f"Match rate: {total_flights}/{len(all_segments)} = "
          f"{total_flights / max(len(all_segments), 1) * 100:.1f}%")

    route_counts = defaultdict(int)
    for seg in final_segments:
        route_counts[f"{seg.origin.code}-{seg.destination.code}"] += 1
    print("\nFlights by route:")
    for route, count in sorted(route_counts.items()):
        print(f"  {route}: {count} flights")
    print("=" * 60)


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
                       help='Output KML file name')
    
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
    
    # Validate opacity if provided
    if args.opacity is not None and (args.opacity < 0 or args.opacity > 100):
        print("Error: Opacity must be between 0 and 100")
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

    process_kml_files(
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
    )
    
    print("\nProcessing complete!")

    # All output files are already written and closed above. On large runs the process
    # otherwise spends tens of seconds freeing the millions of accumulated point objects
    # during interpreter shutdown — work that produces nothing. Flush our streams and exit
    # immediately so the OS reclaims the memory at once instead.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == '__main__':
    main()
