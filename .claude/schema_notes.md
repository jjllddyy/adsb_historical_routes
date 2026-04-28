# Schema Notes

**Project**: Flight Route Splitter  
**Version**: 6.0  
**Last Updated**: 2025-04-27

This document details the data schemas and formats used in the project.

## Input Schemas

### KML Flight Track Schema

#### Namespace Definitions
```xml
xmlns="http://www.opengis.net/kml/2.2"
xmlns:gx="http://www.google.com/kml/ext/2.2"
```

#### Required Elements
```xml
<kml>
  <Document>
    <Folder>                          <!-- Optional wrapper -->
      <name>Registration</name>       <!-- Aircraft registration -->
      <Placemark>                     <!-- Flight track -->
        <Style>                       <!-- Optional styling -->
          <LineStyle>
            <color>AABBGGRR</color>   <!-- KML ABGR format -->
            <width>float</width>
          </LineStyle>
          <IconStyle>
            <scale>0</scale>          <!-- Hide markers -->
          </IconStyle>
        </Style>
        <gx:Track>
          <altitudeMode>absolute</altitudeMode>
          <when>ISO8601_timestamp</when>
          <gx:coord>lon lat alt</gx:coord>
        </gx:Track>
      </Placemark>
    </Folder>
  </Document>
</kml>
```

#### Data Types

| Element | Type | Format | Range | Example |
|---------|------|--------|-------|---------|
| when | datetime | ISO 8601 | UTC | 2025-10-15T04:35:50.490Z |
| lon | float64 | decimal degrees | -180 to 180 | -99.083284 |
| lat | float64 | decimal degrees | -90 to 90 | 19.42846 |
| alt | float64 | feet MSL | 0 to 60000+ | 2500.0 |
| color | hex string | AABBGGRR | 00000000 to FFFFFFFF | 7f0000FF |
| width | float | pixels | 0.1 to 10.0 | 0.5 |

#### Color Format Details

KML uses ABGR (Alpha, Blue, Green, Red) format:
- **AA**: Alpha (transparency) - 00 = transparent, FF = opaque
- **BB**: Blue component - 00 to FF
- **GG**: Green component - 00 to FF
- **RR**: Red component - 00 to FF

**Examples**:
```
7f0000FF = Semi-transparent red (50% opacity)
FF00FF00 = Opaque green
80FF0000 = Semi-transparent blue (50% opacity)
FFFFFFFF = Opaque white
00000000 = Fully transparent black
```

#### Coordinate System

All coordinates use **WGS84** (World Geodetic System 1984):
- Standard GPS coordinate system
- Datum: WGS84
- Units: Decimal degrees
- Altitude: Feet above Mean Sea Level (MSL)

### Airport Database Schema

#### File Format: CSV (Comma-Separated Values)

#### Record Structure
```
RecordType,Code,Name,Latitude,Longitude,Elevation,Field6,Field7,Field8,Field9,...
```

#### Airport Record Fields

| Position | Name | Type | Required | Format | Example |
|----------|------|------|----------|--------|---------|
| 0 | RecordType | char | Yes | 'A' | A |
| 1 | Code | string | Yes | 3-4 chars | MMMX |
| 2 | Name | string | Yes | any | MEXICO CITY INTL |
| 3 | Latitude | float64 | Yes | -90 to 90 | 19.436306 |
| 4 | Longitude | float64 | Yes | -180 to 180 | -99.072083 |
| 5 | Elevation | float64 | Yes | feet MSL | 7316 |
| 6+ | Additional | any | No | any | [ignored] |

#### Example Records
```
A,MMMX,MEXICO CITY INTL,19.436306,-99.072083,7316,18000,18000,4500,0
A,KLAX,LOS ANGELES INTL,33.942536,-118.408075,125,18000,18000,4500,0
A,KJFK,JOHN F KENNEDY INTL,40.639751,-73.778925,13,18000,18000,4500,0
```

#### Non-Airport Records

These record types are present but ignored:
- **R,**: Runway data
- **N,**: Navaid data
- **W,**: Waypoint data
- **X,**: Header/metadata

Only lines starting with `A,` are processed.

## Output Schema

### Hierarchical KML Structure

```xml
<kml xmlns="http://www.opengis.net/kml/2.2" 
     xmlns:gx="http://www.google.com/kml/ext/2.2">
  <Document>
    <name>Flight Routes by Airport</name>
    
    <!-- Origin Airport Folder (alphabetical) -->
    <Folder>
      <name>MMMX</name>
      
      <!-- Route Folder (alphabetical) -->
      <Folder>
        <name>MMMX-KLAX</name>
        
        <!-- Individual Flight (chronological) -->
        <Placemark>
          <name>MMMX-KLAX 2025-10-21_1430 XA-ADC</name>
          <Style>
            <!-- Preserved from input -->
          </Style>
          <gx:Track>
            <!-- Flight points -->
          </gx:Track>
        </Placemark>
        
        <!-- More flights on same route -->
        <Placemark>
          <name>MMMX-KLAX 2025-10-22_0945 XA-ADC</name>
          <!-- ... -->
        </Placemark>
      </Folder>
      
      <!-- More routes from same origin -->
      <Folder>
        <name>MMMX-KJFK</name>
        <!-- ... -->
      </Folder>
    </Folder>
    
    <!-- More origin airports -->
    <Folder>
      <name>KLAX</name>
      <!-- ... -->
    </Folder>
  </Document>
</kml>
```

### Flight Naming Convention

**Format**: `{ORIGIN}-{DESTINATION} {DATE}_{TIME} {REGISTRATION}`

**Components**:
- **ORIGIN**: ICAO/IATA code or "UNKN" (4 chars max)
- **DESTINATION**: ICAO/IATA code or "UNKN" (4 chars max)
- **DATE**: YYYY-MM-DD (10 chars)
- **TIME**: HHMM in UTC (4 chars)
- **REGISTRATION**: Aircraft tail number (variable length)

**Examples**:
```
MMMX-KLAX 2025-10-21_1430 XA-ADC
KJFK-EGLL 2025-10-15_2245 N12345
UNKN-SAEZ 2025-10-17_0000 LV-BRQ
```

## Internal Data Structures

### Airport Class

```python
class Airport:
    code: str           # ICAO/IATA code (e.g., "MMMX")
    name: str           # Full name (e.g., "MEXICO CITY INTL")
    lat: float          # Latitude in decimal degrees
    lon: float          # Longitude in decimal degrees
    elevation: float    # Elevation in feet MSL
```

**Memory Size**: ~200 bytes per instance  
**Example Count**: 16,422 airports = ~3.2 MB

### TrackPoint Class

```python
class TrackPoint:
    timestamp: str      # ISO 8601 (e.g., "2025-10-15T04:35:50.490Z")
    lon: float          # Longitude in decimal degrees
    lat: float          # Latitude in decimal degrees
    alt: float          # Altitude in feet
    datetime: datetime  # Parsed datetime object
```

**Memory Size**: ~100 bytes per instance  
**Typical Track**: 200 points = ~20 KB

### FlightSegment Class

```python
class FlightSegment:
    registration: str           # Aircraft tail number
    points: List[TrackPoint]    # Ordered list of track points
    origin: Optional[Airport]   # Departure airport (or None)
    destination: Optional[Airport]  # Arrival airport (or None)
    style_color: str            # KML ABGR color string
    style_width: str            # Line width as string
    takeoff_time: datetime      # First point datetime
    takeoff_str: str            # Formatted as "YYYY-MM-DD_HHMM"
```

**Memory Size**: ~1 KB + track points  
**Typical Flight**: ~21 KB total

## Validation Rules

### Input Validation

#### KML File Validation

```python
# Required elements check
assert 'kml' in root.tag
assert 'Document' in [e.tag for e in root]

# Track validation
assert len(whens) == len(coords)
assert len(coords) > 0

# Coordinate validation
for coord in coords:
    lon, lat, alt = parse_coord(coord)
    assert -180 <= lon <= 180
    assert -90 <= lat <= 90
    assert alt >= -1000  # Below sea level possible
```

#### Airport Data Validation

```python
# Record format
parts = line.split(',')
assert parts[0] == 'A'
assert len(parts) >= 6

# Coordinate ranges
lat = float(parts[3])
lon = float(parts[4])
assert -90 <= lat <= 90
assert -180 <= lon <= 180

# Elevation
elev = float(parts[5])
assert -1500 <= elev <= 30000  # Dead Sea to Everest+
```

### Processing Validation

#### Flight Segment Validation

```python
# Point count
assert len(segment.points) >= 10

# Temporal ordering
for i in range(1, len(segment.points)):
    assert segment.points[i].datetime >= segment.points[i-1].datetime

# Geographic validity (if altitude-based)
if segment.origin:
    first_dist = haversine(segment.points[0], segment.origin)
    assert first_dist <= 50  # km

if segment.destination:
    last_dist = haversine(segment.points[-1], segment.destination)
    assert last_dist <= 50  # km
```

### Output Validation

#### Hierarchical Structure

```python
# Required elements
assert document.find('.//Folder') is not None

# Naming convention
name_pattern = r'^[A-Z]{3,4}-[A-Z]{3,4} \d{4}-\d{2}-\d{2}_\d{4} .+$'
for placemark in output.findall('.//Placemark'):
    name = placemark.find('name').text
    assert re.match(name_pattern, name)

# Point preservation
input_points = count_all_points(input_kml)
output_points = count_all_points(output_kml)
assert input_points == output_points
```

## Data Flow Schema

```
┌─────────────────┐
│   Input KML     │
│  (gx:Track)     │
└────────┬────────┘
         │
         ▼
┌─────────────────────────┐
│  parse_kml_tracks()     │
│  Extract: registration  │
│          track points   │
│          styling        │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│ detect_flight_segments()│
│  Altitude or Distance   │
│  Detection              │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│create_flight_segments() │
│  Match airports         │
│  Create FlightSegment   │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│combine_incomplete_routes│
│  Merge multi-day        │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│  create_output_kml()    │
│  Build hierarchy        │
│  Write file             │
└────────┬────────────────┘
         │
         ▼
┌─────────────────┐
│   Output KML    │
│  (Organized)    │
└─────────────────┘
```

## Extension Points

### Adding New Input Formats

To support GPX or other formats:

1. Create parser function matching signature:
```python
def parse_gpx_tracks(file: str) -> Dict[str, List[Tuple[List[TrackPoint], str, str]]]:
    # Return same structure as parse_kml_tracks
    pass
```

2. Add format detection:
```python
if file.endswith('.kml'):
    tracks = parse_kml_tracks(file)
elif file.endswith('.gpx'):
    tracks = parse_gpx_tracks(file)
```

### Adding New Airport Sources

To support different airport databases:

1. Create loader matching signature:
```python
def load_airports_from_csv(file: str) -> List[Airport]:
    # Return list of Airport objects
    pass
```

2. Add format detection based on file structure

### Adding New Output Formats

To support GeoJSON or other formats:

1. Create generator function:
```python
def create_output_geojson(segments: List[FlightSegment], file: str):
    # Generate GeoJSON FeatureCollection
    pass
```

2. Add to output selection logic

## Schema Version History

### v6.0 (Current)
- Initial schema documentation
- KML input/output
- Airport CSV format
- Three-level hierarchy

### Future Considerations

**v7.0 Planned Changes**:
- Add GeoJSON output schema
- Add CSV output schema
- Consider Parquet for large datasets
- Add metadata fields to FlightSegment

**v8.0 Potential Changes**:
- Database backend option
- API request/response schemas
- Real-time streaming format

---

**Document Status**: Living Document  
**Review Frequency**: Each major version  
**Last Reviewed**: 2025-04-27
