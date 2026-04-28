# Examples

This directory contains example usage scripts and sample data for the Flight Route Splitter.

## Files

- **example_usage.sh**: Shell script demonstrating common usage patterns
- **sample_input.kml**: (If available) Sample KML input file
- **sample_output.kml**: (If available) Sample organized output

## Running Examples

### Basic Usage

```bash
# Make sure you're in the project root directory
cd ..

# Process sample file (if available)
python adsb_historical_routes.py \
    -i examples/sample_input.kml \
    -a examples/airports.txt \
    -o examples/sample_output.kml
```

### View Example Commands

```bash
# View all example commands without running
bash examples/example_usage.sh
```

## Test Data

For testing purposes, you'll need:

1. **Input KML file**: Flight tracking data with gx:Track elements
2. **Airport database**: Text file with airport codes and coordinates

### Sample Airport Database Format

Create a file `airports.txt` with entries like:

```
A,MMMX,MEXICO CITY INTL,19.436306,-99.072083,7316,18000,18000,4500,0
A,KLAX,LOS ANGELES INTL,33.942536,-118.408075,125,18000,18000,4500,0
A,KJFK,JOHN F KENNEDY INTL,40.639751,-73.778925,13,18000,18000,4500,0
A,SAAN,BUENOS AIRES AEROPARQUE,-34.559292,-58.415606,18,18000,18000,4500,0
A,SAEZ,BUENOS AIRES EZEIZA,-34.822222,-58.535833,67,18000,18000,4500,0
A,SADJ,CORDOBA,-31.323889,-64.208056,1604,18000,18000,4500,0
A,SADR,ROSARIO,-32.903611,-60.785,-82,18000,18000,4500,0
```

## Expected Output

After running the script, you should see:

```
Loading airports from: airports.txt
Loaded 7 airports

Processing: sample_input.kml
Found 1 aircraft registrations

Processing XA-ADC...
  Track with 87 points
    Found complete flight: 81 points
  Detected 1 flight segments
    SADJ -> SAEZ: 81 points, 2025-10-17_0000

Total flight segments: 1

Output written to: sample_output.kml

Summary:
  Complete routes (with both airports): 1
  Incomplete routes: 0
```

## Troubleshooting Examples

If you encounter issues:

1. **No flights detected**: Check that your KML has altitude data
2. **Airport not matched**: Verify airports.txt includes relevant airports
3. **Registration shows UNKNOWN**: Check KML folder structure

See main [README.md](../README.md) for detailed troubleshooting.
