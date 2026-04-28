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
    ),
    "permissive": ConfidencePreset(
        name="permissive",
        airport_radius_km=100,
        max_join_gap_hours=4.0,
        max_join_distance_km=500,
        route_time_tolerance=0.60,
        route_time_rescue=True,
        direction_aware_rescue=True,
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
                                      bearing_tolerance_deg: float = 45.0) -> Optional[str]:
    """Like match_route_by_time, but also requires the bearing from segment's origin to candidate destination to align with the segment's mean cruise bearing within bearing_tolerance_deg degrees."""
    if not segment.origin or not segment.points:
        return None

    seg_brg = mean_bearing(segment.points, window_minutes=15.0)
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

        cand_brg = bearing(segment.origin.lat, segment.origin.lon,
                           airports_dict[dest].lat, airports_dict[dest].lon)
        if angular_diff(cand_brg, seg_brg) > bearing_tolerance_deg:
            continue

        if time_diff < best_diff:
            best_diff = time_diff
            best_match = dest

    return best_match


def assert_join_invariants(current: "FlightSegment", next_seg: "FlightSegment") -> None:
    """Enforce that any join attempt is between two segments of the same aircraft, in chronological order, with a positive time gap.

    These conditions are guaranteed by the upstream partition-by-aircraft + sort-by-time logic; the assertions exist to fail loudly if a future refactor breaks that contract.
    """
    if current.aircraft_id != next_seg.aircraft_id:
        raise AssertionError(
            f"Cross-aircraft join blocked: {current.aircraft_id} vs {next_seg.aircraft_id}"
        )
    if next_seg.takeoff_time <= current.landing_time:
        raise AssertionError(
            f"Non-chronological join blocked: next.takeoff={next_seg.takeoff_time} not after current.landing={current.landing_time}"
        )


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
    else:
        eff_gap = preset.max_join_gap_hours
        eff_dist = preset.max_join_distance_km
        eff_route_tol = preset.route_time_tolerance
        rt_rescue = preset.route_time_rescue
        dir_aware = preset.direction_aware_rescue

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

        # Forward route-time rescue (origin known, dest missing) — only if preset enables it
        if rt_rescue and current.origin and not current.destination:
            dest_code = match_route_by_time_with_bearing(
                current, routes_dict, airports_dict, tolerance=eff_route_tol
            )
            if dest_code and dest_code in airports_dict:
                current.destination = airports_dict[dest_code]
                current.is_complete = True
                rescue_method = "route_time_forward"
                print(f"        Enhanced {current.origin.code} -> {dest_code} via route-time + bearing")

        # Reverse route-time rescue (dest known, origin missing)
        elif rt_rescue and not current.origin and current.destination:
            for (orig, dest), avg_time in routes_dict.items():
                if dest != current.destination.code:
                    continue
                time_diff = abs(current.flight_duration - avg_time)
                if time_diff < avg_time * eff_route_tol and orig in airports_dict:
                    if dir_aware:
                        # Bearing from origin → destination should align with current's mean bearing
                        cand_brg = bearing(airports_dict[orig].lat, airports_dict[orig].lon,
                                           current.destination.lat, current.destination.lon)
                        seg_brg = mean_bearing(current.points, window_minutes=15.0)
                        if angular_diff(cand_brg, seg_brg) > 45.0:
                            continue
                    current.origin = airports_dict[orig]
                    current.is_complete = True
                    rescue_method = "route_time_reverse"
                    print(f"        Enhanced {orig} -> {current.destination.code} via reverse route-time + bearing")
                    break

        # Try to join with the next segment
        if i + 1 < len(segments):
            next_seg = segments[i + 1]
            if current.aircraft_id == next_seg.aircraft_id:
                assert_join_invariants(current, next_seg)
                time_gap_h = (next_seg.takeoff_time - current.landing_time).total_seconds() / 3600

                if time_gap_h < eff_gap:
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

                    # Case 5 (new): direction-aware mid-cruise join
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
                                rescue_method = "bearing_join"

                    if should_join:
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
                        joined_idxs = [raw_idx(current), raw_idx(next_seg)]
                        # Record the consumed `next_seg` outcome before overwriting `current`
                        record_outcome(next_seg, "kept_joined", joined_with=[raw_idx(current)],
                                       rescue_method=rescue_method)
                        current = new_segment
                        i += 1

        if current.is_valid_flight():
            disp = "kept_rescued" if rescue_method != "none" else (
                "kept_joined" if joined_idxs else "kept_complete"
            )
            combined.append(current)
            record_outcome(current, disp, joined_with=joined_idxs, rescue_method=rescue_method)
        else:
            # Last-chance route-time rescue with looser tolerance
            if rt_rescue and current.origin and not current.destination and current.max_altitude > 3000:
                dest_code = match_route_by_time_with_bearing(
                    current, routes_dict, airports_dict, tolerance=min(eff_route_tol + 0.1, 0.7)
                )
                if dest_code and dest_code in airports_dict:
                    current.destination = airports_dict[dest_code]
                    current.is_complete = True
                    if current.is_valid_flight():
                        print(f"        Rescued segment: {current.origin.code} -> {dest_code}")
                        combined.append(current)
                        record_outcome(current, "kept_rescued",
                                       joined_with=joined_idxs, rescue_method="route_time_forward")
                        i += 1
                        continue
            record_outcome(current, "dropped_unjoined",
                           drop_reason="no_airport_match" if not current.origin or not current.destination else "invalid",
                           joined_with=joined_idxs)

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
                     extend_to_ground: bool = False):
    """Create output KML file with organized folder structure"""
    
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
    name_elem.text = f'Flight Routes by {group_by.capitalize()}'
    
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
                
                placemark = ET.SubElement(route_folder, f'{{{KML_NS}}}Placemark')
                
                if show_labels:
                    placemark_name = ET.SubElement(placemark, f'{{{KML_NS}}}name')
                    placemark_name.text = segment.get_segment_name()
                
                description = ET.SubElement(placemark, f'{{{KML_NS}}}description')
                desc_text = f"Aircraft ID: {segment.aircraft_id}\n"
                desc_text += f"Registration: {segment.registration}\n"
                desc_text += f"Route: {segment.origin.code} -> {segment.destination.code}\n"
                desc_text += f"Date: {segment.takeoff_date_str}\n"
                desc_text += f"Duration: {segment.flight_duration:.1f} minutes\n"
                desc_text += f"Max altitude: {segment.max_altitude:.0f}m ({segment.max_altitude*3.28084:.0f}ft)\n"
                desc_text += f"Distance: {segment.total_distance:.1f} km\n"
                desc_text += f"Points: {len(sampled_points)} (sampled from {len(segment.points)})\n"
                if segment.has_gaps:
                    desc_text += "Note: Contains data gaps\n"
                description.text = desc_text
                
                style = ET.SubElement(placemark, f'{{{KML_NS}}}Style')
                line_style = ET.SubElement(style, f'{{{KML_NS}}}LineStyle')
                
                color_elem = ET.SubElement(line_style, f'{{{KML_NS}}}color')
                if override_opacity is not None:
                    # Convert percentage (0-100) to hex (00-ff)
                    opacity_hex = format(int(override_opacity * 255 / 100), '02x')
                    if override_color:
                        # Use override color with opacity
                        color_elem.text = opacity_hex + override_color[2:] if len(override_color) >= 8 else opacity_hex + override_color
                    else:
                        # Use segment color with override opacity
                        color_elem.text = opacity_hex + segment.style_color[2:] if len(segment.style_color) >= 2 else opacity_hex + segment.style_color
                elif override_color:
                    color_elem.text = override_color
                else:
                    color_elem.text = segment.style_color
                
                width_elem = ET.SubElement(line_style, f'{{{KML_NS}}}width')
                width_elem.text = override_width if override_width else segment.style_width
                
                # Icon style - hide by default unless show_icons is True
                icon_style = ET.SubElement(style, f'{{{KML_NS}}}IconStyle')
                if show_icons:
                    icon = ET.SubElement(icon_style, f'{{{KML_NS}}}Icon')
                    href = ET.SubElement(icon, f'{{{KML_NS}}}href')
                    href.text = 'http://maps.google.com/mapfiles/kml/shapes/airports.png'
                else:
                    # Hide icon
                    scale = ET.SubElement(icon_style, f'{{{KML_NS}}}scale')
                    scale.text = '0'
                
                track = ET.SubElement(placemark, f'{{{GX_NS}}}Track')
                
                altitude_mode = ET.SubElement(track, f'{{{KML_NS}}}altitudeMode')
                altitude_mode.text = 'absolute'
                
                extrude = ET.SubElement(track, f'{{{KML_NS}}}extrude')
                extrude.text = '1' if extend_to_ground else '0'
                
                for point in sampled_points:
                    when = ET.SubElement(track, f'{{{KML_NS}}}when')
                    when.text = point.timestamp
                
                for point in sampled_points:
                    coord = ET.SubElement(track, f'{{{GX_NS}}}coord')
                    coord.text = f"{point.lon} {point.lat} {point.alt}"
    
    tree = ET.ElementTree(kml)
    ET.indent(tree, space='  ')
    tree.write(output_file, encoding='utf-8', xml_declaration=True)
    
    print(f"\nOutput written to: {output_file}")
    print(f"Data reduction: {total_points_original} points -> {total_points_sampled} points")
    print(f"Compression ratio: {total_points_sampled/max(total_points_original,1)*100:.1f}%")
    
    return total_segments


def process_kml_files(kml_files: List[str], airports: List[Airport], routes: List[Route],
                     output_file: str, group_by: str = "destination",
                     sample_minutes: float = 2.0,
                     max_gap_minutes: float = 20.0,
                     override_color: Optional[str] = None,
                     override_width: Optional[str] = None,
                     override_opacity: Optional[int] = None,
                     show_labels: bool = False,
                     show_icons: bool = False,
                     extend_to_ground: bool = False):
    """Main processing function with improved segment joining"""
    
    airports_dict = {airport.code: airport for airport in airports}
    
    routes_dict = {}
    for route in routes:
        routes_dict[(route.origin, route.destination)] = route.avg_time_min
    
    # Collect ALL segments from ALL files first (for cross-file joining)
    all_segments = []
    
    # Sort files by date
    kml_files.sort()
    
    for input_file in kml_files:
        aircraft_id = extract_aircraft_id(input_file)
        print(f"\nProcessing: {os.path.basename(input_file)} (Aircraft: {aircraft_id})")
        
        all_tracks = parse_kml_tracks(input_file, aircraft_id)
        print(f"Found {len(all_tracks)} useful tracks (zero-altitude tracks skipped)")
        
        for track_points, registration, style_color, style_width, style_opacity in all_tracks:
            if not track_points:
                continue
            
            print(f"  Processing track with {len(track_points)} points (Reg: {registration})")
            
            raw_segments = detect_flight_segments(track_points, airports, max_gap_minutes)
            print(f"    Detected {len(raw_segments)} raw segments")
            
            for segment_points, has_gap, origin, destination in raw_segments:
                if len(segment_points) < 20:
                    continue
                
                segment = FlightSegment(
                    aircraft_id, registration, segment_points,
                    origin, destination,
                    style_color, style_width, style_opacity,
                    os.path.basename(input_file)
                )
                segment.has_gaps = has_gap
                
                all_segments.append(segment)
                
                orig = origin.code if origin else "NONE"
                dest = destination.code if destination else "NONE"
                print(f"      {orig} -> {dest}: {len(segment_points)} pts, "
                      f"{segment.flight_duration:.1f} min, max alt {segment.max_altitude:.0f}m")
    
    print(f"\n\nTotal raw segments collected: {len(all_segments)}")
    
    # Group by aircraft for intelligent combination
    print("\nCombining segments intelligently by aircraft...")
    segments_by_aircraft = defaultdict(list)
    for seg in all_segments:
        segments_by_aircraft[seg.aircraft_id].append(seg)
    
    final_segments = []
    for aircraft_id, segments in segments_by_aircraft.items():
        print(f"\n  Aircraft {aircraft_id}: {len(segments)} raw segments")
        
        combined = combine_segments_intelligently(segments, airports, routes_dict)
        
        complete_valid = [s for s in combined if s.is_complete and s.is_valid_flight()]
        print(f"    Final valid flights: {len(complete_valid)}")
        
        for seg in complete_valid:
            print(f"      {seg.origin.code} -> {seg.destination.code}: "
                  f"{seg.flight_duration:.1f} min, {seg.max_altitude:.0f}m, "
                  f"{seg.total_distance:.0f}km")
        
        final_segments.extend(complete_valid)
    
    print(f"\n\nTOTAL VALID COMPLETE FLIGHTS: {len(final_segments)}")
    
    print("\nCreating output KML...")
    total_flights = create_output_kml(
        final_segments, output_file, group_by,
        sample_minutes,
        override_color, override_width, override_opacity,
        show_labels, show_icons, extend_to_ground
    )
    
    print("\n" + "="*60)
    print("FLIGHT PARSING SUMMARY")
    print("="*60)
    print(f"Total valid complete flights parsed: {total_flights}")
    print(f"Match rate: {total_flights}/{len(all_segments)} = {total_flights/max(len(all_segments),1)*100:.1f}%")
    
    route_counts = defaultdict(int)
    for seg in final_segments:
        route = f"{seg.origin.code}-{seg.destination.code}"
        route_counts[route] += 1
    
    print("\nFlights by route:")
    for route, count in sorted(route_counts.items()):
        print(f"  {route}: {count} flights")
    
    print("="*60)


def main():
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
    
    args = parser.parse_args()
    
    # Convert string arguments to boolean
    show_labels = args.labels == 'true'
    show_icons = args.icons == 'true'
    extend_to_ground = args.ground == 'true'
    
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
    
    process_kml_files(
        kml_files, airports, routes, args.output,
        group_by=args.group,
        sample_minutes=args.sample,
        max_gap_minutes=args.maxgap,
        override_color=args.color,
        override_width=args.width,
        override_opacity=args.opacity,
        show_labels=show_labels,
        show_icons=show_icons,
        extend_to_ground=extend_to_ground
    )
    
    print("\nProcessing complete!")


if __name__ == '__main__':
    main()
