# Known Issues and Limitations

**Project**: Flight Route Splitter  
**Version**: 6.0  
**Last Updated**: 2025-04-27

This document tracks known bugs, limitations, and planned fixes.

## Critical Issues

None currently identified.

## High Priority Issues

### ISSUE-001: No Spatial Indexing for Airport Lookup

**Severity**: Performance  
**Impact**: O(n) search for each flight endpoint  
**Affects**: Large airport databases (> 10,000 airports)

**Description**:
Airport matching performs linear search through all airports for each flight's origin and destination. With 16,000+ airports, this means 16,000 distance calculations per flight.

**Workaround**:
- Pre-filter airport database to relevant geographic region
- Performance acceptable for typical use (< 100 flights)

**Planned Fix**:
Implement R-tree spatial indexing in v7.0:
```python
from rtree import index
# Create spatial index
idx = index.Index()
for i, airport in enumerate(airports):
    idx.insert(i, (airport.lon, airport.lat, airport.lon, airport.lat))
```

**Effort**: Medium (requires external dependency)

---

### ISSUE-002: Memory-Resident Processing

**Severity**: Scalability  
**Impact**: Cannot process extremely large files (> 1M points)  
**Affects**: Full-day operations for large fleets

**Description**:
All track points loaded into memory simultaneously. For very large datasets (100+ aircraft, 24 hours of data), memory usage can exceed available RAM.

**Workaround**:
- Process files individually
- Split large files into smaller chunks
- Use 64-bit Python

**Planned Fix**:
Implement streaming processing in v7.0:
- Process one aircraft at a time
- Write incrementally to output
- Reduce peak memory by 80%

**Effort**: High (significant refactoring)

---

## Medium Priority Issues

### ISSUE-003: No Progress Indicators

**Severity**: Usability  
**Impact**: User uncertainty during long processing  
**Affects**: Files with > 50K points (> 10 second processing)

**Description**:
Console output shows results only after completion. No indication of progress during processing.

**Current Output**:
```
Processing: large_file.kml
Found 50 aircraft registrations
[10 seconds of silence]
Detected 200 flight segments
```

**Desired Output**:
```
Processing: large_file.kml
Found 50 aircraft registrations
Processing XA-ADC... [==========          ] 50% (25/50)
```

**Workaround**:
None - users must wait patiently.

**Planned Fix**:
Add progress bar in v6.1 using built-in itertools:
```python
for i, registration in enumerate(tracks_by_registration):
    progress = (i+1) / len(tracks_by_registration) * 100
    print(f"\rProcessing: {progress:.0f}%", end='', flush=True)
```

**Effort**: Low

---

### ISSUE-004: Single Export Format

**Severity**: Feature Gap  
**Impact**: Limited integration options  
**Affects**: Users needing CSV, GeoJSON, or database export

**Description**:
Only exports to KML format. Many downstream tools prefer:
- CSV for spreadsheet analysis
- GeoJSON for web mapping
- Shapefile for GIS tools

**Workaround**:
Use external conversion tools:
- ogr2ogr (GDAL)
- geojson.io
- Custom Python scripts

**Planned Fix**:
Add export formats in v7.0:
```bash
python adsb_historical_routes.py --format geojson ...
python adsb_historical_routes.py --format csv ...
```

**Effort**: Medium

---

### ISSUE-005: No Unit Tests

**Severity**: Quality Assurance  
**Impact**: Difficult to verify correctness after changes  
**Affects**: Development confidence, refactoring safety

**Description**:
No automated test suite. Testing currently manual using sample files.

**Workaround**:
Manual testing with known-good input/output pairs.

**Planned Fix**:
Add pytest-based test suite in v6.1:
- Unit tests for each function
- Integration tests for full pipeline
- Regression tests for bug fixes
- Performance benchmarks

**Effort**: Medium

---

## Low Priority Issues

### ISSUE-006: No GUI Interface

**Severity**: Usability (for non-technical users)  
**Impact**: Requires command-line familiarity  
**Affects**: Non-developer users

**Description**:
Command-line interface only. Some users prefer graphical interface with drag-and-drop.

**Workaround**:
Create wrapper scripts or batch files.

**Planned Fix**:
Desktop GUI in v7.0 using tkinter:
- Drag-and-drop file selection
- Visual parameter adjustment
- Progress visualization
- Output preview

**Effort**: High

---

### ISSUE-007: Fixed Threshold Parameters

**Severity**: Flexibility  
**Impact**: Some use cases need different thresholds  
**Affects**: General aviation, cargo operations, unusual aircraft

**Description**:
Detection thresholds hardcoded:
- Altitude threshold: 500 ft
- Minimum cruise: 3000 ft
- Time gap: 3600 seconds
- Distance: 10 km

**Workaround**:
Edit source code to change values.

**Planned Fix**:
Add command-line parameters in v6.1:
```bash
python adsb_historical_routes.py \
    --altitude-threshold 1000 \
    --cruise-altitude 5000 \
    --time-gap 1800 \
    --min-distance 20 \
    ...
```

**Effort**: Low

---

### ISSUE-008: No Validation Mode

**Severity**: Quality Assurance  
**Impact**: Cannot verify output correctness programmatically  
**Affects**: Automated workflows, CI/CD pipelines

**Description**:
No built-in validation to verify:
- All input points included in output
- No duplicate flights
- Airport matches reasonable
- Style preservation correct

**Workaround**:
Manual inspection in Google Earth.

**Planned Fix**:
Add `--validate` flag in v6.1:
```bash
python adsb_historical_routes.py --validate input.kml output.kml
# Reports:
# - Point count match
# - Duplicate detection
# - Airport match statistics
# - Style verification
```

**Effort**: Low

---

### ISSUE-009: Inefficient XML Writing

**Severity**: Performance  
**Impact**: Slow output generation for large result sets  
**Affects**: > 500 flight segments

**Description**:
Uses ElementTree.indent() which can be slow for large trees. Output generation takes ~30% of total processing time for large files.

**Workaround**:
None - output is still generated, just slower.

**Planned Fix**:
Use streaming XML writer in v7.0:
```python
from xml.etree.ElementTree import XMLGenerator
# Stream output directly to file
```

**Effort**: Medium

---

## Limitations (By Design)

### LIM-001: Python 3.6+ Required

**Description**: Does not support Python 2.x or Python 3.5 and earlier.

**Rationale**: 
- Type hints (3.5+)
- f-strings (3.6+)
- Ordered dicts (3.7+, but using 3.6 for compatibility)

**Impact**: Minimal - Python 3.6 is widely deployed.

---

### LIM-002: KML Format Only for Input

**Description**: Only accepts KML with gx:Track elements.

**Rationale**: 
- Project scope focused on KML
- Other formats (GPX, CSV) have different use cases

**Workaround**: Convert to KML first using external tools.

**Future**: May add GPX support in v8.0.

---

### LIM-003: No Real-Time Processing

**Description**: Batch processing only, not streaming/real-time.

**Rationale**: 
- Multi-day route combination requires seeing all data
- Batch processing simpler and sufficient for use case

**Workaround**: Process files as they become available.

**Future**: Real-time mode possible in v8.0 for single-day processing.

---

### LIM-004: Simple Airport Matching

**Description**: Nearest-neighbor only, no machine learning or advanced matching.

**Rationale**: 
- Haversine distance sufficient for 95%+ cases
- ML would add complexity and dependencies
- Manual review handles edge cases

**Workaround**: Manual review of "UNKN" matches.

**Future**: ML-based matching possible in v8.0 (optional).

---

### LIM-005: No Turbulence Detection

**Description**: Does not analyze altitude variations for turbulence.

**Rationale**: 
- Out of scope for route splitting
- Requires different analysis techniques
- Separate tools more appropriate

**Workaround**: Use dedicated turbulence detection tools.

**Future**: May add in v8.0 as optional feature.

---

## Edge Cases

### EDGE-001: Parallel Runways

**Scenario**: Airport with parallel runways > 50km apart

**Behavior**: May match wrong runway/airport

**Frequency**: Rare (few airports have 50km+ parallel runways)

**Mitigation**: Manual review, adjust search radius

---

### EDGE-002: Ferry Flights

**Scenario**: Empty aircraft repositioning, may have unusual flight profile

**Behavior**: May be detected incorrectly or not at all

**Frequency**: ~5% of flights in some datasets

**Mitigation**: Manual review, adjust thresholds

---

### EDGE-003: Touch-and-Go

**Scenario**: Training flights with multiple touchdowns without stopping

**Behavior**: May split into multiple segments or miss some touchdowns

**Frequency**: Rare in commercial operations

**Mitigation**: Manual review, specialized handling in future version

---

### EDGE-004: International Date Line

**Scenario**: Flights crossing longitude ±180°

**Behavior**: Distance calculations handle correctly (haversine formula)

**Frequency**: Common for Pacific routes

**Mitigation**: None needed - working as designed

---

### EDGE-005: Data Gaps Mid-Flight

**Scenario**: Signal loss or coverage gap during cruise

**Behavior**: May split single flight if gap > 1 hour

**Frequency**: Occasional for oceanic flights

**Mitigation**: Reduce time-gap threshold for oceanic routes

---

## Bug Reporting Template

When reporting issues, please include:

```markdown
**Title**: Brief description

**Version**: 6.0

**Environment**:
- OS: [Windows 10 / Ubuntu 22.04 / macOS 13]
- Python: [3.9.5]

**Input**:
- File size: [500 KB]
- Number of tracks: [50]
- Time span: [24 hours]

**Command**:
```bash
python adsb_historical_routes.py -i input.kml -a airports.txt -o output.kml
```

**Expected**:
[Describe expected behavior]

**Actual**:
[Describe actual behavior]

**Console Output**:
```
[Paste full console output]
```

**Additional Info**:
[Screenshots, sample files, etc.]
```

---

**Document Status**: Living Document  
**Review Frequency**: Each release  
**Last Reviewed**: 2025-04-27
