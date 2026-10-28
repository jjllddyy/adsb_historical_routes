# GeoPackage build instructions: data flow, who does what, building from scratch

- **Status:** 2026-10-28. This describes the agreed target flow. Each step is marked **available** (works today) or **planned** (specified, not built yet).
- **Audience:** anyone rebuilding the route-analysis data for an airline, and the maintainers of each tool in the chain.

## 1. Principles

1. **Every dataset is built once, by one tool, and read by everyone else.** No tool re-parses another tool's raw input.
2. **Separate files by what they depend on:**
   - Nav data depends only on the **AIRAC cycle** and is shared by every airline.
   - Flown routes and company routes depend on the **airline and period**.
   - Analysis outputs depend on both, and store **keys and small derived geometry**, never copies of the inputs.
3. **QGIS reads many GeoPackages in one project** (§5). Nothing needs merging into one big file to be viewed together.
4. **Only GeoPackages travel between tools.** KML is an *export* for Google Earth, generated from the GeoPackage, and never an input to another tool. Once each tool writes GeoPackage, this is true end to end. The legacy text formats from `navdb_extractor` exist only for tools not yet migrated.

## 2. The chain

```
                      once per AIRAC cycle (all airlines)
Navigraph DFD navdb.s3db ──► [1] navdb_extractor ──► navdb_<cycle>.gpkg ─────────────┐
                                                                                      │
                      per airline + period                                            │
raw ADS-B (per-tail KML, positions) ─► [2] adsb_historical_routes ─► <al>_flown_<from>_<to>.gpkg ─┤
authoritative OFPs (ARINC 633)      ─► [3] company_route_extractor ─► <al>_company_routes_<from>_<to>.gpkg ─┤
                                                                                      │
                      per airline + period (derived)                                  ▼
                                    [4] qgis prepare ─► analysis.gpkg + <al>_<from>_<to>.qgz
                                    [5] direct_detect ─► matched_flights + direct_patterns.gpkg
                                                                                      │
                                                                                      ▼
                                              QGIS opens the .qgz (references all files)
```

| # | Tool (repo) | Input | Output | Cadence | Status |
|---|---|---|---|---|---|
| 1 | `navdb_extractor` (`~/Git/conversion_tools/navdb_extractor`) | Navigraph DFD v1 `navdb.s3db` | `navdb_<AIRAC>.gpkg` (13 layers), optional FIR GeoJSON, airports CSV, legacy text | Once per AIRAC cycle | **available** (v0.1.0) |
| 2 | `adsb_historical_routes` (`~/Git/kml/adsb_historical_routes`) | Raw per-tail ADS-B KML (+ POS), airports/routes CSV from Cirium | `flown_routes` v1 GeoPackage; KML exported from it | Per airline and period | KML **available**; GeoPackage **planned** (spec §4) |
| 3 | `company_route_extractor` (`~/Git/kml/company_route_extractor`) | Authoritative OFPs (e.g. `/Volumes/jy-ext/data_viv/ofp_all_auth`) | `company_routes` v1 GeoPackage; KML exported from it | Per airline, re-run as OFPs accumulate | KML **available**; GeoPackage **planned** (spec §5) |
| 4 | `qgis` `prepare` (this repo) | Outputs of 1–3 | `analysis.gpkg` (typical tracks, flight metrics, procedure usage) and the `.qgz` project | Per airline and period | **planned** (viewer design) |
| 5 | `direct_detect` (`~/Git/analytics_tools/direct_detect`) | Outputs of 1–3, plus `adsb_pos`, OFPs and flight records | `matched_flights`, `direct_patterns` GeoPackage, `catalog.json` | Per airline/scope and period | **planned** (its design) |

**Specs for each step:**
- Step 1: [`docs/specs/2026-10-27-navdb-dfd-sqlite-input-spec.md`](specs/2026-10-27-navdb-dfd-sqlite-input-spec.md).
- Steps 2 and 3: [`docs/specs/2026-10-27-route-gpkg-output-spec.md`](specs/2026-10-27-route-gpkg-output-spec.md), including its 2026-10-28 addendum. **This is the document to hand to those two projects.**
- Step 4: [`docs/superpowers/specs/2026-10-28-route-explorer-viewer-design.md`](superpowers/specs/2026-10-28-route-explorer-viewer-design.md).
- Step 5: `~/Git/analytics_tools/direct_detect/docs/specs/2026-10-28-direct-detect-design.md`.

## 3. Division of effort: what each step does and must not do

| Concern | Owner | Not done by |
|---|---|---|
| Parsing ARINC 424 / DFD, airway segmentation, procedure legs, FIR rings | 1 `navdb_extractor` | Anyone else. Nobody reads `navdb.s3db` or the legacy `.txt` once migrated. |
| Segmenting ADS-B into flights, joining split segments, merging ADS-B + POS, airport assignment, **checking origin/destination labels** | 2 `adsb_historical_routes` | `qgis` prepare (it only flags what it finds, and reports it upstream) |
| Parsing OFPs into company routes and variants, `first_seen`/`last_seen`, **dropping alternate routing** | 3 `company_route_extractor` | `qgis`, `direct_detect` |
| Exact flight ↔ OFP ↔ flight-record matching | 5 `direct_detect` (`matched_flights`) | `qgis` (it reads `matched_flights` in phase 2) |
| Typical track, spread, SID/STAR usage for the viewer | 4 `qgis` prepare | the builders (spec §2 non-goal) |
| Directs, start/rejoin, FIR span, zones | 5 `direct_detect` | `qgis` (it only renders them) |
| Styling, themes, panel, basemaps | 4 `qgis` | everyone else; `direct_detect` ships QML styles for its own layers |

## 4. Where files live, and how much is shared

**Proposed storage root:** `/Volumes/jy-ext/geodata/`. Keep it on a local disk; WAL journaling is unsafe on network shares.

```
geodata/
  navdb/
    navdb_2610.gpkg                     557 MB, world, all airlines
    navdb_2611.gpkg                     next cycle when it arrives (keep every cycle)
  airlines/
    viv/
      flown/viv_flown_2026-09-30_2026-10-19.gpkg
      company/viv_company_routes_2026-07-22_2026-10-28.gpkg
      qgis/2026-09-30_2026-10-19/analysis.gpkg
      qgis/2026-09-30_2026-10-19/viv_2026-09-30_2026-10-19.qgz
      direct_detect/<run_id>/direct_patterns.gpkg
    voi/ …
    amx/ …
```

**Redundancy rules:**
- **navdb:** one file per cycle, shared by every airline and tool. It is **never copied** into an airline file: at 557 MB, that would duplicate it for every airline and period.
  - For a small portable bundle, `navdb_extractor --bbox … --tag nam` makes a regional cut.
- **Flown routes:**
  - `flights` holds the 2-minute display lines and is small.
  - `tracks_full` (all points, about 10× the size) is written by default because SID/STAR checks and `direct_detect` need it.
  - `track_points` is optional (`--gpkg-points`).
- **Company routes:** one file per airline, covering all OFPs to date. Variants carry `first_seen`/`last_seen`, so periods are handled by date, not by separate files.
- **Derived files** (`analysis.gpkg`, `direct_patterns.gpkg`) store keys (`flight_id`, `route_key`, procedure ident) plus small derived geometry such as typical tracks, zones and events. They **never copy** flights or nav data.

**Alternative: one GeoPackage holding both nav data and an airline's flights.** It works, and is acceptable if a single file is ever needed, for example to send to someone. But each airline and period would carry its own 557 MB copy of the nav data, and every new cycle would mean rebuilding the airline files. **Recommendation: keep separate files**, and, if needed, add a later `prepare --bundle` that writes one self-contained GeoPackage with a regional navdb cut.

## 5. How QGIS combines several GeoPackages

- **A project (`.qgz`) can reference layers from any number of GeoPackages.** Each layer is `path/to/file.gpkg|layername=<table>`.
  - The spike already did this: `poe.gpkg` + `airways.gpkg` + `procs.gpkg`.
  - `prepare` writes the project with **relative paths** (Project → Properties → General → Save paths: *relative*). The whole `geodata/` tree can then move without breaking the project.
- **Filters (subset strings) run inside one file.** A filter on a layer can use tables in the *same* GeoPackage (e.g. "fixes that are on airways" works inside the navdb). It **cannot** reference a table in another file.
  - **So cross-file relationships are computed by `prepare` and stored** in `analysis.gpkg`, e.g. which procedures a flight flew, or a flight's spread.
  - Alternatively, the plugin turns them into value lists, e.g. `procedure IN ('ANJLL4','BRUEN2')` on the navdb `proc_paths` layer.
- **QGIS layer joins** (Layer Properties → Joins) work across files but are slow on big tables. Use them only for small lookups, e.g. showing a flight's metrics in the identify panel.
- **Virtual layers** can run SQL across files, but they are too slow for map layers. Avoid them for drawing.
- **Which cycle a project uses:**
  - The project references the navdb for its period's cycle.
  - A period that spans two cycles uses the later cycle, and flights outside it are flagged `navdb_cycle_mismatch`. Only the current cycle can be obtained, airways are stable, and procedures are flagged.
  - Once older cycles are archived, a project can load both, as separate layer groups.
- **Adding data by hand:** drag any `.gpkg` from Finder into QGIS, or use the Browser panel. Each table shows up as a layer, with the default style stored inside the file (`layer_styles`).

## 6. Building from scratch (VIV example)

Each step reads the previous step's output. Steps 2 and 3 are independent of each other.

### Step 1: nav data (once per AIRAC cycle) [available]

1. Download the Navigraph DFD for RealTraffic. The file is `navdb.s3db` (DFD v1, ARINC 424-18).
2. Run:
   ```bash
   cd ~/Git/conversion_tools/navdb_extractor
   uv run navdb_extractor --navdb <path>/navdb.s3db --out /Volumes/jy-ext/geodata/navdb --fir-geojson --airports-csv
   ```
3. **Check:** the `meta` table shows the expected AIRAC and its effective dates, and the layer counts are close to the navdb spec §11 (e.g. 382 FIR keys, 14,324 airway segments for 2610).
4. Keep every cycle's file. History can only use cycles that were saved.

### Step 2: flown routes (per airline and period) [KML available, GeoPackage planned]

1. Fetch the raw per-tail ADS-B: `adsb_kml_downloader` / `adsb_kml_fetch`. The tail→hex maps are in `~/Git/kml/adsb_kml_downloader/input/<al>_active_tails_icao.csv`.
2. Build the airports and routes CSVs from a Cirium export (`build_cirium_inputs.py`), or use the airports CSV from step 1.
3. Run:
   ```bash
   cd ~/Git/kml/adsb_historical_routes
   python3 adsb_historical_routes.py --kml-folder <raw> --airports <al>_airports.csv \
     --routes <al>_routes_time.csv --confidence permissive --corridor-cross-track-km 50 \
     --airline VIV --gpkg --output /Volumes/jy-ext/geodata/airlines/viv/flown/viv_flown_<from>_<to>.kml
   ```
   `--gpkg` is planned. The GeoPackage is written next to the KML.
4. **Check:**
   - `flights` count equals the number of distinct flights. It does **not** equal the number of KML placemarks when `--integrate-pos` or `--stitch-gaps` is on.
   - The share of `suspect_od` flights is reported and small.
   - `tracks_full` is present.

### Step 3: company routes (per airline) [KML available, GeoPackage planned]

1. Run:
   ```bash
   cd ~/Git/kml/company_route_extractor
   python3 company_route_extractor.py -i /Volumes/jy-ext/data_viv/ofp_all_auth \
     --gpkg -o /Volumes/jy-ext/geodata/airlines/viv/company/viv_company_routes_<from>_<to>.kml
   ```
   `--gpkg` is planned.
2. **Check:**
   - Every route ends at its destination; no alternate routing is included.
   - `first_seen` and `last_seen` are filled.
   - `company_route_sources` links each route to its OFPs.

### Step 4: viewer project (per airline and period) [planned]

1. Run:
   ```bash
   cd ~/Git/qgis && source spike/env.sh   # env setup moves out of spike/ in the build
   $QGIS_APP/MacOS/python -m prepare --airline VIV \
     --navdb /Volumes/jy-ext/geodata/navdb/navdb_2610.gpkg \
     --flown /Volumes/jy-ext/geodata/airlines/viv/flown/viv_flown_<from>_<to>.gpkg \
     --company /Volumes/jy-ext/geodata/airlines/viv/company/viv_company_routes_<from>_<to>.gpkg \
     --out /Volumes/jy-ext/geodata/airlines/viv/qgis/<from>_<to>
   ```
2. **Check:** the project opens in QGIS 3.44 with no missing layers, and the headless render test passes.

### Step 5: direct_detect (per airline/scope) [planned]

- See that project's README once it is built. It reads the files from steps 1–3, plus `adsb_pos`, OFPs and flight records, and writes into `airlines/<al>/direct_detect/<run_id>/`.
- The QGIS project from step 4 gains a "Patterns" group pointing at that output in phase 2.

## 7. Order of work across projects (recommended)

1. **`adsb_historical_routes` gains GeoPackage output** (spec §4 plus the addendum), including the fixes for the issues found in the VIV data. The owner is taking this on in that project.
2. **`company_route_extractor` gains GeoPackage output** (spec §5 plus the addendum).
3. **The `qgis` viewer is built on those files.** Phase 1 does **not** write its own KML importer; that would duplicate step 2's logic for merging sources and handling gaps. Until step 2 lands, viewer development uses small schema-conformant test fixtures, plus the navdb, which is already real.
4. `direct_detect` runs in parallel. Its Phase A reads `adsb_pos` and OFPs directly, and can switch to the step 2 and 3 GeoPackages when they exist.
