# Style Guide

**Project**: Flight Route Splitter  
**Version**: 6.0  
**Last Updated**: 2025-04-27

This document defines coding standards and conventions for the project.

## Python Version

**Target**: Python 3.6+  
**Reason**: Type hints (3.5+), f-strings (3.6+)

## Code Formatting

### PEP 8 Compliance

Follow [PEP 8](https://www.python.org/dev/peps/pep-0008/) with these specifics:

**Line Length**: 100 characters (not 79)
```python
# Good
def find_nearest_airport(lat: float, lon: float, airports: List[Airport], 
                        max_distance_km: float = 50.0) -> Optional[Airport]:

# Avoid
def find_nearest_airport(lat: float, lon: float, airports: List[Airport], max_distance_km: float = 50.0) -> Optional[Airport]:
```

**Indentation**: 4 spaces (no tabs)

**Blank Lines**:
- 2 blank lines between top-level functions/classes
- 1 blank line between methods in a class
- 1 blank line to separate logical sections within functions

### Imports

**Order**:
1. Standard library
2. Third-party libraries (if any)
3. Local modules

**Format**:
```python
# Standard library
import xml.etree.ElementTree as ET
from datetime import datetime
from math import radians, cos, sin, asin, sqrt
import argparse
import sys
from collections import defaultdict
from typing import List, Tuple, Dict, Optional

# No third-party imports in this project

# Local imports (if using modules)
from .utils import haversine_distance
```

### Naming Conventions

| Type | Convention | Example |
|------|-----------|---------|
| Modules | snake_case | flight_route_splitter |
| Classes | PascalCase | FlightSegment, Airport |
| Functions | snake_case | detect_flight_segments |
| Methods | snake_case | get_route_name |
| Variables | snake_case | track_points, max_distance |
| Constants | UPPER_CASE | DEFAULT_RADIUS, MAX_ALTITUDE |
| Private | _leading_underscore | _detect_by_altitude |

**Examples**:
```python
# Constants
MAX_AIRPORT_DISTANCE_KM = 50.0
DEFAULT_ALTITUDE_THRESHOLD = 500.0

# Classes
class FlightSegment:
    pass

class TrackPoint:
    pass

# Functions
def haversine_distance(lat1, lon1, lat2, lon2):
    pass

# Variables
track_points = []
current_segment = None
```

## Type Hints

**Required**: All function signatures must have type hints.

### Function Signatures

```python
def find_nearest_airport(lat: float, lon: float, airports: List[Airport], 
                        max_distance_km: float = 50.0) -> Optional[Airport]:
    """Find nearest airport within radius."""
    pass
```

### Class Attributes

```python
class TrackPoint:
    """Represents a single point in a flight track"""
    def __init__(self, timestamp: str, lon: float, lat: float, alt: float):
        self.timestamp: str = timestamp
        self.lon: float = lon
        self.lat: float = lat
        self.alt: float = alt
        self.datetime: datetime = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
```

### Common Types

```python
from typing import List, Tuple, Dict, Optional, Union

# Lists
airports: List[Airport] = []
points: List[TrackPoint] = []

# Dictionaries
tracks: Dict[str, List[TrackPoint]] = {}
config: Dict[str, Any] = {}

# Tuples
coord: Tuple[float, float, float] = (lon, lat, alt)
result: Tuple[bool, str] = (True, "Success")

# Optional
airport: Optional[Airport] = None
error: Optional[str] = None

# Union (avoid if possible)
value: Union[int, str] = 42
```

## Docstrings

### Format: Google Style

```python
def detect_flight_segments(track_points: List[TrackPoint], 
                          airports: List[Airport],
                          altitude_threshold: float = 500.0,
                          min_flight_altitude: float = 3000.0,
                          time_gap_threshold: float = 3600.0) -> List[List[TrackPoint]]:
    """
    Detect individual flight segments from a continuous track.
    
    Uses hybrid algorithm: altitude-based (primary) or distance-based (fallback)
    depending on data quality.
    
    Args:
        track_points: Ordered list of track points
        airports: List of available airports for matching
        altitude_threshold: Ground/airborne threshold in feet (default 500)
        min_flight_altitude: Minimum cruise altitude in feet (default 3000)
        time_gap_threshold: Flight separation time in seconds (default 3600)
        
    Returns:
        List of flight segments, each segment is a list of TrackPoints
        
    Example:
        >>> points = [TrackPoint(...), TrackPoint(...), ...]
        >>> airports = load_airports('airports.txt')
        >>> segments = detect_flight_segments(points, airports)
        >>> print(f"Found {len(segments)} flights")
        Found 3 flights
    """
    pass
```

### Class Docstrings

```python
class FlightSegment:
    """
    Represents a complete flight from origin to destination.
    
    Attributes:
        registration: Aircraft tail number (e.g., "XA-ADC")
        points: Ordered list of track points for this flight
        origin: Departure airport (None if not matched)
        destination: Arrival airport (None if not matched)
        style_color: KML line color in ABGR format
        style_width: KML line width as string
        takeoff_time: Datetime of first point
        takeoff_str: Formatted takeoff time for filenames
        
    Example:
        >>> segment = FlightSegment(
        ...     registration="XA-ADC",
        ...     points=track_points,
        ...     origin=mmmx_airport,
        ...     destination=klax_airport,
        ...     style_color="7f0000FF",
        ...     style_width="0.5"
        ... )
        >>> print(segment.get_route_name())
        MMMX-KLAX 2025-10-21_1430 XA-ADC
    """
    pass
```

## Comments

### Inline Comments

Use sparingly - prefer clear code over comments:

```python
# Good - comment explains WHY
# Haversine formula requires radians
lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])

# Bad - comment explains WHAT (obvious from code)
# Convert to radians
lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
```

### Section Comments

Use for logical sections within long functions:

```python
def process_kml_files(input_files, airports_file, output_file):
    """Main processing function."""
    
    # Load airport database
    airports = load_airports(airports_file)
    
    # Process each input file
    for input_file in input_files:
        tracks = parse_kml_tracks(input_file)
        
        # Detect flight segments
        segments = detect_flight_segments(tracks, airports)
        
        # Match airports
        flights = match_airports(segments, airports)
    
    # Generate output
    create_output_kml(flights, output_file)
```

### TODO Comments

Format: `# TODO(name): Description`

```python
# TODO(john): Add spatial indexing for faster airport lookup
# TODO(team): Consider streaming processing for large files
# FIXME(alice): Haversine fails at poles - add special case
```

## Error Handling

### Explicit is Better

```python
# Good
try:
    airport_code = parts[1]
    lat = float(parts[3])
    lon = float(parts[4])
except (IndexError, ValueError) as e:
    print(f"Warning: Invalid airport record: {line}")
    continue

# Bad - too broad
try:
    # ... lots of code
except Exception:
    pass
```

### Meaningful Messages

```python
# Good
if not os.path.exists(input_file):
    print(f"Error: Input file not found: {input_file}")
    sys.exit(1)

# Bad
if not os.path.exists(input_file):
    print("Error")
    sys.exit(1)
```

## Function Design

### Single Responsibility

Each function should do one thing:

```python
# Good - separate concerns
def parse_kml_tracks(kml_file):
    """Parse KML and extract tracks."""
    pass

def detect_flight_segments(tracks):
    """Detect flight segments from tracks."""
    pass

# Bad - mixed concerns
def parse_and_detect(kml_file):
    """Parse KML and detect flights."""
    pass
```

### Small Functions

Target: < 50 lines per function
- Easier to test
- Easier to understand
- Easier to reuse

```python
# Good - helper functions
def _detect_by_altitude(points):
    """Altitude-based detection."""
    pass

def _detect_by_distance(points):
    """Distance-based detection."""
    pass

def detect_flight_segments(points):
    """Detect using appropriate method."""
    if _has_altitude_data(points):
        return _detect_by_altitude(points)
    else:
        return _detect_by_distance(points)
```

### Default Arguments

Place mutable defaults carefully:

```python
# Good
def process_files(files: List[str], config: Optional[Dict] = None):
    if config is None:
        config = {}
    
# Bad - mutable default
def process_files(files: List[str], config: Dict = {}):
    pass
```

## Variable Naming

### Descriptive Names

```python
# Good
max_altitude_ft = 35000
airport_search_radius_km = 50.0
track_points_count = len(points)

# Bad
max_alt = 35000
radius = 50
n = len(points)
```

### Boolean Prefixes

```python
# Good
is_complete = True
has_altitude_data = max(altitudes) > 0
can_combine_routes = registration_matches

# Bad
complete = True
altitude_data = max(altitudes) > 0
combine = registration_matches
```

## Constants

Define at module level:

```python
# At top of file
DEFAULT_ALTITUDE_THRESHOLD_FT = 500.0
DEFAULT_MIN_CRUISE_ALTITUDE_FT = 3000.0
DEFAULT_TIME_GAP_SECONDS = 3600.0
DEFAULT_AIRPORT_SEARCH_RADIUS_KM = 50.0
MIN_FLIGHT_DISTANCE_KM = 10.0
MIN_SEGMENT_POINTS = 10

# Use in functions
def detect_flights(points, threshold=DEFAULT_ALTITUDE_THRESHOLD_FT):
    pass
```

## List Comprehensions

### When to Use

Simple transformations:

```python
# Good
altitudes = [point.alt for point in points]
airports_above_5000ft = [a for a in airports if a.elevation > 5000]

# Bad - too complex
result = [process_complex(x, y, z) for x in items 
          if x.valid and x.status == 'active' 
          for y in x.children 
          if y.type == 'foo']

# Better - explicit loop
result = []
for x in items:
    if x.valid and x.status == 'active':
        for y in x.children:
            if y.type == 'foo':
                result.append(process_complex(x, y, z))
```

## String Formatting

### f-strings (Preferred)

```python
# Good
message = f"Found {count} flights from {origin} to {dest}"
filename = f"{origin}-{dest}_{date}_{time}_{registration}.kml"

# Acceptable for complex formatting
value = f"{number:,.2f}"  # 1,234.56
coord = f"{lat:.6f},{lon:.6f}"  # 6 decimal places
```

### Format Strings

Use for templates:

```python
template = "{origin}-{dest} {date}_{time} {registration}"
name = template.format(
    origin=segment.origin.code,
    dest=segment.destination.code,
    date=date_str,
    time=time_str,
    registration=segment.registration
)
```

## File I/O

### Context Managers

Always use `with` statements:

```python
# Good
with open(filepath, 'r', encoding='utf-8') as f:
    for line in f:
        process(line)

# Bad
f = open(filepath, 'r')
for line in f:
    process(line)
f.close()
```

### Encoding

Always specify encoding:

```python
# Good
with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
    data = f.read()

# Bad - platform-dependent
with open(filepath, 'r') as f:
    data = f.read()
```

## Testing Patterns

### Assertions

Use descriptive messages:

```python
# Good
assert len(segments) > 0, f"No segments found in {filename}"
assert origin is not None, f"No origin found for {registration}"

# Bad
assert len(segments) > 0
assert origin is not None
```

### Test Data

Use clear, minimal examples:

```python
def test_haversine_distance():
    """Test distance calculation between known points."""
    # Los Angeles to New York
    lat1, lon1 = 34.0522, -118.2437
    lat2, lon2 = 40.7128, -74.0060
    
    distance = haversine_distance(lat1, lon1, lat2, lon2)
    
    # Expected: ~3944 km (allow 1% tolerance)
    assert 3900 < distance < 3990
```

## Performance Guidelines

### Prefer Built-ins

```python
# Good - built-in
points_count = len(points)
max_altitude = max(p.alt for p in points)

# Bad - manual
points_count = 0
for p in points:
    points_count += 1
```

### Avoid Repeated Lookups

```python
# Good
max_alt = max(p.alt for p in points)
if max_alt > threshold:
    process_altitude_data(max_alt)

# Bad
if max(p.alt for p in points) > threshold:
    process_altitude_data(max(p.alt for p in points))
```

### Use Generators for Large Data

```python
# Good - generator
altitudes = (point.alt for point in points if point.alt > 0)
max_altitude = max(altitudes)

# Bad - unnecessary list
altitudes = [point.alt for point in points if point.alt > 0]
max_altitude = max(altitudes)
```

## Project-Specific Conventions

### Airport Codes

Always uppercase, 3-4 characters:

```python
# Good
UNKNOWN_AIRPORT_CODE = "UNKN"
origin_code = airport.code.upper()

# Bad
unknown_code = "unknown"
origin_code = airport.code
```

### Distances

Always specify units in variable names:

```python
# Good
distance_km = haversine_distance(p1, p2)
radius_km = 50.0
altitude_ft = point.alt

# Bad
distance = haversine_distance(p1, p2)
radius = 50.0
altitude = point.alt
```

### Timestamps

Always work with datetime objects internally:

```python
# Good
def __init__(self, timestamp: str, ...):
    self.timestamp = timestamp  # Original string
    self.datetime = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))

# Use datetime for comparisons
if point1.datetime < point2.datetime:
    pass
```

## Anti-Patterns to Avoid

### Magic Numbers

```python
# Bad
if distance < 50:
    pass

# Good
MAX_AIRPORT_DISTANCE_KM = 50.0
if distance < MAX_AIRPORT_DISTANCE_KM:
    pass
```

### Overly Clever Code

```python
# Bad
result = list(map(lambda x: x.alt if x.alt > 500 else 0, 
                  filter(lambda x: x.valid, points)))

# Good
result = []
for point in points:
    if point.valid:
        altitude = point.alt if point.alt > 500 else 0
        result.append(altitude)

# Even better
result = [p.alt if p.alt > 500 else 0 for p in points if p.valid]
```

### Premature Optimization

```python
# Bad - premature caching
_distance_cache = {}
def cached_distance(p1, p2):
    key = (p1, p2)
    if key not in _distance_cache:
        _distance_cache[key] = haversine_distance(p1, p2)
    return _distance_cache[key]

# Good - simple and clear
def distance(p1, p2):
    return haversine_distance(p1, p2)

# Optimize only after profiling shows it's a bottleneck
```

## Checklist for New Code

Before committing:

- [ ] All functions have type hints
- [ ] All functions have docstrings
- [ ] No lines > 100 characters
- [ ] PEP 8 compliant (use flake8)
- [ ] No bare `except:` clauses
- [ ] All file I/O uses context managers
- [ ] All file I/O specifies encoding
- [ ] Meaningful variable names
- [ ] Constants defined at module level
- [ ] Error messages are informative
- [ ] Code is tested (when tests available)

---

**Document Status**: Living Guide  
**Review Frequency**: As needed  
**Last Reviewed**: 2025-04-27
