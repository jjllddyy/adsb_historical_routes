# Cirium → adsb Input Automation — Design

**Date:** 2026-07-30
**Branch:** `cirium-input-automation`
**Status:** Approved design

## Problem

`adsb_historical_routes.py` requires two hand-built input files per airline:
a `*_routes_time.csv` (`origin,destination,avg_enroute_min`, ICAO) and a
`*_airports.csv` (`airport,latitude,longitude,elevation_ft`, ICAO). Today these are
produced manually. Cirium exports schedule/block-time data keyed by **IATA** codes with
no coordinates. We want a script that converts a raw Cirium export into both input files.

## Inputs

Raw Cirium CSV, e.g. `input/lan_route_cirium_202606-202607.csv`. Columns:

```
report_source, Mkt Al, Op Al, Orig, Dest, Manu, Type, Aircraft Family,
Aircraft Type, Equip, Flights, Seats, ASMs, Block Mins
```

- `report_source` (a report month, e.g. `2026-06`) is **optional**. When present, the file
  merges multiple monthly reports and a route's totals may vary by month.
- `Orig`/`Dest` are **IATA** codes.
- `Flights` is a count; `Block Mins` is total block minutes. Both may carry thousands
  separators inside quotes (e.g. `"5,100"`) and must be de-comma'd.
- A route (orig–dest) may appear on many rows (different aircraft types and/or months).

## Outputs

Written to `<prefix>_routes_time.csv` and `<prefix>_airports.csv`, where `<prefix>` is the
`--output` folder+prefix (e.g. `--output input/lan_202606-202607`).

- **routes_time**: `origin,destination,avg_enroute_min` — ICAO codes, `avg_enroute_min`
  rounded to 1 decimal. One row per directional orig–dest, sorted by `(origin, destination)`
  for deterministic, diff-friendly output.
- **airports**: `airport,latitude,longitude,elevation_ft` — ICAO, deduped, sorted; the
  union of every origin and destination present in the output routes_time file.

## Route-time aggregation (per orig–dest route)

For each **(Orig, Dest)** route independently:
1. Group its rows by `report_source` month (a single group if the column is absent).
2. Per month: pool aircraft types → `Σ Block Mins ÷ Σ Flights` = that month's average
   block time for the route. Rows with `Flights ≤ 0` are skipped.
3. Route value = **max** over the monthly pooled averages.

Rationale: the value is an over-estimate used only to trim ADS-B routes, so err high; the
max-across-months pooling is per-route and matches "use the larger value" for both aircraft
type (pooled within a month) and report month (max across months).

## IATA → ICAO + coordinates: `airport_lookup` (shared tool)

Core dependency: `~/Git/conversion_tools/airport_lookup/airport_converter.py`
(`AirportDatabase`, OurAirports-backed, ~70k airports). It resolves IATA→ICAO well but
currently **discards** OurAirports' `latitude_deg`/`longitude_deg`/`elevation_ft`.

### Enhancement to `airport_lookup` (backward-compatible, ~10 lines)
1. **Expose coordinates**: in `load_database` carry `latitude`, `longitude`, `elevation_ft`
   from the OurAirports row into the per-airport dict; in `_index_airport` store them in
   `airport_data`. `convert_iata_to_icao()` then returns coordinates + elevation.
2. **Pin-to-cache option**: add `auto_update: bool = True` to `__init__`;
   `should_update()` returns `False` when it is off (still downloads if the cache file is
   missing). Lets the script use the existing cache deterministically and avoids a surprise
   network re-download / 10-airport minimal-DB fallback when the cache is > 7 days old.

No change to the converter CLI or its CSV output columns; existing callers unaffected.
`conversion_tools` is not a git repo — the change is applied in place and noted for the
user's records (nothing to commit there).

OurAirports coordinates are consistent with existing hand-built files (spot check: OurAirports
SBGR `(-23.4313, -46.4700, 2461 ft)` vs existing `latam_la_airports.csv` `(-23.4356, -46.4731,
2461)` — ~450 m, identical elevation).

## Script: `build_cirium_inputs.py` (adsb repo root)

CLI:
```
python3 build_cirium_inputs.py \
    --input  input/lan_route_cirium_202606-202607.csv \
    --output input/lan_202606-202607
# → input/lan_202606-202607_routes_time.csv
#   input/lan_202606-202607_airports.csv
```

Pipeline:
1. Parse the Cirium CSV (BOM-aware `DictReader`); detect the optional `report_source`;
   de-comma `Flights`/`Block Mins`.
2. Aggregate route times per the rule above.
3. Map Orig/Dest IATA→ICAO (+coords) via the enhanced `airport_lookup`, cache pinned
   (`auto_update=False`). Collect the airport set from all mapped endpoints.
4. Write routes_time.csv (ICAO, 1 dp) and airports.csv (ICAO, deduped/sorted).
5. Validate & alert (see below).

Row/skip guards: `Flights ≤ 0` or blank, blank `Block Mins`, or `Orig == Dest` → skip row.
Route guard: if a route has no surviving rows in any month (no valid `Flights`), it yields no
time and is dropped with an alert. Endpoint guards: unmappable IATA or an airport with missing
lat/lon → drop the route and alert; missing elevation → default `0` and warn.

## Validation & "ready to run" check

After writing, the script verifies the outputs are sufficient to run
`adsb_historical_routes.py`:
- Every `origin`/`destination` in routes_time appears in the airports file (invariant).
- Both output files are non-empty with correct headers.
- Prints a summary: source rows read, unique routes written, unique airports written,
  dropped routes, and any unmapped IATA codes (enumerated).
- Prints a final verdict: **READY** (both files complete, invariant holds) or
  **NEEDS INPUT** with the specific missing items listed (e.g. IATA codes with no ICAO
  mapping, airports with no coordinates).

## Testing (`tests/test_build_cirium_inputs.py`)

- Thousands-comma parsing of `Flights`/`Block Mins`.
- Per-route pooled-months-max aggregation: (a) multi-month case picks the larger monthly
  average; (b) multi-aircraft-within-month pools via Σblock÷Σflights; (c) no-`report_source`
  single-group case.
- Skip rules (`Flights ≤ 0`, `Orig == Dest`).
- IATA→ICAO mapping and airports-file completeness invariant (every route endpoint present).
- Unmapped-IATA handling (route dropped, reported).
- One check that the `airport_lookup` enhancement returns coordinates for a known IATA.

## Git workflow

1. **Push existing work to main** — already done: fast-forwarded `main` to the geometry/
   multi-hop commit and pushed (`efd6158..862ec76`). Bulk files (226 MB KML, 3.8 MB
   `Airports_2606.txt`, stray `{docs,…}` dir) gitignored; small reference CSVs committed.
2. **Feature branch** `cirium-input-automation` off main (this branch) holds the spec,
   `build_cirium_inputs.py`, and its tests.

## Out of scope

- No changes to `adsb_historical_routes.py` behavior.
- No re-download/refresh of the OurAirports cache (uses the existing pinned cache).
- No reverse-route synthesis: output exactly the directional routes present in the source.
