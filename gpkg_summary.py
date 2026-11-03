#!/usr/bin/env python3
"""
Summarize a ``flown_routes`` GeoPackage written by adsb_historical_routes.py.

Reads only the attribute columns (no geometry), so it takes seconds on a multi-GB file.
Prints build settings, flight counts, dates, data sources, endpoint rescue methods, O/D
plausibility, diagnostics dispositions and the busiest city pairs.

    python3 gpkg_summary.py /Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.gpkg
    python3 gpkg_summary.py lan.gpkg -o lan_summary.txt --top 50
"""

import argparse
import json
import sqlite3
import sys


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM gpkg_contents WHERE table_name = ?",
                        (name,)).fetchone() is not None


def _pct(n: int, total: int) -> str:
    return f"{100.0 * n / total:.1f}%" if total else "-"


def _evidence_sections(conn: sqlite3.Connection, meta: dict, n: int, w) -> None:
    """flown_routes v3: endpoint provenance, confidence, flight numbers, POS-only flights
    and (when the build ran the evidence stage) its report."""
    w("")
    w("Endpoints by source (origin / dest):")
    o = dict(conn.execute("SELECT orig_source, count(*) FROM flights GROUP BY 1"))
    d = dict(conn.execute("SELECT dest_source, count(*) FROM flights GROUP BY 1"))
    for src in sorted(set(o) | set(d), key=lambda k: -(o.get(k, 0) + d.get(k, 0))):
        w(f"  {src or '-':22s} {o.get(src, 0):8d} {d.get(src, 0):8d}")
    w("Endpoint confidence:")
    for c, k in conn.execute("SELECT endpoint_confidence, count(*) FROM flights GROUP BY 1 "
                             "ORDER BY 2 DESC"):
        w(f"  {c or '-':22s} {k:8d}  {_pct(k, n)}")
    numbered = conn.execute("SELECT count(*) FROM flights WHERE flight_number IS NOT NULL"
                            ).fetchone()[0]
    w(f"Flights with a flight number: {numbered} ({_pct(numbered, n)})")
    for src, k in conn.execute("SELECT flight_number_source, count(*) FROM flights WHERE "
                               "flight_number IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"):
        w(f"  {src:22s} {k:8d}")
    conflicts = conn.execute("SELECT count(*) FROM flights WHERE endpoint_conflict = 1"
                             ).fetchone()[0]
    planned = conn.execute("SELECT count(*) FROM flights WHERE planned_leg_key IS NOT NULL"
                           ).fetchone()[0]
    w(f"Endpoint conflicts: {conflicts}  matched to a planned leg: {planned} "
      f"({_pct(planned, n)})")
    rows = conn.execute("SELECT registration FROM flights WHERE disposition = 'pos_only'"
                        ).fetchall()
    if rows:
        prefixes: dict = {}
        for (reg,) in rows:
            reg = (reg or "").upper()
            p = reg.split("-")[0] if "-" in reg else (reg[:1] if reg.startswith("N") else reg[:2])
            prefixes[p] = prefixes.get(p, 0) + 1
        w(f"POS-only flights by registration prefix: {len(rows)} in total")
        for p, k in sorted(prefixes.items(), key=lambda x: -x[1]):
            w(f"  {p:22s} {k:8d}")
    report = json.loads(meta.get("evidence_report") or "null")
    if not report:
        return
    ext = report["extended"]
    w(f"Extended by POS (F2): {ext['flights']} flights, {ext['origin']} origins "
      f"(+{ext['before_km']:,.0f} km), {ext['dest']} destinations (+{ext['after_km']:,.0f} km), "
      f"{ext['endpoints_changed']} endpoints changed")
    w(f"Dropped segments recovered (F9): {report['recovered']}; still dropped: "
      f"{report['still_dropped']}")
    w("Planned-leg agreement (rule 1, plan-free answer):")
    for end in ("origin", "dest"):
        for src, (agree, disagree) in sorted(report["agreement"].get(end, {}).items(),
                                            key=lambda x: -sum(x[1])):
            w(f"  {end:6s} {src:22s} {agree + disagree:8d}  agree {_pct(agree, agree + disagree)}")
    if report.get("contradicted"):
        w(f"1.4.0 inferred ends the plan contradicts: {report['contradicted']}")


def summarize(path: str, top: int = 25) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    meta = dict(conn.execute("SELECT key, value FROM meta"))
    out = []
    w = out.append

    w(f"GeoPackage: {path}")
    w(f"Schema: {meta.get('schema_name')} v{meta.get('schema_version')}  "
      f"tool {meta.get('tool')} {meta.get('tool_version')}  created {meta.get('created_utc')}")
    w(f"Airline: {meta.get('airline') or '-'}  preset: {meta.get('preset')}  "
      f"flight dates: {meta.get('date_from')} -> {meta.get('date_to')}")
    try:
        w("Command: " + " ".join(json.loads(meta.get("argv", "[]"))))
    except ValueError:
        pass

    n, aircraft, minutes, km = conn.execute(
        "SELECT count(*), count(DISTINCT aircraft_id), sum(duration_min), sum(distance_km) "
        "FROM flights").fetchone()
    w("")
    w(f"Flights: {n}  aircraft: {aircraft}  track hours (first to last point): "
      f"{(minutes or 0) / 60:,.0f}  flown km: {km or 0:,.0f}")
    n_days = conn.execute("SELECT count(DISTINCT flight_date) FROM flights").fetchone()[0]
    w(f"Days with flights: {n_days}  mean flights/day: {n / n_days if n_days else 0:.0f}")
    suspect = conn.execute("SELECT count(*) FROM flights WHERE suspect_od = 1").fetchone()[0]
    w(f"Suspect O/D (flown > {meta.get('suspect_od_ratio')}x great circle): "
      f"{suspect} ({_pct(suspect, n)})")
    gaps = conn.execute("SELECT count(*), sum(gap_duration_min) FROM flights "
                        "WHERE has_gaps = 1").fetchone()
    w(f"Flights with coverage gaps > {meta.get('stitch_gap_min')} min: {gaps[0]} "
      f"({_pct(gaps[0], n)}), {(gaps[1] or 0) / 60:,.0f} h of gaps in total")

    w("")
    w("Flights by data sources:")
    for src, k in conn.execute("SELECT sources, count(*) FROM flights GROUP BY 1 ORDER BY 2 DESC"):
        w(f"  {src or '-':20s} {k:8d}  {_pct(k, n)}")
    if _table_exists(conn, "track_points"):
        w("Track points by source:")
        rows = conn.execute("SELECT source, count(*) FROM track_points GROUP BY 1 "
                            "ORDER BY 2 DESC").fetchall()
        total = sum(k for _, k in rows)
        for src, k in rows:
            w(f"  {src or '-':20s} {k:10d}  {_pct(k, total)}")

    w("")
    w("Flights by disposition / endpoint rescue:")
    for disp, rm, k in conn.execute(
            "SELECT disposition, coalesce(rescue_method, '-'), count(*) FROM flights "
            "GROUP BY 1, 2 ORDER BY 3 DESC"):
        w(f"  {disp:16s} {rm:20s} {k:8d}  {_pct(k, n)}")

    w("")
    w("Diagnostics (raw segment outcomes):")
    for disp, k in conn.execute("SELECT disposition, count(*) FROM diagnostics "
                                "WHERE phase = 'final' GROUP BY 1 ORDER BY 2 DESC"):
        w(f"  {disp or '-':20s} {k:8d}")

    cols = {r[1] for r in conn.execute("PRAGMA table_info(flights)")}
    if "orig_source" in cols:
        _evidence_sections(conn, meta, n, w)

    w("")
    w("Flights per month:")
    for month, k in conn.execute("SELECT substr(flight_date, 1, 7), count(*) FROM flights "
                                 "GROUP BY 1 ORDER BY 1"):
        w(f"  {month}  {k:8d}")

    w("")
    w(f"Top {top} city pairs:")
    for pair, k, med in conn.execute(
            "SELECT city_pair, count(*), avg(duration_min) FROM flights "
            "WHERE suspect_od = 0 GROUP BY 1 ORDER BY 2 DESC LIMIT ?", (top,)):
        w(f"  {pair:12s} {k:6d}  mean {med:5.0f} min")
    n_pairs = conn.execute("SELECT count(DISTINCT city_pair) FROM flights").fetchone()[0]
    w(f"City pairs in total: {n_pairs}")
    conn.close()
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("gpkg", help="flown_routes GeoPackage")
    p.add_argument("-o", "--output", help="Also write the summary to this text file")
    p.add_argument("--top", type=int, default=25, help="City pairs to list (default 25)")
    args = p.parse_args(argv)
    text = summarize(args.gpkg, top=args.top)
    sys.stdout.write(text)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
