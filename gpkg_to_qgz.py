#!/usr/bin/env python3
"""
Build the QGIS project (.qgz) for a ``flown_routes`` GeoPackage, without re-running the
builder.

The project is built by ``~/Git/qgis`` ``prepare``, which owns the chart layers, basemaps
and styles; it links to the GeoPackages and copies no data.

    python3 gpkg_to_qgz.py /Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.gpkg \\
        --navdb ~/Git/qgis/input/navdb_2610.gpkg
    python3 gpkg_to_qgz.py lan.gpkg --navdb navdb_2610.gpkg --company company_lan.gpkg -o lan.qgz
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adsb_historical_routes as ahr  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("gpkg", help="flown_routes GeoPackage")
    p.add_argument("-o", "--output", default=None,
                   help="Output .qgz path (default: the GeoPackage path with .qgz)")
    ahr.add_qgz_args(p, with_switch=False)
    args = p.parse_args(argv)

    if not os.path.isfile(args.gpkg):
        print(f"error: GeoPackage does not exist: {args.gpkg}", file=sys.stderr)
        return 2
    if not args.navdb:
        print("error: --navdb is required (the navdb_<AIRAC>.gpkg for the chart layers)",
              file=sys.stderr)
        return 2
    error = ahr.validate_qgz_options(True, "gpkg", args.navdb, args.company)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    try:
        conn = sqlite3.connect(f"file:{args.gpkg}?mode=ro", uri=True)
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta"))
        finally:
            conn.close()
        if meta.get("schema_name") != ahr.FLOWN_SCHEMA_NAME:
            raise ValueError(f"schema_name is {meta.get('schema_name')!r}")
    except (sqlite3.Error, ValueError) as exc:
        print(f"error: {args.gpkg} is not a {ahr.FLOWN_SCHEMA_NAME} GeoPackage: {exc}",
              file=sys.stderr)
        return 1
    output = args.output or ahr.qgz_path_for(args.gpkg)
    return ahr.build_qgz(args.gpkg, output, args.navdb, meta.get("airline", ""),
                         args.qgis_repo, company=args.company)


if __name__ == "__main__":
    sys.exit(main())
