# Handoff to `direct_detect`: `flown_routes` v2 geometry

**From:** `adsb_historical_routes`, 2026-10-30 (branch `gpkg-output`, tool 1.4.0).

`direct_detect` does not read `flown_routes` yet; its DEVLOG plans to use it as the Phase B
track source. This note is so that reader is written for v2.

Paste everything below the line into a Claude Code session started in
`~/Git/analytics_tools/direct_detect`.

---

## `flown_routes` v2 (adsb_historical_routes 1.4.0)

- `meta.schema_version` is `"2"`. Tables, columns, indexes and `flight_id` are unchanged.
- `tracks_full.geom` is **MULTILINESTRING ZM** (v1: LineString ZM); M = Unix epoch seconds.
  `flights.geom` is MULTILINESTRING Z. `track_points` (PointZ) is unchanged.
- **Date-line rule:** a line is cut where consecutive vertices are more than 180° apart in
  longitude. Each part but the last ends with an added vertex at ±180, and each part but the
  first starts with an added vertex at ∓180 (lat, Z and M interpolated). A line that does not cross
  is one part with exactly the v1 coordinates.
- No LAN or VIV flight crosses today (0 of 51,345 and 0 of 7,125), but every row is multi.

## When you add the reader

- Accept `schema_version` "1" and "2".
- For v2, read every part and join them, dropping the added ±180 vertices (the last vertex of
  every part but the final one, the first vertex of every part but the first). The result is the
  original point sequence, with the builder's timestamps in M.
- Or read `track_points` ordered by (`flight_id`, `seq`): it is unchanged and has one row per
  original point, with `t`, `alt_ft` and `source` (`ADSB` / `POS` / `FIREHOSE`).
- Unwrap longitudes per flight before planar math; haversine is unaffected.

Example files: `/Volumes/jy-ext/route_gpkg/lan/2026_0714-0930.gpkg` (LAN, v2) and
`/Volumes/jy-ext/geodata/airlines/viv/flown/viv_flown_2026-09-30_2026-10-19.gpkg` (VIV, v1).
