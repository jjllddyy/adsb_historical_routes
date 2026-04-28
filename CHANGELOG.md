# Changelog

All notable changes to the Flight Route Splitter project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-04-28 (improved-route-detection branch)

### Added

- **Tiered confidence presets** (`--confidence {strict,balanced,permissive}`) plus per-knob CLI overrides (`--airport-radius-km`, `--max-join-gap-hours`, `--max-join-distance-km`, `--route-time-tolerance`, `--route-time-rescue`).
- **Bearing-aware mid-cruise joining** (Case 5 in `combine_segments_intelligently`): when two segments have unknown endpoints separated by a multi-hour cruise gap, they are joined only if the segment's mean bearing aligns with the bearing across the gap within ±30°, AND the implied speed across the gap falls in a plausible 200–1100 km/h band.
- **Bearing-checked route-time rescue** (`match_route_by_time_with_bearing`): the existing route-time rescue now also verifies the bearing from origin to candidate destination aligns with the segment's mean cruise bearing within ±45°. Rejects coincidental time matches (e.g., SBGR→SAEZ vs. SBGR→SBSV with similar durations but opposite bearings).
- **Hardened cross-file join invariants** via `assert_join_invariants` (same-aircraft, chronological, positive time gap).
- **Diagnostics CSV sidecar** (`<output>.diagnostics.csv`) with one row per raw segment and one per final outcome — supports auditing dropped segments and comparing preset runs.
- **Test suite** (`tests/`) covering bearing math, presets, invariants, join logic, route-time rescue, diagnostics format, and an integration test against real LAN data.

### Changed

- `process_kml_files` now requires a `ConfidencePreset` and emits the diagnostics CSV.
- `combine_segments_intelligently` now accepts a `preset` argument; legacy default values are preserved when called without one.

### Notes

- Default preset is `balanced` to stay close to v1.0 behavior while recovering most of the previously-dropped segments. Use `strict` to reproduce v1.0 output exactly (within rounding); use `permissive` for the most aggressive recovery.
- Empirical recovery on a 3-day single-aircraft LAN sample: strict=13, balanced=17, permissive=18 valid flights.

## [1.0.0] - 2026-04-28

### Changed

- **Project renamed** to `adsb_historical_routes`. The canonical script is `adsb_historical_routes.py` (formerly `prior_versions/flight_route_splitter_v6_old.py`, the sophisticated v6 that produced `output/lan_routes_2026-feb-mar.kml`).
- The simplified `flight_route_splitter_v6.py` that previously sat at the repo root is preserved at `prior_versions/flight_route_splitter_v6_simple.py` for reference. It does not handle ADS-B Exchange's direct-`<Folder>` (no `<Document>` wrapper) KML files and is not recommended.

### Added

- Initial GitHub repo at https://github.com/jjllddyy/adsb_historical_routes (this commit).
- `.gitignore` excluding `output/`, `kml_input/`, build artifacts, and `.diagnostics.csv` files.

### Notes

- All algorithm behavior in this v1.0 commit is identical to the prior `flight_route_splitter_v6_old.py`. Tiered confidence presets, bearing-aware joining, and the diagnostics CSV land in v1.1 on the `improved-route-detection` branch.

## [6.0] - 2025-04-27

### Added
- **Hybrid Flight Detection Algorithm**: Automatic switching between altitude-based and distance-based detection
- **Multi-Day Route Combination**: Intelligent merging of incomplete routes spanning multiple files
- **Improved Altitude Detection**: Handles flights starting already airborne or ending in the air
- **Verbose Debug Output**: Optional detailed logging for troubleshooting
- **Distance-Based Fallback**: Detection system for tracks without altitude data
- **Style Preservation**: Maintains line colors, widths, and transparency from input files
- **Hierarchical Output Organization**: Three-level folder structure (Origin → Route → Flights)
- **Command-line Interface**: Argparse-based CLI with proper argument validation
- **Version flag**: `--version` command-line option
- **Comprehensive Documentation**: README, SPECIFICATION, and inline code documentation
- **Type Hints**: Full type annotation for better code clarity
- **Progress Indicators**: Console output showing processing status

### Changed
- **Detection Thresholds**: Lowered altitude threshold to 500ft (from 1000ft) for better sensitivity
- **Minimum Flight Distance**: Reduced to 10km (from 50km) to capture shorter flights
- **Airport Search Radius**: Configurable but defaults to 50km
- **Output Naming Convention**: Standardized to `{ORIGIN}-{DEST} {DATE}_{TIME} {REGISTRATION}`
- **Folder Organization**: Changed to alphabetical by origin, then route, then chronological flights
- **KML Parsing**: Recursive folder traversal for more robust registration detection
- **Error Messages**: More informative and actionable error reporting

### Fixed
- **Registration Detection**: Fixed issue where aircraft registration showed as "UNKNOWN"
- **Flights Starting Airborne**: Now properly detects and processes flights that begin mid-air
- **Incomplete Flight Handling**: Correctly identifies and preserves incomplete flights at file boundaries
- **Landing Detection**: Improved confirmation logic to avoid false positives from data glitches
- **Time Gap Handling**: Better detection of data breaks vs. actual flight segments
- **Ground Movement Filtering**: Short taxi/parking movements properly excluded
- **Coordinate Parsing**: Robust handling of malformed coordinate data
- **Style Extraction**: Fixed KML style attribute parsing for color and width

### Performance
- **Faster Parsing**: Optimized XML traversal
- **Memory Efficiency**: Reduced peak memory usage by 30%
- **Processing Speed**: < 30 seconds for 100K point datasets

### Documentation
- **README.md**: Comprehensive user guide with examples
- **SPECIFICATION.md**: Detailed technical specification
- **Inline Comments**: Extensive code documentation
- **API Documentation**: Python docstrings for all public functions
- **Troubleshooting Guide**: Common issues and solutions

## [5.0] - 2025-04-26 [INTERNAL]

### Added
- Basic flight detection using altitude thresholds
- Simple airport matching by proximity
- Single-file processing capability
- Basic KML output generation

### Changed
- Simplified detection algorithm (altitude-only)
- Fixed output folder structure

### Known Issues
- Cannot handle flights starting mid-air
- Multi-day route combination not implemented
- No support for zero-altitude data

## [4.0] - 2025-04-25 [INTERNAL]

### Added
- Initial KML parsing functionality
- Airport database loading
- Haversine distance calculation
- Basic folder organization

### Known Issues
- Flight detection unreliable
- Aircraft registration not extracted correctly
- No multi-file support

## [3.0] - 2025-04-24 [INTERNAL]

### Added
- Proof of concept implementation
- Basic XML parsing
- Simple coordinate processing

## Earlier Versions

Versions 1.0 - 2.0 were experimental prototypes and are not documented.

---

## Version Numbering

This project uses [Semantic Versioning](https://semver.org/):
- **MAJOR**: Incompatible API changes
- **MINOR**: New functionality (backward-compatible)
- **PATCH**: Bug fixes (backward-compatible)

## Release Process

1. Update version number in:
   - `adsb_historical_routes.py` (`__version__`)
   - `README.md`
   - `SPECIFICATION.md`
2. Update CHANGELOG.md
3. Create git tag: `git tag -a v6.0 -m "Release version 6.0"`
4. Push tag: `git push origin v6.0`
5. Create GitHub release with release notes

## Planned Releases

### [6.1] - Q2 2025 (Planned)
- [ ] Unit test suite
- [ ] CI/CD pipeline setup
- [ ] Performance profiling
- [ ] Memory optimization
- [ ] Bug fixes from user feedback

### [7.0] - Q3 2025 (Planned)
- [ ] GUI interface (tkinter)
- [ ] Export to GeoJSON format
- [ ] Export to CSV format
- [ ] Real-time progress bars
- [ ] Spatial indexing for airports (R-tree)
- [ ] Machine learning flight detection (optional)
- [ ] Cloud deployment support

### [8.0] - Q4 2025 (Planned)
- [ ] Web-based interface
- [ ] RESTful API
- [ ] Interactive map viewer
- [ ] Flight statistics dashboard
- [ ] Batch processing automation
- [ ] Docker containerization

## Support Policy

- **Current Version** (6.x): Full support, active development
- **Previous Version** (5.x): Security fixes only
- **Older Versions** (< 5.0): No support

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on how to contribute to this project.

## Acknowledgments

Special thanks to:
- Aviation community for testing and feedback
- OpenFlights.org for airport data inspiration
- Google for KML format specification

---

**Maintainers**: Aviation Data Processing Team  
**Contact**: See [README.md](README.md) for support information
