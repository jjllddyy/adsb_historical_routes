#!/usr/bin/env python3
"""
Flight Route Splitter
Processes KML files containing continuous flight tracks and splits them into individual routes
based on takeoff/landing events, matching airports by proximity.
"""

import xml.etree.ElementTree as ET
from datetime import datetime
from math import radians, cos, sin, asin, sqrt
import argparse
import sys
from collections import defaultdict
from typing import List, Tuple, Dict, Optional


class Airport:
    """Represents an airport with location data"""
    def __init__(self, code: str, name: str, lat: float, lon: float, elevation: float):
        self.code = code
        self.name = name
        self.lat = lat
        self.lon = lon
        self.elevation = elevation
    
    def __repr__(self):
        return f"Airport({self.code}, {self.name}, {self.lat:.4f}, {self.lon:.4f})"


class TrackPoint:
    """Represents a single point in a flight track"""
    def __init__(self, timestamp: str, lon: float, lat: float, alt: float):
        self.timestamp = timestamp
        self.lon = lon
        self.lat = lat
        self.alt = alt
        self.datetime = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    
    def __repr__(self):
        return f"TrackPoint({self.timestamp}, {self.lat:.4f}, {self.lon:.4f}, {self.alt:.0f})"


class FlightSegment:
    """Represents a complete flight from origin to destination"""
    def __init__(self, registration: str, points: List[TrackPoint], 
                 origin: Optional[Airport], destination: Optional[Airport],
                 style_color: str, style_width: str):
        self.registration = registration
        self.points = points
        self.origin = origin
        self.destination = destination
        self.style_color = style_color
        self.style_width = style_width
        
        # Extract takeoff datetime from first point
        if points:
            self.takeoff_time = points[0].datetime
            self.takeoff_str = points[0].datetime.strftime('%Y-%m-%d_%H%M')
        else:
            self.takeoff_time = None
            self.takeoff_str = "UNKNOWN"
    
    def get_route_name(self) -> str:
        """Generate route name in format: ORIG-DEST DATE-TIME TAIL"""
        orig = self.origin.code if self.origin else "UNKN"
        dest = self.destination.code if self.destination else "UNKN"
        return f"{orig}-{dest} {self.takeoff_str} {self.registration}"
    
    def get_folder_name(self) -> str:
        """Generate folder name in format: ORIG-DEST"""
        orig = self.origin.code if self.origin else "UNKN"
        dest = self.destination.code if self.destination else "UNKN"
        return f"{orig}-{dest}"
    
    def __repr__(self):
        return f"FlightSegment({self.registration}, {len(self.points)} pts, {self.origin} -> {self.destination})"


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points on Earth (in kilometers)
    """
    # Convert decimal degrees to radians
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    
    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    
    # Radius of Earth in kilometers
    r = 6371
    
    return c * r


def load_airports(filepath: str) -> List[Airport]:
    """Load airports from the airport database file"""
    airports = []
    
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line or not line.startswith('A,'):
                continue
            
            parts = line.split(',')
            if len(parts) < 6:
                continue
            
            try:
                code = parts[1]
                name = parts[2]
                lat = float(parts[3])
                lon = float(parts[4])
                elevation = float(parts[5])
                
                airports.append(Airport(code, name, lat, lon, elevation))
            except (ValueError, IndexError):
                continue
    
    print(f"Loaded {len(airports)} airports")
    return airports


def find_nearest_airport(lat: float, lon: float, airports: List[Airport], 
                        max_distance_km: float = 50.0) -> Optional[Airport]:
    """Find the nearest airport within max_distance_km"""
    nearest = None
    min_distance = float('inf')
    
    for airport in airports:
        distance = haversine_distance(lat, lon, airport.lat, airport.lon)
        if distance < min_distance and distance <= max_distance_km:
            min_distance = distance
            nearest = airport
    
    return nearest


def parse_kml_tracks(kml_file: str) -> Dict[str, List[Tuple[List[TrackPoint], str, str]]]:
    """
    Parse KML file and extract flight tracks by registration
    Returns: dict mapping registration -> list of (track_points, style_color, style_width)
    """
    # Parse with namespaces
    tree = ET.parse(kml_file)
    root = tree.getroot()
    
    # Define namespaces
    ns = {
        'kml': 'http://www.opengis.net/kml/2.2',
        'gx': 'http://www.google.com/kml/ext/2.2'
    }
    
    tracks_by_registration = defaultdict(list)
    
    # Recursive function to find registration folder
    def find_registration_folder(element, path=[]):
        """Recursively search for folders that look like registrations"""
        name_elem = element.find('kml:name', ns)
        current_name = name_elem.text if name_elem is not None and name_elem.text else None
        
        if current_name:
            path = path + [current_name]
        
        # Check if this folder contains placemarks directly
        placemarks = element.findall('kml:Placemark', ns)
        if placemarks:
            # This is likely the registration folder - use the current name
            registration = current_name if current_name else "UNKNOWN"
            
            for placemark in placemarks:
                # Extract style information
                style = placemark.find('kml:Style', ns)
                style_color = '7f0000FF'  # Default red semi-transparent
                style_width = '0.5'  # Default width
                
                if style is not None:
                    line_style = style.find('kml:LineStyle', ns)
                    if line_style is not None:
                        color_elem = line_style.find('kml:color', ns)
                        width_elem = line_style.find('kml:width', ns)
                        if color_elem is not None and color_elem.text:
                            style_color = color_elem.text
                        if width_elem is not None and width_elem.text:
                            style_width = width_elem.text
                
                # Extract track data
                track = placemark.find('.//gx:Track', ns)
                if track is None:
                    continue
                
                # Get all when and coord elements
                whens = track.findall('kml:when', ns)
                coords = track.findall('gx:coord', ns)
                
                if len(whens) != len(coords):
                    print(f"Warning: Mismatch in when/coord count for {registration}: {len(whens)} vs {len(coords)}")
                    continue
                
                # Create track points
                track_points = []
                for when, coord in zip(whens, coords):
                    if when.text and coord.text:
                        timestamp = when.text
                        coord_parts = coord.text.strip().split()
                        
                        if len(coord_parts) >= 2:
                            try:
                                lon = float(coord_parts[0])
                                lat = float(coord_parts[1])
                                alt = float(coord_parts[2]) if len(coord_parts) > 2 else 0.0
                                
                                track_points.append(TrackPoint(timestamp, lon, lat, alt))
                            except ValueError:
                                continue
                
                if track_points:
                    tracks_by_registration[registration].append((track_points, style_color, style_width))
        
        # Recurse into child folders
        for child_folder in element.findall('kml:Folder', ns):
            find_registration_folder(child_folder, path)
    
    # Start recursive search from document
    document = root.find('.//kml:Document', ns)
    if document is not None:
        find_registration_folder(document)
    
    return tracks_by_registration


def detect_flight_segments(track_points: List[TrackPoint], 
                          airports: List[Airport],
                          altitude_threshold: float = 500.0,  # ft - lower threshold for better detection
                          min_flight_altitude: float = 3000.0,  # ft - must reach this to be considered a flight
                          time_gap_threshold: float = 3600.0,
                          distance_threshold: float = 50.0) -> List[FlightSegment]:  # km - if no altitude, use distance
    """
    Detect individual flight segments from a continuous track
    Returns list of FlightSegment objects
    
    Strategy:
    1. Primary: Use altitude changes for detection
    2. Fallback: If altitudes are all zero/low, use distance-based detection
    3. Handle time gaps that might indicate data breaks
    """
    if not track_points:
        return []
    
    # Check if we have meaningful altitude data
    max_alt = max(p.alt for p in track_points)
    use_altitude = max_alt > altitude_threshold
    
    if use_altitude:
        # Use altitude-based detection
        return _detect_by_altitude(track_points, altitude_threshold, min_flight_altitude, time_gap_threshold)
    else:
        # Use distance/position based detection
        return _detect_by_distance(track_points, airports, distance_threshold, time_gap_threshold)


def _detect_by_altitude(track_points: List[TrackPoint],
                       altitude_threshold: float,
                       min_flight_altitude: float,
                       time_gap_threshold: float) -> List[List[TrackPoint]]:
    """
    Altitude-based flight detection
    Handles:
    - Complete flights (takeoff to landing)
    - Flights starting already airborne
    - Flights ending still airborne
    """
    segments = []
    current_segment_points = []
    in_flight = False
    
    # Check if track starts already airborne
    if track_points and track_points[0].alt > min_flight_altitude:
        print(f"    Track starts airborne at {track_points[0].alt:.0f}ft")
        in_flight = True
        current_segment_points = []
    
    for i, point in enumerate(track_points):
        # Check if we're at the start of a flight (takeoff)
        if not in_flight and point.alt > altitude_threshold:
            in_flight = True
            current_segment_points = [point]
            
            # Add previous points if they're on the ground
            look_back = min(10, i)
            for j in range(look_back, 0, -1):
                prev_point = track_points[i - j]
                if prev_point.alt <= altitude_threshold:
                    current_segment_points.insert(0, prev_point)
                else:
                    break
        
        elif in_flight:
            current_segment_points.append(point)
            
            # Check for landing (altitude drops below threshold and stays low)
            if point.alt < altitude_threshold:
                is_landing = True
                look_ahead = min(10, len(track_points) - i - 1)
                for j in range(1, look_ahead + 1):
                    if i + j < len(track_points):
                        if track_points[i + j].alt > altitude_threshold * 2:
                            is_landing = False
                            break
                
                if is_landing and len(current_segment_points) > 10:
                    # Add a few more points after landing for taxi
                    for j in range(1, min(10, len(track_points) - i)):
                        if i + j < len(track_points):
                            next_pt = track_points[i + j]
                            if next_pt.alt <= altitude_threshold:
                                current_segment_points.append(next_pt)
                            else:
                                break
                    
                    segments.append(current_segment_points[:])
                    print(f"      Found complete flight: {len(current_segment_points)} points")
                    
                    in_flight = False
                    current_segment_points = []
        
        # Check for time gaps
        if i > 0:
            time_diff = (point.datetime - track_points[i-1].datetime).total_seconds()
            if time_diff > time_gap_threshold:
                if in_flight and len(current_segment_points) > 10:
                    segments.append(current_segment_points[:])
                    print(f"      Found flight segment (time gap): {len(current_segment_points)} points")
                    current_segment_points = []
                    in_flight = False
    
    # Handle any remaining points (flight still airborne at end of track)
    if current_segment_points and len(current_segment_points) > 10:
        # Check if still at high altitude (incomplete flight)
        if track_points[-1].alt > altitude_threshold:
            print(f"      Found incomplete flight (still airborne): {len(current_segment_points)} points")
        else:
            print(f"      Found complete flight: {len(current_segment_points)} points")
        segments.append(current_segment_points)
    
    return segments


def _detect_by_distance(track_points: List[TrackPoint],
                       airports: List[Airport],
                       distance_threshold: float,
                       time_gap_threshold: float) -> List[List[TrackPoint]]:
    """
    Distance-based flight detection for data without altitude
    Detects flights by:
    1. Large time gaps (> 1 hour = likely different flights)
    2. Proximity to different airports (origin != destination)
    3. Minimum distance traveled
    """
    if not track_points:
        return []
    
    # Lower threshold for shorter flights
    min_distance_km = 10.0  # Minimum 10km to be considered a flight
    min_points = 10  # Minimum points for a valid segment
    
    segments = []
    current_segment = []
    
    print(f"    Distance-based detection: {len(track_points)} total points")
    
    for i, point in enumerate(track_points):
        # Check for time gap
        if i > 0:
            time_diff = (point.datetime - track_points[i-1].datetime).total_seconds()
            
            if time_diff > time_gap_threshold:
                # End current segment if it's substantial
                if len(current_segment) >= min_points:
                    first = current_segment[0]
                    last = current_segment[-1]
                    distance = haversine_distance(first.lat, first.lon, last.lat, last.lon)
                    duration_hours = (last.datetime - first.datetime).total_seconds() / 3600
                    
                    print(f"      Segment: {len(current_segment)} pts, {distance:.1f}km, {duration_hours:.1f}h, gap={time_diff/3600:.1f}h")
                    
                    if distance >= min_distance_km:
                        segments.append(current_segment[:])
                        print(f"        ✓ Added as flight segment")
                    else:
                        print(f"        ✗ Distance too short ({distance:.1f}km < {min_distance_km}km)")
                
                # Start new segment
                current_segment = [point]
            else:
                current_segment.append(point)
        else:
            current_segment.append(point)
    
    # Handle final segment
    if len(current_segment) >= min_points:
        first = current_segment[0]
        last = current_segment[-1]
        distance = haversine_distance(first.lat, first.lon, last.lat, last.lon)
        duration_hours = (last.datetime - first.datetime).total_seconds() / 3600
        
        print(f"      Final segment: {len(current_segment)} pts, {distance:.1f}km, {duration_hours:.1f}h")
        
        if distance >= min_distance_km:
            segments.append(current_segment)
            print(f"        ✓ Added as flight segment")
        else:
            print(f"        ✗ Distance too short ({distance:.1f}km < {min_distance_km}km)")
    
    return segments


def create_flight_segments(segments_points: List[List[TrackPoint]], 
                          registration: str,
                          airports: List[Airport],
                          style_color: str,
                          style_width: str) -> List[FlightSegment]:
    """Create FlightSegment objects with airport matching"""
    flight_segments = []
    
    for points in segments_points:
        if not points:
            continue
        
        # Find origin (first point)
        first_point = points[0]
        origin = find_nearest_airport(first_point.lat, first_point.lon, airports)
        
        # Find destination (last point)
        last_point = points[-1]
        destination = find_nearest_airport(last_point.lat, last_point.lon, airports)
        
        segment = FlightSegment(registration, points, origin, destination, 
                               style_color, style_width)
        flight_segments.append(segment)
    
    return flight_segments


def combine_incomplete_routes(all_segments: List[FlightSegment]) -> List[FlightSegment]:
    """
    Combine incomplete routes that span multiple days for the same aircraft
    An incomplete route has no destination airport at the end
    """
    combined_segments = []
    incomplete_by_registration = {}
    
    # Sort segments by takeoff time
    sorted_segments = sorted(all_segments, key=lambda s: s.takeoff_time if s.takeoff_time else datetime.min)
    
    for segment in sorted_segments:
        reg = segment.registration
        
        # Check if this segment has no origin but we have an incomplete segment for this registration
        if segment.origin is None and reg in incomplete_by_registration:
            # This might be a continuation - check if destination matches
            prev_segment = incomplete_by_registration[reg]
            
            # Combine the segments
            combined_points = prev_segment.points + segment.points
            combined_segment = FlightSegment(
                reg,
                combined_points,
                prev_segment.origin,  # Use origin from previous segment
                segment.destination,   # Use destination from current segment
                segment.style_color,
                segment.style_width
            )
            
            if segment.destination is not None:
                # Complete route now
                combined_segments.append(combined_segment)
                del incomplete_by_registration[reg]
            else:
                # Still incomplete
                incomplete_by_registration[reg] = combined_segment
        
        # Check if this segment has no destination
        elif segment.destination is None:
            # Save as incomplete
            incomplete_by_registration[reg] = segment
        
        else:
            # Complete segment with both origin and destination
            combined_segments.append(segment)
    
    # Add any remaining incomplete segments
    for incomplete in incomplete_by_registration.values():
        combined_segments.append(incomplete)
    
    return combined_segments


def create_output_kml(flight_segments: List[FlightSegment], output_file: str):
    """Create output KML file with organized folder structure"""
    
    # Create root elements with proper namespaces
    kml = ET.Element('kml')
    kml.set('xmlns', 'http://www.opengis.net/kml/2.2')
    kml.set('xmlns:gx', 'http://www.google.com/kml/ext/2.2')
    
    document = ET.SubElement(kml, 'Document')
    name_elem = ET.SubElement(document, 'name')
    name_elem.text = 'Flight Routes by Airport'
    
    # Organize segments by origin airport and route
    routes_by_origin = defaultdict(lambda: defaultdict(list))
    
    for segment in flight_segments:
        origin_code = segment.origin.code if segment.origin else "UNKNOWN"
        route_key = segment.get_folder_name()
        routes_by_origin[origin_code][route_key].append(segment)
    
    # Sort origins alphabetically
    for origin_code in sorted(routes_by_origin.keys()):
        # Create origin folder
        origin_folder = ET.SubElement(document, 'Folder')
        origin_name = ET.SubElement(origin_folder, 'name')
        origin_name.text = origin_code
        
        # Sort routes alphabetically within origin
        for route_key in sorted(routes_by_origin[origin_code].keys()):
            # Create route folder (ORIG-DEST)
            route_folder = ET.SubElement(origin_folder, 'Folder')
            route_name_elem = ET.SubElement(route_folder, 'name')
            route_name_elem.text = route_key
            
            # Add all flights for this route
            segments = routes_by_origin[origin_code][route_key]
            
            # Sort by takeoff time
            segments.sort(key=lambda s: s.takeoff_time if s.takeoff_time else datetime.min)
            
            for segment in segments:
                # Create placemark for this flight
                placemark = ET.SubElement(route_folder, 'Placemark')
                
                placemark_name = ET.SubElement(placemark, 'name')
                placemark_name.text = segment.get_route_name()
                
                # Add style
                style = ET.SubElement(placemark, 'Style')
                line_style = ET.SubElement(style, 'LineStyle')
                
                color_elem = ET.SubElement(line_style, 'color')
                color_elem.text = segment.style_color
                
                width_elem = ET.SubElement(line_style, 'width')
                width_elem.text = segment.style_width
                
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
                extrude.text = '0'
                
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


def process_kml_files(input_files: List[str], airports_file: str, output_file: str):
    """Main processing function"""
    
    print(f"Loading airports from: {airports_file}")
    airports = load_airports(airports_file)
    
    if not airports:
        print("Error: No airports loaded!")
        return
    
    all_flight_segments = []
    
    # Process each input file
    for input_file in input_files:
        print(f"\nProcessing: {input_file}")
        
        # Parse KML file
        tracks_by_registration = parse_kml_tracks(input_file)
        print(f"Found {len(tracks_by_registration)} aircraft registrations")
        
        # Process each registration's tracks
        for registration, track_list in tracks_by_registration.items():
            print(f"\nProcessing {registration}...")
            
            for track_points, style_color, style_width in track_list:
                print(f"  Track with {len(track_points)} points")
                
                # Detect flight segments
                segments = detect_flight_segments(track_points, airports)
                print(f"  Detected {len(segments)} flight segments")
                
                # Create flight segment objects with airport matching
                flight_segments = create_flight_segments(
                    segments, registration, airports, style_color, style_width
                )
                
                # Print segment details
                for seg in flight_segments:
                    orig = seg.origin.code if seg.origin else "UNKN"
                    dest = seg.destination.code if seg.destination else "UNKN"
                    print(f"    {orig} -> {dest}: {len(seg.points)} points, {seg.takeoff_str}")
                
                all_flight_segments.extend(flight_segments)
    
    print(f"\nTotal flight segments before combining: {len(all_flight_segments)}")
    
    # Combine incomplete routes across days
    print("\nCombining incomplete routes...")
    combined_segments = combine_incomplete_routes(all_flight_segments)
    print(f"Total flight segments after combining: {len(combined_segments)}")
    
    # Create output KML
    print("\nCreating output KML...")
    create_output_kml(combined_segments, output_file)
    
    # Summary statistics
    complete_routes = sum(1 for s in combined_segments if s.origin and s.destination)
    incomplete_routes = len(combined_segments) - complete_routes
    
    print(f"\nSummary:")
    print(f"  Complete routes (with both airports): {complete_routes}")
    print(f"  Incomplete routes: {incomplete_routes}")


def main():
    parser = argparse.ArgumentParser(
        description='Split continuous flight tracks into individual routes with airport matching',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process single file
  python flight_route_splitter.py -i flights_day1.kml -a airports.txt -o routes.kml
  
  # Process multiple files (for multi-day routes)
  python flight_route_splitter.py -i day1.kml day2.kml day3.kml -a airports.txt -o routes.kml
        """
    )
    
    parser.add_argument('-i', '--input', nargs='+', required=True,
                       help='Input KML file(s) (can specify multiple for multi-day routes)')
    parser.add_argument('-a', '--airports', required=True,
                       help='Airport database file (e.g., airports.txt)')
    parser.add_argument('-o', '--output', required=True,
                       help='Output KML file')
    
    args = parser.parse_args()
    
    # Validate input files exist
    import os
    for input_file in args.input:
        if not os.path.exists(input_file):
            print(f"Error: Input file not found: {input_file}")
            sys.exit(1)
    
    if not os.path.exists(args.airports):
        print(f"Error: Airports file not found: {args.airports}")
        sys.exit(1)
    
    # Process files
    process_kml_files(args.input, args.airports, args.output)
    
    print("\nProcessing complete!")


if __name__ == '__main__':
    main()
