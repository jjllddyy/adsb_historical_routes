# Spec: GeoPackage as the primary output of the route builders

**Date:** 2026-10-27, updated 2026-10-28. **Status:** ready for implementation in the builder repos. Nothing in them changes as part of the `qgis` project.
**Read with:** `docs/geopackage_build_instructions.md` (the data flow, and where each tool fits).
**Repos affected:** `~/Git/kml/adsb_historical_routes`, `~/Git/kml/company_route_extractor`
**Consumer:** the QGIS-based route viewer in `~/Git/qgis`. Any later custom map app should read the same files.
**Evidence:** `docs/spike/2026-10-27-qgis-poe-spike.md`

## 1. Why

Today both tools write KML directly. KML works for Google Earth, but as a working format it has three problems:

- **Its structure is lost when read.** GDAL and QGIS see each folder as a separate layer (172 layers for POE), and every attribute is buried in one `description` text field.
- **Data is thrown away.** Full-resolution track points, ground speed, segment-join diagnostics, OFP airways and timestamps are all computed and then discarded.
- **It is slow.** QGIS draws POE about 4× slower from KML than from GeoPackage, and the `_altcolor` variant has 23× more features.

The change: **GeoPackage becomes the primary output.** KML is then generated *from* the GeoPackage. The folder navigation stays exactly as it is today (group → city pair → flights), but it becomes indexed columns rather than nesting.

## 2. Goals and non-goals

**Goals**
1. Each tool writes a GeoPackage that follows the versioned schema below.
2. Today's KML output can be reproduced from that GeoPackage: the same folders, placemark order, descriptions, styling options and sampling.
3. Data that is computed today but thrown away is kept: full-resolution points, ground speed, diagnostics, OFP airway names, timestamps.
4. Stable IDs, so flights can later be joined to workpackage data, OFPs and weather.

**Non-goals**
- No changes to segment detection, join/rescue logic, presets or OFP parsing rules, beyond capturing fields that are currently dropped.
- No analysis inside the builders. Deviation metrics, STAR/SID matching, FIR crossings and company-route matching are computed downstream in the `qgis` project, so the builders stay simple.
- No GUI changes beyond passing the new flags through (`route_builder/server.py`).

**Anti-goals** (the change fails if any of these happen)
- The existing KML output changes in any way a Google Earth user would notice.
- Either tool gains a heavy native dependency (GDAL, geopandas) that makes install harder. Both are standard-library-only today.

## 3. Shared conventions (both tools)

| Rule | Value |
|---|---|
| CRS | EPSG:4326 (lon/lat), registered in `gpkg_spatial_ref_sys` |
| Geometry Z | **metres**, as in the source KML. Altitude attribute columns are in **feet** (`*_ft`). |
| Time | UTC ISO-8601 strings with a `Z` suffix (`2026-07-02T17:56:26.820Z`), stored as GeoPackage `DATETIME`. Dates are stored as `DATE`. |
| Grouping columns | `group_key` (the top folder name: destination or origin, depending on `--group`), `city_pair` (`ORIG-DEST`), `origin`, `dest`. **These are copied onto every child table** so that filters never need a join. The spike showed subquery filters were fragile. |
| Indexes | R-tree spatial index on every geometry table, plus B-tree indexes as listed per table. |
| Metadata | A non-spatial `meta` table of key/value rows: `schema_name`, `schema_version` (=`1`), `tool`, `tool_version`, `created_utc`, `airline`, `date_from`, `date_to`, `argv` (JSON), `source_count`. |
| Writer | The standard-library `sqlite3` module plus a small GeoPackage writer module: header, WKB geometry blobs, `gpkg_contents`, `gpkg_geometry_columns`, R-tree built after bulk insert. **No GDAL dependency.** The same module is copied (vendored) into both repos. Builder's call: a shared pip-installable package instead is fine. |
| Validity | The file must open in QGIS 3.44 and with `ogrinfo` (GDAL ≥ 3.8) without warnings. Each layer's `ogrinfo -so` feature count must match the number of rows written. |
| Output naming | `<output>.gpkg` next to `<output>.kml`. The diagnostics CSV is still written as well. |

## 4. adsb_historical_routes: schema `flown_routes` v1

### 4.1 `flights`: one row per kept flight (LineStringZ)

The geometry is the **sampled display line**: the same points the KML gets with `--sample` (default 2 min), first and last points always kept. This keeps network-scale drawing at the speed the spike measured (about 0.4 s for 14 k flights).

| Column | Type | Source / notes |
|---|---|---|
| `flight_id` | TEXT, unique | `"{aircraft_id}_{takeoff_time:%Y%m%dT%H%M%SZ}"`. Stable across reruns of the same data. |
| `airline` | TEXT | New `--airline` flag. Also stored in `meta`. |
| `aircraft_id` | TEXT | ICAO hex |
| `registration` | TEXT | |
| `callsign` | TEXT, nullable | **New, reserved.** No current loader reads a callsign: the KML parser is at :439 and the firehose loader at :647. For v1 this column stays null. Filling it is a separate decision; see §10. |
| `origin`, `dest`, `city_pair`, `group_key` | TEXT | Same values as today's folder names |
| `takeoff_time`, `landing_time` | DATETIME | |
| `flight_date` | DATE | Same as `takeoff_date_str` |
| `duration_min`, `distance_km` | REAL | |
| `max_alt_ft` | REAL | |
| `n_points_full`, `n_points_display` | INTEGER | Replaces today's "Points: 43 (sampled from 435)" text |
| `has_gaps` | INTEGER (0/1) | |
| `gap_duration_min` | REAL | |
| `disposition` | TEXT | `kept_complete` / `kept_joined` / `kept_rescued` |
| `rescue_method` | TEXT, nullable | |
| `joined_segment_count` | INTEGER | |
| `sources` | TEXT | Comma-joined set, for example `ADSB,POS` |
| `source_file` | TEXT | |
| `preset` | TEXT | `strict` / `balanced` / `permissive` / `custom` |
| `style_color`, `style_width`, `style_opacity` | TEXT / REAL / REAL | Copied from `FlightSegment.style_*`, so the KML exporter can reproduce per-flight colours inherited from the input KML when `--color`/`--width` aren't given (:1568, :1647). The viewer ignores them. |

Indexes: `city_pair`, `group_key`, `dest`, `origin`, `flight_date`, `registration`, `aircraft_id`.

### 4.2 `tracks_full`: full-resolution line per flight (LineStringZM)

- One row per `flight_id`, holding **every** point from before sampling, after POS/firehose enrichment. M = Unix epoch seconds as a double, which keeps millisecond precision.
- The exporter rebuilds `when` as `YYYY-MM-DDTHH:MM:SS.fffZ`. The equivalence test compares parsed instants to 1 ms, not strings.
- Used for terminal-area and procedure-adherence views, where 2-minute sampling is too coarse.
- Columns: `flight_id`, `city_pair`, `group_key`, `origin`, `dest`, `flight_date`. Indexes on `flight_id` and `city_pair`.

### 4.3 `track_points`: full-resolution points (PointZ). Optional, behind `--gpkg-points`

- One row per point from before sampling. Off by default, because it is roughly 10× the size of `flights`.
- **Forced on when `--integrate-pos` is used.** The exporter needs each point's `source` to reproduce the per-source colour runs (:1657).
- Columns: `flight_id`, `city_pair`, `group_key`, `seq`, `t` (DATETIME), `alt_ft`, `gs_kt`, `vs_fpm`, `track_deg`, `source` (ADSB/POS/FIREHOSE).
- **Recompute `gs_kt`, `vs_fpm` and `track_deg` after enrichment, from consecutive points.** Today `groundspeed` exists only for ADS-B KML points (:531). It is None for the first point and for every POS or FIREHOSE point (:144), and it is not recomputed after the merge (:741). The first point of a flight is null.
- Indexes on (`flight_id`, `seq`) and `city_pair`.
- Purpose: altitude and speed colouring, the time slider, and later joins to wind and weather at that time and place.

### 4.4 Inferred gap connectors: not stored in v1

- `--stitch-gaps` connectors stay an **export-time** feature. The exporter recomputes them from the sampled points using `--stitch-gap-min`, exactly as today (:1583, :1667).
- `--stitch-gap-min` also sets the gap that POS enrichment fills (:1800), so it is a **build-time** setting too. The build value is recorded in `meta.argv`, and the exporter defaults to it.
- The viewer gets gap information from `flights.has_gaps` and `gap_duration_min`.

### 4.5 `diagnostics` (non-spatial)

- The same columns as `<output>.diagnostics.csv` (`DiagnosticsRecorder`, :1324), one row per CSV row, plus `flight_id`.
- `flight_id` is filled for the final kept row of each flight **and** for each `kept_joined` fragment absorbed into it (:1265). It is null for dropped segments.
- The CSV is still written as well.

### 4.6 Altitude colour mode

- `--color-mode altitude` is **not** stored as geometry. The GeoPackage holds one line per flight whatever the colour mode.
- The KML exporter keeps today's order: **sample first** (the same `sample_points` logic as :1643, applied to `tracks_full`), **then** split the sampled points into the 24 altitude bands (:1660). Reversing the order would change the vertices.
- This removes the need for a separate `_altcolor` data file.

## 5. company_route_extractor: schema `company_routes` v1

### 5.1 `company_routes`: one row per final route (LineString, 2D)

Drop the fixed 35,000 ft Z. The route's altitude is unknown, and a fake value gets in the way in 3D.

| Column | Type | Notes |
|---|---|---|
| `route_key` | TEXT, unique | **Content-based and stable across reruns.** The first 12 hex characters of `sha1("{origin}|{dest}|{is_manual}|{WPT1,WPT2,…}")`. Display names are renumbered as OFPs are added (`aggregator.py`:83-100) and can collide, so they can't be the key. |
| `display_name` | TEXT | Today's label (`KEWRKAVL01`, `…_v2`, `MAN1`). Not guaranteed unique or stable. |
| `airline` | TEXT | `--airline`, otherwise the input folder name (`cli.py`:55) |
| `fms_route_name`, `route_name_label` | TEXT | |
| `origin`, `dest`, `city_pair`, `group_key` | TEXT | |
| `is_manual` | INTEGER | |
| `variant` | INTEGER | 1 for the primary route, 2+ for `_v2` and dated variants |
| `n_waypoints` | INTEGER | |
| `first_seen`, `last_seen` | DATETIME | **Keep `ofp_timestamp`.** It is dropped today. |
| `n_source_files` | INTEGER | |
| `route_string` | TEXT, nullable | AAY: take `<RouteDescription>` from the OFP as written. Other formats: rebuild `WPT AWY WPT …` from waypoints plus airways, using `DCT` where no airway is given. |
| `sid`, `star` | TEXT, nullable | If the OFP names them |

Indexes: `city_pair`, `group_key`, `origin`, `dest`.

### 5.2 `company_route_waypoints` (Point)

- Columns: `route_key`, `city_pair`, `group_key`, `seq`, `name`, `airway_in` (the airway used to reach this fix, null for DCT), `icao_region`, `msa_ft`. Indexes on (`route_key`, `seq`) and `city_pair`.
- **Parser work needed:** AAY OFPs already carry per-waypoint `<Airway>`, `sequenceId`, `MinimumSafeAltitude` and `countryICAOCode`, but `Waypoint` currently keeps only name, lat and lon. Add these as optional fields:
  - `countryICAOCode` is really an ICAO region code (for example `K7`), so store it as `icao_region`.
  - `MinimumSafeAltitude` is in hundreds of feet; multiply by 100 for `msa_ft`.
  - The fixtures in `tests/fixtures/aay/` lack these elements, so add a fixture from `input/aay_ofp_samples/`.
  - VOI may not carry them; leave them null.
- These waypoint names are what the viewer labels, so they must be kept even though flown routes don't need them.

### 5.3 `company_route_sources` (non-spatial)

Columns: `route_key`, `source_file`, `ofp_timestamp`. This lets the viewer show which OFPs support each route variant.

## 6. KML from GeoPackage

- Add a `gpkg_to_kml` exporter to each tool. It reads the tool's own GeoPackage and writes KML. The **current KML writer is kept until the exporter's output matches it**; after that, the exporter replaces it.
- **Flown routes:**
  - Group folder → `ORIG-DEST` folder → placemarks sorted by `takeoff_time`.
  - Same description text, same `when`/`gx:coord` (Z in metres).
  - `--sample`, `--color-mode`, `--stitch-gaps` and the style flags are applied at export time.
  - Per-flight style comes from `flights.style_*` unless overridden.
  - Per-source runs (`--integrate-pos`) come from `track_points.source`.
- **Company routes:**
  - Airport → `ORIG-DEST` → route folder → LineString plus a `WAYPOINTS` subfolder; manual routes in a hidden `MAN` folder.
  - The `--altitude-ft` Z is applied at export only.
- **Acceptance:** for a fixture input, KML written directly and KML via the GeoPackage give the same:
  - folder tree;
  - placemark count per folder;
  - coordinates (to 1e-6°);
  - `when` instants (to 1 ms);
  - description text;
  - line styles.

  Whitespace and attribute order may differ.
- Run the comparison in each mode: default, `--color-mode altitude`, `--stitch-gaps`, `--integrate-pos`, and `--group origin`.

## 7. Delivery order (per repo)

1. Vendor the GeoPackage writer module and add unit tests: header/WKB round-trip, R-tree exists, `meta` rows exist, and the file opens with `sqlite3` and `ogrinfo` when GDAL is installed. Skip the `ogrinfo` check otherwise.
2. Add the output selection from §12 (`--format`, default `both`). It writes the GeoPackage alongside today's KML, from the same in-memory model. No change to KML output.
3. Capture the fields that are currently dropped:
   - **Flown:** recomputed `gs_kt`, `vs_fpm`, `track_deg`, per-flight style, and diagnostics linkage.
   - **Company:** airway, sequence, MSA, ICAO region, `RouteDescription` and OFP timestamp.
4. Add `gpkg_to_kml` and the equivalence test from §6.
5. Switch the default KML path to go through the GeoPackage. The direct writer stays behind `--legacy-kml` for one release.
6. Pass `--format`, `--gpkg-points` and `--airline` through `route_builder/server.py` (flown only).

## 8. Acceptance (POE, Jul–Sep 2026 as the reference run)

Reference command: today's POE invocation with default flags (`--preset balanced --sample 2.0`, colour mode default, no `--stitch-gaps`, no `--integrate-pos`), plus `--format both`.

- `flights` row count = number of flights written to the KML. In this default mode, that is one placemark per flight.
- `flights` row count = number of diagnostics rows with a `final` phase and a `kept_*` disposition, **minus** the absorbed `kept_joined` fragment rows (:1265). Equivalently: the number of distinct non-null `flight_id` values in `diagnostics`.
- `SELECT DISTINCT group_key, city_pair` matches the KML folder tree exactly.
- Writing the GeoPackage adds **≤ 25 %** to total run time with `--gpkg-points` off.
- The QGIS spike project loads the new file after the spike is updated to the new names (`takeoff_time`/`landing_time`, with `--gpkg-points` on). The spike code is throwaway, so updating it is expected.

### 8.1 Second reference run: VIV (findings from §11)

Use the VIV inputs that produced `viv_routes_2026_oct.kml`: the per-tail KML in `/Volumes/jy-ext/kml/kml-raw/viv_2026_oct/`, run with `--integrate-pos --stitch-gaps`, and the VIV OFPs for `company_route_extractor`.

**adsb_historical_routes:**
- `flights` has one row per flight (about 6,835 for that window), not one per placemark.
- `suspect_od` is present. The MMTO→MMSM-type mislabels are either fixed at the root or flagged, and the diagnostics count them.
- `tracks_full` is present.
- `meta` `date_from`/`date_to` are the actual first and last flight dates. The file name uses them.
- `--format kml` reproduces today's KML exactly (§6 equivalence).

**company_route_extractor:**
- Every route ends at its destination; trailing alternate waypoints are counted in a diagnostic.
- `first_seen`/`last_seen` are filled.
- `company_route_sources` maps every OFP to the route it produced.

## 9. Left to the builder

- Internal layout of the shared writer module, and whether it is vendored or packaged.
- Exact `vs_fpm`/`track_deg` smoothing. A simple difference between consecutive points is acceptable for v1.
- How `route_string` is rebuilt when airway data is partial. Use `DCT` for any segment with no airway.
- Batching and transaction strategy, as long as the run-time target in §8 is met.

## 10. Open items for the owner

- ~~**Callsign / flight number on flown routes.**~~ **Decided 2026-10-27:** the ADS-B trace data has no flight number. Linking will be a **separate join step** after the QGIS viewer is built, not part of this builder; see `docs/ROADMAP.md`. `callsign` stays reserved and null.
- ~~Whether the AAY parser's extra fields (`SegmentWind`, planned flight level per waypoint) should be captured now.~~ **Decided 2026-10-28:** not in this change. Plan wind and flight level per waypoint come from the OFP/flight-record join in phase 2 (`direct_detect`).
- Parsers for the `amx`, `cmp` and `jat` OFP folders are out of scope here.

## 11. Addendum, 2026-10-28: findings from the VIV data, and the agreed data flow

The data flow and who does what across all tools are in [`docs/geopackage_build_instructions.md`](../geopackage_build_instructions.md). Hand both documents to these projects.

### 11.1 adsb_historical_routes (from `viv_routes_2026_oct.kml`)

- **Placemarks are not flights.**
  - With `--integrate-pos --stitch-gaps`, the VIV file holds 15,538 placemarks but only **6,835 flights**:
    - 10,634 ADS-B runs;
    - 3,556 POS runs, each repeating the flight's description;
    - 1,348 grey "Inferred gap connector" placemarks with no flight identity.
  - The GeoPackage must have **one `flights` row per flight**, with the per-source runs only in `track_points.source` and `tracks_full`, as §4 already intends. Gap connectors stay export-time only (§4.4).
  - **Acceptance addition:** in that mode, the `flights` count equals the number of distinct flights, not the number of placemarks.
- **Origin/destination labels that can't be right.**
  - About 185 flights (2.7%) fly more than 2.5× the great-circle distance between their labelled airports.
  - Example: MMTO→MMSM, about 50 km apart, has 131 flights of 145–594 min and up to 3,627 km.
  - **Fix the airport-assignment root cause** in the builder.
  - Also add `od_gc_km`, `od_distance_ratio` (flown distance ÷ great circle) and a `suspect_od` flag (ratio > 2.5, configurable) to `flights`, and count them in `diagnostics`.
  - Downstream tools exclude `suspect_od` flights from statistics.
- **Date range and naming.** The file named `…_2026_oct` covers 2026-09-30 → 10-19 (UTC takeoff dates). Name outputs by their actual first and last flight dates, and record those in `meta` as `date_from`/`date_to`.
- **`tracks_full` is required, not optional, for the downstream analysis.** 2-minute samples are too coarse for SID/STAR adherence and direct detection.

### 11.2 company_route_extractor

- **Variant dates.** The date suffix on variant names (e.g. `MMMYKAUS01_v2_2026-08-26`) is the variant's earliest OFP timestamp, i.e. its `first_seen`.
  - Keep `first_seen` and `last_seen` as columns (§5.1).
  - Downstream tools compare a flight only with the variants **in force on its date**: `first_seen ≤ flight date ≤ last_seen`, with a configurable grace period after `last_seen`. Some city pairs have 1–2 options, others 7–8 or more, and all are plotted in the viewer to show that variability.
- **Routes must end at the destination.** The owner flagged that an OFP navlog can continue past the destination into the **alternate** routing, which can look like a continuation of the route.
  - The extractor already ignores `<AlternateRoutes>` in ARINC 633 OFPs.
  - Add a check that each route's last waypoint is the destination, or within a set tolerance of it. Trailing waypoints beyond it are removed and counted in a diagnostic.
  - Do the same for any other OFP format.
- **`company_route_sources` (§5.3) is essential, not optional.** It links each OFP file to the variant it produced. Combined with `direct_detect`'s exact flight↔OFP match, that tells us **which variant each flight actually filed**.

## 12. Output selection (both tools), added 2026-10-28

The owner wants KML and GeoPackage built by parameter, so either or both can be produced.

| Flag | Values | Default | Effect |
|---|---|---|---|
| `--format` | `both`, `gpkg`, `kml` | `both` | `both` writes `<output>.gpkg` and `<output>.kml`. `gpkg` writes only the GeoPackage, for the fastest runs feeding QGIS, `direct_detect` and the viewer. `kml` writes only KML, exactly as today, for Google Earth users. |
| `--gpkg-points` | flag | off | Flown only. Also writes `track_points` (§4.3). It is forced on by `--integrate-pos`. |
| `--legacy-kml` | flag | off | One release only. KML comes from the old direct writer instead of via the GeoPackage (§7 step 5). |

**Rules:**
- **The in-memory model is built once**, whatever the format. Only the writers differ.
- **With `--format kml`,** the GeoPackage path is still used internally once §7 step 5 lands (KML via GeoPackage), but the GeoPackage is written to a temporary file and deleted. Only the KML is kept.
- **The `--output` naming rule is unchanged:** `<output>.kml` and `<output>.gpkg` sit side by side. The diagnostics CSV is always written.
- **Standalone export:** `gpkg_to_kml <file.gpkg> -o <file.kml> [style/sample flags]` (§6) rebuilds KML from an existing GeoPackage without re-running the builder. This is the long-term path: build GeoPackage once, export KML when someone needs it.
- **`--gpkg`** from earlier drafts is accepted as an alias for `--format both` for one release, then removed.
- **The GUI** (`route_builder/server.py`) exposes `--format` as a three-way choice.
