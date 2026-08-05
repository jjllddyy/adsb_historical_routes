#!/usr/bin/env python3
"""Build adsb_historical_routes input files (routes_time + airports) from a raw Cirium CSV."""

import argparse
import csv
import os
import sys
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Tuple


def parse_number(raw: str) -> Optional[float]:
    """Parse a Cirium numeric cell, stripping thousands-commas/quotes/whitespace.

    Returns None for blank or non-numeric values.
    """
    if raw is None:
        return None
    s = raw.strip().strip('"').replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def read_cirium(path: str) -> Tuple[List[dict], bool]:
    """Read a Cirium CSV into normalized row dicts.

    Returns (rows, has_report_source). Each row: report_source (str, '' if column
    absent), orig/dest (upper IATA), flights/block_mins (float or None).
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        has_rs = "report_source" in fields
        rows = []
        for row in reader:
            rows.append({
                "report_source": (row.get("report_source") or "").strip() if has_rs else "",
                "orig": (row.get("Orig") or "").strip().upper(),
                "dest": (row.get("Dest") or "").strip().upper(),
                "flights": parse_number(row.get("Flights", "")),
                "block_mins": parse_number(row.get("Block Mins", "")),
            })
    return rows, has_rs


def aggregate_route_times(rows: List[dict]) -> Tuple[Dict[Tuple[str, str], float], List[Tuple[str, str]]]:
    """Per (orig,dest): pool aircraft types per report month (Σblock/Σflights), take max month.

    Rows with flights<=0/None, blank block_mins, or orig==dest are skipped. Routes seen but
    with no valid flight rows in any month are returned in dropped_no_flights.
    """
    # route -> month -> [sum_block, sum_flights]
    acc: Dict[Tuple[str, str], Dict[str, List[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    seen = set()
    for r in rows:
        o, d = r["orig"], r["dest"]
        if not o or not d or o == d:
            continue
        seen.add((o, d))
        f, b = r["flights"], r["block_mins"]
        if f is None or b is None or f <= 0:
            continue
        month = r["report_source"] or "_single"
        bucket = acc[(o, d)][month]
        bucket[0] += b
        bucket[1] += f

    route_times: Dict[Tuple[str, str], float] = {}
    for route, months in acc.items():
        monthly = [blk / fl for (blk, fl) in months.values() if fl > 0]
        if monthly:
            route_times[route] = max(monthly)
    dropped_no_flights = sorted(seen - set(route_times))
    return route_times, dropped_no_flights


def convert_routes_to_icao(route_times: Dict[Tuple[str, str], float],
                           resolve: Callable[[str], Optional[dict]]) -> dict:
    """Map IATA route endpoints to ICAO (+coords) and assemble output records.

    Routes whose endpoints cannot be mapped to ICAO or lack coordinates are dropped and
    reported. Missing elevation defaults to 0 (recorded in elev_defaulted).
    """
    cache: Dict[str, Optional[dict]] = {}

    def look(iata):
        if iata not in cache:
            cache[iata] = resolve(iata)
        return cache[iata]

    routes: List[Tuple[str, str, float]] = []
    airports: Dict[str, dict] = {}
    unmapped, missing_coords, elev_defaulted = set(), set(), set()
    dropped_routes: List[Tuple[str, str, str]] = []

    for (o, d), minutes in route_times.items():
        recs = {o: look(o), d: look(d)}
        bad = []
        for iata, rec in recs.items():
            if rec is None or not rec.get("icao"):
                unmapped.add(iata)
                bad.append(iata)
            elif rec.get("latitude") in (None, "") or rec.get("longitude") in (None, ""):
                missing_coords.add(iata)
                bad.append(iata)
        if bad:
            dropped_routes.append((o, d, "no ICAO/coords for " + ",".join(sorted(set(bad)))))
            continue
        for iata in (o, d):
            rec = recs[iata]
            icao = rec["icao"]
            if icao not in airports:
                elev = rec.get("elevation_ft")
                if elev in (None, ""):
                    elev = 0
                    elev_defaulted.add(icao)
                airports[icao] = {
                    "airport": icao,
                    "latitude": rec["latitude"],
                    "longitude": rec["longitude"],
                    "elevation_ft": elev,
                }
        routes.append((recs[o]["icao"], recs[d]["icao"], minutes))

    routes.sort(key=lambda t: (t[0], t[1]))
    return {
        "routes": routes,
        "airports": airports,
        "unmapped": sorted(unmapped),
        "missing_coords": sorted(missing_coords),
        "elev_defaulted": sorted(elev_defaulted),
        "dropped_routes": dropped_routes,
    }


def write_routes_time(path: str, routes: List[Tuple[str, str, float]]) -> None:
    """Write routes to CSV: origin,destination,avg_enroute_min (rounded to 1 dp)."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["origin", "destination", "avg_enroute_min"])
        for orig, dest, minutes in routes:
            w.writerow([orig, dest, round(minutes, 1)])


def write_airports(path: str, airports: Dict[str, dict]) -> None:
    """Write airports to CSV: airport,latitude,longitude,elevation_ft (sorted by ICAO)."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["airport", "latitude", "longitude", "elevation_ft"])
        for icao in sorted(airports):
            a = airports[icao]
            w.writerow([a["airport"], a["latitude"], a["longitude"], a["elevation_ft"]])


def build_report(result: dict, dropped_no_flights: List[Tuple[str, str]], n_rows: int) -> Tuple[str, bool]:
    """Compose the human-readable validation report and compute the READY verdict."""
    endpoints = set()
    for orig, dest, _ in result["routes"]:
        endpoints.update((orig, dest))
    missing_in_airports = sorted(endpoints - set(result["airports"]))

    ready = bool(result["routes"]) and not result["unmapped"] \
        and not result["missing_coords"] and not missing_in_airports

    lines = ["=" * 60, "Cirium input build report", "=" * 60,
             f"Source rows read:        {n_rows}",
             f"Routes written:          {len(result['routes'])}",
             f"Airports written:        {len(result['airports'])}",
             f"Routes dropped (no map): {len(result['dropped_routes'])}",
             f"Routes dropped (no flts):{len(dropped_no_flights)}", ""]

    def section(title, items):
        if items:
            lines.append(f"{title} ({len(items)}):")
            lines.extend(f"    {x}" for x in items)
            lines.append("")

    section("Unmapped IATA codes (need ICAO mapping in airport_lookup)", result["unmapped"])
    section("Airports missing coordinates", result["missing_coords"])
    section("Routes dropped for unmapped/missing endpoints",
            [f"{o}-{d}: {why}" for o, d, why in result["dropped_routes"]])
    section("Routes dropped for no valid flights", [f"{o}-{d}" for o, d in dropped_no_flights])
    section("Elevations defaulted to 0 (verify)", result["elev_defaulted"])
    section("Route endpoints missing from airports file (INVARIANT BREACH)", missing_in_airports)

    if ready:
        lines.append("VERDICT: READY — both files complete; adsb_historical_routes.py can run.")
    else:
        reasons = []
        if not result["routes"]:
            reasons.append("no routes produced")
        if result["unmapped"]:
            reasons.append(f"{len(result['unmapped'])} unmapped IATA code(s)")
        if result["missing_coords"]:
            reasons.append(f"{len(result['missing_coords'])} airport(s) without coordinates")
        if missing_in_airports:
            reasons.append("airports-file invariant breached")
        lines.append("VERDICT: NEEDS INPUT — " + "; ".join(reasons) + ".")
    lines.append("=" * 60)
    return "\n".join(lines), ready


def write_report(path: str, report_text: str) -> None:
    """Write validation report to file."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(report_text + "\n")


DEFAULT_AIRPORT_LOOKUP_DIR = os.path.expanduser("~/Git/conversion_tools/airport_lookup")
DEFAULT_AIRPORT_OVERRIDE = "airport_overrides.csv"


def load_airport_overrides(path: str) -> Dict[str, dict]:
    """Load an IATA -> {icao, latitude, longitude, elevation_ft} override manifest.

    A small, hand-curated CSV that takes precedence over the airport_lookup/OurAirports
    result for airports it resolves wrongly or misses (e.g. IATA ``LIM`` -> current ICAO
    ``SPJC``, which OurAirports still files under the retired ``SPIM``). Grows as new cases
    are found, so codes stay consistent across builds. Lines starting with ``#`` are comments.

    Returns ``{}`` for the sentinel ``"ignore"``, an empty/missing path, or a default file
    that isn't present — so a run works with no manifest at all.
    """
    if not path or path.strip().lower() == "ignore" or not os.path.exists(path):
        return {}
    overrides: Dict[str, dict] = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        data = [ln for ln in f if not ln.lstrip().startswith("#")]
    for row in csv.DictReader(data):
        iata = (row.get("iata") or "").strip().upper()
        icao = (row.get("icao") or "").strip().upper()
        if not iata or not icao:
            continue
        overrides[iata] = {
            "icao": icao,
            "latitude": (row.get("latitude") or "").strip(),
            "longitude": (row.get("longitude") or "").strip(),
            "elevation_ft": (row.get("elevation_ft") or "").strip(),
        }
    return overrides


def make_resolver(airport_lookup_dir: str,
                  overrides: Optional[Dict[str, dict]] = None) -> Callable[[str], Optional[dict]]:
    """Return an iata->record resolver: the override manifest first, then the shared
    airport_lookup tool (cache pinned)."""
    overrides = overrides or {}
    if airport_lookup_dir not in sys.path:
        sys.path.insert(0, airport_lookup_dir)
    from airport_converter import AirportDatabase  # lazy: only needed for real runs
    db = AirportDatabase(
        cache_dir=os.path.join(airport_lookup_dir, ".airport_cache"),
        auto_update=False,
    )
    db.load_database(min_type="small_airport")

    def resolve(iata: str) -> Optional[dict]:
        key = (iata or "").strip().upper()
        if key in overrides:
            return dict(overrides[key])
        rec = db.convert_iata_to_icao(iata)
        if not rec:
            return None
        return {
            "icao": rec.get("icao"),
            "latitude": rec.get("latitude"),
            "longitude": rec.get("longitude"),
            "elevation_ft": rec.get("elevation_ft"),
        }
    return resolve


def run(input_path: str, output_prefix: str, resolve: Callable[[str], Optional[dict]]) -> bool:
    """Full pipeline: read -> aggregate -> convert -> write 3 files. Returns the READY verdict."""
    out_dir = os.path.dirname(os.path.abspath(output_prefix))
    os.makedirs(out_dir, exist_ok=True)

    rows, _ = read_cirium(input_path)
    route_times, dropped_no_flights = aggregate_route_times(rows)
    result = convert_routes_to_icao(route_times, resolve)

    write_routes_time(output_prefix + "_routes_time.csv", result["routes"])
    write_airports(output_prefix + "_airports.csv", result["airports"])
    report_text, ready = build_report(result, dropped_no_flights, n_rows=len(rows))
    write_report(output_prefix + "_error.txt", report_text)
    print(report_text)
    return ready


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Build routes_time + airports inputs for adsb_historical_routes.py from a Cirium CSV."
    )
    parser.add_argument("--input", required=True, help="Raw Cirium CSV file")
    parser.add_argument("--output", required=True,
                        help="Output folder+prefix (e.g. input/lan_202606-202607)")
    parser.add_argument("--airport-lookup-dir", default=DEFAULT_AIRPORT_LOOKUP_DIR,
                        help="Path to the airport_lookup tool (default: %(default)s)")
    parser.add_argument("--airport-override", default=DEFAULT_AIRPORT_OVERRIDE,
                        help="Airport override manifest CSV (iata,icao,latitude,longitude,"
                             "elevation_ft) applied on top of airport_lookup. Default "
                             "'%(default)s' when present; pass 'ignore' to disable, or a path "
                             "to another file.")
    args = parser.parse_args(argv)

    if not os.path.exists(args.input):
        print(f"Error: input file not found: {args.input}")
        return 1
    overrides = load_airport_overrides(args.airport_override)
    if overrides:
        print(f"Airport overrides applied: {len(overrides)} from {args.airport_override} "
              f"({', '.join(sorted(overrides))})")
    elif args.airport_override.strip().lower() != "ignore" and not os.path.exists(args.airport_override):
        print(f"(no airport override manifest at '{args.airport_override}'; using airport_lookup only)")
    resolve = make_resolver(args.airport_lookup_dir, overrides)
    ready = run(args.input, args.output, resolve)
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
