# Handoff to `~/Git/qgis`: `flown_routes` v2 and the flown-only `.qgz` call

**From:** `adsb_historical_routes`, 2026-10-30 (branch `gpkg-output`, tool 1.4.0).

This adds to `~/Git/kml/company_route_extractor/docs/handoff/qgis_prepare_partial_inputs.md`,
which already asks for flown v2, optional `--flown`/`--company` and `--project-name`. This note
gives the exact v2 geometry and the call that `adsb_historical_routes` now makes.

Paste everything below the line into a Claude Code session started in `~/Git/qgis`.

---

## What changed in `flown_routes` v2

- `meta.schema_version` is `"2"`. The schema name, every table, column and index are unchanged.
- **`flights.geom`** is now **MULTILINESTRING Z** (it was LineString Z): the sampled display line.
- **`tracks_full.geom`** is now **MULTILINESTRING ZM** (it was LineString ZM): every point,
  M = Unix epoch seconds.
- `track_points`, `diagnostics` and `meta` are unchanged.
- **The rule:** a line is cut where two consecutive vertices are more than 180° apart in
  longitude. The first part ends at ±180 and the next starts at ∓180, at the latitude, Z and M
  interpolated linearly in lon/lat. A line that does not cross is **one part with exactly the
  v1 coordinates**. All longitudes stay within -180..180. Same rule as `company_routes` v2.
- **Today no LAN or VIV flight crosses** (0 of 51,345 and 0 of 7,125), so every v2 line is a
  single part; but the geometry *type* is multi for every row.

## What breaks in `prepare` today

1. `prepare/inputs.py` `_EXPECT["flown"]` requires `schema_version == "1"`: a v2 file is refused.
2. `_zm(g)` and `_xyz(g)` call `g.GetPointCount()` / `g.GetPoints()` on the geometry. On an OGR
   MultiLineString these return 0 points / nothing, so **every flight would be skipped as
   `degenerate_track`** once the version check is relaxed.
3. `prepare` has no `--project-name`, and requires `--company`.

## Requested changes

1. Accept flown `schema_version` "1" and "2".
2. Read lines part by part. For a MultiLineString, take each part with
   `g.GetGeometryRef(i)` (`g.GetGeometryCount()` parts) and join them, dropping the vertex each
   part *adds* at ±180 (the last vertex of every part but the final one, and the first vertex of
   every part but the first). That gives back the original vertices, so metrics are unchanged.
   `ogr.ForceToLineString` is not enough: it keeps the ±180 vertices and does not unwrap.
3. Before any planar math (numpy longitude differences, local projections, bounding boxes),
   unwrap longitudes relative to the flight. Haversine distances are unaffected.
4. Keep the builder interface of the partial-inputs handoff §3.7 stable. `adsb_historical_routes`
   calls, from the repo root:

   ```bash
   bash -c 'source scripts/env.sh && exec "$QGIS_PY" -m prepare "$@"' prepare \
     --airline LAN --navdb <abs navdb> --flown <abs flown.gpkg> [--company <abs company.gpkg>] \
     --out <abs folder of the .qgz> --project-name <qgz file stem>
   ```

   Success = exit 0 and `<out>/<project-name>.qgz` exists. On failure it shows your last five
   stderr lines.

## Test data

- Flown v2, LAN: `/Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.gpkg` (LATAM Brasil, UTC takeoff
  2026-07-14 → 2026-09-15, POS integrated, `track_points` present).
- Company v2, LAN: `/Volumes/jy-ext/company_gpkg/lan/company_route_lan_2026_0507-0908.gpkg`.
- navdb: `~/Git/qgis/input/navdb_2610.gpkg`.
- A crossing example to test the join: `adsb_historical_routes/tests/test_antimeridian.py`
  builds Nadi ↔ Apia flights (two parts each) in a temporary folder.

## Done when

- `python3 gpkg_to_qgz.py /Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.gpkg --navdb
  ~/Git/qgis/input/navdb_2610.gpkg --company <company v2>` (run in `adsb_historical_routes`) exits
  0 and writes `2026_0714-0930.qgz` next to the GeoPackage.
- The same call without `--company` builds a flown-only project.
- A v1 flown file still builds the project it builds today.
