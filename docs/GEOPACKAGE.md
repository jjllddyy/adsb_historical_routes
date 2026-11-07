# GeoPackage output (`flown_routes` v3)

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
| `--qgz` | off | Also write `<output base>.qgz`, the QGIS project, with `~/Git/qgis` `prepare` (§6). Needs `--navdb`; not allowed with `--format kml`. |
| `--navdb`, `--company`, `--qgis-repo` | none, none, `$QGIS_REPO` or `~/Git/qgis` | The project's chart-layer navdb (required with `--qgz`, checked before the build), an optional `company_routes` GeoPackage, and the qgis checkout. |
| `--takeoff-from`, `--takeoff-to` | none | Keep only flights whose UTC takeoff date is in the range (inclusive, `YYYY-MM-DD`). Daily KML files dated more than one day outside it are not read. Excluded flights stay in the diagnostics as `dropped_date_range`. |
| `--airports A.csv [B.csv ...]` | | One or more airport files; a code in a later file replaces the same code in an earlier one (v3). |
| Endpoint evidence | all off | `--acars-identity`, `--pos-extend-ends`, `--pos-only-flights`, `--pos-fleets`, `--rotation-endpoints`, `--planned-legs`, `--recover-dropped` and their limits: §7. With all of them off the flights are 1.4.0's. |

- **Naming:** `{from}` and `{to}` in `--output` become the first and last flight dates (UTC takeoff).
  - `--output` may end in `.gpkg` to name the GeoPackage directly; the KML and CSV take the same stem (`x.gpkg`, `x.kml`, `x.kml.diagnostics.csv`).
  - The same dates go into `meta.date_from` and `meta.date_to`.
  - The diagnostics CSV is always written, as `<output>.kml.diagnostics.csv`.
- **Rebuilding KML without re-running the builder:**

  ```bash
  python3 gpkg_to_kml.py viv_flown_2026-09-30_2026-10-19.gpkg -o viv.kml \
      [--sample 1] [--color-mode altitude] [--group origin] [--stitch-gaps true] ...
  ```

  Every option defaults to the value the file was built with (`meta.kml_params`).
- **Summary of a finished file:** `python3 gpkg_summary.py x.gpkg -o x_summary.txt` (counts, dates, sources, rescue methods, suspect O/D, top city pairs).
- **The GUI** (`route_builder/`) has Output format, Airline code and GeoPackage track points in its Output section.

## 2. Schema

All geometry is EPSG:4326. Z is in **metres**; `*_ft` attribute columns are in feet. Times are UTC
`DATETIME` strings (`2026-10-19T14:34:55.437Z`). `city_pair`, `group_key`, `origin` and `dest` are
copied onto every child table, so no filter ever needs a join.

### `flights`: one row per kept flight (MultiLineStringZ, the sampled display line)

| Column | Notes |
|---|---|
| `flight_id` | `{aircraft_id}_{takeoff:%Y%m%dT%H%M%SZ}`; unique. A repeat (same aircraft, same takeoff second) gets `_2`. |
| `airline`, `aircraft_id`, `registration` | `callsign` is reserved and stays null (spec §10). **v3:** `aircraft_id` (the ICAO hex) is null on POS-only flights of registrations not in the tail map (`--pos-fleets all`, for example CC-, HC-, N- aircraft); `registration` is then set and `flight_id` starts with the normalized registration (`CCBMD_…`). |
| `origin`, `dest`, `city_pair`, `group_key` | `group_key` is the KML top folder: the destination, or the origin with `--group origin`. |
| `takeoff_time`, `landing_time`, `flight_date` | |
| `duration_min`, `distance_km`, `max_alt_ft` | `distance_km` is the flown distance along every point. |
| `n_points_full`, `n_points_display` | |
| `has_gaps`, `gap_duration_min` | Measured: point-to-point gaps longer than `--stitch-gap-min` in the full track. |
| `disposition`, `rescue_method`, `joined_segment_count` | `kept_complete` / `kept_joined` / `kept_rescued`; v3 adds `kept_recovered` (a segment 1.4.0 dropped, completed by the new evidence, §7.1 F9) and `pos_only` (§7.1 F3). `rescue_method` keeps its 1.4.0 meaning (the 1.4.0 rescue that ran) and is null when none was used; the `*_source` columns give the final answer. |
| `sources` | For example `ADSB,FIREHOSE`. |
| `source_file`, `preset` | |
| `style_color`, `style_width`, `style_opacity` | The input KML's style, so the KML can be reproduced. The viewer ignores them. |
| `od_gc_km`, `od_distance_ratio`, `suspect_od` | Great circle between the labelled airports; flown ÷ great circle; ratio > `--suspect-od-ratio` (§3). |
| **v3:** `flight_number`, `flight_number_source`, `flight_number_conflict` | The ACARS `FI` number (`acars_fi`), FlightAware's (`flightaware`) or the planned leg's (`planned:<source>`); null without any. `flight_number_conflict = 1`: reports with two different numbers fell inside the flight (majority kept). |
| **v3:** `orig_source`, `dest_source` | Where each end came from (§7.2). |
| **v3:** `orig_free`, `dest_free`, `orig_free_source`, `dest_free_source` | The end the builder gives **without** planned legs, and its source. Equal to `origin` / `dest` unless a planned leg changed the end. |
| **v3:** `orig_candidates`, `dest_candidates` | `\|`-separated airports a route-time / geometry rescue accepted for that end, best first; null when no rescue ran on it. |
| **v3:** `endpoint_confidence` | `high` / `medium` / `low`: the lower of the two ends (§7.3). |
| **v3:** `endpoint_conflict` | 1 when rotation or the planned leg contradicts an *observed* end (a probable missing leg, tail swap or diversion). Observed ends are never changed. Also 1 on a 1.4.0 flight whose new evidence would have made it invalid; it keeps its 1.4.0 ends. |
| **v3:** `planned_leg_key`, `n_pos_fixes` | The matched planned leg; the number of POS points in the track. |

Indexes: `flight_id` (unique), `city_pair`, `group_key`, `dest`, `origin`, `flight_date`,
`registration`, `aircraft_id`.

### Other tables

- **`tracks_full`** (MultiLineStringZM): every point of each flight, after POS/firehose enrichment.
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
  - **v3:** `phase = evidence` rows, one per endpoint F2, F4 or F5 changed: `evidence_feature`, `evidence_source`, `endpoint` (`origin` / `dest`), `before_code`, `after_code`. POS-only flights and dropped POS sessions have `phase = final` rows (`pos_only` / `dropped_pos_only`, `segment_idx` -1).
- **`meta`**: key/value rows.
  - `schema_name` (`flown_routes`), `schema_version` (`3`), `tool`, `tool_version`, `created_utc`, `airline`, `date_from`, `date_to`, `argv` (JSON), `source_count`.
  - Also `preset`, `group_by`, `sample_minutes`, `stitch_gap_min`, `integrate_pos`, `track_points`, `kml_params` (JSON), `flight_count`, `suspect_od_ratio` and `suspect_od_count`.
  - **v3:** `evidence_options` (JSON, always). When the evidence stage ran: `planned_legs` (files, rows, date range), `planned_leg_airports_missing`, `flight_number_prefixes` (ACARS prefix → plan carrier, learned from rule-1 matches), `endpoint_sources`, `endpoint_confidence`, `flight_number_sources`, `flights_by_sources_disposition`, `endpoint_conflict_count`, `evidence_report` (the whole report as JSON) and `pos_read_seconds`.

**Date line (v2).** `flights` and `tracks_full` are MULTILINESTRINGs. A line is cut where two
consecutive vertices are more than 180° apart in longitude: one part ends at ±180 and the next
starts at ∓180, at the latitude (and Z, M) interpolated linearly in lon/lat. A line that does not
cross is a single part with exactly its coordinates; every longitude stays within -180..180.
This keeps QGIS (EPSG:3857 wraps longitudes) from drawing a crossing flight across the whole map.
`track_points` is unchanged. The rule is the same as `company_route_extractor`'s
`company_routes` v2. `gpkg_to_kml.py` reads v1 and v2; it rejoins the parts without the ±180
vertices, so the KML is unchanged (byte-identical). Measured on 2026-10-30: 0 of 7,125 VIV flights
and 0 of 51,345 LAN (LATAM Brasil) flights cross; the rule matters for VOI and long-haul fleets. The LAN v3 file (2026-11-03) has 122 crossing flights, all LATAM Chile POS-only flights over the Pacific.

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
    - `add_feature_table(name, "LINESTRING"|"MULTILINESTRING"|"POINT", [(col, type)], z=, m=)`;
    - `add_attribute_table`;
    - `insert_features(name, [(blob, bbox, values)])` with `encode_linestring` /
      `encode_multilinestring` / `encode_point`;
    - `insert_rows`;
    - `add_index(name, cols, unique=)`;
    - `close()`, which writes the file atomically from `<path>.part`.
  - Use `format_datetime` for `DATETIME` columns; GDAL warns on any other format.
  - The routes are 2D: call `encode_linestring(coords)` without `has_z`.
- **`qgis`** (viewer and spike):
  - **v2:** `flights` is MULTILINESTRING Z and `tracks_full` MULTILINESTRING ZM, split at the
    date line. `prepare` must accept `schema_version` 2 (it checks "1" exactly today) and read
    multi-part lines; see `docs/handoff/qgis_flown_v2.md`.
  - The new file uses `takeoff_time`/`landing_time`.
  - `tracks_full` is always present; `track_points` is present when `meta.track_points = 1`.
  - Exclude `suspect_od = 1` from statistics.
  - `flights.has_gaps` is now measured, not the always-false builder flag.
- **`direct_detect`:**
  - **v2:** `tracks_full` is MULTILINESTRING ZM; see `docs/handoff/direct_detect_flown_v2.md`.
  - `tracks_full` M values are epoch seconds.
  - `flight_id` is stable across reruns of the same inputs.
  - The diagnostics `flight_id` links absorbed fragments to their flight.

## 6. QGIS project (`.qgz`)

The GeoPackage holds data only. The map (navdb chart layers, basemaps, styles, the Route Explorer
configuration) is a QGIS project built by `~/Git/qgis` `prepare`, which owns all styling. This tool
only runs `prepare`; the project links to the GeoPackages and copies no data (about 70–90 KB).

```bash
# with the build
python3 adsb_historical_routes.py ... --format gpkg --output out/lan.gpkg \
    --qgz --navdb ~/Git/qgis/input/navdb_2610.gpkg [--company company_routes.gpkg]
# for an existing GeoPackage
python3 gpkg_to_qgz.py out/lan.gpkg --navdb ~/Git/qgis/input/navdb_2610.gpkg \
    [--company company_routes.gpkg] [-o out/lan.qgz] [--qgis-repo ~/Git/qgis]
```

- **The call** (the interface agreed in the qgis handoff §3.7), run from the qgis checkout:
  `bash -c 'source scripts/env.sh && exec "$QGIS_PY" -m prepare "$@"' prepare --airline LAN
  --navdb <abs> --flown <abs> [--company <abs>] --out <abs folder> --project-name <stem>`.
- **Success** is exit 0 and the `.qgz` existing: `Wrote N bytes to <qgz> (QGIS project; links to …)`.
- **Failure** prints `error: .qgz not built: qgis prepare exited N`, the last five stderr lines and
  a pointer to `~/Git/kml/company_route_extractor/docs/handoff/qgis_prepare_partial_inputs.md`, and
  exits 1. The GeoPackage is already written and stays.
- **Today's `prepare` fails** (2026-10-30): it has no `--project-name`, requires `--company`, and
  accepts flown `schema_version` "1" only. Until the qgis handoff lands, add the layers to QGIS
  directly (*Layer → Add Layer → Add Vector Layer…*).
- `--navdb` (and `--company`, when given) must exist; this is checked before the long build starts.

## 7. Endpoint evidence (v3)

The core is unchanged: each aircraft's (ICAO hex) 24-hour ADS-B stream is cut into flights
between airports by position, elevation and route time (1.4.0). The evidence stage then runs
after the joins, per aircraft (hex, else registration), using whatever inputs exist. Every switch
is optional and off by default; with all of them off the output is 1.4.0's (proven by
`tests/test_v3_equivalence.py` against digests of the 1.4.0 output). **No flight needs a
flight number or a planned leg**: their absence never drops, splits or downgrades a flight.

### 7.1 Switches

| Flag | Default | What it does |
|---|---|---|
| `--acars-identity true` (F1) | off | Each flight gets the `FI` flight number of the POS reports inside its time window (same aircraft); the most frequent wins, a second number sets `flight_number_conflict`. Needs `--integrate-pos true --pos-folder`. |
| `--pos-extend-ends true` (F2) | off | An end ADS-B did not see at an airport is carried on with POS fixes of the same aircraft, before the first or after the last ADS-B fix. Each step must be ≤ `--pos-extend-max-gap-min` (60) and ≤ 1,300 km/h; the walk stops at a ground stop (a gap over 20 min crossed slower than 150 km/h), at the neighbouring ADS-B segment, at a change of `FI` number (when the flight has one), and at the first fix seen at an airport. The end is then re-detected: `pos_observed` when that fix is within the airport radius and below the arrival / takeoff height (the same test as ADS-B detection); otherwise the 1.4.0 rescue re-runs on the longer track. |
| `--pos-only-flights true` (F3) | off | POS fixes no ADS-B segment covers are cut into sessions (at a gap over `--pos-extend-max-gap-min`, a ground stop or an `FI` change) and each becomes a flight: `sources = POS`, `disposition = pos_only`. Fixes are never interpolated; `n_points_full` is the number of reports. |
| `--pos-fleets mapped\|all` | `mapped` | `all` also builds POS-only flights for registrations not in the tail map, and reads every POS file (1.4.0 skips unmapped files by name). |
| `--rotation-endpoints true` (F4) | off | An end not observed takes the neighbouring flight's observed end (next flight's origin for a destination, previous flight's destination for an origin) when the ground gap is 0 to `--rotation-max-gap-h` (18) hours and it differs from the flight's own observed other end. Ends with no other evidence may then chain from an inferred neighbour (`rotation_chained`). |
| `--planned-legs A.csv [B.csv ...]` (F5) | none | Planned legs (OFP or schedule, columns below). Rule 1: same tail, `flight_digits` = the ACARS number, takeoff in `[sched_dep − 60 min, sched_dep + 8 h]`. Rule 2 (flights without a number): same tail, the window, and one observed end equal to the leg's. A match must be unique both ways (one candidate leg for the flight, no other flight with that leg as a candidate); `ambiguous = 1` rows never match. A matched leg fills ends that are not observed, fills `flight_number` when F1 found none, and writes `planned_leg_key`; it never overrides an observed end (a disagreement sets `endpoint_conflict`). Leg airports missing from the airport list are listed in the build log. |
| `--flightaware-identity true` | off | For a firehose folder of FlightAware reports (`flight_id`, `flight`, `orig`, `dest`, `reg` per fix): each flight takes the FlightAware flight with the most fixes inside its own time window (same hex). Its first valid `orig` and last valid `dest` (FA updates the destination on a diversion) fill ends that are not observed; placeholders such as `L 15.80 -97.20` and airports missing from the list are skipped. Its number fills `flight_number` when ACARS gave none (`flight_number_source = flightaware`). A second FA flight in the window is noted; a disagreement with an observed end sets `endpoint_conflict`. Needs `--integrate-pos true --firehose-folder`. |
| `--recover-dropped true` (F9) | off | Segments 1.4.0 drops (`no_airport_match`, `invalid`) go through F2, F3's neighbours, F4 and F5 too; one that then has two airports and passes the 1.4.0 validity test is kept as `kept_recovered`, the rest stay dropped with their reason. |

Planned-legs CSV (`tail` canonical: upper case, no hyphen; prefixes never matter, legs match on
`flight_digits`):

```
leg_key,source,carrier_icao,flight_number,flight_digits,tail,orig,dest,sched_dep_utc,ofp_date,ambiguous
2026-07-14_TAM3036_SBSP-SBCF_b2,ofp,TAM,TAM3036,3036,PRMAG,SBSP,SBCF,2026-07-14T11:00:00Z,2026-07-14,0
```

### 7.2 `orig_source` / `dest_source` and the evidence order

The first that applies wins (request F8); every change is a `diagnostics` `evidence` row.

| Order | Value | Meaning |
|---|---|---|
| 1 | `observed` | Seen at the airport by ADS-B (1.4.0 detection). |
| 2 | `pos_observed` | Seen at the airport by a POS fix (F2 extension, or the end of a POS-only flight). |
| 2a | `flightaware` | FlightAware's `orig` / `dest` for the FA flight whose fixes are inside this flight (`--flightaware-identity`). Comes from the filed flight plan, so it is excluded from `*_free`. |
| 3 | `planned:<source>:fn` | Planned leg, rule 1 (flight number, tail, time). |
| 4 | `rotation` | The aircraft's neighbouring flight's observed end (F4). |
| 5 | `planned:<source>:tail` | Planned leg, rule 2 (tail, time, one observed end). |
| 6 | `route_time`, `geometry`, `bearing_join` | The 1.4.0 rescue (route time + bearing + corridor; corridor geometry; an end reached across a Case-5 bearing join). |
| 6 | `rotation_chained` | Rotation from a neighbour whose end is itself inferred; only for ends nothing else filled. |
| – | `unresolved` | Nothing (only on dropped POS sessions; kept flights always have two ends). |

`orig_free` / `dest_free` are the same order without rows 2a, 3 and 5. Use them, not `origin` /
`dest`, whenever a planned-leg end would be counted as independent agreement with that same plan.

### 7.3 `endpoint_confidence`

Per end: `high` = `observed`, `flightaware` or `planned:*:fn`; `medium` = `pos_observed`, `rotation` or
`planned:*:tail`; `low` = row 6 sources, and any `medium` end whose rescue had more than one candidate
airport (`*_candidates`); a `high` end stays high. The flight's value is the lower of its two ends.

### 7.4 Report

With any switch on, the build writes `<gpkg stem>_evidence_report.md` next to the GeoPackage:
flights by sources and disposition, endpoints by source and confidence, flight-number share and
the learned prefix table, POS-only flights by registration prefix and month, F2 extensions and km
gained, F9 recoveries per reason, and the **agreement table**: for flights matched to a planned
leg on rule 1, how often `orig_free` / `dest_free` equal the leg, by the evidence that produced
them (the accuracy of the plan-free logic), next to the same flights as 1.4.0 labelled them.
With `--flightaware-identity` the same tables are written against FlightAware's airports.
`gpkg_summary.py` prints the same sections from the file.
