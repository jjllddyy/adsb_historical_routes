#!/bin/bash
# Example Usage Script for ADSB Historical Routes
# This demonstrates common usage patterns

echo "==================================="
echo "ADSB Historical Routes - Examples"
echo "==================================="
echo

# Example 1: Basic single file processing
echo "Example 1: Process single file"
echo "--------------------------------"
echo "Command:"
echo "  python ../adsb_historical_routes.py \\"
echo "    --kml-files flights_day1.kml \\"
echo "    --airports ../input/latam_la_airports.csv \\"
echo "    --routes ../input/latam_la_routes_time.csv \\"
echo "    --output organized_routes.kml"
echo
echo "This processes one day of flight data and creates organized output."
echo

# Example 2: Multi-day processing
echo "Example 2: Multi-day route combination"
echo "---------------------------------------"
echo "Command:"
echo "  python ../adsb_historical_routes.py \\"
echo "    --kml-files day1.kml day2.kml day3.kml \\"
echo "    --airports ../input/latam_la_airports.csv \\"
echo "    --routes ../input/latam_la_routes_time.csv \\"
echo "    --output combined_routes.kml \\"
echo "    --group destination"
echo
echo "This combines routes spanning multiple days for the same aircraft."
echo

# Example 3: Batch processing
echo "Example 3: Batch process multiple airlines"
echo "-------------------------------------------"
echo "Script:"
echo "  for airline in copa volaris breeze; do"
echo "    python ../adsb_historical_routes.py \\"
echo "      --kml-files \${airline}_*.kml \\"
echo "      --airports ../input/latam_la_airports.csv \\"
echo "      --routes ../input/latam_la_routes_time.csv \\"
echo "      --output \${airline}_organized.kml \\"
echo "      --group destination"
echo "  done"
echo
echo "This processes each airline's files separately."
echo

# Example 4: Help information
echo "Example 4: Get help"
echo "-------------------"
echo "Command:"
echo "  python ../adsb_historical_routes.py --help"
echo
echo "This displays all available options and usage information."
echo

# Example 5: Folder-based processing (ADS-B Exchange traces)
echo "Example 5: Process a folder of per-aircraft KML files"
echo "------------------------------------------------------"
echo "Command:"
echo "  python ../adsb_historical_routes.py \\"
echo "    --kml-folder ../kml_input/kml_downloads \\"
echo "    --airports ../input/latam_la_airports.csv \\"
echo "    --routes ../input/latam_la_routes_time.csv \\"
echo "    --output lan_routes.kml \\"
echo "    --group destination"
echo
echo "This walks an ADS-B Exchange-style folder of per-aircraft-per-date KML files."
echo

echo "==================================="
echo "For more information, see README.md"
echo "==================================="
