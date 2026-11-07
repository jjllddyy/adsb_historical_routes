#!/usr/bin/env python3
"""
Rebuild KML from a ``flown_routes`` GeoPackage written by adsb_historical_routes.py,
without re-running the builder (spec §6, §12).

Every rendering option defaults to the value the GeoPackage was built with (stored in
``meta.kml_params``); pass a flag to override it.

    python3 gpkg_to_kml.py output/viv_flown_2026-09-30_2026-10-19.gpkg -o viv.kml
    python3 gpkg_to_kml.py viv.gpkg -o viv_alt.kml --color-mode altitude --sample 1
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adsb_historical_routes as ahr  # noqa: E402


def _bool(value: str) -> bool:
    return value == "true"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("gpkg", help="flown_routes GeoPackage")
    p.add_argument("-o", "--output", required=True, help="KML file to write")
    p.add_argument("--sample", type=float, dest="sample_minutes",
                   help="Point sampling interval in minutes")
    p.add_argument("--group", choices=["origin", "destination"], dest="group_by",
                   help="Top-level folders by origin or destination")
    p.add_argument("--color", dest="override_color", help="Line colour override (AABBGGRR)")
    p.add_argument("--width", dest="override_width", help="Line width override")
    p.add_argument("--opacity", type=int, metavar="0-100", dest="override_opacity",
                   help="Line opacity override, percent")
    p.add_argument("--labels", choices=["true", "false"], dest="show_labels")
    p.add_argument("--icons", choices=["true", "false"], dest="show_icons")
    p.add_argument("--ground", choices=["true", "false"], dest="extend_to_ground")
    p.add_argument("--stitch-gaps", choices=["true", "false"], dest="stitch_gaps")
    p.add_argument("--inferred-color", dest="inferred_color")
    p.add_argument("--stitch-gap-min", type=float, dest="stitch_gap_min")
    p.add_argument("--mark-sources", choices=["true", "false"], dest="mark_sources",
                   help="Colour POS/firehose stretches (needs the track_points layer)")
    p.add_argument("--pos-color", dest="pos_color")
    p.add_argument("--firehose-color", dest="firehose_color")
    p.add_argument("--color-mode", choices=["source", "altitude"], dest="color_mode")
    p.add_argument("--altitude-bands", type=int, dest="altitude_bands")
    args = p.parse_args(argv)

    if not os.path.exists(args.gpkg):
        print(f"Error: GeoPackage not found: {args.gpkg}")
        return 1
    if args.override_opacity is not None and not 0 <= args.override_opacity <= 100:
        print("Error: Opacity must be between 0 and 100")
        return 1
    if args.altitude_bands is not None and args.altitude_bands < 1:
        print("Error: --altitude-bands must be >= 1")
        return 1

    overrides = {k: getattr(args, k) for k in ahr.KML_PARAM_KEYS}
    for k in ("show_labels", "show_icons", "extend_to_ground", "stitch_gaps", "mark_sources"):
        if overrides[k] is not None:
            overrides[k] = _bool(overrides[k])
    try:
        n = ahr.export_gpkg_to_kml(args.gpkg, args.output, **overrides)
    except ValueError as e:
        print(f"Error: {e}")
        return 1
    print(f"Exported {n} flights")
    return 0


if __name__ == "__main__":
    sys.exit(main())
