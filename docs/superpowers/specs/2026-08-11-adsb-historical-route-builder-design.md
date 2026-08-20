# ADSB Historical Route Builder — Design Spec

**Date:** 2026-08-11
**Status:** Approved for planning
**Branch:** `route-builder-app`

## 1. Purpose & goals

Give non-command-line users a professional local GUI that drives
`adsb_historical_routes.py`: pick input KML folders/files, choose the airport and
route-time reference CSVs, set an output path, apply any of the 27 script parameters
(via plain-language controls with examples), run the script, and watch live
progress → a graphical success/fail result with the parsed run summary.

Concretely the tool must:

1. Browse the filesystem by path (default root `~/Git/kml/adsb_kml_downloader/output`),
   navigating into per-airline batch folders that hold thousands of KML files.
2. Select the airports CSV, the routes-time CSV, and an output folder + filename
   (with a suggested name and an inline list of existing files in that folder).
3. Expose every CLI parameter with a default, a one-line explanation, and an example.
4. Offer built-in presets and let the user save/enumerate their own (stored as JSON).
5. Run the script as a subprocess and stream status: mirror stdout, report phase
   milestones, show a progress bar + ETA, and list run context (input file count,
   airports, routes).
6. On completion show a graphical ✓/✗ with the parsed summary; offer **Run another
   batch** that re-opens the same settings pre-filled.
7. Add altitude-gradient line coloring to the script itself (new render mode).
8. Launch friction-free via a double-clickable `.command` file — no terminal typing.

## 2. Non-goals (this iteration)

- No variant queue / multi-output-per-setup. Single run at a time; **Run another
  batch** pre-fills the last config for a quick tweak-and-rerun. (User decision.)
- No map/preview of the produced KML.
- No building of the airport CSV, route-time CSV, or tail↔icao mapping yet — those
  are future sibling tools. This spec documents the extension contract they will use
  (`EXTENSION_SPEC.md`) but implements only the Route Builder.
- No authentication, multi-user, or remote hosting. Localhost-only personal tool.

## 3. Architecture

A minimal **stdlib-only** local web app in a new `route_builder/` directory. No pip,
no npm, no build step. The user launches `python3 route_builder/server.py` (or
double-clicks the launcher); the browser opens to the tool.

```
route_builder/
├── server.py            # stdlib http.server (ThreadingHTTPServer) + subprocess runner
├── index.html           # single self-contained file: ft-system tokens + vanilla JS
├── manifest.json        # writable: roots, defaults, presets (built-in + user)
├── parameters.json      # the parameter catalog: label/help/example/type per flag
├── EXTENSION_SPEC.md    # tool-shell contract for future sibling tools
└── Route Builder.command  # double-clickable launcher (chmod +x)
```

**Data flow:** browser ⇄ `server.py` JSON/SSE API ⇄ subprocess
`python3 -u adsb_historical_routes.py …`. The server contains **no flight logic** — it
builds the argv, streams the child's stdout, parses the final summary, and lists
folders. In-memory state only (a jobs dict); the sole disk write is `manifest.json`
when a preset is saved.

**Why a server at all (vs. pure HTML):** a browser page cannot spawn a process or
navigate the filesystem by path — both are hard sandbox limits. The server is the
minimal shim granting those two powers; it is also more reliable (browser never loads
10k files; progress streams instead of blocking).

**Why `python3 -u`:** the script block-buffers stdout under a pipe and ends with
`os._exit(0)`, which discards buffered output. `-u` (unbuffered) makes every `print`
flush immediately, so progress is live and the summary survives.

## 4. Backend — `server.py`

Stdlib modules only: `http.server` (`ThreadingHTTPServer`, `BaseHTTPRequestHandler`),
`subprocess`, `threading`, `queue`, `json`, `urllib.parse`, `os`, `os.path`, `re`,
`time`, `webbrowser`, `argparse`, `shutil`.

- **Server:** `ThreadingHTTPServer` bound to `127.0.0.1`, so a streaming run does not
  block status/cancel calls. **Port resolution order:** `--port N` (CLI/launcher) →
  `manifest.server.port` → `8765`. If the chosen port is already in use, the server
  prints a clear message and exits non-zero (no silent fallback, so the URL is
  predictable). `--open` opens the browser to the resolved URL. Serves `index.html` at
  `/` and the JSON/SSE API under `/api/*`.
- **Path confinement:** every filesystem path from the client is resolved with
  `os.path.realpath` and must live inside one of `manifest.roots.allowed_roots`
  (default: `~/Git/kml` and the repo directory). Otherwise `403`. Guards `browse`,
  `validate`, `run`, `outputs`.
- **Run manager:** `JOBS: dict[str, Job]` guarded by a `Lock`. A `Job` holds the
  `Popen`, a `queue.Queue` of events, accumulated stdout, status, start time, total
  file count, processed count, and parsed summary. A per-job reader thread reads
  `proc.stdout` line-by-line, classifies each line (progress / phase / summary / raw),
  and enqueues events; on EOF it waits the process, sets terminal status, and enqueues
  a `done` event.

### 4.1 Endpoints

| Method + path | Purpose | Response |
|---|---|---|
| `GET /` | Serve the frontend | `index.html` |
| `GET /api/config` | Manifest + parameter catalog, roots resolved to absolute | `{roots, defaults, presets, parameters}` |
| `POST /api/presets` | Create/update a **user** preset `{name, params}` → written to `manifest.json` | `{presets}` (built-ins preserved) |
| `DELETE /api/presets?name=` | Delete a user preset from `manifest.json` (409 if built-in) | `{presets}` |
| `GET /api/browse?path=` | List a directory (default = `roots.input_default`) | `{path, parent, entries[]}` |
| `GET /api/outputs?dir=` | List files in an output dir (collision/suggestion) | `{files:[{name,size}]}` |
| `POST /api/validate` | Pre-flight counts + exact argv + warnings | see §4.3 |
| `POST /api/run` | Start a run from a params object | `{job_id}` |
| `GET /api/run/<id>/stream` | SSE event stream for a job | see §4.4 |
| `POST /api/run/<id>/cancel` | Terminate the subprocess | `{status:"cancelled"}` |
| `POST /api/run/<id>/reveal` | Reveal the output file in Finder (`open -R`; macOS) | `{status}` |

`browse` entries: `{name, type:"dir"|"file", kml_count?, size?, kind?}` where
`kind ∈ {"kml","csv","json","other"}` and `kml_count` is set on directories
(via `os.scandir`, counting `*.kml` cheaply — names only, never opening files).

### 4.2 argv construction

A single `build_argv(params) -> list[str]` maps the params object to script flags:

- Input: `--kml-folder <dir>` **or** `--kml-files f1 f2 …` (mutually exclusive).
- Required: `--airports`, `--routes`, `--output <output_dir/output_name>`.
- All optional flags are emitted **only when set to a non-default / non-empty value**,
  so the command stays minimal and readable. Booleans emit `--flag true|false` only
  when the user changed them from the script default.
- The same function backs both `/api/validate` (returns the argv for display) and
  `/api/run` (executes it) — one source of truth, unit-testable without a browser.

### 4.3 `/api/validate` response

```json
{
  "kml_count": 1674,
  "airports_count": 41,
  "routes_count": 128,
  "tail_icao_count": 54,
  "output_path": "/…/output/jat_routes_2026_jun-jul.kml",
  "output_exists": false,
  "argv": ["python3","-u","adsb_historical_routes.py","--kml-folder","…", …],
  "warnings": ["Output file already exists and will be overwritten"],
  "errors": ["Airports CSV not found"]
}
```

Counts come from cheap reads: `kml_count` via `scandir`; `airports_count` /
`routes_count` / `tail_icao_count` via CSV line counts (minus header). `errors`
(missing files, no input selected, integrate-pos without sources) block Run; the Run
button is disabled while `errors` is non-empty.

### 4.4 SSE stream & progress/ETA

`GET /api/run/<id>/stream` emits `text/event-stream` events:

- `event: line` — raw stdout line (mirrored to the console).
- `event: progress` — `{processed, total, percent, eta_sec, phase}`.
- `event: phase` — a milestone label (see below).
- `event: done` — `{exit_code, success, summary, output_size}`.
- `event: error` — `{message}` (spawn failure / crash).

**Progress** is derived by counting `Processing: <file>` lines against the pre-flight
`kml_count`; `percent = processed/total`; `eta_sec = elapsed/processed × (total −
processed)`. **Phase milestones** are detected by line prefixes:
`Loading extra position sources`, `Combining segments by aircraft`,
`Creating output KML`, `Position-source integration:`. During the position-source
load (reading up to ~1.4M files with no per-file line) the UI shows an indeterminate
"Loading position sources…" state so ETA does not mislead.

**Summary parse** on completion (regex against accumulated stdout):

- `TOTAL VALID COMPLETE FLIGHTS:\s*(\d+)`
- `Position-source integration: filled ([\d,]+) real fix\(es\) into (\d+) of (\d+) flights`
- `Output written to:\s*(.+)`
- `Data reduction:\s*(\d+) points -> (\d+) points`
- `Compression ratio:\s*([\d.]+)%`
- `Gap stitching:\s*(\d+) inferred connector`

Success = exit code 0 **and** an `Output written to:` line. Failure captures the
stderr/stdout tail. `output_size` = `os.path.getsize` of the produced KML.

## 5. Frontend — `index.html`

One self-contained file: inlined CSS (the route_explorer **ft-system** design tokens)
+ vanilla JS (no framework, no CDN except the browser's own APIs). Reuses the token
palette for sibling-tool familiarity — indigo `#5B4DE0` accent, **light-first** with a
GitHub-dark alt theme, flat dividered panels, dense 5px-radius controls, tabular
numerals, system font — persisted theme in `localStorage`.

### 5.1 Layout

- **Topbar (48px):** `ADSB HISTORICAL ROUTE BUILDER` wordmark (indigo) · live status
  chip (Idle / Running / ✓ Success / ✗ Failed) · theme toggle.
- **Left column — form** (scrollable), a **Presets bar** on top (preset `<select>` +
  *Save as preset* / *Delete*), then collapsible sections (§5.2). Advanced overrides
  under a disclosure, collapsed by default.
- **Right column — run panel** (§5.3): pre-flight summary → Run → progress/console →
  result.
- Responsive: single column below ~900px.

### 5.2 Form sections (control types)

The form is **generated from `parameters.json`**; each field renders an ⓘ tooltip with
its `help` + `example`.

1. **Inputs** — input-folder picker (browse modal rooted at `roots.input_default`),
   with a **whole-folder / specific-files** toggle and a live KML count; airports CSV
   picker; routes CSV picker. Auto-suggest: choosing a `jat_*` batch pre-fills
   `input/jat_*_airports.csv` and `input/jat_*_routes_time.csv` by airline-prefix
   match.
2. **Output** — output-folder picker (default `output/`); filename field with a
   **suggested** name (batch stem + feature suffixes, §5.4) and an inline list of the
   folder's existing files with a collision warning + one-click auto-increment.
3. **Detection & sampling** — `confidence` (segmented strict/balanced/permissive),
   `sample`, `maxgap`, `group` (segmented). **Advanced overrides** disclosure:
   `airport-radius-km`, `max-join-gap-hours`, `max-join-distance-km`,
   `route-time-tolerance`, `corridor-cross-track-km`, `route-time-rescue`.
4. **Styling** — `color-mode` (segmented **source / altitude**). Source mode:
   `color`, `width`, `opacity` (slider 0–100), `labels`, `icons`, `ground`. Altitude
   mode shows the **ADS-B Exchange legend** (the labeled gradient bar) and `altitude-bands`;
   `width`/`labels`/`icons`/`ground` still apply.
5. **Gap stitching** — `stitch-gaps` (toggle), `inferred-color`, `stitch-gap-min`.
6. **Position sources** — `integrate-pos` (toggle) reveals `pos-folder`,
   `firehose-folder`, `tail-icao`, `pos-color`, `firehose-color`.

**Color inputs:** KML uses `AABBGGRR`, which is unintuitive. Each color control shows a
native color swatch + opacity and converts to/from `AABBGGRR` under the hood, while
also accepting the raw 8-hex string for power users. A small JS `rgbaToKml` /
`kmlToRgba` pair handles conversion (unit-testable pure functions, mirrored server-side
is unnecessary — the client sends the final `AABBGGRR`).

### 5.3 Run panel & lifecycle

- **Pre-flight summary card** (from `/api/validate`, refreshed on form change, debounced):
  `N KML files · N airports · N routes`, output path + collision status, and **the exact
  command line that will run** (transparency + teachability). Errors listed here disable
  Run.
- **Run** (primary button) → opens the SSE stream. **Running:** progress bar + ETA + a
  live auto-scrolling console (toggle raw / errors-only) + **Cancel**.
- **Result:** large ✓ (green) or ✗ (red) with the parsed summary — valid flights, fixes
  filled (if integrate-pos), data reduction, gap connectors (if stitching), output file
  name + size — and actions: **Reveal in Finder** (a `POST /api/run/<id>/reveal` that
  runs `open -R <path>`; macOS) and **Run another batch** (resets only the run panel,
  keeps the form pre-filled, clears the filename so it re-suggests).

### 5.4 Filename suggestion

`suggest_name(batch_stem, params)` → `<airline>_routes_<period>` + suffixes:
`_stitched` (stitch-gaps), `_pos` (integrate-pos), `_altcolor` (color-mode=altitude),
`_strict` (confidence=strict). Example: batch `jat_2026_jun-jul` with stitching →
`jat_routes_2026_jun-jul_stitched.kml`. Matches the existing `_stitched` convention in
`output/`.

## 6. Manifest & presets — `manifest.json`

**User presets save to `manifest.json`** — this is a first-class supported feature.
`POST /api/presets` read-modify-writes the manifest's `presets` array and the new
preset immediately enumerates in the dropdown; `DELETE` removes it (built-ins, marked
`"builtin": true`, are protected). To keep the git tree clean while still shipping the
built-ins under version control, there are two files:

- **`manifest.default.json`** — committed seed (roots, defaults, `server`, the built-in
  presets). Version-controlled; edited by developers.
- **`manifest.json`** — the live, **gitignored** runtime file the app reads and writes.
  On first launch the server copies `manifest.default.json` → `manifest.json` if it
  doesn't exist; thereafter all reads and all preset saves target `manifest.json`.

So from the user's view there is one manifest they save presets into; from git's view
only the seed is tracked, so personal presets and local path tweaks never show up as
diffs. Seed/runtime structure:

```json
{
  "version": "1.0.0",
  "roots": {
    "input_default": "~/Git/kml/adsb_kml_downloader/output",
    "airports_dir": "input",
    "routes_dir": "input",
    "output_default": "output",
    "allowed_roots": ["~/Git/kml", "."]
  },
  "defaults": {
    "confidence": "permissive", "corridor_cross_track_km": 50,
    "sample": 2, "maxgap": 20, "group": "destination",
    "color_mode": "source", "color": "", "width": "", "opacity": 100,
    "labels": false, "icons": false, "ground": false,
    "stitch_gaps": false, "inferred_color": "ff888888", "stitch_gap_min": 5,
    "integrate_pos": false, "pos_color": "ff00a5ff", "firehose_color": "ff00ff00",
    "altitude_palette": "adsbex", "altitude_bands": 24
  },
  "server": { "port": 8765 },
  "presets": [
    {"name": "Recommended — clean & complete", "builtin": true,
     "params": {"confidence": "permissive", "corridor_cross_track_km": 50}},
    {"name": "Stitched — mark inferred gaps", "builtin": true,
     "params": {"confidence": "permissive", "corridor_cross_track_km": 50, "stitch_gaps": true}},
    {"name": "Positions integrated (POS + firehose)", "builtin": true,
     "params": {"confidence": "permissive", "corridor_cross_track_km": 50, "integrate_pos": true}},
    {"name": "Altitude-colored", "builtin": true,
     "params": {"confidence": "permissive", "corridor_cross_track_km": 50, "color_mode": "altitude"}},
    {"name": "Strict — proximity only", "builtin": true,
     "params": {"confidence": "strict"}}
  ]
}
```

- `roots` paths are `~`-expanded and made absolute by the server. `allowed_roots`
  drives path confinement.
- `defaults` seed the form on load; a preset's `params` overlay the defaults (a partial
  set — only the knobs it changes).
- Built-in presets carry `"builtin": true` and cannot be deleted or overwritten. User
  presets are appended to the same `presets` array in `manifest.json` (via
  `POST /api/presets`) — no `builtin` flag — and enumerate in the dropdown alongside the
  built-ins.
- `server.port` (default `8765`) is the default listen port; it is overridable at launch
  by `--port` and by the launcher (§9), so the user can pick a port without editing the
  manifest.

## 7. Parameter catalog — `parameters.json`

Drives form generation and tooltips. One entry per control:

```json
{
  "key": "corridor_cross_track_km",
  "flag": "--corridor-cross-track-km",
  "group": "detection_advanced",
  "type": "number",
  "label": "Corridor cross-track (km)",
  "default": null,
  "help": "The main recovery/noise knob. Lower = stricter (less mis-attribution, fewer recoveries).",
  "example": "50 with the permissive preset gives the recommended clean-and-complete result."
}
```

`type ∈ {folder, file, files, text, number, slider, toggle, segmented, color}`. The
catalog covers all 27 script params plus the new altitude controls, grouped as: `input`,
`output`, `detection`, `detection_advanced`, `styling`, `styling_altitude`, `stitching`,
`positions`. Every entry has a `help` and an `example` (the tooltip content the user
requested). The catalog is data, not code — future tools ship their own catalog.

## 8. New script feature — altitude-gradient coloring

Additive change to `adsb_historical_routes.py`; default behavior unchanged.

The default palette **matches the ADS-B Exchange (tar1090) map exactly** (per the user's
screenshot): a multi-stop **HSL hue ramp** keyed in **feet**, not a two-color blend.
Hue rotates orange-red → yellow → green → cyan → blue → violet → magenta as altitude
rises, at fixed saturation 85% / lightness 50%.

**Default palette (`adsbex`)** — altitude (ft) → hue (deg), S=85, L=50:

| Alt (ft) | Hue | ≈ Color | Hex (S85 L50) |
|---|---|---|---|
| 0 | 20 | orange-red | `#ED4F13` |
| 2 000 | 40 | orange | `#ED9213` |
| 4 000 | 55 | amber | `#EDC513` |
| 6 000 | 70 | yellow-green | `#C5ED13` |
| 8 000 | 100 | green | `#62ED13` |
| 10 000 | 140 | spring green | `#13ED74` |
| 20 000 | 190 | cyan | `#13C5ED` |
| 30 000 | 240 | blue | `#1313ED` |
| 35 000 | 275 | violet | `#7E13ED` |
| 40 000+ | 300 | magenta | `#ED13ED` |

**New CLI flags:**

- `--color-mode {source,altitude}` (default `source`). `source` = today's behavior
  (segment/source coloring). `altitude` = ADS-B-Exchange-style altitude ramp.
- `--altitude-palette {adsbex}` (default `adsbex`) — the built-in palette. Baked as an
  ordered `(alt_ft, hue)` stop table in the script; the name argument leaves room for
  more palettes later without changing the interface (YAGNI: only `adsbex` now).
- `--altitude-bands` (default `24`) — number of discrete color steps the continuous
  ramp is quantized into (KML colors a whole placemark at once, so the ramp is banded;
  24 across 0–40 000 ft ≈ 1 700 ft/band, visually smooth without bloating the file).

**Rendering algorithm** (`create_output_kml`): when `color_mode == "altitude"`, each
sampled track is split into contiguous runs by **altitude band** — reusing the existing
run-splitting approach (`_runs_and_gaps`), generalized so a run breaks on band change
(and still on a coverage gap when `stitch_gaps`). For each point: `alt_ft = alt_m /
0.3048`; find the band index `b ∈ [0, bands)` for the band-center altitude; look up the
band color by interpolating **hue** linearly between the two surrounding palette stops
(altitudes above 40 000 ft clamp to the top stop), then convert HSL(h, 85, 50) → RGB →
KML `AABBGGRR`. Runs overlap by one point at a band change so lines stay joined.
Altitude mode takes precedence over source/segment color; gap connectors remain gray
when stitching is on. `--color`/source coloring is ignored in altitude mode (a
validate-time warning notes this).

The UI renders the same legend as the screenshot — a horizontal gradient bar labeled
`0 · 500 · 1 000 · 2 000 · 4 000 · 6 000 · 8 000 · 10 000 · 20 000 · 30 000 · 40 000+`
(ft) — generated from the palette stops so it always matches the output.

**Tests** (`tests/test_altitude_coloring.py`): HSL→RGB conversion at known stops
(hue 20/140/240/300 → expected hexes); band-color lookup for sample altitudes (500 ft →
orange, 35 000 ft → violet); a synthetic climbing track produces multiple distinct band
colors in ascending-hue order; `color-mode source` output is byte-identical to
pre-feature output (regression guard); altitude mode + stitch-gaps still emits gray
connectors.

## 9. Launcher — `Route Builder.command`

A `chmod +x` shell script (double-clickable in Finder on macOS):

```sh
#!/bin/sh
cd "$(dirname "$0")/.."          # repo root
exec python3 route_builder/server.py --open "$@"   # forwards e.g. --port 9000
```

`--open` starts the server on `127.0.0.1:<resolved-port>` and opens the default browser
to it. No terminal typing. Passing `--port N` (to the launcher or `server.py`) picks a
non-default port. README documents the double-click, the `python3` invocation, and the
port option.

## 10. Security & runtime

- Bind `127.0.0.1` only. Not reachable off-machine.
- Path confinement (realpath containment within `allowed_roots`) on all filesystem
  endpoints; reject traversal with `403`.
- The subprocess runs the repo's own `adsb_historical_routes.py` with a fixed argv
  built from typed params — no shell string interpolation (`Popen(list, shell=False)`),
  so no command injection from field values.
- One concurrent run per browser session is expected; the jobs dict supports more but
  the UI serializes.

## 11. Extension spec — `EXTENSION_SPEC.md` (contract only, no code)

Documents the **tool-shell contract** so the future airport-file builder, route-time
builder, and icao↔hex matcher slot in without rework:

- A tool = `{ id, title, parameters.json catalog, command template (script path + argv
  mapping), manifest section (roots/defaults/presets) }`.
- The server endpoints (`browse`, `outputs`, `validate`, `run`+`stream`, `presets`) are
  **tool-agnostic**: they already operate on a generic params object + a command
  template; a new tool registers a catalog and a `build_argv` mapping.
- The frontend shell (topbar + form-from-catalog + run panel + SSE console) is reused;
  the topbar gains a tool tab/switcher when a second tool is added.
- Progress/summary parsing is per-tool (regex set in the tool's registration), since
  each script prints its own milestones.

The Route Builder is the reference implementation of this contract.

## 12. Files created / changed

**New:** `route_builder/server.py`, `route_builder/index.html`,
`route_builder/manifest.default.json`, `route_builder/parameters.json`,
`route_builder/EXTENSION_SPEC.md`, `route_builder/Route Builder.command`,
`tests/test_altitude_coloring.py`, `tests/test_route_builder_server.py`.

**Changed:** `adsb_historical_routes.py` (+altitude coloring), `README.md`
(document the tool + the new flags), `.gitignore` (add `route_builder/manifest.json`).

**Gitignore:** `route_builder/manifest.default.json` (seed, built-in presets) is
committed; `route_builder/manifest.json` (live file the app reads/writes, incl. user
presets) is gitignored and created from the seed on first launch. No bulk output is ever
committed (`output/` stays gitignored).

## 13. Testing strategy

- **Script:** `tests/test_altitude_coloring.py` (§8) + the existing 83-test suite stays
  green (source-mode regression guard).
- **Server (no browser):** `tests/test_route_builder_server.py` — `build_argv`
  (folder vs files, boolean emission, defaults omitted), `suggest_name` suffixes,
  summary-parse regexes against a captured stdout sample, path-confinement accept/reject,
  and `/api/validate` counts against tiny fixture CSVs/folders.
- **Manual:** launch, browse to a JAT batch, run a small subset, confirm live progress +
  ✓ result + Reveal + Run-another-batch.

## 14. Open implementation notes

- ETA is coarse during the position-source load phase (no per-file line); UI shows an
  indeterminate state there, then resumes file-count ETA.
- macOS-specific niceties (`open -R`, `.command`) are guarded; on other platforms the
  Reveal button falls back to showing the path.
- The `-u` flag is mandatory in the runner; document why (buffering + `os._exit`).
