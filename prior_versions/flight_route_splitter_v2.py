#!/usr/bin/env python3
"""
Enhanced Flight Route Splitter
Processes KML files containing continuous flight tracks and splits them into individual routes
based on takeoff/landing events, matching airports by proximity and handling incomplete segments.
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
        self.groundspeed = None  # Will be calculated if possible
    
    def __repr__(self):
        return f"TrackPoint({self.timestamp}, {self.lat:.4f}, {self.lon:.4f}, {self.alt:.0f}m)"


class FlightSegment:
    """Represents a complete flight from origin to destination"""
    def __init__(self, registration: str, points: List[TrackPoint], 
                 origin: Optional[Airport], destination: Optional[Airport],
                 style_color: str, style_width: str, style_opacity: str = "ff"):
        self.registration = registration
        self.points = points
        self.origin = origin
        self.destination = destination
        self.style_color = style_color
        self.style_width = style_width
        self.style_opacity = style_opacity
        self.is_complete = (origin is not None and destination is not None)
        self.has_gaps = False
        self.gap_duration_min = 0
        
        # Extract flight times
        if points:
            self.takeoff_time = points[0].datetime
            self.landing_time = points[-1].datetime
            self.flight_duration = (self.landing_time - self.takeoff_time).total_seconds() / 60
            self.takeoff_date_str = points[0].datetime.strftime('%Y-%m-%d')
        else:
            self.takeoff_time = None
            self.landing_time = None
            self.flight_duration = 0
            self.takeoff_date_str = "UNKNOWN"
    
    def get_segment_name(self) -> str:
        """Generate segment name in format: REGNUM-ORIG-DEST-DATE"""
        orig = self.origin.code if self.origin else "UNKN"
        dest = self.destination.code if self.destination else "UNKN"
        return f"{self.registration}-{orig}-{dest}-{self.takeoff_date_str}"
    
    def get_route_folder(self) -> str:
        """Generate route folder name in format: ORIG-DEST"""
        orig = self.origin.code if self.origin else "UNKN"
        dest = self.destination.code if self.destination else "UNKN"
        return f"{orig}-{dest}"
    
    def __repr__(self):
        orig = self.origin.code if self.origin else "UNKN"
        dest = self.destination.code if self.destination else "UNKN"
        status = "complete" if self.is_complete else "incomplete"
        return f"FlightSegment({self.registration}, {orig}->{dest}, {len(self.points)}pts, {status})"


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


def parse_kml_tracks(kml_file: str) -> Dict[str, List[Tuple[List[TrackPoint], str, str, str]]]:
    """
    Parse KML file and extract flight tracks by registration
    Returns: dict mapping registration -> list of (track_points, style_color, style_width, style_opacity)
    """
    tree = ET.parse(kml_file)
    root = tree.getroot()
    
    ns = {
        'kml': 'http://www.opengis.net/kml/2.2',
        'gx': 'http://www.google.com/kml/ext/2.2'
    }
    
    tracks_by_registration = defaultdict(list)
    
    def extract_registration_from_name(name: str) -> str:
        """Extract registration from various name formats"""
        if not name:
            return "UNKNOWN"
        
        # Common patterns: "N549VL", "N549VL track", etc.
        parts = name.split()
        if parts:
            # Check if first part looks like a registration
            reg = parts[0].upper()
            # Simple validation - starts with letter, has alphanumeric
            if reg and reg[0].isalpha():
                return reg
        return name.upper()
    
    def find_tracks_recursive(element, current_registration="UNKNOWN"):
        """Recursively search for tracks in folders"""
        # Check for folder name that might be registration
        name_elem = element.find('kml:name', ns)
        if name_elem is not None and name_elem.text:
            potential_reg = extract_registration_from_name(name_elem.text)
            if potential_reg != "UNKNOWN":
                current_registration = potential_reg
        
        # Process placemarks in this element
        for placemark in element.findall('kml:Placemark', ns):
            # Check placemark name for registration
            pm_name = placemark.find('kml:name', ns)
            if pm_name is not None and pm_name.text:
                reg = extract_registration_from_name(pm_name.text)
                if reg != "UNKNOWN":
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
            
            # Create track points
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
            
            if track_points:
                # Calculate groundspeed for points
                for i in range(1, len(track_points)):
                    track_points[i].groundspeed = calculate_groundspeed(
                        track_points[i-1], track_points[i]
                    )
                tracks_by_registration[current_registration].append(
                    (track_points, style_color, style_width, style_opacity)
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
    
    return tracks_by_registration


def detect_flight_segments(track_points: List[TrackPoint], airports: List[Airport],
                          max_gap_minutes: float = 15.0) -> List[Tuple[List[TrackPoint], bool]]:
    """
    Detect individual flight segments from continuous track
    Returns list of (points, has_gap) tuples
    """
    if not track_points:
        return []
    
    segments = []
    current_segment = []
    last_point = None
    segment_has_gap = False
    
    # Thresholds
    GROUND_ALTITUDE_THRESHOLD = 500  # meters AGL
    MIN_CRUISE_ALTITUDE = 6096  # 20,000 feet in meters
    MIN_GROUNDSPEED = 50  # knots for being airborne
    
    for point in track_points:
        if last_point:
            time_gap = (point.datetime - last_point.datetime).total_seconds() / 60
            
            # Check for data gap
            if time_gap > max_gap_minutes:
                # Check if we're likely still in flight
                if (point.alt > MIN_CRUISE_ALTITUDE or 
                    (point.groundspeed and point.groundspeed > MIN_GROUNDSPEED)):
                    # Likely still in flight, mark as having gap but continue segment
                    segment_has_gap = True
                    current_segment.append(point)
                else:
                    # End current segment and start new one
                    if current_segment:
                        segments.append((current_segment, segment_has_gap))
                    current_segment = [point]
                    segment_has_gap = False
            else:
                # Normal continuation
                current_segment.append(point)
                
                # Check for landing (significant altitude drop near airport)
                if len(current_segment) > 10:  # Need some points to detect pattern
                    # Check if we're near ground level and near an airport
                    nearest = find_nearest_airport(
                        point.lat, point.lon, point.alt, airports,
                        max_distance_km=5.0, max_alt_diff_ft=1000.0
                    )
                    
                    if nearest:
                        # Check if altitude is near airport elevation
                        alt_feet = point.alt * 3.28084
                        if abs(alt_feet - nearest.elevation) < 500:
                            # Check if we've been descending
                            recent_points = current_segment[-10:]
                            alt_trend = recent_points[-1].alt - recent_points[0].alt
                            
                            if alt_trend < -100:  # Descended at least 100m
                                # This looks like a landing
                                segments.append((current_segment, segment_has_gap))
                                current_segment = []
                                segment_has_gap = False
        else:
            current_segment = [point]
            segment_has_gap = False
        
        last_point = point
    
    # Add final segment
    if current_segment:
        segments.append((current_segment, segment_has_gap))
    
    return segments


def match_incomplete_segments(incomplete_segments: List[FlightSegment], 
                             routes: List[Route],
                             airports_dict: Dict[str, Airport]) -> List[FlightSegment]:
    """
    Try to match incomplete segments using known routes and flight times
    """
    matched_segments = []
    
    # Create route lookup dictionary
    routes_dict = {}
    for route in routes:
        key = (route.origin, route.destination)
        routes_dict[key] = route.avg_time_min
    
    for segment in incomplete_segments:
        if segment.origin and not segment.destination:
            # Has origin, missing destination - check possible routes
            best_match = None
            best_score = float('inf')
            
            for route_key, avg_time in routes_dict.items():
                if route_key[0] == segment.origin.code:
                    # This route starts from our origin
                    dest_code = route_key[1]
                    if dest_code in airports_dict:
                        # Calculate time difference from average
                        time_diff = abs(segment.flight_duration - avg_time)
                        
                        # Score based on time difference and last known position
                        if segment.points:
                            last_point = segment.points[-1]
                            dest_airport = airports_dict[dest_code]
                            distance = haversine_distance(
                                last_point.lat, last_point.lon,
                                dest_airport.lat, dest_airport.lon
                            )
                            
                            # Combined score (lower is better)
                            score = time_diff * 0.5 + distance * 0.1
                            
                            if score < best_score and time_diff < avg_time * 0.5:  # Within 50% of expected time
                                best_score = score
                                best_match = dest_airport
            
            if best_match:
                segment.destination = best_match
                segment.is_complete = True
                print(f"  Matched incomplete segment: {segment.origin.code} -> {best_match.code}")
        
        elif segment.destination and not segment.origin:
            # Has destination, missing origin - check possible routes
            best_match = None
            best_score = float('inf')
            
            for route_key, avg_time in routes_dict.items():
                if route_key[1] == segment.destination.code:
                    # This route ends at our destination
                    orig_code = route_key[0]
                    if orig_code in airports_dict:
                        # Calculate time difference from average
                        time_diff = abs(segment.flight_duration - avg_time)
                        
                        # Score based on time difference and first known position
                        if segment.points:
                            first_point = segment.points[0]
                            orig_airport = airports_dict[orig_code]
                            distance = haversine_distance(
                                first_point.lat, first_point.lon,
                                orig_airport.lat, orig_airport.lon
                            )
                            
                            # Combined score
                            score = time_diff * 0.5 + distance * 0.1
                            
                            if score < best_score and time_diff < avg_time * 0.5:
                                best_score = score
                                best_match = orig_airport
            
            if best_match:
                segment.origin = best_match
                segment.is_complete = True
                print(f"  Matched incomplete segment: {best_match.code} -> {segment.destination.code}")
        
        matched_segments.append(segment)
    
    return matched_segments


def combine_cross_day_segments(all_segments: List[FlightSegment]) -> List[FlightSegment]:
    """
    Combine segments that span across daily file boundaries
    """
    # Group by registration
    segments_by_reg = defaultdict(list)
    for segment in all_segments:
        segments_by_reg[segment.registration].append(segment)
    
    combined_segments = []
    
    for registration, segments in segments_by_reg.items():
        # Sort by takeoff time
        segments.sort(key=lambda s: s.takeoff_time if s.takeoff_time else datetime.min)
        
        i = 0
        while i < len(segments):
            current = segments[i]
            
            # Check if this segment is incomplete and can be combined with next
            if i < len(segments) - 1:
                next_seg = segments[i + 1]
                
                # Check if segments should be combined
                should_combine = False
                
                if not current.destination and not next_seg.origin:
                    # Current ends incomplete, next starts incomplete
                    if current.points and next_seg.points:
                        # Check time continuity
                        time_gap = (next_seg.takeoff_time - current.landing_time).total_seconds() / 60
                        
                        if time_gap < 30:  # Less than 30 minutes gap
                            # Check spatial continuity
                            last_point = current.points[-1]
                            first_point = next_seg.points[0]
                            distance = haversine_distance(
                                last_point.lat, last_point.lon,
                                first_point.lat, first_point.lon
                            )
                            
                            if distance < 50:  # Less than 50km apart
                                should_combine = True
                
                if should_combine:
                    # Combine the segments
                    combined_points = current.points + next_seg.points
                    combined = FlightSegment(
                        registration,
                        combined_points,
                        current.origin,
                        next_seg.destination,
                        current.style_color,
                        current.style_width,
                        current.style_opacity
                    )
                    combined.has_gaps = current.has_gaps or next_seg.has_gaps
                    combined_segments.append(combined)
                    print(f"  Combined cross-day segment for {registration}")
                    i += 2  # Skip both segments
                    continue
            
            combined_segments.append(current)
            i += 1
    
    return combined_segments


def create_flight_segments(raw_segments: List[Tuple[List[TrackPoint], bool]], 
                          registration: str,
                          airports: List[Airport],
                          style_color: str, style_width: str, style_opacity: str) -> List[FlightSegment]:
    """Create flight segment objects with airport matching"""
    flight_segments = []
    
    for segment_points, has_gap in raw_segments:
        if len(segment_points) < 2:
            continue
        
        # Find origin airport (first point)
        first_point = segment_points[0]
        origin = find_nearest_airport(
            first_point.lat, first_point.lon, first_point.alt,
            airports, max_distance_km=10.0, max_alt_diff_ft=1500.0
        )
        
        # Find destination airport (last point)
        last_point = segment_points[-1]
        destination = find_nearest_airport(
            last_point.lat, last_point.lon, last_point.alt,
            airports, max_distance_km=10.0, max_alt_diff_ft=1500.0
        )
        
        # Create segment
        segment = FlightSegment(
            registration, segment_points, origin, destination,
            style_color, style_width, style_opacity
        )
        segment.has_gaps = has_gap
        
        flight_segments.append(segment)
    
    return flight_segments


def create_output_kml(flight_segments: List[FlightSegment], output_file: str,
                     group_by: str = "destination", 
                     override_color: Optional[str] = None,
                     override_width: Optional[str] = None,
                     override_opacity: Optional[str] = None):
    """Create output KML file with organized folder structure"""
    
    # Create root elements with proper namespaces
    kml = ET.Element('kml')
    kml.set('xmlns', 'http://www.opengis.net/kml/2.2')
    kml.set('xmlns:gx', 'http://www.google.com/kml/ext/2.2')
    
    document = ET.SubElement(kml, 'Document')
    name_elem = ET.SubElement(document, 'name')
    name_elem.text = f'Flight Routes by {group_by.capitalize()}'
    
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
    total_complete = 0
    total_incomplete = 0
    total_with_gaps = 0
    
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
                # Update statistics
                if segment.is_complete:
                    total_complete += 1
                else:
                    total_incomplete += 1
                if segment.has_gaps:
                    total_with_gaps += 1
                
                # Create placemark for this flight
                placemark = ET.SubElement(route_folder, 'Placemark')
                
                placemark_name = ET.SubElement(placemark, 'name')
                placemark_name.text = segment.get_segment_name()
                
                # Add description with flight details
                description = ET.SubElement(placemark, 'description')
                desc_text = f"Aircraft: {segment.registration}\n"
                desc_text += f"Route: {segment.origin.code if segment.origin else 'UNKN'} -> "
                desc_text += f"{segment.destination.code if segment.destination else 'UNKN'}\n"
                desc_text += f"Date: {segment.takeoff_date_str}\n"
                desc_text += f"Duration: {segment.flight_duration:.1f} minutes\n"
                desc_text += f"Points: {len(segment.points)}\n"
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
                
                # Create track
                track = ET.SubElement(placemark, '{http://www.google.com/kml/ext/2.2}Track')
                
                altitude_mode = ET.SubElement(track, 'altitudeMode')
                altitude_mode.text = 'absolute'
                
                extrude = ET.SubElement(track, 'extrude')
                extrude.text = '1'
                
                # Add all points
                for point in segment.points:
                    when = ET.SubElement(track, 'when')
                    when.text = point.timestamp
                
                for point in segment.points:
                    coord = ET.SubElement(track, '{http://www.google.com/kml/ext/2.2}coord')
                    coord.text = f"{point.lon} {point.lat} {point.alt}"
    
    # Write to file
    tree = ET.ElementTree(kml)
    ET.indent(tree, space='  ')
    tree.write(output_file, encoding='utf-8', xml_declaration=True)
    
    print(f"\nOutput written to: {output_file}")
    return total_complete, total_incomplete, total_with_gaps


def process_kml_files(kml_files: List[str], airports: List[Airport], routes: List[Route],
                     output_file: str, group_by: str = "destination",
                     override_color: Optional[str] = None,
                     override_width: Optional[str] = None,
                     override_opacity: Optional[str] = None):
    """Main processing function"""
    
    # Create airports dictionary for quick lookup
    airports_dict = {airport.code: airport for airport in airports}
    
    all_flight_segments = []
    
    # Sort files by date to ensure chronological processing
    kml_files.sort()
    
    # Process each input file
    for input_file in kml_files:
        print(f"\nProcessing: {os.path.basename(input_file)}")
        
        # Parse KML file
        tracks_by_registration = parse_kml_tracks(input_file)
        print(f"Found {len(tracks_by_registration)} aircraft registrations")
        
        # Process each registration's tracks
        for registration, track_list in tracks_by_registration.items():
            print(f"\n  Processing {registration}...")
            
            for track_points, style_color, style_width, style_opacity in track_list:
                if not track_points:
                    continue
                    
                print(f"    Track with {len(track_points)} points")
                
                # Detect flight segments
                segments = detect_flight_segments(track_points, airports)
                print(f"    Detected {len(segments)} flight segments")
                
                # Create flight segment objects with airport matching
                flight_segments = create_flight_segments(
                    segments, registration, airports, 
                    style_color, style_width, style_opacity
                )
                
                # Print segment details
                for seg in flight_segments:
                    orig = seg.origin.code if seg.origin else "UNKN"
                    dest = seg.destination.code if seg.destination else "UNKN"
                    status = "complete" if seg.is_complete else "incomplete"
                    gaps = " (with gaps)" if seg.has_gaps else ""
                    print(f"      {orig} -> {dest}: {len(seg.points)} points, {seg.flight_duration:.1f} min, {status}{gaps}")
                
                all_flight_segments.extend(flight_segments)
    
    print(f"\nTotal flight segments before processing: {len(all_flight_segments)}")
    
    # Combine segments across day boundaries
    print("\nCombining cross-day segments...")
    combined_segments = combine_cross_day_segments(all_flight_segments)
    print(f"Total flight segments after combining: {len(combined_segments)}")
    
    # Try to match incomplete segments using route information
    print("\nMatching incomplete segments using route data...")
    incomplete = [s for s in combined_segments if not s.is_complete]
    if incomplete:
        matched_segments = match_incomplete_segments(incomplete, routes, airports_dict)
        # Replace incomplete segments with matched ones
        complete = [s for s in combined_segments if s.is_complete]
        combined_segments = complete + matched_segments
    
    # Create output KML
    print("\nCreating output KML...")
    total_complete, total_incomplete, total_with_gaps = create_output_kml(
        combined_segments, output_file, group_by,
        override_color, override_width, override_opacity
    )
    
    # Print detailed summary
    print("\n" + "="*60)
    print("FLIGHT PARSING SUMMARY")
    print("="*60)
    print(f"Total flights detected and parsed: {len(combined_segments)}")
    print(f"  - Complete flights (with origin and destination): {total_complete}")
    print(f"  - Incomplete flights: {total_incomplete}")
    print(f"  - Flights with data gaps: {total_with_gaps}")
    
    if total_incomplete > 0:
        print(f"\nIncomplete flights breakdown:")
        for seg in combined_segments:
            if not seg.is_complete:
                orig = seg.origin.code if seg.origin else "MISSING"
                dest = seg.destination.code if seg.destination else "MISSING"
                print(f"  {seg.registration}: {orig} -> {dest} ({seg.takeoff_date_str})")
    
    print("="*60)


def main():
    parser = argparse.ArgumentParser(
        description='Enhanced Flight Route Splitter - Split ADSB KML tracks into flight segments',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process all KML files in a folder with custom parameters
  python flight_route_splitter.py --kml-folder ./kml_files/ --airports voi_airports.csv \\
                                  --routes voi_routes_time.csv --output flights.kml \\
                                  --group destination --color ff0000ff --width 3 --opacity 80
  
  # Process specific files
  python flight_route_splitter.py --kml-files file1.kml file2.kml --airports voi_airports.csv \\
                                  --routes voi_routes_time.csv --output flights.kml
        """
    )
    
    # Input file arguments
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument('--kml-files', nargs='+', 
                            help='Specific KML files to process')
    input_group.add_argument('--kml-folder', 
                            help='Folder containing KML files to process')
    
    parser.add_argument('--airports', required=True,
                       help='CSV file containing airport data (voi_airports.csv)')
    parser.add_argument('--routes', required=True,
                       help='CSV file containing route times (voi_routes_time.csv)')
    parser.add_argument('--output', required=True,
                       help='Output KML file name')
    
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
    
    # Process files
    process_kml_files(
        kml_files, airports, routes, args.output,
        group_by=args.group,
        override_color=args.color,
        override_width=args.width,
        override_opacity=args.opacity
    )
    
    print("\nProcessing complete!")


if __name__ == '__main__':
    main()
