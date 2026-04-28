#!/usr/bin/env python3
"""
Enhanced Flight Route Splitter v5
Processes KML files containing continuous flight tracks and splits them into individual routes
based on takeoff/landing events, matching airports by proximity and handling incomplete segments.
Version 5: Aggressive intermediate landing detection before filtering
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from math import radians, cos, sin, asin, sqrt
import argparse
import sys
import os
import glob
from collections import defaultdict
from typing import List, Tuple, Dict, Optional, Set
import csv
import re


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
        self.alt = alt  # in meters - PRESERVE THIS
        self.datetime = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        self.groundspeed = None  # Will be calculated if possible
        self.vertical_speed = None  # Will be calculated if possible
    
    def __repr__(self):
        return f"TrackPoint({self.timestamp}, {self.lat:.4f}, {self.lon:.4f}, {self.alt:.0f}m)"


class FlightSegment:
    """Represents a complete flight from origin to destination"""
    def __init__(self, aircraft_id: str, registration: str, points: List[TrackPoint], 
                 origin: Optional[Airport], destination: Optional[Airport],
                 style_color: str, style_width: str, style_opacity: str = "ff"):
        self.aircraft_id = aircraft_id  # From filename
        self.registration = registration  # From KML content
        self.points = points
        self.origin = origin
        self.destination = destination
        self.style_color = style_color
        self.style_width = style_width
        self.style_opacity = style_opacity
        self.is_complete = (origin is not None and destination is not None)
        self.has_gaps = False
        self.gap_duration_min = 0
        self.max_altitude = 0
        self.total_distance = 0
        
        # Calculate flight metrics
        if points:
            self.takeoff_time = points[0].datetime
            self.landing_time = points[-1].datetime
            self.flight_duration = (self.landing_time - self.takeoff_time).total_seconds() / 60
            self.takeoff_date_str = points[0].datetime.strftime('%Y-%m-%d')
            
            # Calculate max altitude and total distance
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
    
    def is_valid_flight(self, min_altitude_m: float = 1000, min_duration_min: float = 20, 
                        min_distance_km: float = 20) -> bool:
        """Check if this is a valid flight vs ground operations"""
        # Same airport segments are not valid flights
        if self.origin and self.destination and self.origin.code == self.destination.code:
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
        """Sample points at specified interval to reduce data size while preserving altitude"""
        if not self.points or sample_minutes <= 0:
            return self.points
        
        sampled = [self.points[0]]  # Always include first point with its altitude
        last_sampled_time = self.points[0].datetime
        
        for point in self.points[1:-1]:  # Exclude first and last
            time_diff = (point.datetime - last_sampled_time).total_seconds() / 60
            if time_diff >= sample_minutes:
                sampled.append(point)  # Point includes original altitude
                last_sampled_time = point.datetime
        
        # Always include last point with its altitude
        if len(self.points) > 1:
            sampled.append(self.points[-1])
        
        return sampled
    
    def __repr__(self):
        orig = self.origin.code if self.origin else "UNKN"
        dest = self.destination.code if self.destination else "UNKN"
        status = "complete" if self.is_complete else "incomplete"
        return f"FlightSegment({self.aircraft_id}, {orig}->{dest}, {len(self.points)}pts, {self.flight_duration:.1f}min, {status})"


def extract_aircraft_id(filename: str) -> str:
    """Extract aircraft ID from filename like 'a708bf_2025-08-25_baro_avg.kml'"""
    basename = os.path.basename(filename)
    # Match pattern like 'a708bf' at start of filename
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
    return c * 6371  # Radius of Earth in kilometers


def calculate_groundspeed(p1: TrackPoint, p2: TrackPoint) -> float:
    """Calculate groundspeed between two points (in knots)"""
    distance_km = haversine_distance(p1.lat, p1.lon, p2.lat, p2.lon)
    time_hours = (p2.datetime - p1.datetime).total_seconds() / 3600
    if time_hours > 0:
        speed_kmh = distance_km / time_hours
        return speed_kmh * 0.539957  # Convert to knots
    return 0


def calculate_vertical_speed(p1: TrackPoint, p2: TrackPoint) -> float:
    """Calculate vertical speed between two points (in feet/min)"""
    alt_diff_m = p2.alt - p1.alt
    alt_diff_ft = alt_diff_m * 3.28084
    time_min = (p2.datetime - p1.datetime).total_seconds() / 60
    if time_min > 0:
        return alt_diff_ft / time_min
    return 0


def load_airports_csv(filepath: str) -> List[Airport]:
    """Load airports from CSV file"""
    airports = []
    
    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:  # utf-8-sig handles BOM
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
                        max_distance_km: float = 10.0, max_alt_diff_ft: float = 2000.0) -> Optional[Airport]:
    """Find the nearest airport within distance and altitude constraints"""
    nearest = None
    min_distance = float('inf')
    alt_feet = alt_meters * 3.28084  # Convert meters to feet
    
    for airport in airports:
        distance = haversine_distance(lat, lon, airport.lat, airport.lon)
        alt_diff = abs(alt_feet - airport.elevation)
        
        # Check if within distance and altitude constraints
        if distance < min_distance and distance <= max_distance_km:
            # Be more lenient with altitude for higher elevation airports
            adjusted_alt_threshold = max_alt_diff_ft + (airport.elevation * 0.1 if airport.elevation > 3000 else 0)
            if alt_diff <= adjusted_alt_threshold:
                min_distance = distance
                nearest = airport
    
    return nearest


def parse_kml_tracks(kml_file: str, aircraft_id: str) -> List[Tuple[List[TrackPoint], str, str, str, str]]:
    """
    Parse KML file and extract flight tracks
    Returns: list of (track_points, registration, style_color, style_width, style_opacity)
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
            return aircraft_id  # Use aircraft ID as fallback
        
        # Common patterns: "N549VL", "N549VL track", etc.
        parts = name.split()
        if parts:
            # Check if first part looks like a registration
            reg = parts[0].upper()
            # Simple validation - starts with letter, has alphanumeric
            if reg and reg[0].isalpha():
                return reg
        return aircraft_id  # Use aircraft ID as fallback
    
    def find_tracks_recursive(element, current_registration=None):
        """Recursively search for tracks in folders"""
        # Check for folder name that might be registration
        name_elem = element.find('kml:name', ns)
        if name_elem is not None and name_elem.text:
            potential_reg = extract_registration_from_name(name_elem.text)
            if potential_reg:
                current_registration = potential_reg
        
        if not current_registration:
            current_registration = aircraft_id
        
        # Process placemarks in this element
        for placemark in element.findall('kml:Placemark', ns):
            # Check placemark name for registration
            pm_name = placemark.find('kml:name', ns)
            if pm_name is not None and pm_name.text:
                reg = extract_registration_from_name(pm_name.text)
                if reg:
                    current_registration = reg
            
            # Extract style information
            style = placemark.find('kml:Style', ns)
            style_color = 'ff0000ff'  # Default blue
            style_width = '2'  # Default width
            style_opacity = 'ff'  # Default fully opaque
            
            if style is not None:
                line_style = style.find('kml:LineStyle', ns)
                if line_style is not None:
                    color_elem = line_style.find('kml:color', ns)
                    width_elem = line_style.find('kml:width', ns)
                    if color_elem is not None and color_elem.text:
                        style_color = color_elem.text
                        # Extract opacity from color (first 2 hex chars)
                        if len(style_color) >= 2:
                            style_opacity = style_color[:2]
                    if width_elem is not None and width_elem.text:
                        style_width = width_elem.text
            
            # Extract track data
            track = placemark.find('.//gx:Track', ns)
            if track is None:
                continue
            
            whens = track.findall('kml:when', ns)
            coords = track.findall('gx:coord', ns)
            
            if len(whens) != len(coords):
                print(f"Warning: Mismatch in when/coord count for {current_registration}")
                continue
            
            # Create track points - PRESERVE ALTITUDE
            track_points = []
            for when, coord in zip(whens, coords):
                if when.text and coord.text:
                    timestamp = when.text
                    parts = coord.text.strip().split()
                    if len(parts) >= 3:
                        try:
                            lon = float(parts[0])
                            lat = float(parts[1])
                            alt = float(parts[2])  # Keep original altitude in meters
                            track_points.append(TrackPoint(timestamp, lon, lat, alt))
                        except ValueError:
                            continue
            
            if track_points:
                # Calculate speeds for points
                for i in range(1, len(track_points)):
                    track_points[i].groundspeed = calculate_groundspeed(
                        track_points[i-1], track_points[i]
                    )
                    track_points[i].vertical_speed = calculate_vertical_speed(
                        track_points[i-1], track_points[i]
                    )
                all_tracks.append(
                    (track_points, current_registration, style_color, style_width, style_opacity)
                )
        
        # Recurse into child folders
        for folder in element.findall('kml:Folder', ns):
            find_tracks_recursive(folder, current_registration)
    
    # Start recursive search from document
    document = root.find('.//kml:Document', ns)
    if document is not None:
        find_tracks_recursive(document)
    else:
        # Try from root if no Document
        find_tracks_recursive(root)
    
    return all_tracks


def detect_intermediate_landings(track_points: List[TrackPoint], airports: List[Airport]) -> List[int]:
    """
    Detect intermediate landing points in a track
    Returns list of indices where landings occur
    """
    if not track_points or len(track_points) < 50:
        return []
    
    landing_indices = []
    MIN_ALTITUDE_DROP = 1000  # meters descent to consider landing
    LANDING_ALT_THRESHOLD = 500  # meters above airport to be "landed"
    LANDING_SPEED = 60  # knots - higher threshold for intermediate stops
    MIN_POINTS_ON_GROUND = 5  # Need at least 5 points near ground to confirm
    
    # Look for significant altitude drops near airports
    for i in range(30, len(track_points) - 30):  # Need context before and after
        point = track_points[i]
        
        # Check if we're near an airport
        nearest = find_nearest_airport(
            point.lat, point.lon, point.alt, airports,
            max_distance_km=5.0, max_alt_diff_ft=1000.0
        )
        
        if nearest:
            # Calculate altitude above this airport
            alt_feet = point.alt * 3.28084
            alt_above_airport = abs(alt_feet - nearest.elevation)
            
            # Check if we're at landing altitude
            if alt_above_airport < LANDING_ALT_THRESHOLD:
                # Look at altitude pattern before and after
                points_before = track_points[max(0, i-30):i]
                points_after = track_points[i:min(len(track_points), i+30)]
                
                if points_before and points_after:
                    # Check for descent before
                    alt_before = sum(p.alt for p in points_before[:10]) / min(10, len(points_before))
                    alt_at = point.alt
                    alt_drop_before = alt_before - alt_at
                    
                    # Check for climb after
                    if len(points_after) > 10:
                        alt_after = sum(p.alt for p in points_after[-10:]) / min(10, len(points_after))
                        alt_climb_after = alt_after - alt_at
                        
                        # Significant drop before and climb after indicates landing
                        if alt_drop_before > MIN_ALTITUDE_DROP and alt_climb_after > MIN_ALTITUDE_DROP:
                            # Count low altitude points
                            low_points = sum(1 for p in track_points[i-5:i+5] 
                                           if abs(p.alt * 3.28084 - nearest.elevation) < LANDING_ALT_THRESHOLD)
                            
                            if low_points >= MIN_POINTS_ON_GROUND:
                                # Check speed if available
                                if point.groundspeed is None or point.groundspeed < LANDING_SPEED:
                                    landing_indices.append(i)
                                    print(f"        Intermediate landing detected at index {i} near {nearest.code}")
    
    return landing_indices


def detect_flight_segments_v5(track_points: List[TrackPoint], airports: List[Airport],
                              max_gap_minutes: float) -> List[Tuple[List[TrackPoint], bool]]:
    """
    Enhanced detection with aggressive intermediate landing splitting
    Returns list of (points, has_gap) tuples
    """
    if not track_points or len(track_points) < 20:
        return []
    
    # First detect intermediate landing points
    landing_indices = detect_intermediate_landings(track_points, airports)
    
    # If no intermediate landings, use standard detection
    if not landing_indices:
        return detect_standard_segments(track_points, airports, max_gap_minutes)
    
    # Split at intermediate landing points
    segments = []
    last_split = 0
    
    for landing_idx in sorted(landing_indices):
        # Create segment from last split to landing
        if landing_idx - last_split > 20:  # Minimum segment size
            segment_points = track_points[last_split:landing_idx + 10]  # Include some ground points
            if len(segment_points) > 20:
                segments.append((segment_points, False))
                last_split = landing_idx - 5  # Overlap slightly for continuity
    
    # Add final segment
    if len(track_points) - last_split > 20:
        segments.append((track_points[last_split:], False))
    
    # If splitting created too many tiny segments, fall back to standard
    if len(segments) > 10 or any(len(seg[0]) < 30 for seg in segments):
        return detect_standard_segments(track_points, airports, max_gap_minutes)
    
    return segments


def detect_standard_segments(track_points: List[TrackPoint], airports: List[Airport],
                            max_gap_minutes: float) -> List[Tuple[List[TrackPoint], bool]]:
    """
    Standard segment detection based on gaps and altitude changes
    """
    segments = []
    current_segment = []
    last_point = None
    segment_has_gap = False
    in_flight = False
    
    MIN_FLIGHT_ALTITUDE = 500  # meters
    MIN_GROUNDSPEED = 50  # knots
    
    for point in track_points:
        if last_point:
            time_gap = (point.datetime - last_point.datetime).total_seconds() / 60
            
            if time_gap > max_gap_minutes:
                # Large time gap - check if still in flight
                if point.alt > 3000 or (point.groundspeed and point.groundspeed > MIN_GROUNDSPEED):
                    segment_has_gap = True
                    current_segment.append(point)
                else:
                    # End segment
                    if len(current_segment) > 20:
                        segments.append((current_segment, segment_has_gap))
                    current_segment = [point]
                    segment_has_gap = False
                    in_flight = False
            else:
                current_segment.append(point)
                
                # Simple takeoff detection
                if not in_flight and len(current_segment) > 5:
                    recent = current_segment[-5:]
                    if recent[-1].alt - recent[0].alt > 200 and point.alt > MIN_FLIGHT_ALTITUDE:
                        in_flight = True
                
                # Simple landing detection
                if in_flight and len(current_segment) > 10:
                    nearest = find_nearest_airport(
                        point.lat, point.lon, point.alt, airports,
                        max_distance_km=10.0, max_alt_diff_ft=1500.0
                    )
                    
                    if nearest:
                        alt_feet = point.alt * 3.28084
                        if abs(alt_feet - nearest.elevation) < 500:
                            if point.groundspeed and point.groundspeed < 40:
                                # Landing detected
                                if len(current_segment) > 20:
                                    segments.append((current_segment, segment_has_gap))
                                current_segment = []
                                segment_has_gap = False
                                in_flight = False
        else:
            current_segment = [point]
            segment_has_gap = False
            in_flight = False
        
        last_point = point
    
    # Add final segment
    if len(current_segment) > 20:
        segments.append((current_segment, segment_has_gap))
    
    return segments


def combine_segments_for_aircraft(segments: List[FlightSegment], airports: List[Airport],
                                  routes_dict: Dict[Tuple[str, str], float]) -> List[FlightSegment]:
    """
    Combine segments for the same aircraft to create complete flights
    Only outputs segments with both origin and destination known
    Filters out same-airport segments and ground operations
    """
    if not segments:
        return []
    
    # Sort by takeoff time
    segments.sort(key=lambda s: s.takeoff_time if s.takeoff_time else datetime.min)
    
    combined = []
    i = 0
    
    while i < len(segments):
        current = segments[i]
        
        # Skip invalid flights (same airport, ground operations)
        if not current.is_valid_flight():
            print(f"        Filtering out invalid segment: {current}")
            i += 1
            continue
        
        # Skip if already complete and valid
        if current.is_complete and current.is_valid_flight():
            combined.append(current)
            i += 1
            continue
        
        # Try to combine with subsequent segments
        combined_segment = current
        j = i + 1
        
        while j < len(segments):
            next_seg = segments[j]
            
            # Skip invalid segments
            if not next_seg.is_valid_flight():
                j += 1
                continue
            
            # Check if can be combined
            should_combine = False
            
            # Case 1: Current has no destination, next has no origin
            if not combined_segment.destination and not next_seg.origin:
                if combined_segment.points and next_seg.points:
                    # Check time continuity
                    time_gap = (next_seg.takeoff_time - combined_segment.landing_time).total_seconds() / 60
                    
                    if time_gap < 60:  # Less than 60 minutes gap
                        # Check spatial continuity
                        last_point = combined_segment.points[-1]
                        first_point = next_seg.points[0]
                        distance = haversine_distance(
                            last_point.lat, last_point.lon,
                            first_point.lat, first_point.lon
                        )
                        
                        if distance < 50:  # Less than 50km apart
                            should_combine = True
            
            # Case 2: Next segment completes the current one
            if not combined_segment.destination and next_seg.destination and not next_seg.origin:
                should_combine = True
            
            if should_combine:
                # Combine the segments - PRESERVE ALTITUDE IN POINTS
                new_points = combined_segment.points + next_seg.points
                new_segment = FlightSegment(
                    combined_segment.aircraft_id,
                    combined_segment.registration,
                    new_points,
                    combined_segment.origin or next_seg.origin,
                    next_seg.destination or combined_segment.destination,
                    combined_segment.style_color,
                    combined_segment.style_width,
                    combined_segment.style_opacity
                )
                new_segment.has_gaps = combined_segment.has_gaps or next_seg.has_gaps
                combined_segment = new_segment
                j += 1
            else:
                break
        
        # Only add if complete and valid
        if combined_segment.is_complete and combined_segment.is_valid_flight():
            combined.append(combined_segment)
        else:
            # Try to match using route information
            if combined_segment.origin and not combined_segment.destination:
                # Try to find matching destination
                best_match = None
                best_score = float('inf')
                
                for (orig, dest), avg_time in routes_dict.items():
                    if orig == combined_segment.origin.code:
                        time_diff = abs(combined_segment.flight_duration - avg_time)
                        if time_diff < avg_time * 0.3:  # Within 30% of expected time
                            if time_diff < best_score:
                                best_score = time_diff
                                # Find the airport
                                for airport in airports:
                                    if airport.code == dest:
                                        best_match = airport
                                        break
                
                if best_match:
                    combined_segment.destination = best_match
                    combined_segment.is_complete = True
                    if combined_segment.is_valid_flight():
                        combined.append(combined_segment)
        
        i = j if j > i + 1 else i + 1
    
    return combined


def create_output_kml(flight_segments: List[FlightSegment], output_file: str,
                     group_by: str = "destination", 
                     sample_minutes: float = 2.0,
                     override_color: Optional[str] = None,
                     override_width: Optional[str] = None,
                     override_opacity: Optional[str] = None):
    """Create output KML file with organized folder structure and point sampling"""
    
    # Create root elements with proper namespaces
    kml = ET.Element('kml')
    kml.set('xmlns', 'http://www.opengis.net/kml/2.2')
    kml.set('xmlns:gx', 'http://www.google.com/kml/ext/2.2')
    
    document = ET.SubElement(kml, 'Document')
    name_elem = ET.SubElement(document, 'name')
    name_elem.text = f'Flight Routes by {group_by.capitalize()}'
    
    # Add description
    desc_elem = ET.SubElement(document, 'description')
    desc_elem.text = f'Flight segments sampled at {sample_minutes} minute intervals'
    
    # Organize segments based on grouping preference
    routes_by_group = defaultdict(lambda: defaultdict(list))
    
    for segment in flight_segments:
        if group_by == "origin":
            group_code = segment.origin.code if segment.origin else "UNKNOWN"
        else:  # destination
            group_code = segment.destination.code if segment.destination else "UNKNOWN"
        
        route_key = segment.get_route_folder()
        routes_by_group[group_code][route_key].append(segment)
    
    # Statistics counters
    total_segments = 0
    total_points_original = 0
    total_points_sampled = 0
    
    # Sort groups alphabetically
    for group_code in sorted(routes_by_group.keys()):
        # Create group folder
        group_folder = ET.SubElement(document, 'Folder')
        group_name = ET.SubElement(group_folder, 'name')
        group_name.text = group_code
        
        # Sort routes alphabetically within group
        for route_key in sorted(routes_by_group[group_code].keys()):
            # Create route folder
            route_folder = ET.SubElement(group_folder, 'Folder')
            route_name_elem = ET.SubElement(route_folder, 'name')
            route_name_elem.text = route_key
            
            # Add all flights for this route
            segments = routes_by_group[group_code][route_key]
            
            # Sort by takeoff time
            segments.sort(key=lambda s: s.takeoff_time if s.takeoff_time else datetime.min)
            
            for segment in segments:
                total_segments += 1
                total_points_original += len(segment.points)
                
                # Sample points to reduce file size - altitude is preserved
                sampled_points = segment.sample_points(sample_minutes)
                total_points_sampled += len(sampled_points)
                
                # Create placemark for this flight
                placemark = ET.SubElement(route_folder, 'Placemark')
                
                placemark_name = ET.SubElement(placemark, 'name')
                placemark_name.text = segment.get_segment_name()
                
                # Add description with flight details
                description = ET.SubElement(placemark, 'description')
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
                
                # Add style
                style = ET.SubElement(placemark, 'Style')
                line_style = ET.SubElement(style, 'LineStyle')
                
                # Apply overrides or use original style
                color_elem = ET.SubElement(line_style, 'color')
                if override_color and override_opacity:
                    color_elem.text = override_opacity + override_color[2:] if len(override_color) >= 8 else override_color
                elif override_opacity:
                    color_elem.text = override_opacity + segment.style_color[2:] if len(segment.style_color) >= 2 else segment.style_color
                else:
                    color_elem.text = segment.style_color
                
                width_elem = ET.SubElement(line_style, 'width')
                width_elem.text = override_width if override_width else segment.style_width
                
                # Add invisible icon style
                icon_style = ET.SubElement(style, 'IconStyle')
                icon = ET.SubElement(icon_style, 'Icon')
                href = ET.SubElement(icon, 'href')
                href.text = 'http://maps.google.com/mapfiles/kml/shapes/airports.png'
                scale = ET.SubElement(icon_style, 'scale')
                scale.text = '0'
                
                # Create track with sampled points - PRESERVE ALTITUDE
                track = ET.SubElement(placemark, '{http://www.google.com/kml/ext/2.2}Track')
                
                altitude_mode = ET.SubElement(track, 'altitudeMode')
                altitude_mode.text = 'absolute'  # Use absolute altitude, not clamped to ground
                
                extrude = ET.SubElement(track, 'extrude')
                extrude.text = '1'
                
                # Add sampled points with altitude preserved
                for point in sampled_points:
                    when = ET.SubElement(track, 'when')
                    when.text = point.timestamp
                
                for point in sampled_points:
                    coord = ET.SubElement(track, '{http://www.google.com/kml/ext/2.2}coord')
                    # IMPORTANT: Write altitude from point, not 0
                    coord.text = f"{point.lon} {point.lat} {point.alt}"
    
    # Write to file
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
                     max_gap_minutes: float = 15.0,
                     override_color: Optional[str] = None,
                     override_width: Optional[str] = None,
                     override_opacity: Optional[str] = None):
    """Main processing function with enhanced intermediate landing detection"""
    
    # Create airports dictionary for quick lookup
    airports_dict = {airport.code: airport for airport in airports}
    
    # Create routes dictionary
    routes_dict = {}
    for route in routes:
        routes_dict[(route.origin, route.destination)] = route.avg_time_min
    
    # Group segments by aircraft ID
    segments_by_aircraft = defaultdict(list)
    
    # Sort files by date to ensure chronological processing
    kml_files.sort()
    
    # Process each input file
    for input_file in kml_files:
        # Extract aircraft ID from filename
        aircraft_id = extract_aircraft_id(input_file)
        print(f"\nProcessing: {os.path.basename(input_file)} (Aircraft: {aircraft_id})")
        
        # Parse KML file
        all_tracks = parse_kml_tracks(input_file, aircraft_id)
        print(f"Found {len(all_tracks)} tracks")
        
        # Process each track
        for track_points, registration, style_color, style_width, style_opacity in all_tracks:
            if not track_points:
                continue
            
            print(f"  Processing track with {len(track_points)} points (Reg: {registration})")
            
            # Use enhanced detection with intermediate landing splitting
            raw_segments = detect_flight_segments_v5(track_points, airports, max_gap_minutes)
            print(f"    Detected {len(raw_segments)} raw segments (max gap: {max_gap_minutes} min)")
            
            # Create flight segment objects
            for segment_points, has_gap in raw_segments:
                if len(segment_points) < 20:  # Skip very short segments
                    continue
                
                # Find origin and destination airports
                first_point = segment_points[0]
                origin = find_nearest_airport(
                    first_point.lat, first_point.lon, first_point.alt,
                    airports, max_distance_km=15.0, max_alt_diff_ft=2000.0
                )
                
                last_point = segment_points[-1]
                destination = find_nearest_airport(
                    last_point.lat, last_point.lon, last_point.alt,
                    airports, max_distance_km=15.0, max_alt_diff_ft=2000.0
                )
                
                # Create segment with altitude preserved in points
                segment = FlightSegment(
                    aircraft_id, registration, segment_points,
                    origin, destination,
                    style_color, style_width, style_opacity
                )
                segment.has_gaps = has_gap
                
                segments_by_aircraft[aircraft_id].append(segment)
                
                orig = origin.code if origin else "NONE"
                dest = destination.code if destination else "NONE"
                status = "complete" if segment.is_complete else "incomplete"
                gaps = " (with gaps)" if has_gap else ""
                validity = " [VALID]" if segment.is_valid_flight() else " [INVALID]"
                print(f"      {orig} -> {dest}: {len(segment_points)} pts, {segment.flight_duration:.1f} min, "
                      f"max alt {segment.max_altitude:.0f}m, {status}{gaps}{validity}")
    
    # Combine segments for each aircraft
    print("\nCombining and filtering segments by aircraft...")
    all_complete_segments = []
    
    for aircraft_id, segments in segments_by_aircraft.items():
        print(f"\n  Aircraft {aircraft_id}: {len(segments)} raw segments")
        
        # Combine segments to create complete flights
        combined = combine_segments_for_aircraft(segments, airports, routes_dict)
        
        # Only keep complete and valid segments
        complete = [s for s in combined if s.is_complete and s.is_valid_flight()]
        print(f"    Valid complete flights: {len(complete)}")
        
        for seg in complete:
            print(f"      {seg.origin.code} -> {seg.destination.code}: "
                  f"{seg.flight_duration:.1f} min, {seg.max_altitude:.0f}m, {seg.total_distance:.0f}km")
        
        all_complete_segments.extend(complete)
    
    print(f"\nTotal valid complete flights: {len(all_complete_segments)}")
    
    # Create output KML
    print("\nCreating output KML with point sampling...")
    total_flights = create_output_kml(
        all_complete_segments, output_file, group_by,
        sample_minutes,
        override_color, override_width, override_opacity
    )
    
    # Print detailed summary
    print("\n" + "="*60)
    print("FLIGHT PARSING SUMMARY")
    print("="*60)
    print(f"Total valid complete flights parsed: {total_flights}")
    print(f"Point sampling interval: {sample_minutes} minutes")
    print(f"Max gap allowed: {max_gap_minutes} minutes")
    
    # Show breakdown by route
    route_counts = defaultdict(int)
    for seg in all_complete_segments:
        route = f"{seg.origin.code}-{seg.destination.code}"
        route_counts[route] += 1
    
    print("\nFlights by route:")
    for route, count in sorted(route_counts.items()):
        print(f"  {route}: {count} flights")
    
    print("="*60)


def main():
    parser = argparse.ArgumentParser(
        description='Enhanced Flight Route Splitter v5 - Split ADSB KML tracks with aggressive intermediate detection',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process with custom sampling and max gap
  python flight_route_splitter_v5.py --kml-folder ./kml_files/ --airports voi_airports.csv \\
                                     --routes voi_routes_time.csv --output flights.kml \\
                                     --sample 1 --maxgap 20 --group destination
  
  # Process specific files with aggressive settings
  python flight_route_splitter_v5.py --kml-files file1.kml file2.kml --airports voi_airports.csv \\
                                     --routes voi_routes_time.csv --output flights.kml \\
                                     --sample 3 --maxgap 10
        """
    )
    
    # Input file arguments
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
    
    # Sampling parameter
    parser.add_argument('--sample', type=float, default=2.0,
                       help='Point sampling interval in minutes (default: 2)')
    
    # Max gap parameter
    parser.add_argument('--maxgap', type=float, default=15.0,
                       help='Maximum gap time allowed in flight segment in minutes (default: 15)')
    
    # Grouping option
    parser.add_argument('--group', choices=['origin', 'destination'], default='destination',
                       help='Group flights by origin or destination airport (default: destination)')
    
    # Style overrides
    parser.add_argument('--color', 
                       help='Override line color (KML format, e.g., ff0000ff for blue)')
    parser.add_argument('--width', 
                       help='Override line width (e.g., 2)')
    parser.add_argument('--opacity', 
                       help='Override line opacity (hex, e.g., 80 for 50%% opacity)')
    
    args = parser.parse_args()
    
    # Validate and gather input files
    kml_files = []
    if args.kml_files:
        for f in args.kml_files:
            if not os.path.exists(f):
                print(f"Error: KML file not found: {f}")
                sys.exit(1)
        kml_files = args.kml_files
    else:
        # Get all KML files from folder
        pattern = os.path.join(args.kml_folder, "*.kml")
        kml_files = glob.glob(pattern)
        if not kml_files:
            print(f"Error: No KML files found in {args.kml_folder}")
            sys.exit(1)
    
    # Validate other input files
    if not os.path.exists(args.airports):
        print(f"Error: Airports file not found: {args.airports}")
        sys.exit(1)
    
    if not os.path.exists(args.routes):
        print(f"Error: Routes file not found: {args.routes}")
        sys.exit(1)
    
    # Load data
    print(f"Loading airports from: {args.airports}")
    airports = load_airports_csv(args.airports)
    
    if not airports:
        print("Error: No airports loaded!")
        sys.exit(1)
    
    print(f"Loading routes from: {args.routes}")
    routes = load_routes_csv(args.routes)
    
    print(f"\nConfiguration:")
    print(f"  Sample interval: {args.sample} minutes")
    print(f"  Max gap allowed: {args.maxgap} minutes")
    print(f"  Grouping: by {args.group}")
    print(f"  Minimum flight criteria:")
    print(f"    - Altitude: 1000m")
    print(f"    - Duration: 20 minutes")
    print(f"    - Distance: 20 km")
    print(f"  Intermediate landing detection: ENABLED")
    
    # Process files
    process_kml_files(
        kml_files, airports, routes, args.output,
        group_by=args.group,
        sample_minutes=args.sample,
        max_gap_minutes=args.maxgap,
        override_color=args.color,
        override_width=args.width,
        override_opacity=args.opacity
    )
    
    print("\nProcessing complete!")


if __name__ == '__main__':
    main()
