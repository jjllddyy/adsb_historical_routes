# Changelog

All notable changes to the Flight Route Splitter project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] - 2026-10-28 (gpkg-output branch)

### Added

- **GeoPackage output** (`flown_routes` schema v1, see [`docs/GEOPACKAGE.md`](docs/GEOPACKAGE.md)): `flights` (one row per flight, sampled display line), `tracks_full` (every point, M = epoch seconds), optional `track_points` (per-point source, ground speed, vertical speed, track), `diagnostics` and `meta`. Written with the new standard-library `gpkg_writer.py` (no GDAL); validated with GDAL's `validate_gpkg.py`, `ogrinfo` and QGIS 3.44.
- **Output selection** `--format both|gpkg|kml` (default `both`), `--gpkg-points`, `--airline`, `--suspect-od-ratio`; `--gpkg` is a deprecated alias for `--format both`.
- **`gpkg_to_kml.py`**: rebuild the KML from a GeoPackage without re-running the builder; every styling/sampling option defaults to the build's.
- **`kml_compare.py`**: the spec §6 KML equivalence check (folder tree, placemarks, descriptions, styles, coordinates to 1e-6, `when` to 1 ms), streaming, for multi-hundred-MB files.
- `{from}` / `{to}` in `--output` are replaced by the first/last flight dates.
- Origin/destination plausibility check: `od_gc_km`, `od_distance_ratio`, `suspect_od` on each flight, counted in the diagnostics and the run summary.
- Diagnostics CSV gains `flight_id`, `od_gc_km`, `od_distance_ratio`, `suspect_od`.
- Route Builder GUI: Output format, Airline code and GeoPackage track points in the Output section; result card shows suspect O/D count, flight dates and the GeoPackage.

### Changed

- KML is now rendered from the GeoPackage (a temporary one with `--format kml`). Output is byte-identical to the direct writer in every mode tested; the direct writer remains behind `--legacy-kml` for one release.

### Fixed

- **Origin/destination mislabels** (about 2.7% of VIV October flights flew > 2.5× the great circle between their labelled airports, e.g. 131 "MMTO→MMSM" flights of 2½–10 h). Four root causes in airport assignment:
  - A farther airport could "steal" an endpoint: a climbing departure first seen 16 km from MMSM failed MMSM's altitude allowance but passed the larger, elevation-scaled one of MMTO 89 km away. The closest airport now wins when the height above it fits a 3° climb/descent path.
  - Route-time rescue accepted a destination the track had already flown past (corridor cross-track has no along-track limit); it now applies the same along-track limit as corridor geometry.
  - Fragments of an out-and-back across a coverage hole (MMSM→MMVA→MMSM with no coverage at MMVA) were joined into one "flight"; a reversed heading with no progress across the gap now blocks the join.
  - A destination inferred for a short fragment survived a join that extended the track past it; it is now dropped and re-inferred on the merged track.

## [1.2.0] - 2026-09-22 (route-builder branch)

### Added

- **Route Builder GUI** (`route_builder/`): a stdlib-only local web app that drives `adsb_historical_routes.py` from a browser form — folder/file pickers, built-in and user presets, a pre-flight card showing the exact command line, live SSE progress + ETA + console, and a graphical pass/fail result. Launch with `python3 route_builder/server.py --open` or the double-click `Route Builder.command`; binds `127.0.0.1` only. See [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) and `route_builder/EXTENSION_SPEC.md`.
- **Integrated Cirium input builder** in the GUI (Inputs → *Build from Cirium…*): generate the `*_airports.csv` / `*_routes_time.csv` without a terminal; on `READY` it auto-fills the Airports/Routes fields. Optional — if the files exist you just load them.
- **Position-source integration** (`--integrate-pos`): fill in-flight ADS-B coverage gaps with real ACARS position reports and/or ADS-B firehose fixes, color-marked by source (`--pos-color` / `--firehose-color`). Flight detection is unchanged; the extra sources only densify flights already found.
- **Altitude color mode** (`--color-mode altitude`, `--altitude-palette adsbex`, `--altitude-bands`): colors lines by the ADS-B Exchange altitude gradient (orange at ground → magenta at 40 000  ft+).
- **Gap stitching** (`--stitch-gaps`, `--inferred-color`, `--stitch-gap-min`): draw straight-line connectors across coverage gaps in a distinct colour to mark inferred data.
- **Corridor cross-track gate** (`--corridor-cross-track-km`): the main recovery/noise knob for endpoint rescue.
- **Airport override manifest** (`airport_overrides.csv`, `--airport-override`) for `build_cirium_inputs.py`.
- **User Guide** (`docs/USER_GUIDE.md`) covering the GUI, every setting, worked examples, and the two-file rule.

### Changed

- The GUI's Airports/Routes/Tail file pickers accept `.csv` **and** `.txt`, and their field help now points at the generated `*_airports.csv` / `*_routes_time.csv` (not the OurAirports `Airports_*.txt` DB or the raw Cirium export).
- README documents the GUI, the Cirium build option, and the altitude/position/stitching flags.

### Fixed

- Run console now batches and caps output (last 500 lines) so large batches no longer freeze the browser tab.
- Path confinement (realpath containment in `allowed_roots`) applied to every GUI filesystem endpoint, including `kml_files`, the output path, and the Cirium build's inputs.

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
