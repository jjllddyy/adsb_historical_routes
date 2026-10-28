# GeoPackage output (`flown_routes` v1)

`adsb_historical_routes.py` writes a GeoPackage as its primary output. It is the input for the
QGIS route viewer (`~/Git/qgis`) and for `direct_detect` (`~/Git/analytics_tools/direct_detect`).
The KML for Google Earth is rendered *from* the GeoPackage.

- **Spec:** [`superpowers/specs/2026-10-27-route-gpkg-output-spec.md`](superpowers/specs/2026-10-27-route-gpkg-output-spec.md) (§3, §4, §6, §7, §8, §11.1, §12)
- **Where this tool sits in the chain:** [`superpowers/specs/geopackage_build_instructions.md`](superpowers/specs/geopackage_build_instructions.md)

## 1. Running it

```bash
python3 adsb_historical_routes.py \
    --kml-folder /Volumes/jy-ext/kml/kml-raw/viv_2026_oct \
    --airports input/viv_202606-2020607_airports.csv \
    --routes   input/viv_202606-2020607_routes_time.csv \
    --confidence permissive --corridor-cross-track-km 50 \
    --integrate-pos true --firehose-folder input/viv_adsb \
    --stitch-gaps true --airline VIV \
    --output /Volumes/jy-ext/geodata/airlines/viv/flown/viv_flown_{from}_{to}.kml
```

| Flag | Default | Effect |
|---|---|---|
| `--format` | `both` | `both`: `<output>.gpkg` and `<output>.kml`. `gpkg`: GeoPackage only, the fastest. `kml`: KML only. The GeoPackage is still built, in a temporary file that is then deleted. |
| `--airline` | empty | Stored in `flights.airline` and `meta.airline`. A warning is printed when it is missing. |
| `--gpkg-points` | off | Also writes `track_points`. Always on with `--integrate-pos`, because the KML needs each point's source. |
| `--suspect-od-ratio` | `2.5` | Threshold for `suspect_od`. |
| `--legacy-kml` | off | Writes the KML with the direct writer, not from the GeoPackage. Kept for one release. |
| `--gpkg` | n/a | Deprecated alias for `--format both`. |

- **Naming:** `{from}` and `{to}` in `--output` become the first and last flight dates (UTC takeoff).
  - The same dates go into `meta.date_from` and `meta.date_to`.
  - The diagnostics CSV is always written, as `<output>.kml.diagnostics.csv`.
- **Rebuilding KML without re-running the builder:**

  ```bash
  python3 gpkg_to_kml.py viv_flown_2026-09-30_2026-10-19.gpkg -o viv.kml \
      [--sample 1] [--color-mode altitude] [--group origin] [--stitch-gaps true] ...
  ```

  Every option defaults to the value the file was built with (`meta.kml_params`).
- **The GUI** (`route_builder/`) has Output format, Airline code and GeoPackage track points in its Output section.

## 2. Schema

All geometry is EPSG:4326. Z is in **metres**; `*_ft` attribute columns are in feet. Times are UTC
`DATETIME` strings (`2026-10-19T14:34:55.437Z`). `city_pair`, `group_key`, `origin` and `dest` are
copied onto every child table, so no filter ever needs a join.

### `flights`: one row per kept flight (LineStringZ, the sampled display line)

| Column | Notes |
|---|---|
| `flight_id` | `{aircraft_id}_{takeoff:%Y%m%dT%H%M%SZ}`; unique. A repeat (same aircraft, same takeoff second) gets `_2`. |
| `airline`, `aircraft_id`, `registration` | `callsign` is reserved and stays null (spec §10). |
| `origin`, `dest`, `city_pair`, `group_key` | `group_key` is the KML top folder: the destination, or the origin with `--group origin`. |
| `takeoff_time`, `landing_time`, `flight_date` | |
| `duration_min`, `distance_km`, `max_alt_ft` | `distance_km` is the flown distance along every point. |
| `n_points_full`, `n_points_display` | |
| `has_gaps`, `gap_duration_min` | Measured: point-to-point gaps longer than `--stitch-gap-min` in the full track. |
| `disposition`, `rescue_method`, `joined_segment_count` | `kept_complete` / `kept_joined` / `kept_rescued`. `rescue_method` is null when none was used. |
| `sources` | For example `ADSB,FIREHOSE`. |
| `source_file`, `preset` | |
| `style_color`, `style_width`, `style_opacity` | The input KML's style, so the KML can be reproduced. The viewer ignores them. |
| `od_gc_km`, `od_distance_ratio`, `suspect_od` | Great circle between the labelled airports; flown ÷ great circle; ratio > `--suspect-od-ratio` (§3). |

Indexes: `flight_id` (unique), `city_pair`, `group_key`, `dest`, `origin`, `flight_date`,
`registration`, `aircraft_id`.

### Other tables

- **`tracks_full`** (LineStringZM): every point of each flight, after POS/firehose enrichment.
  - M is Unix epoch seconds, as a double.
  - Columns: `flight_id`, `city_pair`, `group_key`, `origin`, `dest`, `flight_date`.
  - Indexes on `flight_id` and `city_pair`.
- **`track_points`** (PointZ, optional): one row per point.
  - Columns: `seq`, `t`, `alt_ft`, `gs_kt`, `vs_fpm`, `track_deg` and `source` (`ADSB` / `POS` / `FIREHOSE`), plus the grouping columns.
  - Ground speed, vertical speed and track are recomputed from consecutive points after enrichment. They are null for the first point, and wherever two points share a timestamp.
  - Indexes on (`flight_id`, `seq`) and `city_pair`.
- **`diagnostics`**: the diagnostics CSV rows, typed.
  - `flight_id` is set on each kept flight's own row and on every `kept_joined` fragment absorbed into it.
  - `od_*` and `suspect_od` are set on the flight's own row.
- **`meta`**: key/value rows.
  - `schema_name` (`flown_routes`), `schema_version` (`1`), `tool`, `tool_version`, `created_utc`, `airline`, `date_from`, `date_to`, `argv` (JSON), `source_count`.
  - Also `preset`, `group_by`, `sample_minutes`, `stitch_gap_min`, `integrate_pos`, `track_points`, `kml_params` (JSON), `flight_count`, `suspect_od_ratio` and `suspect_od_count`.

There are no gap-connector features: `--stitch-gaps` connectors are drawn only at KML export (§4.4).
The altitude colour mode is also export-only (§4.6).

## 3. Origin/destination fixes and `suspect_od`

The VIV October KML had about 2.7% of flights flying more than 2.5× the great circle between their
labelled airports, for example 131 "MMTO→MMSM" flights of 2½–10 h. Four root causes were found in
airport assignment and fixed:

1. **A farther airport could steal an endpoint.**
   - The altitude allowance grows with airport elevation.
   - A departure first seen 16 km from MMSM (7,369 ft) while climbing through 12,100 ft failed MMSM's allowance, but passed MMTO's (8,466 ft) 89 km away.
   - **Fix:** the closest airport now wins when the height above it fits a 3° climb/descent path. This only re-labels endpoints that already matched; it never creates a match.
2. **Route-time rescue accepted a destination the track had already flown past.**
   - The corridor cross-track test has no along-track limit.
   - MMSM departures last seen near Mazatlán were labelled MMSM→MMLO, 450 km behind them.
   - **Fix:** the corridor geometry's along-track limit now applies to route-time rescue too.
3. **Out-and-back fragments were joined.**
   - Example: MMSM→MMVA→MMSM with no coverage at MMVA, joined into one "flight".
   - **Fix:** a join is refused when the heading reverses (> 120°) and the aircraft made no progress across the gap (< 200 km/h).
4. **A guessed destination survived a join.**
   - A destination inferred for a short fragment was kept after a join extended the track past it.
   - **Fix:** it is now dropped and re-inferred on the merged track.

Every remaining flight gets `od_distance_ratio`, and `suspect_od` marks ratio > 2.5. The count is
printed in the run summary and stored in `meta.suspect_od_count`. Downstream statistics exclude
`suspect_od` flights. What remains flagged is mostly:

- corrupt positions from a few transponders (flown distances of thousands of km in an hour);
- real short positioning or holding flights between nearby Mexico City-area airports.

## 4. Validation

- **KML equivalence (spec §6):** `kml_compare.py` compares the folder tree, the placemarks per folder, descriptions, line and icon styles, coordinates (to 1e-6) and `when` instants (to 1 ms).
  - `tests/test_gpkg_output.py` runs it on a synthetic fixture in every mode: default, `--color-mode altitude`, `--stitch-gaps`, `--integrate-pos`, `--group origin`, and a labels/width/opacity mix.
  - On real VIV data the KML rendered from the GeoPackage is **byte-identical** to the direct writer's.
- **File validity:**
  - GDAL's `validate_gpkg.py` passes with no messages.
  - `ogrinfo -so` feature counts equal the rows written.
  - QGIS 3.44 loads every layer with no log warnings.
  - `tests/test_gpkg_writer.py` runs `ogrinfo` when GDAL is installed.
- **Counts:** `flights` = the distinct `flight_id`s in `diagnostics`, whatever the number of KML placemarks.

## 5. Notes for the other projects

These projects were not modified as part of this change. This is what each needs.

- **`company_route_extractor`** (spec §5):
  - Vendor `gpkg_writer.py` from this repo unchanged.
  - The API:
    - `GeoPackageWriter(path)`;
    - `add_feature_table(name, "LINESTRING"|"POINT", [(col, type)], z=, m=)`;
    - `add_attribute_table`;
    - `insert_features(name, [(blob, bbox, values)])` with `encode_linestring` / `encode_point`;
    - `insert_rows`;
    - `add_index(name, cols, unique=)`;
    - `close()`, which writes the file atomically from `<path>.part`.
  - Use `format_datetime` for `DATETIME` columns; GDAL warns on any other format.
  - The routes are 2D: call `encode_linestring(coords)` without `has_z`.
- **`qgis`** (viewer and spike):
  - The new file uses `takeoff_time`/`landing_time`.
  - `tracks_full` is always present; `track_points` is present when `meta.track_points = 1`.
  - Exclude `suspect_od = 1` from statistics.
  - `flights.has_gaps` is now measured, not the always-false builder flag.
- **`direct_detect`:**
  - `tracks_full` M values are epoch seconds.
  - `flight_id` is stable across reruns of the same inputs.
  - The diagnostics `flight_id` links absorbed fragments to their flight.
