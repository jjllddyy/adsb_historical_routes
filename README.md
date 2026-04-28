# Flight Route Splitter

[![Version](https://img.shields.io/badge/version-6.0-blue.svg)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-3.6+-green.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-orange.svg)](LICENSE)

A robust Python tool for processing aviation KML files containing continuous flight tracks. Automatically detects and splits individual flight segments, matches airports by proximity, and organizes output into a hierarchical folder structure.

## 🎯 Key Features

- **Intelligent Flight Detection**: Hybrid algorithm using altitude data or distance/time heuristics
- **Airport Matching**: Automatic origin/destination identification using haversine distance
- **Multi-Day Route Handling**: Combines incomplete flights spanning multiple files
- **Style Preservation**: Maintains line colors, widths, and transparency from source files
- **Hierarchical Organization**: Creates logical folder structure by origin airport and route
- **Production Ready**: Handles real-world edge cases (flights starting airborne, missing data, etc.)

## 📋 Table of Contents

- [Quick Start](#quick-start)
- [Installation](#installation)
- [Usage](#usage)
- [Input File Formats](#input-file-formats)
- [Output Structure](#output-structure)
- [Algorithm Details](#algorithm-details)
- [Configuration](#configuration)
- [Examples](#examples)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

## 🚀 Quick Start

```bash
# Single file
python adsb_historical_routes.py \
    --kml-files daily_tracks.kml \
    --airports input/latam_la_airports.csv \
    --routes input/latam_la_routes_time.csv \
    --output organized_routes.kml

# Multi-day, folder of per-aircraft-per-date files (e.g., ADS-B Exchange traces)
python adsb_historical_routes.py \
    --kml-folder kml_input/kml_downloads \
    --airports input/latam_la_airports.csv \
    --routes input/latam_la_routes_time.csv \
    --output lan_routes.kml \
    --group destination
```

## Confidence Presets

The `--confidence` flag controls how aggressively the script recovers flight routes from incomplete ADS-B data. Each preset is a bundle of detection/joining knobs:

| Knob | strict | balanced (default) | permissive |
|---|---|---|---|
| `airport_radius_km` | 20 | 50 | 100 |
| `max_join_gap_hours` | 2.0 | 3.0 | 4.0 |
| `max_join_distance_km` | 100 | 200 | 500 |
| `route_time_tolerance` | 0.25 | 0.40 | 0.60 |
| `route_time_rescue` | off | on | on |
| `direction_aware_rescue` | off | on | on |

- **strict** — Closest to v1.0 behavior. Highest confidence per route, lowest recovery rate.
- **balanced** — Default. Recovers most legitimate dropped routes; safe for downstream ML.
- **permissive** — Maximum recovery; emit-and-audit. Some routes may be wrong joins; review the diagnostics CSV.

Override individual knobs:

```bash
python adsb_historical_routes.py \
    --kml-folder kml_input/kml_downloads \
    --airports input/latam_la_airports.csv \
    --routes input/latam_la_routes_time.csv \
    --output lan_routes.kml \
    --confidence balanced \
    --airport-radius-km 75 \
    --route-time-rescue off
```

When any knob is overridden, logs and the diagnostics CSV mark the run as `custom` (the named preset is no longer truthful).

## Diagnostics CSV

Every run writes `<output>.diagnostics.csv` next to the output KML:

```
out.kml
out.kml.diagnostics.csv
```

One row per raw segment (`phase=raw`) and one per final outcome (`phase=final`). Useful columns:

- `disposition`: `kept_complete`, `kept_rescued`, `kept_joined`, `dropped_invalid`, `dropped_unjoined`
- `nearest_airport_*_km`: distance to nearest airport regardless of preset radius — answers "would a wider radius recover this?"
- `joined_with_segment_idx`: traceability for joined segments
- `rescue_method`: which logic path saved or merged this segment

Compare runs at different presets by diffing their CSVs.

## Same-Aircraft + Chronological Invariants

Cross-file segment joining strictly enforces:

1. Same aircraft (ICAO hex from filename).
2. `next.takeoff >= current.landing` (equal boundary timestamps are allowed — ADS-B Exchange placemark splits often share the boundary instant).
3. Time gap < `preset.max_join_gap_hours`.

Cross-aircraft joins and true chronological inversion (`next.takeoff < current.landing`) fail loudly with `AssertionError`.

## 💾 Installation

### Requirements

- Python 3.6 or higher
- No external dependencies (uses only Python standard library)

### Setup

```bash
# Clone or download the repository
git clone <repository-url>
cd flight-route-splitter

# Make the script executable (Unix/Linux/macOS)
chmod +x adsb_historical_routes.py

# Verify installation
python adsb_historical_routes.py --help
```

## 📖 Usage

### Command-Line Interface

```
usage: adsb_historical_routes.py [-h]
                                 (--kml-files KML_FILES [KML_FILES ...] | --kml-folder KML_FOLDER)
                                 --airports AIRPORTS --routes ROUTES --output
                                 OUTPUT [--sample SAMPLE] [--maxgap MAXGAP]
                                 [--group {origin,destination}]
                                 [--color COLOR] [--width WIDTH]
                                 [--opacity 0-100] [--labels {true,false}]
                                 [--icons {true,false}]
                                 [--ground {true,false}]

Enhanced Flight Route Splitter v6 - Improved segment joining and altitude handling

optional arguments:
  -h, --help            show this help message and exit
  --kml-files KML_FILES [KML_FILES ...]
                        Specific KML files to process
  --kml-folder KML_FOLDER
                        Folder containing KML files to process
  --airports AIRPORTS   CSV file containing airport data
  --routes ROUTES       CSV file containing route times
  --output OUTPUT       Output KML file name
  --sample SAMPLE       Point sampling interval in minutes (default: 2)
  --maxgap MAXGAP       Maximum gap time allowed in flight segment in minutes
                        (default: 20)
  --group {origin,destination}
                        Group flights by origin or destination airport
                        (default: destination)
  --color COLOR         Override line color (KML format, e.g., ff0000ff for
                        blue)
  --width WIDTH         Override line width (e.g., 2)
  --opacity 0-100       Override line opacity as percentage: 0-100 (default:
                        use original)
  --labels {true,false}
                        Show flight labels/names (default: false)
  --icons {true,false}  Show airport icons (default: false)
  --ground {true,false}
                        Extend flight paths to ground (default: false)
```

Run `python3 adsb_historical_routes.py --help` for the live synopsis.

### Python API

A `process_kml_files(...)` entry point is available in `adsb_historical_routes.py` for programmatic use. See the function signature in the source for the full parameter list (it mirrors the CLI flags above, plus pre-loaded `airports`/`routes` lists).

## 📁 Input File Formats

### KML File Structure

Expected structure with `gx:Track` elements:

```xml
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">
  <Document>
    <Folder>
      <name>2025-10-15</name>
      <Folder>
        <name>XA-ADC</name>
        <Placemark>
          <Style>
            <LineStyle>
              <color>7f0000FF</color>
              <width>0.5</width>
            </LineStyle>
          </Style>
          <gx:Track>
            <when>2025-10-15T04:35:50.490Z</when>
            <gx:coord>-99.083284 19.42846 2500</gx:coord>
            ...
          </gx:Track>
        </Placemark>
      </Folder>
    </Folder>
  </Document>
</kml>
```

### Airport Database Format

Comma-separated text file with airport records:

```
A,CODE,NAME,LATITUDE,LONGITUDE,ELEVATION,...
A,MMMX,MEXICO CITY INTL,19.436306,-99.072083,7316,18000,18000,4500,0
A,KLAX,LOS ANGELES INTL,33.942536,-118.408075,125,18000,18000,4500,0
```

**Note**: Only lines starting with `A,` are processed. Additional fields after elevation are ignored.

## 📂 Output Structure

The output KML creates a three-level hierarchy:

```
📁 MMMX (Origin Airport - Alphabetical)
  📁 MMMX-KLAX (Route - Alphabetical)
    ✈️ MMMX-KLAX 2025-10-21_1430 XA-ADC
    ✈️ MMMX-KLAX 2025-10-22_0945 XA-ADC
  📁 MMMX-KJFK
    ✈️ MMMX-KJFK 2025-10-20_1015 XA-ADC
📁 KLAX (Origin Airport)
  📁 KLAX-MMMX
    ✈️ KLAX-MMMX 2025-10-23_1600 XA-ADC
```

### Naming Convention

Flight names follow the format: `{ORIGIN}-{DESTINATION} {DATE}_{TIME} {REGISTRATION}`

- **ORIGIN/DESTINATION**: ICAO or IATA airport codes (or "UNKN" if not matched)
- **DATE**: YYYY-MM-DD format
- **TIME**: HHMM (24-hour format, UTC)
- **REGISTRATION**: Aircraft tail number (e.g., XA-ADC)

## 🔬 Algorithm Details

### Flight Detection Strategy

The system uses a **hybrid detection algorithm**:

#### 1. Altitude-Based Detection (Primary)

Used when altitude data is available (max altitude > 500ft):

- **Takeoff Detection**: Altitude exceeds 500ft threshold
- **Cruise Verification**: Must reach 3000ft to be considered a flight
- **Landing Detection**: Altitude drops below 500ft and stays low
- **Edge Cases**: 
  - Handles flights starting already airborne
  - Handles flights ending still in the air
  - Includes ground taxi points before/after flight

#### 2. Distance-Based Detection (Fallback)

Used when altitude data is missing or all near zero:

- **Segmentation**: Splits on time gaps > 1 hour
- **Validation**: Segment must travel > 10km to be considered a flight
- **Minimum Points**: Requires at least 10 track points

### Airport Matching

- **Algorithm**: Haversine formula for great circle distance
- **Search Radius**: 50km from takeoff/landing point
- **Precision**: Nearest airport within radius is selected
- **Fallback**: "UNKN" code if no airport found

### Multi-Day Route Combination

For incomplete routes spanning multiple files:

1. Sort all segments by takeoff time
2. Identify incomplete segments (missing origin or destination)
3. Match by aircraft registration
4. Combine points and use known airports
5. Preserve styling from continuing segment

## ⚙️ Configuration

### Adjustable Parameters

Edit these values in the source code for fine-tuning:

```python
# Detection thresholds
altitude_threshold = 500.0       # ft - ground/airborne boundary
min_flight_altitude = 3000.0     # ft - minimum cruise altitude
time_gap_threshold = 3600.0      # seconds - flight separation
distance_threshold = 10.0        # km - minimum flight distance

# Airport matching
max_distance_km = 50.0           # km - airport search radius
```

### Custom Date Format

To change the flight naming date format:

```python
# In FlightSegment.__init__()
self.takeoff_str = points[0].datetime.strftime('%Y-%m-%d_%H%M')
# Example alternatives:
# '%Y%m%d_%H%M'  → 20251021_1430
# '%d%b%y_%H%M'  → 21Oct25_1430
```

## 💡 Examples

### Example 1: Airline Daily Operations

Process a full day of operations for multiple aircraft:

```bash
python adsb_historical_routes.py \
    --kml-files copa_20251021.kml \
    --airports input/latam_la_airports.csv \
    --routes input/latam_la_routes_time.csv \
    --output copa_organized_20251021.kml
```

### Example 2: Multi-Day Route Reconstruction

Combine flights across multiple days:

```bash
python adsb_historical_routes.py \
    --kml-files flights_oct15.kml flights_oct16.kml flights_oct17.kml \
    --airports input/latam_la_airports.csv \
    --routes input/latam_la_routes_time.csv \
    --output october_routes.kml
```

### Example 3: Batch Processing

Process multiple airlines:

```bash
#!/bin/bash
for airline in copa volaris breeze; do
    python adsb_historical_routes.py \
        --kml-files ${airline}_*.kml \
        --airports input/latam_la_airports.csv \
        --routes input/latam_la_routes_time.csv \
        --output ${airline}_organized.kml \
        --group destination
done
```

## 🔧 Troubleshooting

### No Flights Detected

**Symptoms**: Script runs but reports 0 flight segments

**Common Causes**:
1. **Missing altitude data**: Check that `gx:coord` elements include altitude (3rd value)
2. **Insufficient track length**: Tracks need > 10 points minimum
3. **Short distances**: For ground-level data, flights must travel > 10km
4. **Time continuity**: Large time gaps might split flights unexpectedly

**Solutions**:
```python
# Adjust detection thresholds in source code
altitude_threshold = 200.0    # Lower for general aviation
min_flight_altitude = 1000.0  # Lower for short flights
distance_threshold = 5.0      # Accept shorter flights
```

### Airport Matching Failures

**Symptoms**: Many routes show "UNKN-UNKN" or "UNKN-DEST"

**Common Causes**:
1. **Limited airport database**: Database doesn't cover flight region
2. **Search radius too small**: Airports > 50km from track endpoints
3. **Coordinate format issues**: Lat/lon reversed or incorrect format

**Solutions**:
1. Expand airport database to include relevant regions
2. Increase `max_distance_km` parameter
3. Verify coordinate order in airport database (lat, lon)

### Registration Not Detected

**Symptoms**: Aircraft shows as "UNKNOWN"

**Common Causes**:
1. **Folder structure**: Registration not in proper folder hierarchy
2. **Naming convention**: Folder name doesn't match expected format

**Solutions**:
- Ensure folder structure: `Document > Date > Registration > Placemark`
- Check that registration folder immediately contains `Placemark` elements

### Memory Issues

**Symptoms**: Script crashes or slows with large files

**Solutions**:
1. Process files individually instead of batching
2. Split large multi-day files into smaller chunks
3. Use 64-bit Python for > 4GB file processing

## 📊 Performance

### Benchmarks

- **Small dataset** (< 1000 points): < 1 second
- **Medium dataset** (< 10,000 points): < 5 seconds  
- **Large dataset** (< 100,000 points): < 30 seconds

### Optimization Tips

1. **Airport database**: Pre-filter by geographic region if possible
2. **Batch processing**: Process files sequentially rather than all at once
3. **SSD storage**: Improves read/write performance for large files

## 🤝 Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### Development Setup

```bash
# Clone repository
git clone <repository-url>
cd flight-route-splitter

# Create development branch
git checkout -b feature/your-feature

# Make changes and test
python adsb_historical_routes.py \
    --kml-files test_data/*.kml \
    --airports input/latam_la_airports.csv \
    --routes input/latam_la_routes_time.csv \
    --output output.kml

# Submit pull request
```

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Aviation community for KML format standards
- OpenFlights.org for airport database inspiration
- Contributors and testers

## 📞 Support

- **Documentation**: See [docs/](docs/) folder for detailed guides
- **Issues**: Report bugs via GitHub Issues
- **Questions**: Check [docs/troubleshooting.md](docs/troubleshooting.md)

## 🗺️ Roadmap

### Version 6.x
- ✅ Hybrid flight detection algorithm
- ✅ Multi-day route combination
- ✅ Style preservation

### Version 7.0 (Planned)
- [ ] GUI interface
- [ ] Real-time progress indicators
- [ ] Export to multiple formats (GeoJSON, CSV)
- [ ] Interactive web viewer
- [ ] Spatial indexing for faster airport lookups
- [ ] Machine learning for improved flight detection

---

**Version**: 6.0  
**Last Updated**: 2025-04-27  
**Maintainer**: Aviation Data Processing Team
