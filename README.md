# Flight Route Splitter (`adsb_historical_routes`)

Segments continuous ADS-B flight-tracking KML into individual, airport-to-airport flight
routes: it detects takeoffs/landings, recovers flights whose tracks are truncated or split by
coverage gaps, matches origin/destination airports, and writes a **GeoPackage** (for QGIS and
`direct_detect`) and an organized **KML** (for Google Earth, rendered from the GeoPackage), plus a
per-segment diagnostics CSV. See [docs/GEOPACKAGE.md](docs/GEOPACKAGE.md) for the GeoPackage schema.

- **Zero runtime dependencies** — Python 3.6+ standard library only.
- **Two stages**: build the airport/route reference inputs from a Cirium schedule export
  (`build_cirium_inputs.py`), then split the KML tracks (`adsb_historical_routes.py`).

---

## ⭐ Recommended starting point (run this)

If you just want good results with little tuning, use the **`permissive` preset with a tight
corridor gate**. This is the current best-validated general configuration (see
[Recommended settings by scenario](#-recommended-settings-by-scenario) for why):

```bash
python3 adsb_historical_routes.py \
    --kml-folder <folder of per-tail/per-day KML files> \
    --airports   <airline>_airports.csv \
    --routes     <airline>_routes_time.csv \
    --output     output/<airline>_routes.kml \
    --confidence permissive --corridor-cross-track-km 50
```

- `--confidence permissive` casts a wide net (recovers truncated/gap-split flights).
- `--corridor-cross-track-km 50` tightens the recovery so inferred endpoints must lie on the
  real route corridor — this is what keeps bad data (mislabeled destinations) out.
- Add styling if you like: `--width 0.8 --opacity 80 --color ff0000` (blue; see
  [Colors](#colors)). Add `--stitch-gaps true` to draw inferred coverage-gap connectors in gray.

If you don't yet have the two input CSVs, build them from a Cirium export first — see
[Building the inputs](#-building-the-inputs-build_cirium_inputspy).

---

## 🖥 GUI: ADSB Historical Route Builder

Prefer a form over a terminal? `route_builder/` ships a local, browser-based GUI that drives
this exact script — every field in it maps directly to an `adsb_historical_routes.py` flag
documented below, so the CLI reference doubles as the GUI's field reference.

> 📖 **New to the tool? Start with the [User Guide](docs/USER_GUIDE.md)** — a step-by-step
> walk-through of the GUI, every setting with recommendations, worked examples, and the
> **two-file rule** (which files go in the Airports / Routes fields — the most common mistake).

The GUI can also **build the Airports/Routes CSVs from a Cirium export** (Inputs →
**⚙ Build from Cirium…**), so the whole pipeline is terminal-free; it's optional — if the CSVs
already exist you just load them.

**Launch it:**
- Double-click `route_builder/Route Builder.command` in Finder, **or**
- `python3 route_builder/server.py --open` from a terminal (`--open` opens your browser
  automatically once the server is up).
- Add `--port <N>` to either form to use a port other than the default `8765`, e.g.
  `python3 route_builder/server.py --open --port 8080`.

**How it works:** the server binds to `127.0.0.1` only — nothing is exposed on the network
and nothing is uploaded anywhere. It just shells out to `adsb_historical_routes.py` as a
subprocess (with the argv built from your form values) and streams its stdout back to the
browser live; it has no processing logic of its own.

**Run workflow:**
1. Pick the input KML folder (or explicit files), the Airports CSV, and the Routes CSV
   through the folder/file browse modal (or type a path directly).
2. Pick an output folder and filename — a sensible filename is suggested automatically from
   the input batch name and whichever options (stitching, position integration, altitude
   coloring, …) are turned on.
3. Tune detection/recovery, styling, gap-stitching, and position-source-integration —
   the same knobs as the [full parameter reference](#full-parameter-reference) below, grouped
   into sections with inline help text and an example for every field.
4. Review the pre-flight card: input file/row counts, the exact command line that will run,
   and any validation errors or warnings (e.g. "output already exists").
5. Click **Run** — see per-file progress, phase updates, and the final summary stream in
   live; cancel a run in progress if needed.
6. When it finishes, reveal the output KML in Finder directly from the run console.

**Presets:** the presets bar has one-click built-ins mirroring the
[recommended settings by scenario](#-recommended-settings-by-scenario) — *Recommended —
clean & complete*, *Stitched — mark inferred gaps*, *Positions integrated (POS + firehose)*,
*Altitude-colored*, and *Strict — proximity only* — and you can save your own current form
values as a named preset. Presets and other local settings live in `route_builder/manifest.json`
(seeded on first run from `manifest.default.json`); it's git-ignored, so it's local-only and
safe to edit or delete to reset to defaults.

---

## 📋 Table of contents

- [GUI: ADSB Historical Route Builder](#-gui-adsb-historical-route-builder)
- [Recommended starting point](#-recommended-starting-point-run-this)
- [Recommended settings by scenario](#-recommended-settings-by-scenario)
- [Installation](#-installation)
- [Workflow overview](#-workflow-overview)
- [Building the inputs (`build_cirium_inputs.py`)](#-building-the-inputs-build_cirium_inputspy)
- [Splitting the tracks (`adsb_historical_routes.py`)](#-splitting-the-tracks-adsb_historical_routespy)
  - [Full parameter reference](#full-parameter-reference)
  - [Confidence presets](#confidence-presets)
  - [Colors](#colors)
  - [Altitude coloring](#altitude-coloring)
- [Gap stitching (inferred data)](#-gap-stitching-inferred-data)
- [Position-source integration (POS + firehose)](#-position-source-integration-pos--firehose)
- [Diagnostics & tuning without ground truth](#-diagnostics--tuning-without-ground-truth)
- [Examples](#-examples)
- [Input & output formats](#-input--output-formats)
- [How recovery works](#-how-recovery-works)
- [Performance](#-performance)
- [Troubleshooting](#-troubleshooting)

---

## 🌎 Recommended settings by scenario

The processing **method is universal**; the best *config* depends mostly on **ADS-B coverage
density**, which determines how often tracks truncate before reaching the airport. Validated
across Brazilian (LAN), Spanish-South-American (JAT), and US (AAY) datasets, including against
real flight records:

| Scenario | Recommended | Why |
|---|---|---|
| **General / unknown** | `--confidence permissive --corridor-cross-track-km 50` | Safe everywhere: no penalty on dense-coverage data, large clean gains on sparse-coverage data. |
| **Sparse coverage** (Latin America, remote/oceanic, small secondary fields) | `--confidence permissive --corridor-cross-track-km 50` | Tracks frequently truncate short of the field; permissive recovers ~25–30% more flights than `balanced`, and the tight corridor keeps mis-attribution ~1%. |
| **Dense coverage** (US domestic, most of Europe) | `--confidence balanced` (default) is already excellent | Tracks are complete, so detection matches airports directly; the preset barely matters and mis-attribution is naturally ~0.2%. `permissive --corridor-cross-track-km 50` also works with no downside. |
| **Maximum precision, fewer flights** | `--confidence strict` | No inference/rescue at all — only airport-proximity matches. Use when you want zero inferred data. |
| **Maximum recall, accept some noise** | `--confidence permissive` (default corridor 120) | Most flights, but higher mis-attribution; prefer corridor 50–70 unless recall is paramount. |

**Rule of thumb:** widen the net with `permissive`, then control quality with the corridor gate
(`--corridor-cross-track-km`): lower = stricter/cleaner/fewer, higher = looser/more. To choose a
value for a new dataset, minimize the mis-attribution metric in the diagnostics
(see [tuning](#-diagnostics--tuning-without-ground-truth)).

---

## 💾 Installation

**Requirements:** Python 3.6+ (standard library only). No `pip install` needed to run the
splitter. Optional dev tools: `pytest`, `black`, `flake8`.

```bash
git clone <repo-url>
cd adsb_historical_routes
python3 --version           # 3.6+
python3 -m pytest tests/ -q # optional: run the test suite
```

`build_cirium_inputs.py` additionally uses the shared **`airport_lookup`** tool
(`~/Git/conversion_tools/airport_lookup`, OurAirports-backed) for IATA→ICAO + coordinates.

---

## 🔭 Workflow overview

```
Cirium schedule CSV ──▶ build_cirium_inputs.py ──▶ <prefix>_routes_time.csv   ┐
                                                    <prefix>_airports.csv     │
                                                    <prefix>_error.txt        │
                                                                              ▼
per-tail/per-day KML tracks ─────────────────▶ adsb_historical_routes.py ──▶ organized KML
                                                                              + diagnostics CSV
```

Stage 1 (once per airline/period) turns a Cirium export into the two reference files the
splitter needs. Stage 2 splits the ADS-B KML tracks into flights using those references.

---

## 🏗 Building the inputs (`build_cirium_inputs.py`)

Converts a raw Cirium schedule export into the `*_routes_time.csv` and `*_airports.csv` the
splitter requires (both keyed by **ICAO**), plus a validation report.

```bash
python3 build_cirium_inputs.py \
    --input  input/<airline>_route_cirium_<period>.csv \
    --output input/<airline>_<period>
# writes: input/<airline>_<period>_routes_time.csv
#         input/<airline>_<period>_airports.csv
#         input/<airline>_<period>_error.txt   (validation report; READY / NEEDS INPUT)
```

**Route time** = per (Orig,Dest): group source rows by report month, pool aircraft types as
Σ Block Mins ÷ Σ Flights, then take the **max** month (an over-estimate, used only to trim
ADS-B routes, so it errs high). IATA→ICAO and coordinates come from `airport_lookup`.

### Parameters

| Parameter | Default | Description |
|---|---|---|
| `--input` | *required* | Raw Cirium CSV (columns incl. `Orig, Dest, Flights, Block Mins`; optional `report_source`). |
| `--output` | *required* | Output folder + filename prefix (e.g. `input/lan_202606-202607`). |
| `--airport-lookup-dir` | `~/Git/conversion_tools/airport_lookup` | Path to the shared airport_lookup tool. |
| `--airport-override` | `airport_overrides.csv` | Manifest that overrides airport_lookup for codes it resolves wrongly/misses (see below). Pass `ignore` to disable, or a path to another file. |

### Airport override manifest

A small, hand-curated CSV that takes precedence over airport_lookup/OurAirports. Used
automatically when `airport_overrides.csv` is present; disable with `--airport-override ignore`.
`#` lines are comments.

```csv
# iata,icao,latitude,longitude,elevation_ft
LIM,SPJC,-12.0219,-77.114305,113
```

Example above fixes Lima: OurAirports still files it under the retired ICAO `SPIM`, but flight
records use the current `SPJC`. Add rows as new mismatches are found.

---

## ✂️ Splitting the tracks (`adsb_historical_routes.py`)

### Full parameter reference

**Inputs / outputs**

| Parameter | Default | Description |
|---|---|---|
| `--kml-folder` | *(one of folder/files required)* | Folder of `*.kml` track files (one per tail/day typical). |
| `--kml-files` | *(one of folder/files required)* | Explicit list of KML files (space-separated). |
| `--airports` | *required* | The generated per-airline airports CSV: `airport,latitude,longitude,elevation_ft` (ICAO) — from `build_cirium_inputs.py`. **Not** the OurAirports `Airports_*.txt` database, and **not** the raw Cirium export. |
| `--routes` | *required* | The generated per-airline route-time CSV: `origin,destination,avg_enroute_min` (ICAO) — from `build_cirium_inputs.py`. **Not** the raw Cirium schedule export. |
| `--output` | *required* | Output KML path (parent dir auto-created; a `.diagnostics.csv` sidecar is written next to it, and the GeoPackage as `<output>.gpkg`). `{from}` / `{to}` are replaced by the first/last flight dates, e.g. `viv_flown_{from}_{to}.kml`. |
| `--format` | `both` | `both` = `<output>.gpkg` + `<output>.kml`; `gpkg` = GeoPackage only (fastest); `kml` = KML only (a temporary GeoPackage is used and deleted). |
| `--airline` | *(empty)* | Airline code stored in the GeoPackage (`flights.airline`, `meta`). |
| `--gpkg-points` | off | Also write the per-point `track_points` layer. Forced on by `--integrate-pos`. |
| `--suspect-od-ratio` | `2.5` | A flight whose flown distance exceeds this multiple of its airports' great circle is flagged `suspect_od`. |
| `--legacy-kml` | off | Write KML with the direct (pre-GeoPackage) writer. Kept for one release. |
| `--gpkg` | — | Deprecated alias for `--format both`. |
| `--takeoff-from`, `--takeoff-to` | *(none)* | Keep only flights whose UTC takeoff date is in range (`YYYY-MM-DD`, inclusive); daily KML files more than a day outside it are skipped. |
| `--qgz` | off | Also write `<output base>.qgz` next to the GeoPackage: a QGIS project built by `~/Git/qgis` `prepare` (navdb chart layers, basemaps, styles; links to the GeoPackages, copies no data). Not allowed with `--format kml`. |
| `--navdb` | *(none)* | `navdb_<AIRAC>.gpkg` for the project's chart layers. Required with `--qgz`; checked before the build starts. |
| `--company` | *(none)* | Optional `company_routes` GeoPackage. With it the project is the full Route Explorer (flights matched to company routes); without it, flown routes only. |
| `--qgis-repo` | `$QGIS_REPO`, else `~/Git/qgis` | The QGIS viewer checkout whose `prepare` builds the project. |

**Detection & sampling**

| Parameter | Default | Description |
|---|---|---|
| `--sample` | `2.0` | Point sampling interval in minutes (reduces output size; `0` keeps all points). |
| `--maxgap` | `20.0` | Minutes: a time gap larger than this splits a track into separate segments. Already near-optimal; **raising it merges sequential flights** (loses the middle stop). |
| `--group` | `destination` | Top-level KML folder grouping: `origin` or `destination`. |

**Confidence & recovery** (see [Confidence presets](#confidence-presets))

| Parameter | Default | Description |
|---|---|---|
| `--confidence` | `balanced` | Preset: `strict`, `balanced`, or `permissive`. |
| `--airport-radius-km` | *(preset)* | Override the airport-match radius (km). Presets: strict 20, balanced 50, permissive 100. |
| `--max-join-gap-hours` | *(preset)* | Override max time gap (h) for joining fragments. Presets: 2 / 3 / 4. |
| `--max-join-distance-km` | *(preset)* | Override max spatial gap (km) for joining. Presets: 100 / 200 / 500. |
| `--route-time-tolerance` | *(preset)* | Override route-time match tolerance (0.30 = ±30%). Presets: 0.25 / 0.40 / 0.60. |
| `--corridor-cross-track-km` | *(preset)* | Override the corridor gate (km) for endpoint recovery — **the main recovery/noise knob**. Lower = stricter (less mis-attribution, fewer recoveries). Presets: balanced 70, permissive 120. |
| `--route-time-rescue` | *(preset)* | Force route-time rescue `on`/`off`. |

**Gap stitching** (see [Gap stitching](#-gap-stitching-inferred-data))

| Parameter | Default | Description |
|---|---|---|
| `--stitch-gaps` | `false` | `true` draws straight-line connectors across coverage gaps in a distinct color to mark inferred data. **Off = output unchanged.** |
| `--inferred-color` | `ff888888` | KML color (AABBGGRR) for inferred connectors (default gray). |
| `--stitch-gap-min` | `5.0` | Minutes between consecutive points above which a gap connector is drawn. |

**Position-source integration** (see [Position-source integration](#-position-source-integration-pos--firehose))

| Parameter | Default | Description |
|---|---|---|
| `--integrate-pos` | `false` | `true` fills each detected flight's in-flight ADS-B gaps with real ACARS position reports and/or ADS-B firehose fixes. Detection is unchanged — extras only densify flights already found. **Off = output unchanged.** |
| `--pos-folder` | *(none)* | Folder of ACARS position-report JSON files (keyed to hex via `--tail-icao`). |
| `--firehose-folder` | *(none)* | Folder of ADS-B firehose position JSON files (carry ICAO hex directly). |
| `--tail-icao` | *(none)* | CSV mapping `tail_number,icao_hex` — required to key ACARS POS reports to hex. |
| `--pos-color` | `ff00a5ff` | KML color (AABBGGRR) for ACARS-POS-sourced track portions (default orange). |
| `--firehose-color` | `ff00ff00` | KML color (AABBGGRR) for firehose-sourced track portions (default green). |

**Styling / display**

| Parameter | Default | Description |
|---|---|---|
| `--color` | *(from source)* | Override line color, **KML AABBGGRR** (see [Colors](#colors)). Ignored when `--color-mode altitude`. |
| `--width` | *(from source)* | Override line width (e.g. `0.8`). |
| `--opacity` | *(from source)* | Override opacity as a percentage `0–100` (100 = opaque). |
| `--labels` | `false` | `true` shows per-flight names. |
| `--icons` | `false` | `true` shows airport icons. |
| `--ground` | `false` | `true` extrudes flight paths to the ground. |
| `--color-mode` | `source` | `source` colors lines by data source (ADS-B/POS/firehose, default); `altitude` instead colors by the ADS-B Exchange altitude gradient (see [Altitude coloring](#altitude-coloring)) and takes precedence over `--color`/source labeling. |
| `--altitude-palette` | `adsbex` | Altitude palette used when `--color-mode altitude`. Currently only `adsbex` (ADS-B Exchange hue ramp) is implemented; the flag is reserved for future palettes. |
| `--altitude-bands` | `24` | Number of discrete altitude color bands when `--color-mode altitude` (more = smoother gradient, larger file). Must be `>= 1`. |

### Confidence presets

Each preset bundles the detection/recovery knobs. Recovery = airport-proximity detection first,
then **route-time rescue (corridor-gated)**, **geometry-corridor inference**, and **multi-hop
joining** to reassemble fragments.

| Knob | strict | balanced (default) | permissive |
|---|---|---|---|
| airport radius (km) | 20 | 50 | 100 |
| max join gap (h) | 2 | 3 | 4 |
| max join distance (km) | 100 | 200 | 500 |
| route-time tolerance | 0.25 | 0.40 | 0.60 |
| route-time rescue | off | on | on |
| direction-aware rescue | off | on | on |
| multi-hop join | off | on | on |
| geometry rescue | off | on | on |
| corridor cross-track (km) | 80 | 70 | 120 |

- **strict** — proximity matches only; no inference. Fewest flights, zero inferred data.
- **balanced** — full recovery machinery at moderate tolerances. Great for dense-coverage data.
- **permissive** — wide net for sparse-coverage data. Pair with `--corridor-cross-track-km 50`
  for the recommended clean-and-complete result.

### Colors

KML colors are **`AABBGGRR`** (alpha, blue, green, red) — **not** RGB. Examples:

| Want | RGB hex | `--color` (6-char BBGGRR) + `--opacity` | Final KML color |
|---|---|---|---|
| Blue, 80% opaque | `0000FF` | `--color ff0000 --opacity 80` | `ccff0000` |
| Red, opaque | `FF0000` | `--color 0000ff` | `0000ff` |
| Gray connector | `888888` | `--inferred-color ff888888` | `ff888888` |

### Altitude coloring

`--color-mode altitude` colors each track by height instead of by data source, using the same
hue ramp as **ADS-B Exchange**: near ground level renders **orange**, climbing through
yellow → green → cyan as altitude increases, cruise altitudes render **blue**, and the highest
bands (35,000–40,000+ ft) render **violet/magenta**. Each track is split into
`--altitude-bands` (default `24`) discrete color steps rather than a continuous gradient. Altitude
mode adds a placemark per altitude-band transition along each track, so output files grow modestly
compared to source-mode output.

```bash
python3 adsb_historical_routes.py ... --color-mode altitude --altitude-bands 24
```

`--color-mode altitude` takes precedence over source-based coloring/labeling — `--color` and
any per-source (POS/firehose) coloring are ignored while it's active (the GUI's pre-flight
card warns if `--color` is also set). `--altitude-palette` selects the palette; currently only
`adsbex` is implemented.

---

## ✂️ Gap stitching (inferred data)

ADS-B coverage often drops out mid-flight and resumes. When two fragments of the same flight are
joined (the join logic verifies same tail + consistent time/heading/speed), the gap between them
is a real straight-line inference. `--stitch-gaps true` makes those inferred portions explicit:

- Real data keeps the flight's normal color; **each coverage gap becomes a separate straight-line
  connector placemark in `--inferred-color` (default gray)**, so downstream tools can identify
  inferred data purely by color.
- Altitude is carried on the connector endpoints, so the 3-D line linearly interpolates altitude
  across the gap (altitude is preserved throughout — every coordinate is `lon lat alt` in meters).
- **Opt-in and reversible:** with the flag off, output is byte-identical to before. With it on, a
  flight with N gaps becomes N+1 real placemarks + N connectors (a structural change, so enable it
  only when your downstream tooling expects it).

---

## 🛰 Position-source integration (POS + firehose)

Where ADS-B drops out mid-flight, gap stitching draws an *inferred* straight line. If you have
**real** position data for that window — ACARS position reports (POS) and/or an ADS-B firehose
feed — `--integrate-pos true` fills those gaps with measured fixes instead of inference.

**How it works (measured, not inferred):**

- Flights are detected from **ADS-B alone**, exactly as without the flag — origin, destination,
  timing, and route matching are **unchanged**. The extra sources never create or split flights;
  they only densify flights already found. (Feeding sparse POS *through* the segmenter would
  shatter tracks into thousands of spurious micro-flights — so integration happens strictly
  *after* detection.)
- For each flight, POS/firehose fixes that fall inside its own `[takeoff, landing]` window and
  land in a gap ≥ `--stitch-gap-min` minutes are merged in. ADS-B is authoritative: an extra fix
  within 30 s of an ADS-B point is dropped as redundant, so dense stretches aren't fragmented.
- **Source-marked rendering:** ADS-B keeps the flight color, ACARS-POS portions draw in
  `--pos-color` (orange), firehose portions in `--firehose-color` (green). Because these are real
  data, the gray inferred connector is **not** drawn where a source fills the gap.

**Inputs:**

- `--pos-folder` — ACARS files, each an SQS message. Two body formats are read:
  - `<Airline>_Position=[{…}]` (URL-encoded); each report carries `tail_number`, `created_at`, and
    a `freetext` encoding `HHMMSS, alt_ft, …, lat/lon` (e.g. `S 33.569 W 060.089`).
  - A raw ACARS `POS` report (LATAM, e.g. `FI JJ3634/AN PR-MYV` …
    `-  POSS23156W046116,<last wpt>,003708,216,…`): present position in DDMM.m, time HHMMSS UTC,
    flight level. The report has no date; it comes from the receive stamp in the S3 key
    (`…-20260804003719545.txt`) or the SQS `SentTimestamp`. Files named `<TAIL>_raw_*.json` for
    tails not in `--tail-icao` are skipped without being read.
- `--firehose-folder` — ADS-B firehose files: one report each, carrying `icao` (hex),
  Unix `timestamp`, `latitude`, `longitude`, `altitude` (feet).
- `--tail-icao` — CSV `tail_number,icao_hex`. POS files key on tail; this maps them to the hex
  ADS-B/firehose use. (Tails are normalized — punctuation stripped, upper-cased.)

```bash
python3 adsb_historical_routes.py \
    --kml-folder ./jat_july \
    --airports  input/jat_routes_202606-202607_airports.csv \
    --routes    input/jat_routes_202606-202607_routes_time.csv \
    --output    jat_july.kml \
    --confidence permissive --corridor-cross-track-km 50 \
    --integrate-pos true \
    --pos-folder ./pos --firehose-folder ./adsbpos \
    --tail-icao  input/jat_active_tails_icao.csv
```

**Opt-in and safe:** with the flag off, output is unchanged. With it on, flight *counts* and route
labels are identical to a plain run (verified on JAT July: 130/130 flights either way); only the
geometry of gap-affected flights gets richer, with the added portions color-marked by source.

---

## 📊 Diagnostics & tuning without ground truth

Every run writes `<output>.kml.diagnostics.csv` — one row per raw segment (phase `raw`) and per
final outcome (phase `final`), with columns including `disposition` (`kept_complete`,
`kept_rescued`, `kept_joined`, `dropped_unjoined`), `rescue_method`, `origin_code`/`dest_code`,
`nearest_airport_*`, and distances. Use it to see exactly how each flight was recovered or dropped.

**Tuning knob when you have no flight-record ground truth** (the normal case): the **mis-attribution
rate** — the share of rescued flights whose track actually ends near a *different* valid-route
airport than the one assigned — is a validated proxy for accuracy. It ranks configurations
identically to real flight records. To tune a new airline/region, sweep `--corridor-cross-track-km`
(and preset) and pick the setting that keeps recovery high while minimizing mis-attribution
(≈1% is achievable on sparse-coverage data; ≈0.2% on dense).

---

## 💡 Examples

**1 — Recommended batch run (any airline):**
```bash
python3 adsb_historical_routes.py \
    --kml-folder ~/adsb/output/aay_2026_jun-jul/ \
    --airports input/aay_airports.csv \
    --routes   input/aay_routes_time.csv \
    --output   output/aay_routes.kml \
    --confidence permissive --corridor-cross-track-km 50 \
    --width 0.8 --opacity 80 --color ff0000
```

**2 — Full pipeline from a Cirium export:**
```bash
# Stage 1: build inputs (SPIM->SPJC etc. handled by airport_overrides.csv)
python3 build_cirium_inputs.py \
    --input input/lan_route_cirium_202606-202607.csv \
    --output input/lan_202606-202607

# Stage 2: split the tracks
python3 adsb_historical_routes.py \
    --kml-folder ~/adsb/output/lan_2026_jun-jul/ \
    --airports input/lan_202606-202607_airports.csv \
    --routes   input/lan_202606-202607_routes_time.csv \
    --output   output/lan_routes_2026_0601-0731.kml \
    --confidence permissive --corridor-cross-track-km 50
```

**3 — Visualize inferred coverage gaps (gray connectors):**
```bash
python3 adsb_historical_routes.py ... \
    --confidence permissive --corridor-cross-track-km 50 \
    --stitch-gaps true --inferred-color ff888888
```

**4 — Maximum precision (no inference):**
```bash
python3 adsb_historical_routes.py ... --confidence strict
```

**5 — Batch several airlines:**
```bash
for a in aay lan jat; do
  python3 adsb_historical_routes.py \
      --kml-folder ~/adsb/output/${a}_2026_jun-jul/ \
      --airports input/${a}_airports.csv \
      --routes   input/${a}_routes_time.csv \
      --output   output/${a}_routes.kml \
      --confidence permissive --corridor-cross-track-km 50
done
```

---

## 📁 Input & output formats

**Airports CSV** (`--airports`) — header exact, ICAO codes:
```csv
airport,latitude,longitude,elevation_ft
SBGR,-23.431274,-46.469954,2461
```
Elevations (feet) are used by the altitude gate: a point is only matched to an airport when it is
both near it *and* at a plausible altitude for a takeoff/landing there.

**Routes CSV** (`--routes`) — header exact, ICAO codes, `avg_enroute_min` used to trim/recover:
```csv
origin,destination,avg_enroute_min
SBGR,SBSV,169.9
```

**KML tracks** (`--kml-folder`/`--kml-files`) — `gx:Track` placemarks with matching `when`/`gx:coord`
counts; registration inferred from folder/placemark names. Tracks that are ≥90% zero-altitude are
skipped (ground noise).

**Output KML** — three-level `Document → group (origin/destination) → route (ORIG-DEST) → flight`
placemarks; each `gx:coord` is `lon lat alt` (altitude in meters, preserved). Document title is the
output filename stem + grouping mode (e.g. `lan_routes_2026_0601-0731_destination`).

**Output GeoPackage** (`flown_routes` v2) — `flights` (one row per flight, sampled line),
`tracks_full` (every point, M = epoch seconds), both MULTILINESTRINGs split at the date line;
optional `track_points`, `diagnostics`, `meta`. Opens directly in QGIS. Without re-running the
builder you can rebuild the KML, build the QGIS project, or print a summary:

```bash
python3 gpkg_to_kml.py output/viv_flown_2026-09-30_2026-10-19.gpkg -o viv.kml [--color-mode altitude ...]
python3 gpkg_to_qgz.py output/viv_flown_2026-09-30_2026-10-19.gpkg --navdb ~/Git/qgis/input/navdb_2610.gpkg [--company company.gpkg]
python3 gpkg_summary.py output/viv_flown_2026-09-30_2026-10-19.gpkg -o viv_summary.txt
```

Full schema, columns and the origin/destination check: [docs/GEOPACKAGE.md](docs/GEOPACKAGE.md).

---

## 🔬 How recovery works

1. **Parse & filter** tracks; drop ≥90% zero-altitude tracks.
2. **Detect segments** — split a track wherever the time gap exceeds `--maxgap`; match each end to
   the nearest airport within radius **and** at a plausible altitude (the elevation gate). Ends at
   cruise altitude / beyond radius stay unmatched (`NONE`).
3. **Combine per aircraft** (chronological, same-tail invariant enforced):
   - **Route-time rescue (corridor-gated)** — fill a missing endpoint from the scheduled enroute
     time + bearing, **but only if the airborne endpoint lies within `--corridor-cross-track-km` of
     the corridor toward that airport**. This gate is what prevents truncated tracks (short duration)
     from being mislabeled to a shorter, same-bearing route.
   - **Geometry-corridor inference** — infer the endpoint from the direction the track is actually
     heading, constrained to the route network. Turn-robust (uses corridor cross-track, not
     instantaneous heading).
   - **Multi-hop joining** — reassemble flights split into 3+ fragments across multiple gaps.
4. **Validate** — keep only complete flights meeting altitude/duration/distance minimums with
   distinct origin ≠ destination.

Altitude-gated *detection* asserts a real landing/takeoff; the rescue steps *infer* an intended
endpoint for truncated tracks (recorded as `rescue_method` in the diagnostics).

---

## ⚡ Performance

On large folders (thousands of files, millions of points):

- Cyclic GC is disabled during the run (`gc.disable()`) — otherwise periodic collections over
  millions of live points cause tens-of-seconds stalls. This makes an ~8,000-file run ~6× faster.
- The process exits immediately after writing output (`os._exit(0)`), skipping slow interpreter
  teardown of millions of objects. **Output files are fully written before the program returns.**
- Memory is resident (all points held at once); a ~10,000-file / ~2 GB folder needs a few GB RAM
  and runs in ~2–3 minutes. If a bad/unmounted `--output` path is given, the run fails fast up front.

---

## 🔧 Troubleshooting

- **Too few flights / routes missing** → use `--confidence permissive --corridor-cross-track-km 50`;
  confirm the route exists in `--routes` and both airports in `--airports`.
- **Wrong destinations (mis-attribution)** → lower `--corridor-cross-track-km` (e.g. 50); check the
  diagnostics mis-attribution proxy.
- **A route is entirely absent** → check the diagnostics: `no_airport_match` with a large
  `nearest_airport_*_km` means the tracks aren't in the data (coverage gap) rather than a tool issue.
- **Wrong airport code** (e.g. Lima SPIM vs SPJC) → add an entry to `airport_overrides.csv` and
  rebuild inputs.
- **`Error: Airports/Routes file not found`** → build them first with `build_cirium_inputs.py`, and
  match the `<prefix>` in the output filenames.
- **Run seems to hang at the very end** → it isn't; that was the pre-fix object teardown, now removed
  via `os._exit(0)`. Output is already on disk when `Processing complete!` prints.

---

## 📄 License

MIT License — see [LICENSE](LICENSE). Airport reference data via the shared `airport_lookup` tool
(OurAirports, public domain). KML 2.2 / `gx` extensions; WGS84 coordinates; ISO 8601 timestamps.
