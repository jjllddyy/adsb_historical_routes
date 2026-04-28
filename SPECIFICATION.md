# Flight Route Splitter - Technical Specification

**Version**: 6.0  
**Document Date**: 2025-04-27  
**Status**: Active Development

## 1. Overview

### 1.1 Purpose

The Flight Route Splitter is a Python-based tool designed to process continuous flight tracking data in KML format and automatically segment it into individual flight routes with origin and destination airport identification.

### 1.2 Scope

This specification covers:
- Input data formats and requirements
- Processing algorithms and logic
- Output data structure and organization
- Performance requirements
- Edge case handling

### 1.3 Target Users

- Aviation data analysts
- Flight operations teams
- ADS-B data processors
- Route adherence monitoring systems
- Flight optimization engineers

## 2. Requirements

### 2.1 Functional Requirements

#### FR-001: KML Parsing
- **Description**: Parse KML files with `gx:Track` elements
- **Inputs**: KML file path(s)
- **Outputs**: Structured track data with timestamps and coordinates
- **Dependencies**: xml.etree.ElementTree
- **Priority**: Critical

#### FR-002: Flight Detection
- **Description**: Automatically detect individual flight segments from continuous tracks
- **Methods**: 
  - Altitude-based detection (primary)
  - Distance/time-based detection (fallback)
- **Parameters**:
  - Altitude threshold: 500 ft
  - Minimum cruise altitude: 3000 ft
  - Time gap threshold: 3600 seconds
  - Distance threshold: 10 km
- **Priority**: Critical

#### FR-003: Airport Matching
- **Description**: Match flight endpoints to nearest airports
- **Algorithm**: Haversine distance calculation
- **Search radius**: 50 km (configurable)
- **Fallback**: "UNKN" code if no match found
- **Priority**: Critical

#### FR-004: Multi-Day Route Combination
- **Description**: Combine incomplete routes spanning multiple input files
- **Logic**: Match by aircraft registration and temporal proximity
- **Priority**: High

#### FR-005: Output Organization
- **Description**: Create hierarchical folder structure in output KML
- **Structure**:
  ```
  Origin Airport (alphabetical)
    └── Route (ORIG-DEST, alphabetical)
        └── Individual Flights (chronological)
  ```
- **Naming**: `{ORIGIN}-{DEST} {DATE}_{TIME} {REGISTRATION}`
- **Priority**: Critical

#### FR-006: Style Preservation
- **Description**: Maintain line styling from input files
- **Preserved Attributes**:
  - Color (with alpha/transparency)
  - Width
  - Icon style
- **Priority**: Medium

### 2.2 Non-Functional Requirements

#### NFR-001: Performance
- **Small datasets** (< 1K points): < 1 second processing time
- **Medium datasets** (< 10K points): < 5 seconds processing time
- **Large datasets** (< 100K points): < 30 seconds processing time
- **Memory**: < 500 MB for typical workloads

#### NFR-002: Reliability
- **Uptime**: N/A (batch processing tool)
- **Error handling**: Graceful degradation with informative messages
- **Data integrity**: No loss of input data points

#### NFR-003: Maintainability
- **Code quality**: PEP 8 compliant
- **Documentation**: Comprehensive inline comments
- **Type hints**: Used throughout
- **Testing**: Unit tests for core functions (planned)

#### NFR-004: Portability
- **Python versions**: 3.6+
- **Operating systems**: Windows, macOS, Linux
- **Dependencies**: Standard library only

#### NFR-005: Usability
- **CLI**: Intuitive command-line interface
- **Error messages**: Clear and actionable
- **Progress indicators**: Console output for long operations

## 3. Data Formats

### 3.1 Input KML Format

#### Structure Requirements

```xml
<kml xmlns="http://www.opengis.net/kml/2.2" 
     xmlns:gx="http://www.google.com/kml/ext/2.2">
  <Document>
    <Folder>                          <!-- Optional: Date folder -->
      <name>2025-10-15</name>
      <Folder>                        <!-- Required: Registration folder -->
        <name>XA-ADC</name>
        <Placemark>                   <!-- Required: Flight track -->
          <Style>                     <!-- Optional: Styling -->
            <LineStyle>
              <color>7f0000FF</color> <!-- ABGR format -->
              <width>0.5</width>
            </LineStyle>
            <IconStyle>
              <scale>0</scale>
            </IconStyle>
          </Style>
          <gx:Track>                  <!-- Required: Track data -->
            <altitudeMode>absolute</altitudeMode>
            <when>2025-10-15T04:35:50.490Z</when>
            <gx:coord>-99.083 19.428 2500</gx:coord>
            <!-- lon lat alt in decimal degrees and feet -->
          </gx:Track>
        </Placemark>
      </Folder>
    </Folder>
  </Document>
</kml>
```

#### Field Specifications

| Element | Type | Required | Format | Notes |
|---------|------|----------|--------|-------|
| `when` | ISO 8601 | Yes | `YYYY-MM-DDTHH:MM:SS.sssZ` | UTC timezone |
| `gx:coord` | Float triple | Yes | `lon lat alt` | WGS84, altitude in feet |
| `color` | Hex string | No | `AABBGGRR` | KML ABGR format |
| `width` | Float | No | `>0` | Line width in pixels |
| Registration folder | String | Yes | Any | Aircraft tail number |

### 3.2 Airport Database Format

#### File Structure

```
A,CODE,NAME,LATITUDE,LONGITUDE,ELEVATION[,additional fields...]
A,MMMX,MEXICO CITY INTL,19.436306,-99.072083,7316,18000,18000,4500,0
```

#### Field Specifications

| Position | Field | Type | Required | Format | Notes |
|----------|-------|------|----------|--------|-------|
| 0 | Record Type | Char | Yes | `A` | Must be 'A' for airport |
| 1 | Code | String | Yes | 3-4 chars | ICAO or IATA code |
| 2 | Name | String | Yes | Any | Airport name |
| 3 | Latitude | Float | Yes | -90 to 90 | Decimal degrees |
| 4 | Longitude | Float | Yes | -180 to 180 | Decimal degrees |
| 5 | Elevation | Float | Yes | Feet | Elevation above MSL |
| 6+ | Additional | Any | No | Any | Ignored by parser |

### 3.3 Output KML Format

Output follows same structure as input but with reorganized folder hierarchy:

```
Document
  └── Origin Folder (e.g., "MMMX")
      └── Route Folder (e.g., "MMMX-KLAX")
          └── Flight Placemark (e.g., "MMMX-KLAX 2025-10-21_1430 XA-ADC")
```

## 4. Processing Algorithms

### 4.1 Flight Detection Algorithm

#### 4.1.1 Altitude-Based Detection

**Trigger Condition**: `max(altitude) > 500 ft`

**Algorithm**:
```
1. Initialize state: in_flight = False
2. For each track point:
   a. If not in_flight AND altitude > 500ft:
      - Set in_flight = True
      - Start new segment
      - Include previous ground points (taxi)
   
   b. If in_flight:
      - Add point to segment
      - If altitude < 500ft AND confirmed landing:
         * Save segment
         * Set in_flight = False
         * Include subsequent ground points
   
   c. If time_gap > 3600s:
      - End current segment
      - Reset state
3. Handle remaining points (incomplete flight)
```

**Landing Confirmation**:
- Look ahead 10 points
- All must be < 500ft altitude
- Prevents false positives from data glitches

**Edge Cases**:
- Track starting airborne: Detect and handle
- Track ending airborne: Mark as incomplete
- No cruise altitude reached: Discard segment

#### 4.1.2 Distance-Based Detection

**Trigger Condition**: `max(altitude) <= 500 ft`

**Algorithm**:
```
1. Initialize: current_segment = []
2. For each track point:
   a. Calculate time gap from previous point
   b. If gap > 3600s:
      - Calculate segment distance
      - If distance > 10km:
         * Save segment
      - Start new segment
   c. Else:
      - Add to current segment
3. Process final segment
```

**Distance Calculation**:
```python
distance = haversine(first_point, last_point)
```

**Validation**:
- Minimum 10 points per segment
- Minimum 10 km total distance
- Reasonable time duration

### 4.2 Airport Matching Algorithm

**Algorithm**: Nearest neighbor with distance threshold

```
1. For endpoint (takeoff or landing):
   a. Initialize: min_distance = infinity, nearest = None
   b. For each airport in database:
      - Calculate distance = haversine(point, airport)
      - If distance < min_distance AND distance <= 50km:
         * Update min_distance and nearest
   c. Return nearest (or None if > 50km)
```

**Haversine Formula**:
```python
def haversine(lat1, lon1, lat2, lon2):
    # Convert to radians
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    
    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    
    # Earth radius in km
    return 6371 * c
```

### 4.3 Multi-Day Route Combination

**Purpose**: Merge incomplete flights spanning multiple files

**Algorithm**:
```
1. Sort all segments by takeoff_time
2. Initialize: incomplete_by_registration = {}
3. For each segment:
   a. If segment has no origin:
      - Check incomplete_by_registration[registration]
      - If exists: combine with previous
      - Use origin from previous, destination from current
   
   b. Else if segment has no destination:
      - Store in incomplete_by_registration[registration]
   
   c. Else (complete segment):
      - Add to output list
4. Add remaining incomplete segments to output
```

**Combination Logic**:
```python
combined_points = prev_segment.points + current_segment.points
combined_segment = FlightSegment(
    registration=registration,
    points=combined_points,
    origin=prev_segment.origin,
    destination=current_segment.destination,
    style_color=current_segment.style_color,
    style_width=current_segment.style_width
)
```

## 5. Data Structures

### 5.1 Core Classes

#### Airport
```python
class Airport:
    code: str              # ICAO/IATA code
    name: str              # Full airport name
    lat: float             # Latitude (decimal degrees)
    lon: float             # Longitude (decimal degrees)
    elevation: float       # Elevation (feet MSL)
```

#### TrackPoint
```python
class TrackPoint:
    timestamp: str         # ISO 8601 format
    lon: float             # Longitude (decimal degrees)
    lat: float             # Latitude (decimal degrees)
    alt: float             # Altitude (feet)
    datetime: datetime     # Parsed datetime object
```

#### FlightSegment
```python
class FlightSegment:
    registration: str                # Aircraft tail number
    points: List[TrackPoint]         # Ordered track points
    origin: Optional[Airport]        # Departure airport
    destination: Optional[Airport]   # Arrival airport
    style_color: str                 # KML color (ABGR)
    style_width: str                 # Line width
    takeoff_time: datetime           # First point timestamp
    takeoff_str: str                 # Formatted for filename
```

### 5.2 Data Flow

```
Input KML → parse_kml_tracks() → Dict[registration, List[tracks]]
                                        ↓
                            detect_flight_segments() → List[List[TrackPoint]]
                                        ↓
                            create_flight_segments() → List[FlightSegment]
                                        ↓
                            combine_incomplete_routes() → List[FlightSegment]
                                        ↓
                            create_output_kml() → Output KML
```

## 6. Error Handling

### 6.1 Input Validation

| Error Condition | Detection | Handling | User Message |
|----------------|-----------|----------|--------------|
| File not found | os.path.exists() | Exit with code 1 | "Error: Input file not found: {path}" |
| Invalid XML | ET.parse() | Skip file, continue | "Warning: Invalid XML in {file}" |
| Missing required elements | Element search | Skip placemark | "Warning: Missing track in placemark" |
| Malformed coordinates | Float parsing | Skip point | "Warning: Invalid coordinate: {coord}" |
| When/coord mismatch | Length comparison | Skip track | "Warning: Mismatch in when/coord count" |

### 6.2 Processing Errors

| Error Condition | Detection | Handling | User Message |
|----------------|-----------|----------|--------------|
| No airports loaded | Length check | Exit with code 1 | "Error: No airports loaded!" |
| No flights detected | Segment count | Continue with warning | "Warning: No flights detected in {file}" |
| Airport match failure | None result | Use "UNKN" code | Logged to console |
| Memory exhaustion | Try/except | Exit gracefully | "Error: Insufficient memory" |

### 6.3 Output Errors

| Error Condition | Detection | Handling | User Message |
|----------------|-----------|----------|--------------|
| Write permission denied | IOError | Exit with code 1 | "Error: Cannot write to {path}" |
| Disk full | OSError | Exit with code 1 | "Error: Disk full" |
| Invalid output path | Path validation | Exit with code 1 | "Error: Invalid output path" |

## 7. Performance Considerations

### 7.1 Time Complexity

| Operation | Complexity | Notes |
|-----------|-----------|-------|
| KML Parsing | O(n) | n = number of points |
| Flight Detection | O(n) | Single pass through points |
| Airport Matching | O(m×k) | m = airports, k = flights |
| Route Combination | O(k log k) | Sorting flights |
| KML Generation | O(k) | k = flights |

**Overall**: O(n + m×k + k log k)

### 7.2 Space Complexity

| Component | Memory Usage | Notes |
|-----------|--------------|-------|
| Airport Database | ~50 MB | For 16,000 airports |
| Track Points | ~100 bytes/point | Including overhead |
| Flight Segments | ~1 KB/flight | Including metadata |
| Output Buffer | ~50% of input | Before writing to disk |

**Peak Memory**: `airports + 1.5 × input_size`

### 7.3 Optimization Opportunities

1. **Spatial Indexing**: Use R-tree for airport lookups → O(log m) instead of O(m)
2. **Streaming Processing**: Process tracks incrementally → Reduce memory
3. **Parallel Processing**: Process multiple files concurrently → 2-4x speedup
4. **Caching**: Cache distance calculations → Reduce redundant computation

## 8. Testing Strategy

### 8.1 Unit Tests (Planned)

| Module | Test Cases |
|--------|-----------|
| haversine_distance | Known distances, edge cases (poles, dateline) |
| load_airports | Valid/invalid formats, empty files |
| parse_kml_tracks | Various KML structures, missing elements |
| detect_flight_segments | Different altitude profiles, edge cases |
| find_nearest_airport | Various distances, no matches |

### 8.2 Integration Tests (Planned)

| Scenario | Expected Outcome |
|----------|------------------|
| Single file, single flight | One output route |
| Multi-day, same aircraft | Combined route |
| No altitude data | Distance-based detection |
| No airports matched | UNKN codes in output |
| Large file (100K points) | Completes in < 30s |

### 8.3 Acceptance Tests

| Scenario | Validation |
|----------|-----------|
| Copa Airlines daily ops | All flights detected |
| Volaris multi-day routes | Routes properly combined |
| Breeze Airways tracks | Correct airport matching |
| Ground operations only | Properly filtered out |

## 9. Future Enhancements

### 9.1 Version 7.0 Roadmap

- **GUI Interface**: Desktop application with drag-and-drop
- **Real-time Processing**: Process files as they're created
- **Export Formats**: GeoJSON, CSV, Shapefile
- **Web Viewer**: Interactive map visualization
- **Machine Learning**: Improved flight detection using trained models
- **Spatial Indexing**: R-tree for fast airport lookups
- **Statistics Dashboard**: Flight analytics and reporting

### 9.2 Proposed Features

- **Airport Confidence Score**: Probability-based matching
- **Route Deviation Detection**: Compare against planned routes
- **Turbulence Detection**: Identify altitude variations
- **Fuel Efficiency Metrics**: Calculate from track data
- **API Server**: RESTful API for integration
- **Cloud Processing**: AWS Lambda/Azure Functions deployment

## 10. Appendices

### 10.1 Glossary

- **ADS-B**: Automatic Dependent Surveillance-Broadcast
- **ICAO**: International Civil Aviation Organization code
- **IATA**: International Air Transport Association code
- **Haversine**: Formula for great circle distance
- **KML**: Keyhole Markup Language (Google Earth format)
- **MSL**: Mean Sea Level

### 10.2 References

- KML 2.2 Specification: https://www.ogc.org/standards/kml
- Haversine Formula: https://en.wikipedia.org/wiki/Haversine_formula
- ISO 8601 DateTime: https://en.wikipedia.org/wiki/ISO_8601
- WGS84 Coordinate System: https://en.wikipedia.org/wiki/World_Geodetic_System

### 10.3 Change History

| Version | Date | Changes |
|---------|------|---------|
| 6.0 | 2025-04-27 | Initial specification document |

---

**Document Status**: Living Document  
**Next Review**: 2025-05-27  
**Owner**: Aviation Data Processing Team
