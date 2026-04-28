# Flight Route Splitter - Claude Context

**Project**: Flight Route Splitter  
**Version**: 6.0  
**Status**: Active Development  
**Last Updated**: 2025-04-27

## Project Overview

This is a Python tool for processing aviation KML flight tracking data. It automatically segments continuous flight tracks into individual routes, matches airports, and organizes output hierarchically.

## Primary Use Case

Process ADS-B flight tracking data from multiple airlines (Copa, Volaris, Breeze, Allegiant, etc.) to:
1. Extract individual flight routes from continuous daily tracks
2. Match origin/destination airports using proximity
3. Combine multi-day routes for the same aircraft
4. Organize output for analysis and visualization

## Project Context

### User Profile
- **Name**: John
- **Role**: Flight Optimization Engineering team
- **Focus**: ML models for route adherence prediction
- **Data Sources**: ADS-B, OFPs (Operational Flight Plans), KML traces
- **Infrastructure**: AWS S3, Athena, Python processing pipelines
- **Airlines**: Copa, Allegiant, Volaris, Breeze, and others

### Background Work
John has extensive experience with:
- KML processing and visualization (Google Earth)
- AWS S3 data pipelines
- Flight data aggregation
- Route adherence ML models
- Aviation data formats (ARINC 633, etc.)

### Project Genesis
This project emerged from John's need to process ADS-B tracking data that comes as continuous daily tracks without clear flight segment boundaries. The tool needed to:
- Handle missing or zero altitude data
- Work with flights that start/end mid-air (data boundaries)
- Combine routes across multiple days
- Preserve styling for visualization
- Match airports reliably

## Technical Architecture

### Core Components

1. **KML Parser** (`parse_kml_tracks`)
   - Recursive folder traversal
   - Extracts registration from folder hierarchy
   - Preserves styling information
   - Handles malformed data gracefully

2. **Flight Detection** (`detect_flight_segments`)
   - Hybrid algorithm: altitude-based (primary) or distance-based (fallback)
   - Handles edge cases (starting airborne, ending airborne)
   - Configurable thresholds

3. **Airport Matcher** (`find_nearest_airport`)
   - Haversine distance calculation
   - 50km search radius (configurable)
   - Fallback to "UNKN" if no match

4. **Route Combiner** (`combine_incomplete_routes`)
   - Matches by aircraft registration
   - Temporal ordering
   - Preserves known endpoints

5. **KML Generator** (`create_output_kml`)
   - Three-level hierarchy
   - Alphabetical organization
   - Style preservation

### Key Algorithms

**Haversine Distance**: Great circle distance for airport matching
**Altitude Detection**: Threshold-based flight state detection
**Distance Detection**: Fallback for ground-level tracking
**Temporal Segmentation**: Time gap analysis for flight boundaries

## Development Guidelines

### Code Style
- PEP 8 compliant
- Type hints throughout
- Comprehensive docstrings
- Clear variable names
- Minimal dependencies (standard library only)

### Error Handling
- Graceful degradation
- Informative error messages
- No silent failures
- Log warnings for data quality issues

### Performance Targets
- < 1s for small datasets (< 1K points)
- < 5s for medium datasets (< 10K points)
- < 30s for large datasets (< 100K points)
- < 500 MB memory for typical workloads

## Common Workflows

### Single File Processing
```bash
python adsb_historical_routes.py \
    -i daily_tracks.kml \
    -a airports.txt \
    -o organized_routes.kml
```

### Multi-Day Processing
```bash
python adsb_historical_routes.py \
    -i day1.kml day2.kml day3.kml \
    -a airports.txt \
    -o combined_routes.kml
```

### Batch Airline Processing
```bash
for airline in copa volaris breeze; do
    python adsb_historical_routes.py \
        -i ${airline}_*.kml \
        -a airports.txt \
        -o ${airline}_organized.kml
done
```

## Known Issues & Limitations

See [.claude/known_issues.md](.claude/known_issues.md) for detailed list.

### Current Limitations
1. No spatial indexing (O(n) airport lookups)
2. Sequential processing only (no parallelization)
3. Memory-resident processing (all data in RAM)
4. No GUI interface
5. Limited export formats (KML only)

### Future Enhancements
- R-tree spatial indexing for faster airport matching
- Parallel file processing
- Streaming/incremental processing
- GUI interface (tkinter)
- Export to GeoJSON, CSV, Shapefile
- Web-based viewer

## Testing Strategy

### Unit Tests (Planned)
- Haversine distance calculation
- Airport loading and parsing
- KML structure parsing
- Flight detection edge cases
- Route combination logic

### Integration Tests
- Full pipeline on real-world data
- Multi-airline datasets
- Various data quality scenarios
- Performance benchmarks

### Test Data
Located in `tests/test_data/`:
- Sample KML files (various airlines)
- Airport database subset
- Expected output files
- Edge case scenarios

## Dependencies

**Runtime**: None (Python 3.6+ standard library only)

**Development** (optional):
- pytest (testing)
- black (formatting)
- mypy (type checking)
- flake8/pylint (linting)

**Rationale**: Zero dependencies for maximum portability and ease of deployment in restricted environments.

## Project History

This project evolved through several iterations:

1. **v1-2**: Experimental prototypes (undocumented)
2. **v3**: Proof of concept - basic XML parsing
3. **v4**: Initial KML parsing and airport loading
4. **v5**: Basic altitude-based flight detection
5. **v6**: Hybrid detection, multi-day combination, production-ready

Key breakthrough: Implementing hybrid detection algorithm to handle both altitude-rich and altitude-poor data.

## Integration Points

### Upstream Data Sources
- ADS-B tracking systems
- FlightAware exports
- Flight tracking APIs
- Custom data collection systems

### Downstream Consumers
- Google Earth visualization
- Route adherence ML models
- Flight analysis tools
- AWS Athena queries
- Custom analytics pipelines

### Data Flow
```
ADS-B System → KML Export → Flight Route Splitter → Organized KML → Analysis/ML
```

## Support & Maintenance

### Bug Reports
Include:
- Input KML structure
- Airport database format
- Command-line arguments used
- Console output (with errors)
- Expected vs. actual behavior

### Feature Requests
Consider:
- Use case description
- Data characteristics
- Performance requirements
- Integration needs

### Development Environment Setup
```bash
# Clone repository
git clone <repo-url>
cd flight-route-splitter

# Verify Python version
python --version  # Should be 3.6+

# Run tests (when available)
pytest tests/

# Run with sample data
python adsb_historical_routes.py \
    -i examples/sample_tracks.kml \
    -a examples/airports.txt \
    -o examples/output.kml
```

## Related Projects

- **KML Utilities**: Other tools John has built for KML processing
- **Route Adherence ML**: Downstream consumer of this tool's output
- **AWS Athena Queries**: Data warehouse integration
- **Flight Data Aggregator**: Combines multiple data sources

## References

- KML 2.2 Specification
- Haversine formula documentation
- Aviation coordinate systems (WGS84)
- ISO 8601 datetime format
- Google Earth KML best practices

---

**For AI Assistants**: This file provides high-level context. See other .claude/ files for specific aspects:
- `decisions.md` - Key design decisions and rationale
- `known_issues.md` - Current bugs and limitations
- `project_memory.md` - Historical context and evolution
- `schema_notes.md` - Data format specifications
- `style_guide.md` - Code conventions and patterns
