# ADSB Historical Route Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a stdlib-only local web GUI (`route_builder/`) that drives `adsb_historical_routes.py` for non-command-line users, plus an ADS-B-Exchange-style altitude color mode added to the script.

**Architecture:** A `http.server.ThreadingHTTPServer` (no dependencies) serves a single self-contained `index.html` and a JSON/SSE API; it builds an argv and runs `python3 -u adsb_historical_routes.py …` as a subprocess, streaming stdout back to the browser and parsing the final summary. The script gains a new `--color-mode altitude` render path. All non-UI logic is pure, importable functions with unit tests; the UI is verified by launching and running a small batch.

**Tech Stack:** Python 3.9 standard library only (`http.server`, `subprocess`, `threading`, `queue`, `json`, `urllib`, `re`, `webbrowser`); vanilla HTML/CSS/JS (no framework, no build, no CDN). Tests: pytest.

**Spec:** `docs/superpowers/specs/2026-08-11-adsb-historical-route-builder-design.md`

## Global Constraints

- **Stdlib only.** No pip/npm/CDN. The script's zero-dependency rule extends to the app.
- **Never break the script's default behavior.** `--color-mode` defaults to `source`; the existing 83-test suite must stay green (source-mode regression guard).
- **Runner must use `python3 -u`** (unbuffered) — the script block-buffers under a pipe and ends with `os._exit(0)`, which discards buffered stdout otherwise.
- **Bind `127.0.0.1` only.** `Popen(list, shell=False)` — never a shell string (no injection from field values).
- **Path confinement:** every client-supplied path must resolve (realpath) inside `manifest.roots.allowed_roots`, else HTTP 403.
- **Altitude palette = ADS-B Exchange (tar1090):** HSL hue ramp keyed in **feet**, S=85 L=50, stops `(0,20)(2000,40)(4000,55)(6000,70)(8000,100)(10000,140)(20000,190)(30000,240)(35000,275)(40000,300)`.
- **KML color format is `AABBGGRR`** (alpha, blue, green, red) — not RGB.
- Commit after every task. End commit messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

**Modified:**
- `adsb_historical_routes.py` — add HSL/altitude-palette helpers, `--color-mode`/`--altitude-palette`/`--altitude-bands` flags, altitude-band split in `create_output_kml`.
- `README.md` — document the GUI + the new flags.
- `.gitignore` — ignore `route_builder/manifest.json`.

**Created:**
- `route_builder/parameters.json` — parameter catalog (label/help/example/type per control).
- `route_builder/manifest.default.json` — committed seed (roots, defaults, server, built-in presets).
- `route_builder/server.py` — stdlib HTTP/SSE server + pure helpers (`load_manifest`, `confine_path`, `build_argv`, `suggest_name`, `parse_summary`, `classify_line`, `validate`, `RunManager`, `make_server`).
- `route_builder/index.html` — self-contained frontend (ft-system tokens + vanilla JS).
- `route_builder/Route Builder.command` — double-click launcher.
- `route_builder/EXTENSION_SPEC.md` — tool-shell contract for future tools.
- `tests/test_altitude_coloring.py`, `tests/test_route_builder_server.py`.

Tests import the server by path:
```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "route_builder"))
import server  # noqa: E402
```

---

## Task 1: Altitude palette + HSL helpers (script)

**Files:**
- Modify: `adsb_historical_routes.py` (add helpers near the other module-level render helpers, before `create_output_kml`)
- Test: `tests/test_altitude_coloring.py`

**Interfaces:**
- Produces: `ADSBEX_ALT_STOPS: list[tuple[int,int]]`; `hsl_to_rgb(h,s,l)->(int,int,int)`; `alt_hue(alt_ft, stops=ADSBEX_ALT_STOPS)->float`; `rgb_to_kml(r,g,b,a=255)->str`; `alt_band_index(alt_m, bands)->int`; `alt_band_color(band, bands)->str` (KML AABBGGRR).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_altitude_coloring.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adsb_historical_routes as m


def test_hsl_to_rgb_primaries():
    assert m.hsl_to_rgb(0, 100, 50) == (255, 0, 0)      # red
    assert m.hsl_to_rgb(120, 100, 50) == (0, 255, 0)    # green
    assert m.hsl_to_rgb(240, 100, 50) == (0, 0, 255)    # blue
    assert m.hsl_to_rgb(60, 100, 50) == (255, 255, 0)   # yellow


def test_alt_hue_monotonic_and_clamped():
    assert m.alt_hue(-500) == 20            # clamp low
    assert m.alt_hue(50000) == 300          # clamp high
    hues = [m.alt_hue(a) for a in (0, 4000, 10000, 20000, 30000, 40000)]
    assert hues == sorted(hues)             # non-decreasing (red -> magenta)


def test_rgb_to_kml_is_aabbggrr():
    assert m.rgb_to_kml(255, 0, 0) == "ff0000ff"        # red -> A=ff B=00 G=00 R=ff
    assert m.rgb_to_kml(0, 0, 255) == "ffff0000"        # blue


def test_alt_band_color_low_is_warm_high_is_cool():
    low = m.alt_band_color(m.alt_band_index(300 * 0.3048, 24), 24)    # ~300 ft
    high = m.alt_band_color(m.alt_band_index(38000 * 0.3048, 24), 24) # ~38000 ft
    lr, lg, lb = int(low[6:8], 16), int(low[4:6], 16), int(low[2:4], 16)
    hr, hg, hb = int(high[6:8], 16), int(high[4:6], 16), int(high[2:4], 16)
    assert lr > lb          # low altitude reddish/warm
    assert hb > hr or hr > hg  # high altitude cool/magenta (blue or magenta dominant)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_altitude_coloring.py -v`
Expected: FAIL — `AttributeError: module 'adsb_historical_routes' has no attribute 'hsl_to_rgb'`.

- [ ] **Step 3: Write minimal implementation**

Add to `adsb_historical_routes.py` (module level, above `def create_output_kml`):

```python
# --- Altitude color palette (ADS-B Exchange / tar1090 style) -----------------
# (altitude_ft, hue_deg); fixed saturation 85%, lightness 50%.
ADSBEX_ALT_STOPS = [
    (0, 20), (2000, 40), (4000, 55), (6000, 70), (8000, 100),
    (10000, 140), (20000, 190), (30000, 240), (35000, 275), (40000, 300),
]


def hsl_to_rgb(h, s, l):
    """HSL (h in [0,360), s/l in [0,100]) -> (r, g, b) ints 0-255."""
    h = h % 360.0
    s /= 100.0
    l /= 100.0
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    mm = l - c / 2
    if h < 60:    rp, gp, bp = c, x, 0
    elif h < 120: rp, gp, bp = x, c, 0
    elif h < 180: rp, gp, bp = 0, c, x
    elif h < 240: rp, gp, bp = 0, x, c
    elif h < 300: rp, gp, bp = x, 0, c
    else:         rp, gp, bp = c, 0, x
    return (round((rp + mm) * 255), round((gp + mm) * 255), round((bp + mm) * 255))


def alt_hue(alt_ft, stops=ADSBEX_ALT_STOPS):
    """Interpolate the palette hue (deg) for an altitude in feet; clamped to the ends."""
    if alt_ft <= stops[0][0]:
        return float(stops[0][1])
    if alt_ft >= stops[-1][0]:
        return float(stops[-1][1])
    for (a0, h0), (a1, h1) in zip(stops, stops[1:]):
        if a0 <= alt_ft <= a1:
            t = (alt_ft - a0) / (a1 - a0) if a1 > a0 else 0.0
            return h0 + t * (h1 - h0)
    return float(stops[-1][1])


def rgb_to_kml(r, g, b, a=255):
    """(r,g,b[,a]) 0-255 -> KML AABBGGRR hex string."""
    return f"{a:02x}{b:02x}{g:02x}{r:02x}"


def alt_band_index(alt_m, bands):
    """Altitude in meters -> band index in [0, bands) over 0..top-of-palette (ft)."""
    top_ft = ADSBEX_ALT_STOPS[-1][0]
    alt_ft = max(0.0, alt_m / 0.3048)
    return min(bands - 1, int(min(alt_ft, top_ft) / top_ft * bands))


def alt_band_color(band, bands):
    """Band index -> banded ADS-B-Exchange KML color (AABBGGRR) at the band center."""
    top_ft = ADSBEX_ALT_STOPS[-1][0]
    center_ft = (band + 0.5) / bands * top_ft
    r, g, b = hsl_to_rgb(alt_hue(center_ft), 85, 50)
    return rgb_to_kml(r, g, b)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_altitude_coloring.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add adsb_historical_routes.py tests/test_altitude_coloring.py
git commit -m "feat: add ADS-B Exchange altitude color palette helpers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Altitude color-mode in `create_output_kml` + CLI flags (script)

**Files:**
- Modify: `adsb_historical_routes.py` — generalize `_runs_and_gaps` with a `key` param; add `color_mode`/`altitude_bands` params to `create_output_kml` and the altitude render branch; thread through `process_kml_files` and `main()`; add three argparse flags.
- Test: `tests/test_altitude_coloring.py`

**Interfaces:**
- Consumes: `alt_band_index`, `alt_band_color` (Task 1).
- Produces: `create_output_kml(..., color_mode="source", altitude_bands=24)`; new CLI `--color-mode {source,altitude}`, `--altitude-palette {adsbex}`, `--altitude-bands INT`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_altitude_coloring.py
from datetime import datetime, timedelta, timezone


def _climb_segment(m):
    base = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    pts = []
    for i in range(12):
        ts = (base + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pts.append(m.TrackPoint(ts, -80.0 + i * 0.1, 0.0 + i * 0.1, i * 1000.0))  # climb 0->11000m
    return m.FlightSegment("AC1", "AC1", pts,
                           m.Airport("A", 0.0, -80.0, 0), m.Airport("B", 1.1, -78.9, 0),
                           "ff0000ff", "2", "ff", "t.kml")


def test_altitude_mode_emits_multiple_band_colors(tmp_path):
    seg = _climb_segment(m)
    out = str(tmp_path / "alt.kml")
    m.create_output_kml([seg], out, sample_minutes=0, color_mode="altitude", altitude_bands=24)
    txt = open(out).read()
    import re
    colors = set(re.findall(r"<color>([0-9a-f]{8})</color>", txt))
    assert len(colors) >= 3           # a climbing track spans several bands


def test_source_mode_default_single_track(tmp_path):
    seg = _climb_segment(m)
    out = str(tmp_path / "src.kml")
    m.create_output_kml([seg], out, sample_minutes=0)   # color_mode defaults to source
    txt = open(out).read()
    assert txt.count("<Placemark") == 1   # unchanged single-track behavior
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_altitude_coloring.py -k "altitude_mode or source_mode_default" -v`
Expected: FAIL — `create_output_kml() got an unexpected keyword argument 'color_mode'`.

- [ ] **Step 3: Write minimal implementation**

3a. Generalize `_runs_and_gaps` (add a `key` parameter; default preserves source behavior). Replace its signature/body:

```python
    def _runs_and_gaps(pts, gap_min, split_gaps, key=lambda p: p.source):
        """Split into runs (each a same-key contiguous stretch) plus real coverage gaps.
        `key(point)` decides the grouping — data source (default) or altitude band."""
        runs, connectors = [], []
        cur = [pts[0]]
        for prev, p in zip(pts, pts[1:]):
            is_gap = split_gaps and (p.datetime - prev.datetime).total_seconds() / 60.0 > gap_min
            if is_gap:
                runs.append((cur, key(cur[0])))
                connectors.append((prev, p))
                cur = [p]
            elif key(p) != key(prev):
                runs.append((cur + [p], key(cur[0])))
                cur = [p]
            else:
                cur.append(p)
        runs.append((cur, key(cur[0])))
        return runs, connectors
```

3b. Add `color_mode` and `altitude_bands` to `create_output_kml`'s signature (after `firehose_color`):

```python
                     firehose_color: str = "ff00ff00",
                     color_mode: str = "source",
                     altitude_bands: int = 24):
```

3c. Replace the render branch (the `if (stitch_gaps or mark_sources) and len(sampled_points) >= 2:` block) with mode-aware key/color selection:

```python
                alt_mode = (color_mode == "altitude")
                if (stitch_gaps or mark_sources or alt_mode) and len(sampled_points) >= 2:
                    if alt_mode:
                        keyf = lambda p: alt_band_index(p.alt, altitude_bands)
                        colorf = lambda k: alt_band_color(k, altitude_bands)
                    else:
                        src_color = {"ADSB": seg_color, "POS": pos_color, "FIREHOSE": firehose_color}
                        keyf = lambda p: p.source
                        colorf = lambda k: (src_color.get(k, seg_color) if mark_sources else seg_color)
                    runs, connectors = _runs_and_gaps(sampled_points, stitch_gap_min, stitch_gaps, key=keyf)
                    for run, k in runs:
                        if len(run) >= 2:
                            nm = seg_name
                            if seg_name and mark_sources and not alt_mode and k != "ADSB":
                                nm = seg_name + f" [{str(k).lower()}]"
                            _add_track(route_folder, run, colorf(k), seg_width, nm, seg_desc)
                    for a, b in connectors:
                        total_connectors += 1
                        _add_track(route_folder, [a, b], inferred_color, seg_width,
                                   (seg_name + " [inferred]") if seg_name else None,
                                   "Inferred gap connector (straight-line fill across a coverage "
                                   "gap; interpolated, not measured data)")
                else:
                    _add_track(route_folder, sampled_points, seg_color, seg_width, seg_name, seg_desc)
```

3d. Thread params through `process_kml_files`: add `color_mode: str = "source", altitude_bands: int = 24` to its signature and pass them into the `create_output_kml(...)` call (append `color_mode=color_mode, altitude_bands=altitude_bands`).

3e. Add argparse flags in `main()` (near `--firehose-color`):

```python
    parser.add_argument('--color-mode', choices=['source', 'altitude'], default='source',
                        dest='color_mode',
                        help='Line coloring: source (per data source, default) or altitude '
                             '(ADS-B Exchange altitude gradient).')
    parser.add_argument('--altitude-palette', choices=['adsbex'], default='adsbex',
                        dest='altitude_palette',
                        help='Altitude palette (default adsbex: ADS-B Exchange hue ramp).')
    parser.add_argument('--altitude-bands', type=int, default=24, dest='altitude_bands',
                        help='Number of altitude color bands (default 24).')
```

3f. Pass them into the `process_kml_files(...)` call in `main()`: append `color_mode=args.color_mode, altitude_bands=args.altitude_bands`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_altitude_coloring.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run the full suite (regression guard)**

Run: `python3 -m pytest tests/ -q`
Expected: `85 passed` (the prior 83 + 2 new files' tests; source-mode unchanged).

- [ ] **Step 6: Commit**

```bash
git add adsb_historical_routes.py tests/test_altitude_coloring.py
git commit -m "feat: add --color-mode altitude (ADS-B Exchange gradient) to renderer

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Parameter catalog `parameters.json`

**Files:**
- Create: `route_builder/parameters.json`
- Test: `tests/test_route_builder_server.py`

**Interfaces:**
- Produces: a JSON array of `{key, flag, group, type, label, default, help, example}` objects, one per control; consumed by the frontend and validated by a test.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_route_builder_server.py
import json, os
CAT = os.path.join(os.path.dirname(__file__), "..", "route_builder", "parameters.json")


def test_parameters_catalog_complete():
    cat = json.load(open(CAT))
    keys = {e["key"] for e in cat}
    required = {
        "kml_folder", "airports", "routes", "output_dir", "output_name",
        "sample", "maxgap", "group", "confidence", "airport_radius_km",
        "max_join_gap_hours", "max_join_distance_km", "route_time_tolerance",
        "corridor_cross_track_km", "route_time_rescue",
        "color", "width", "opacity", "labels", "icons", "ground",
        "color_mode", "altitude_bands",
        "stitch_gaps", "inferred_color", "stitch_gap_min",
        "integrate_pos", "pos_folder", "firehose_folder", "tail_icao",
        "pos_color", "firehose_color",
    }
    assert required <= keys, f"missing: {required - keys}"
    for e in cat:
        assert e.get("help"), f"{e['key']} missing help"
        assert e.get("example"), f"{e['key']} missing example"
        assert e.get("type") in {"folder", "file", "files", "text", "number",
                                 "slider", "toggle", "segmented", "color"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_route_builder_server.py::test_parameters_catalog_complete -v`
Expected: FAIL — `FileNotFoundError: parameters.json`.

- [ ] **Step 3: Write `route_builder/parameters.json`**

Create the catalog. Every entry has `help` and a concrete `example`. Use this content (grouped; `input`, `output`, `detection`, `detection_advanced`, `styling`, `styling_altitude`, `stitching`, `positions`):

```json
[
  {"key":"kml_folder","flag":"--kml-folder","group":"input","type":"folder","label":"Input KML folder","default":"","help":"Folder of ADS-B KML tracks to process (one file per tail/day).","example":"~/Git/kml/adsb_kml_downloader/output/jat_2026_jun-jul"},
  {"key":"airports","flag":"--airports","group":"input","type":"file","label":"Airports CSV","default":"","help":"CSV of airport,latitude,longitude,elevation_ft used for endpoint matching.","example":"input/jat_routes_202606-202607_airports.csv"},
  {"key":"routes","flag":"--routes","group":"input","type":"file","label":"Routes-time CSV","default":"","help":"CSV of origin,destination,avg_enroute_min used for route-time rescue.","example":"input/jat_routes_202606-202607_routes_time.csv"},
  {"key":"output_dir","flag":null,"group":"output","type":"folder","label":"Output folder","default":"output","help":"Folder the output KML is written to.","example":"output"},
  {"key":"output_name","flag":null,"group":"output","type":"text","label":"Output filename","default":"","help":"Name of the KML file to write. A name is suggested from the input batch and enabled features.","example":"jat_routes_2026_jun-jul_stitched.kml"},
  {"key":"sample","flag":"--sample","group":"detection","type":"number","label":"Sample interval (min)","default":2,"help":"Point sampling interval in minutes; larger = smaller files, coarser lines.","example":"2 keeps a point every 2 minutes."},
  {"key":"maxgap","flag":"--maxgap","group":"detection","type":"number","label":"Max in-flight gap (min)","default":20,"help":"A time gap larger than this splits one flight segment into two.","example":"20 tolerates a 20-minute ADS-B dropout within a flight."},
  {"key":"group","flag":"--group","group":"detection","type":"segmented","label":"Group by","default":"destination","help":"Organize the output KML folders by origin or destination airport.","example":"destination groups all arrivals into an airport together."},
  {"key":"confidence","flag":"--confidence","group":"detection","type":"segmented","label":"Confidence preset","default":"balanced","help":"Detection/recovery aggressiveness. strict = proximity only; permissive = widest recovery for sparse coverage.","example":"permissive for sparse Latin-America ADS-B; balanced for dense US/EU."},
  {"key":"airport_radius_km","flag":"--airport-radius-km","group":"detection_advanced","type":"number","label":"Airport match radius (km)","default":null,"help":"Override the preset's airport-proximity radius for endpoint detection.","example":"100 (permissive default) matches an airport within 100 km of a track end."},
  {"key":"max_join_gap_hours","flag":"--max-join-gap-hours","group":"detection_advanced","type":"number","label":"Max join gap (h)","default":null,"help":"Override max time gap for joining two fragments of one flight across files.","example":"4 lets fragments up to 4 hours apart join."},
  {"key":"max_join_distance_km","flag":"--max-join-distance-km","group":"detection_advanced","type":"number","label":"Max join distance (km)","default":null,"help":"Override max spatial gap (km) allowed when joining fragments.","example":"500 permits a 500 km straight-line join."},
  {"key":"route_time_tolerance","flag":"--route-time-tolerance","group":"detection_advanced","type":"number","label":"Route-time tolerance","default":null,"help":"Override route-time match tolerance (fraction). Higher = looser, more recoveries.","example":"0.60 = accept enroute times within +/-60% of the scheduled average."},
  {"key":"corridor_cross_track_km","flag":"--corridor-cross-track-km","group":"detection_advanced","type":"number","label":"Corridor cross-track (km)","default":null,"help":"The main recovery/noise knob. Lower = stricter (less mis-attribution, fewer recoveries).","example":"50 with the permissive preset gives the recommended clean-and-complete result."},
  {"key":"route_time_rescue","flag":"--route-time-rescue","group":"detection_advanced","type":"segmented","label":"Route-time rescue","default":"","help":"Force route-time rescue on or off, overriding the preset. Blank = use preset.","example":"on forces rescue even under the strict preset."},
  {"key":"color_mode","flag":"--color-mode","group":"styling","type":"segmented","label":"Color mode","default":"source","help":"source colors lines by data source (ADS-B/POS/firehose); altitude uses the ADS-B Exchange altitude gradient.","example":"altitude paints climb->cruise->descent from orange through blue to magenta."},
  {"key":"color","flag":"--color","group":"styling","type":"color","label":"Line color","default":"","help":"Override the line color (source mode only). KML AABBGGRR.","example":"ff0000ff draws all lines red."},
  {"key":"width","flag":"--width","group":"styling","type":"number","label":"Line width","default":"","help":"Override line width in pixels.","example":"2 draws a 2-px line."},
  {"key":"opacity","flag":"--opacity","group":"styling","type":"slider","label":"Opacity (%)","default":100,"help":"Line opacity 0-100 (100 = opaque).","example":"80 makes lines slightly translucent."},
  {"key":"labels","flag":"--labels","group":"styling","type":"toggle","label":"Show labels","default":false,"help":"Show a per-flight name label on each track.","example":"true labels each flight with its route."},
  {"key":"icons","flag":"--icons","group":"styling","type":"toggle","label":"Show airport icons","default":false,"help":"Draw an airport icon at endpoints.","example":"true shows a marker at each airport."},
  {"key":"ground","flag":"--ground","group":"styling","type":"toggle","label":"Extrude to ground","default":false,"help":"Extrude the 3-D flight path down to the ground.","example":"true draws vertical drop-lines under the track."},
  {"key":"altitude_bands","flag":"--altitude-bands","group":"styling_altitude","type":"number","label":"Altitude bands","default":24,"help":"Number of discrete color steps in altitude mode (more = smoother, bigger file).","example":"24 gives ~1700 ft per band over 0-40000 ft."},
  {"key":"stitch_gaps","flag":"--stitch-gaps","group":"stitching","type":"toggle","label":"Stitch coverage gaps","default":false,"help":"Draw straight-line connectors across coverage gaps in a distinct color to mark inferred data.","example":"true splits a flight into real placemarks + gray connectors."},
  {"key":"inferred_color","flag":"--inferred-color","group":"stitching","type":"color","label":"Inferred color","default":"ff888888","help":"KML AABBGGRR color for inferred gap connectors.","example":"ff888888 = gray."},
  {"key":"stitch_gap_min","flag":"--stitch-gap-min","group":"stitching","type":"number","label":"Stitch gap threshold (min)","default":5,"help":"Minutes between points above which a gap connector is drawn.","example":"5 connects gaps longer than 5 minutes."},
  {"key":"integrate_pos","flag":"--integrate-pos","group":"positions","type":"toggle","label":"Integrate position sources","default":false,"help":"Fill in-flight ADS-B gaps with real ACARS POS and/or firehose fixes (detection unchanged).","example":"true requires the POS/firehose folders + tail-icao below."},
  {"key":"pos_folder","flag":"--pos-folder","group":"positions","type":"folder","label":"ACARS POS folder","default":"","help":"Folder of ACARS position-report JSON files (keyed to hex via the mapping CSV).","example":"/Volumes/jy-ext/data_jat/kmltest/pos"},
  {"key":"firehose_folder","flag":"--firehose-folder","group":"positions","type":"folder","label":"ADS-B firehose folder","default":"","help":"Folder of ADS-B firehose position JSON files (carry ICAO hex directly).","example":"/Volumes/jy-ext/data_jat/kmltest/adsbpos"},
  {"key":"tail_icao","flag":"--tail-icao","group":"positions","type":"file","label":"Tail<->ICAO CSV","default":"","help":"CSV mapping tail_number,icao_hex — required to key ACARS POS to hex.","example":"~/Git/kml/adsb_kml_downloader/input/jat_active_tails_icao.csv"},
  {"key":"pos_color","flag":"--pos-color","group":"positions","type":"color","label":"POS color","default":"ff00a5ff","help":"KML AABBGGRR color for ACARS-POS-sourced portions.","example":"ff00a5ff = orange."},
  {"key":"firehose_color","flag":"--firehose-color","group":"positions","type":"color","label":"Firehose color","default":"ff00ff00","help":"KML AABBGGRR color for firehose-sourced portions.","example":"ff00ff00 = green."}
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_route_builder_server.py::test_parameters_catalog_complete -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add route_builder/parameters.json tests/test_route_builder_server.py
git commit -m "feat: add route_builder parameter catalog

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Manifest seed + `load_manifest` (server)

**Files:**
- Create: `route_builder/manifest.default.json`
- Create: `route_builder/server.py` (start the file; add `load_manifest`)
- Test: `tests/test_route_builder_server.py`

**Interfaces:**
- Produces: `server.load_manifest(app_dir) -> dict` — copies `manifest.default.json` → `manifest.json` on first call, expands `~` and makes `roots.*` absolute, returns the parsed manifest. `server.save_manifest(app_dir, manifest)`.

- [ ] **Step 1: Write `route_builder/manifest.default.json`**

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
  "server": { "port": 8765 },
  "defaults": {
    "confidence": "permissive", "corridor_cross_track_km": 50,
    "sample": 2, "maxgap": 20, "group": "destination",
    "color_mode": "source", "color": "", "width": "", "opacity": 100,
    "labels": false, "icons": false, "ground": false,
    "stitch_gaps": false, "inferred_color": "ff888888", "stitch_gap_min": 5,
    "integrate_pos": false, "pos_color": "ff00a5ff", "firehose_color": "ff00ff00",
    "altitude_palette": "adsbex", "altitude_bands": 24
  },
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

- [ ] **Step 2: Write the failing test**

```python
# add to tests/test_route_builder_server.py
import os, sys, shutil, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "route_builder"))
import server  # noqa: E402

APPDIR = os.path.join(os.path.dirname(__file__), "..", "route_builder")


def test_load_manifest_copies_seed_and_resolves_roots(tmp_path):
    d = str(tmp_path)
    shutil.copy(os.path.join(APPDIR, "manifest.default.json"),
                os.path.join(d, "manifest.default.json"))
    assert not os.path.exists(os.path.join(d, "manifest.json"))
    man = server.load_manifest(d)
    assert os.path.exists(os.path.join(d, "manifest.json"))          # seed copied to runtime
    assert os.path.isabs(man["roots"]["input_default"])              # ~ expanded, absolute
    assert any(p["builtin"] for p in man["presets"])
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_route_builder_server.py::test_load_manifest_copies_seed_and_resolves_roots -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server'` (file not created yet).

- [ ] **Step 4: Write minimal `route_builder/server.py`**

```python
#!/usr/bin/env python3
"""ADSB Historical Route Builder — stdlib-only local server. See EXTENSION_SPEC.md."""
import json
import os
import shutil

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve(path, base=REPO_ROOT):
    """Expand ~ and make absolute (relative paths resolve against the repo root)."""
    p = os.path.expanduser(path)
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(base, p))


def load_manifest(app_dir):
    """Copy manifest.default.json -> manifest.json on first run; return resolved manifest."""
    runtime = os.path.join(app_dir, "manifest.json")
    seed = os.path.join(app_dir, "manifest.default.json")
    if not os.path.exists(runtime):
        shutil.copy(seed, runtime)
    man = json.load(open(runtime))
    roots = man.setdefault("roots", {})
    for k in ("input_default", "airports_dir", "routes_dir", "output_default"):
        if roots.get(k):
            roots[k] = _resolve(roots[k])
    roots["allowed_roots"] = [_resolve(r) for r in roots.get("allowed_roots", [])]
    return man


def save_manifest(app_dir, manifest):
    """Persist the runtime manifest.json (used by preset add/delete)."""
    with open(os.path.join(app_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_route_builder_server.py::test_load_manifest_copies_seed_and_resolves_roots -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add route_builder/manifest.default.json route_builder/server.py tests/test_route_builder_server.py
git commit -m "feat: add route_builder manifest seed + load_manifest

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Path confinement (server)

**Files:**
- Modify: `route_builder/server.py` — add `confine_path`
- Test: `tests/test_route_builder_server.py`

**Interfaces:**
- Produces: `server.confine_path(path, allowed_roots) -> str` (realpath) or raises `PermissionError`.

- [ ] **Step 1: Write the failing test**

```python
def test_confine_path_accepts_inside_rejects_outside(tmp_path):
    root = str(tmp_path)
    inside = os.path.join(root, "sub", "f.kml")
    os.makedirs(os.path.dirname(inside)); open(inside, "w").close()
    assert server.confine_path(inside, [root]) == os.path.realpath(inside)
    import pytest
    with pytest.raises(PermissionError):
        server.confine_path("/etc/passwd", [root])
    with pytest.raises(PermissionError):
        server.confine_path(os.path.join(root, "..", "escape"), [root])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_route_builder_server.py::test_confine_path_accepts_inside_rejects_outside -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'confine_path'`.

- [ ] **Step 3: Write minimal implementation** (add to `server.py`)

```python
def confine_path(path, allowed_roots):
    """Return realpath(path) if it lives inside one of allowed_roots, else PermissionError."""
    rp = os.path.realpath(os.path.expanduser(path))
    for root in allowed_roots:
        rr = os.path.realpath(root)
        if rp == rr or rp.startswith(rr + os.sep):
            return rp
    raise PermissionError(f"path outside allowed roots: {path}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_route_builder_server.py::test_confine_path_accepts_inside_rejects_outside -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add route_builder/server.py tests/test_route_builder_server.py
git commit -m "feat: add path confinement guard

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: `build_argv` + `suggest_name` (server)

**Files:**
- Modify: `route_builder/server.py`
- Test: `tests/test_route_builder_server.py`

**Interfaces:**
- Produces: `server.build_argv(params, script=None) -> list[str]`; `server.suggest_name(batch_stem, params) -> str`. `params` is a dict keyed by the parameter `key`s from `parameters.json`.

- [ ] **Step 1: Write the failing test**

```python
def test_build_argv_folder_and_defaults_omitted():
    argv = server.build_argv({
        "kml_folder": "/data/jat", "airports": "/a.csv", "routes": "/r.csv",
        "output_dir": "/out", "output_name": "j.kml",
        "confidence": "permissive", "corridor_cross_track_km": 50,
        "sample": 2, "opacity": 100, "stitch_gaps": False,
    })
    assert argv[:3] == ["python3", "-u", server.SCRIPT_PATH_BASENAME] or argv[1] == "-u"
    assert "--kml-folder" in argv and "/data/jat" in argv
    assert "--output" in argv and "/out/j.kml" in argv
    assert "--confidence" in argv and "permissive" in argv
    assert "--stitch-gaps" not in argv          # false boolean omitted
    assert "--sample" not in argv               # equals script default -> omitted


def test_build_argv_files_and_booleans():
    argv = server.build_argv({
        "kml_files": ["/a.kml", "/b.kml"], "airports": "/a.csv", "routes": "/r.csv",
        "output_dir": "/out", "output_name": "j.kml",
        "stitch_gaps": True, "integrate_pos": True,
    })
    i = argv.index("--kml-files")
    assert argv[i + 1] == "/a.kml" and argv[i + 2] == "/b.kml"
    assert "--stitch-gaps" in argv and argv[argv.index("--stitch-gaps") + 1] == "true"
    assert "--integrate-pos" in argv


def test_suggest_name_suffixes():
    assert server.suggest_name("jat_2026_jun-jul", {}) == "jat_routes_2026_jun-jul.kml"
    assert server.suggest_name("jat_2026_jun-jul", {"stitch_gaps": True}) \
        == "jat_routes_2026_jun-jul_stitched.kml"
    n = server.suggest_name("jat_2026_jun-jul", {"integrate_pos": True, "color_mode": "altitude"})
    assert n.endswith("_pos_altcolor.kml")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_route_builder_server.py -k "build_argv or suggest_name" -v`
Expected: FAIL — `AttributeError: build_argv`.

- [ ] **Step 3: Write minimal implementation** (add to `server.py`)

```python
SCRIPT_PATH = os.path.join(REPO_ROOT, "adsb_historical_routes.py")
SCRIPT_PATH_BASENAME = "adsb_historical_routes.py"

# key -> (flag, script_default). None default means "always emit if truthy/non-empty".
_ARGV_MAP = [
    ("sample", "--sample", 2), ("maxgap", "--maxgap", 20),
    ("group", "--group", "destination"), ("confidence", "--confidence", "balanced"),
    ("airport_radius_km", "--airport-radius-km", None),
    ("max_join_gap_hours", "--max-join-gap-hours", None),
    ("max_join_distance_km", "--max-join-distance-km", None),
    ("route_time_tolerance", "--route-time-tolerance", None),
    ("corridor_cross_track_km", "--corridor-cross-track-km", None),
    ("route_time_rescue", "--route-time-rescue", ""),
    ("color", "--color", ""), ("width", "--width", ""), ("opacity", "--opacity", 100),
    ("color_mode", "--color-mode", "source"), ("altitude_bands", "--altitude-bands", 24),
    ("inferred_color", "--inferred-color", "ff888888"),
    ("stitch_gap_min", "--stitch-gap-min", 5),
    ("pos_color", "--pos-color", "ff00a5ff"), ("firehose_color", "--firehose-color", "ff00ff00"),
    ("pos_folder", "--pos-folder", ""), ("firehose_folder", "--firehose-folder", ""),
    ("tail_icao", "--tail-icao", ""),
]
_BOOL_FLAGS = [("labels", "--labels"), ("icons", "--icons"), ("ground", "--ground"),
               ("stitch_gaps", "--stitch-gaps"), ("integrate_pos", "--integrate-pos")]


def build_argv(params, script=None):
    """Map a params dict -> a `python3 -u adsb_historical_routes.py …` argv list."""
    argv = ["python3", "-u", script or SCRIPT_PATH]
    if params.get("kml_files"):
        argv += ["--kml-files", *params["kml_files"]]
    elif params.get("kml_folder"):
        argv += ["--kml-folder", params["kml_folder"]]
    argv += ["--airports", params.get("airports", "")]
    argv += ["--routes", params.get("routes", "")]
    out = os.path.join(params.get("output_dir", ""), params.get("output_name", ""))
    argv += ["--output", out]
    for key, flag, default in _ARGV_MAP:
        if key not in params:
            continue
        val = params[key]
        if val in (None, "") or val == default:
            continue
        argv += [flag, str(val)]
    for key, flag in _BOOL_FLAGS:
        if params.get(key):                       # only emit when user turned it on
            argv += [flag, "true"]
    return argv


def suggest_name(batch_stem, params):
    """Suggest an output filename from an input batch folder name + enabled features."""
    m = __import__("re").match(r"([a-z]+)_(.+)", batch_stem)
    airline, period = (m.group(1), m.group(2)) if m else (batch_stem, "")
    stem = f"{airline}_routes_{period}" if period else f"{batch_stem}_routes"
    if params.get("confidence") == "strict":
        stem += "_strict"
    if params.get("stitch_gaps"):
        stem += "_stitched"
    if params.get("integrate_pos"):
        stem += "_pos"
    if params.get("color_mode") == "altitude":
        stem += "_altcolor"
    return stem + ".kml"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_route_builder_server.py -k "build_argv or suggest_name" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add route_builder/server.py tests/test_route_builder_server.py
git commit -m "feat: add build_argv + suggest_name

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Summary parse + line classification (server)

**Files:**
- Modify: `route_builder/server.py`
- Test: `tests/test_route_builder_server.py`

**Interfaces:**
- Produces: `server.parse_summary(text) -> dict` (keys: `flights`, `filled_fixes`, `filled_flights`, `total_flights`, `output`, `points_in`, `points_out`, `compression`, `connectors`; missing keys omitted). `server.classify_line(line) -> (kind, payload)` where `kind ∈ {"progress","phase","raw"}`.

- [ ] **Step 1: Write the failing test**

```python
SAMPLE_STDOUT = """Processing: e80600_2026-07-01_baro_avg.kml (Aircraft: E80600)
Loading extra position sources (this reads many small files)...
Combining segments by aircraft...
Creating output KML...
TOTAL VALID COMPLETE FLIGHTS: 5607
Position-source integration: filled 340,298 real fix(es) into 3356 of 5607 flights
Output written to: output/jat.kml
Data reduction: 2059367 points -> 183302 points
Compression ratio: 8.9%
"""


def test_parse_summary_extracts_fields():
    s = server.parse_summary(SAMPLE_STDOUT)
    assert s["total_flights"] == 5607
    assert s["filled_fixes"] == 340298 and s["filled_flights"] == 3356
    assert s["output"].endswith("jat.kml")
    assert s["points_in"] == 2059367 and s["points_out"] == 183302
    assert s["compression"] == 8.9


def test_classify_line():
    assert server.classify_line("Processing: x.kml (Aircraft: E1)")[0] == "progress"
    assert server.classify_line("Creating output KML...")[0] == "phase"
    assert server.classify_line("random noise")[0] == "raw"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_route_builder_server.py -k "parse_summary or classify_line" -v`
Expected: FAIL — `AttributeError: parse_summary`.

- [ ] **Step 3: Write minimal implementation** (add to `server.py`)

```python
import re

_PHASE_PREFIXES = ("Loading extra position sources", "Combining segments by aircraft",
                   "Creating output KML", "Position-source integration:")


def classify_line(line):
    s = line.strip()
    if s.startswith("Processing:"):
        return ("progress", s)
    for p in _PHASE_PREFIXES:
        if s.startswith(p):
            return ("phase", p.rstrip(":. "))
    return ("raw", s)


def parse_summary(text):
    out = {}
    def grab(pat, cast=str):
        mm = re.search(pat, text)
        return cast(mm.group(1).replace(",", "")) if mm else None
    for key, pat, cast in [
        ("total_flights", r"TOTAL VALID COMPLETE FLIGHTS:\s*(\d+)", int),
        ("output", r"Output written to:\s*(.+)", str),
        ("points_in", r"Data reduction:\s*(\d+) points", int),
        ("points_out", r"Data reduction:\s*\d+ points -> (\d+) points", int),
        ("compression", r"Compression ratio:\s*([\d.]+)%", float),
        ("connectors", r"Gap stitching:\s*(\d+) inferred connector", int),
    ]:
        v = grab(pat, cast)
        if v is not None:
            out[key] = v.strip() if isinstance(v, str) else v
    mm = re.search(r"filled ([\d,]+) real fix\(es\) into (\d+) of (\d+) flights", text)
    if mm:
        out["filled_fixes"] = int(mm.group(1).replace(",", ""))
        out["filled_flights"] = int(mm.group(2))
        out["total_flights"] = int(mm.group(3))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_route_builder_server.py -k "parse_summary or classify_line" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add route_builder/server.py tests/test_route_builder_server.py
git commit -m "feat: add summary parse + line classification

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Pre-flight `validate` (server)

**Files:**
- Modify: `route_builder/server.py`
- Test: `tests/test_route_builder_server.py`

**Interfaces:**
- Produces: `server.validate(params, allowed_roots) -> dict` with `kml_count, airports_count, routes_count, tail_icao_count, output_path, output_exists, argv, warnings, errors`.

- [ ] **Step 1: Write the failing test**

```python
def _csv(path, header, rows):
    with open(path, "w") as f:
        f.write(header + "\n" + "\n".join(rows) + "\n")


def test_validate_counts_and_errors(tmp_path):
    root = str(tmp_path)
    kdir = os.path.join(root, "kml"); os.makedirs(kdir)
    for n in ("a.kml", "b.kml", "c.kml"):
        open(os.path.join(kdir, n), "w").close()
    ap = os.path.join(root, "ap.csv"); _csv(ap, "airport,latitude,longitude,elevation_ft",
                                             ["AAA,0,0,0", "BBB,1,1,1"])
    rt = os.path.join(root, "rt.csv"); _csv(rt, "origin,destination,avg_enroute_min",
                                             ["AAA,BBB,60"])
    params = {"kml_folder": kdir, "airports": ap, "routes": rt,
              "output_dir": root, "output_name": "o.kml"}
    v = server.validate(params, [root])
    assert v["kml_count"] == 3 and v["airports_count"] == 2 and v["routes_count"] == 1
    assert v["output_exists"] is False and not v["errors"]
    # missing airports -> error
    v2 = server.validate({**params, "airports": os.path.join(root, "nope.csv")}, [root])
    assert any("airport" in e.lower() for e in v2["errors"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_route_builder_server.py::test_validate_counts_and_errors -v`
Expected: FAIL — `AttributeError: validate`.

- [ ] **Step 3: Write minimal implementation** (add to `server.py`)

```python
def _count_kml(folder):
    try:
        return sum(1 for e in os.scandir(folder) if e.is_file() and e.name.endswith(".kml"))
    except OSError:
        return 0


def _count_csv_rows(path):
    try:
        with open(path) as f:
            return max(0, sum(1 for _ in f) - 1)   # minus header
    except OSError:
        return 0


def validate(params, allowed_roots):
    errors, warnings = [], []

    def ok(path, label):
        if not path:
            errors.append(f"{label} not selected")
            return None
        try:
            rp = confine_path(path, allowed_roots)
        except PermissionError:
            errors.append(f"{label} is outside the allowed folders")
            return None
        if not os.path.exists(rp):
            errors.append(f"{label} not found: {path}")
            return None
        return rp

    kml_count = 0
    if params.get("kml_files"):
        kml_count = len(params["kml_files"])
    else:
        kf = ok(params.get("kml_folder"), "Input KML folder")
        if kf:
            kml_count = _count_kml(kf)
            if kml_count == 0:
                errors.append("Input folder contains no .kml files")
    ap = ok(params.get("airports"), "Airports CSV")
    rt = ok(params.get("routes"), "Routes CSV")

    tail_count = 0
    if params.get("integrate_pos"):
        if not params.get("tail_icao"):
            errors.append("Integrate positions is on but no tail<->icao CSV is set")
        if not (params.get("pos_folder") or params.get("firehose_folder")):
            errors.append("Integrate positions is on but no POS or firehose folder is set")
        ti = ok(params.get("tail_icao"), "Tail<->ICAO CSV") if params.get("tail_icao") else None
        tail_count = _count_csv_rows(ti) if ti else 0

    out_path = os.path.join(params.get("output_dir", ""), params.get("output_name", ""))
    if not params.get("output_name"):
        errors.append("Output filename not set")
    output_exists = bool(out_path) and os.path.exists(out_path)
    if output_exists:
        warnings.append("Output file already exists and will be overwritten")
    if params.get("color_mode") == "altitude" and params.get("color"):
        warnings.append("Line-color override is ignored in altitude color mode")

    return {
        "kml_count": kml_count,
        "airports_count": _count_csv_rows(ap) if ap else 0,
        "routes_count": _count_csv_rows(rt) if rt else 0,
        "tail_icao_count": tail_count,
        "output_path": out_path,
        "output_exists": output_exists,
        "argv": build_argv(params),
        "warnings": warnings,
        "errors": errors,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_route_builder_server.py::test_validate_counts_and_errors -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add route_builder/server.py tests/test_route_builder_server.py
git commit -m "feat: add pre-flight validate (counts + errors + argv)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: RunManager — subprocess + progress (server)

**Files:**
- Modify: `route_builder/server.py`
- Test: `tests/test_route_builder_server.py`

**Interfaces:**
- Produces: `server.RunManager()` with `start(argv, total_files) -> job_id`, `events(job_id) -> generator of (event_name, payload_dict)` (blocks until `done`), `cancel(job_id)`, `get(job_id) -> dict`. Emits events `line`, `progress` (`{processed,total,percent,eta_sec,phase}`), `phase`, `done` (`{exit_code,success,summary,output_size}`), `error`.

- [ ] **Step 1: Write the failing test** (uses a tiny fake script so it runs fast)

```python
def test_runmanager_streams_and_parses(tmp_path):
    fake = os.path.join(str(tmp_path), "fake.py")
    with open(fake, "w") as f:
        f.write(
            "print('Processing: a.kml (Aircraft: E1)')\n"
            "print('Processing: b.kml (Aircraft: E2)')\n"
            "print('Creating output KML...')\n"
            "print('TOTAL VALID COMPLETE FLIGHTS: 2')\n"
            "print('Output written to: %s')\n" % os.path.join(str(tmp_path), "o.kml")
        )
    open(os.path.join(str(tmp_path), "o.kml"), "w").close()
    rm = server.RunManager()
    jid = rm.start(["python3", "-u", fake], total_files=2)
    kinds, done = [], None
    for name, payload in rm.events(jid):
        kinds.append(name)
        if name == "done":
            done = payload
    assert "progress" in kinds and "done" in kinds
    assert done["success"] is True
    assert done["summary"]["total_flights"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_route_builder_server.py::test_runmanager_streams_and_parses -v`
Expected: FAIL — `AttributeError: RunManager`.

- [ ] **Step 3: Write minimal implementation** (add to `server.py`)

```python
import subprocess
import threading
import queue
import time
import uuid


class RunManager:
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def start(self, argv, total_files=0, cwd=REPO_ROOT):
        jid = uuid.uuid4().hex[:12]
        job = {"q": queue.Queue(), "proc": None, "status": "running",
               "total": total_files, "processed": 0, "start": time.time(),
               "buf": [], "summary": {}, "output": None}
        with self._lock:
            self._jobs[jid] = job
        try:
            proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
        except OSError as e:
            job["status"] = "error"
            job["q"].put(("error", {"message": str(e)}))
            job["q"].put(("done", {"exit_code": -1, "success": False, "summary": {}}))
            return jid
        job["proc"] = proc
        threading.Thread(target=self._pump, args=(jid, job), daemon=True).start()
        return jid

    def _pump(self, jid, job):
        proc = job["proc"]
        for line in proc.stdout:
            line = line.rstrip("\n")
            job["buf"].append(line)
            job["q"].put(("line", {"text": line}))
            kind, payload = classify_line(line)
            if kind == "progress":
                job["processed"] += 1
                elapsed = time.time() - job["start"]
                done_n = job["processed"]
                eta = (elapsed / done_n) * (job["total"] - done_n) if done_n and job["total"] else None
                pct = (done_n / job["total"] * 100) if job["total"] else None
                job["q"].put(("progress", {"processed": done_n, "total": job["total"],
                                           "percent": pct, "eta_sec": eta, "phase": None}))
            elif kind == "phase":
                job["q"].put(("phase", {"phase": payload}))
        code = proc.wait()
        text = "\n".join(job["buf"])
        summary = parse_summary(text)
        job["summary"] = summary
        out = summary.get("output")
        size = os.path.getsize(out) if out and os.path.exists(out) else None
        success = (code == 0 and "output" in summary)
        job["status"] = "done" if success else "error"
        job["q"].put(("done", {"exit_code": code, "success": success,
                               "summary": summary, "output_size": size}))

    def events(self, jid):
        job = self._jobs.get(jid)
        if not job:
            return
        while True:
            name, payload = job["q"].get()
            yield name, payload
            if name == "done":
                break

    def cancel(self, jid):
        job = self._jobs.get(jid)
        if job and job.get("proc") and job["status"] == "running":
            job["proc"].terminate()

    def get(self, jid):
        return self._jobs.get(jid)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_route_builder_server.py::test_runmanager_streams_and_parses -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add route_builder/server.py tests/test_route_builder_server.py
git commit -m "feat: add RunManager (subprocess + progress + summary)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: HTTP layer — `make_server` + JSON/SSE handlers

**Files:**
- Modify: `route_builder/server.py` — add `browse`, `list_outputs`, the request handler, `make_server(port, app_dir, allowed_roots=None)`, and `main()`.
- Test: `tests/test_route_builder_server.py`

**Interfaces:**
- Consumes: all prior helpers + `RunManager`.
- Produces: `server.make_server(port, app_dir) -> ThreadingHTTPServer` (configured, not serving). Routes: `GET /`, `GET /parameters.json`, `GET /api/config`, `GET /api/browse?path=`, `GET /api/outputs?dir=`, `POST /api/validate`, `POST /api/presets`, `DELETE /api/presets?name=`, `POST /api/run`, `GET /api/run/<id>/stream`, `POST /api/run/<id>/cancel`, `POST /api/run/<id>/reveal`.

- [ ] **Step 1: Write the failing test**

```python
import json as _json
import threading as _th
import urllib.request as _rq


def _start(tmp_path):
    d = str(tmp_path)
    shutil.copy(os.path.join(APPDIR, "manifest.default.json"),
                os.path.join(d, "manifest.default.json"))
    shutil.copy(os.path.join(APPDIR, "parameters.json"), os.path.join(d, "parameters.json"))
    # index.html may not exist yet in this task; a stub keeps GET / happy
    if not os.path.exists(os.path.join(d, "index.html")):
        open(os.path.join(d, "index.html"), "w").write("<!doctype html><title>rb</title>")
    srv = server.make_server(0, d, allowed_roots=[d])
    port = srv.server_address[1]
    t = _th.Thread(target=srv.serve_forever, daemon=True); t.start()
    return srv, f"http://127.0.0.1:{port}"


def _get(base, path):
    return _json.loads(_rq.urlopen(base + path).read())


def test_http_config_browse_and_presets(tmp_path):
    srv, base = _start(tmp_path)
    try:
        cfg = _get(base, "/api/config")
        assert "presets" in cfg and "parameters" in cfg
        # browse the temp dir
        sub = os.path.join(str(tmp_path), "kml"); os.makedirs(sub)
        open(os.path.join(sub, "x.kml"), "w").close()
        br = _get(base, "/api/browse?path=" + _rq.quote(str(tmp_path)))
        assert any(e["name"] == "kml" and e["type"] == "dir" for e in br["entries"])
        # add a preset
        req = _rq.Request(base + "/api/presets", method="POST",
                          data=_json.dumps({"name": "Mine", "params": {"sample": 3}}).encode(),
                          headers={"Content-Type": "application/json"})
        res = _json.loads(_rq.urlopen(req).read())
        assert any(p["name"] == "Mine" for p in res["presets"])
    finally:
        srv.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_route_builder_server.py::test_http_config_browse_and_presets -v`
Expected: FAIL — `AttributeError: make_server`.

- [ ] **Step 3: Write minimal implementation** (add to `server.py`)

```python
import argparse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def browse(path, allowed_roots):
    rp = confine_path(path, allowed_roots)
    entries = []
    for e in sorted(os.scandir(rp), key=lambda x: (x.is_file(), x.name.lower())):
        if e.name.startswith("."):
            continue
        if e.is_dir():
            entries.append({"name": e.name, "type": "dir", "kml_count": _count_kml(e.path)})
        else:
            ext = e.name.rsplit(".", 1)[-1].lower() if "." in e.name else ""
            kind = {"kml": "kml", "csv": "csv", "json": "json"}.get(ext, "other")
            entries.append({"name": e.name, "type": "file", "kind": kind,
                            "size": e.stat().st_size})
    parent = os.path.dirname(rp)
    return {"path": rp, "parent": parent if parent != rp else None, "entries": entries}


def list_outputs(dir_path, allowed_roots):
    rp = confine_path(dir_path, allowed_roots)
    files = [{"name": e.name, "size": e.stat().st_size}
             for e in os.scandir(rp) if e.is_file() and e.name.endswith(".kml")]
    return {"files": sorted(files, key=lambda f: f["name"])}


def make_server(port, app_dir=APP_DIR, allowed_roots=None):
    manifest = load_manifest(app_dir)
    roots = allowed_roots or manifest["roots"]["allowed_roots"]
    params_catalog = json.load(open(os.path.join(app_dir, "parameters.json")))
    runs = RunManager()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj=None, ctype="application/json"):
            body = b"" if obj is None else (obj if isinstance(obj, bytes)
                                            else json.dumps(obj).encode())
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path == "/":
                    return self._send(200, open(os.path.join(app_dir, "index.html"), "rb").read(),
                                      "text/html; charset=utf-8")
                if u.path == "/parameters.json":
                    return self._send(200, params_catalog)
                if u.path == "/api/config":
                    man = load_manifest(app_dir)
                    return self._send(200, {"roots": man["roots"], "defaults": man["defaults"],
                                            "presets": man["presets"], "parameters": params_catalog})
                if u.path == "/api/browse":
                    path = unquote(q.get("path", [man_input(app_dir)])[0])
                    return self._send(200, browse(path, roots))
                if u.path == "/api/outputs":
                    return self._send(200, list_outputs(unquote(q["dir"][0]), roots))
                if u.path.startswith("/api/run/") and u.path.endswith("/stream"):
                    return self._stream(u.path.split("/")[3])
                return self._send(404, {"error": "not found"})
            except PermissionError as e:
                return self._send(403, {"error": str(e)})
            except Exception as e:
                return self._send(400, {"error": str(e)})

        def do_POST(self):
            u = urlparse(self.path)
            try:
                if u.path == "/api/validate":
                    return self._send(200, validate(self._body(), roots))
                if u.path == "/api/presets":
                    return self._send(200, self._add_preset(self._body()))
                if u.path == "/api/run":
                    params = self._body()
                    v = validate(params, roots)
                    if v["errors"]:
                        return self._send(400, {"error": "; ".join(v["errors"])})
                    for p in params.get("kml_folder", "") and [params["kml_folder"]] or []:
                        confine_path(p, roots)
                    jid = runs.start(build_argv(params), total_files=v["kml_count"])
                    return self._send(200, {"job_id": jid})
                if u.path.startswith("/api/run/") and u.path.endswith("/cancel"):
                    runs.cancel(u.path.split("/")[3]); return self._send(200, {"status": "cancelled"})
                if u.path.startswith("/api/run/") and u.path.endswith("/reveal"):
                    return self._reveal(u.path.split("/")[3])
                return self._send(404, {"error": "not found"})
            except PermissionError as e:
                return self._send(403, {"error": str(e)})
            except Exception as e:
                return self._send(400, {"error": str(e)})

        def do_DELETE(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/api/presets":
                return self._send(200, self._del_preset(unquote(q.get("name", [""])[0])))
            return self._send(404, {"error": "not found"})

        def _add_preset(self, body):
            man = load_manifest(app_dir)
            name = body.get("name", "").strip()
            if not name:
                raise ValueError("preset name required")
            man["presets"] = [p for p in man["presets"] if p["name"] != name or p.get("builtin")]
            if any(p["name"] == name and p.get("builtin") for p in man["presets"]):
                raise ValueError("cannot overwrite a built-in preset")
            man["presets"].append({"name": name, "params": body.get("params", {})})
            save_manifest(app_dir, man)
            return {"presets": man["presets"]}

        def _del_preset(self, name):
            man = load_manifest(app_dir)
            tgt = [p for p in man["presets"] if p["name"] == name]
            if tgt and tgt[0].get("builtin"):
                return self._send(409, {"error": "cannot delete a built-in preset"})
            man["presets"] = [p for p in man["presets"] if p["name"] != name]
            save_manifest(app_dir, man)
            return {"presets": man["presets"]}

        def _stream(self, jid):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                for name, payload in runs.events(jid):
                    chunk = f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _reveal(self, jid):
            job = runs.get(jid)
            out = (job or {}).get("summary", {}).get("output")
            if out and os.path.exists(out) and shutil.which("open"):
                subprocess.Popen(["open", "-R", out])
                return self._send(200, {"status": "revealed"})
            return self._send(200, {"status": "unavailable", "path": out})

    def man_input(_dir):
        return load_manifest(_dir)["roots"]["input_default"]

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    ap = argparse.ArgumentParser(description="ADSB Historical Route Builder server")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()
    manifest = load_manifest(APP_DIR)
    port = args.port or manifest.get("server", {}).get("port", 8765)
    try:
        srv = make_server(port, APP_DIR)
    except OSError as e:
        raise SystemExit(f"Could not bind 127.0.0.1:{port} ({e}). Try --port <other>.")
    url = f"http://127.0.0.1:{port}"
    print(f"ADSB Historical Route Builder → {url}  (Ctrl-C to stop)")
    if args.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")


if __name__ == "__main__":
    main()
```

Note: move `man_input` above its use if the executor's linter complains — it is only
called at request time, so definition order inside `make_server` is fine at runtime.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_route_builder_server.py::test_http_config_browse_and_presets -v`
Expected: PASS.

- [ ] **Step 5: Run the whole server test file**

Run: `python3 -m pytest tests/test_route_builder_server.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add route_builder/server.py tests/test_route_builder_server.py
git commit -m "feat: add HTTP/SSE layer (config, browse, validate, presets, run)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 11: Frontend `index.html`

**Files:**
- Create: `route_builder/index.html`
- Verify: manual (launch + run a small batch)

**Interfaces:**
- Consumes: `/api/config`, `/api/browse`, `/api/outputs`, `/api/validate`, `/api/presets`, `/api/run`, `/api/run/<id>/stream`, `/api/run/<id>/cancel`, `/api/run/<id>/reveal`.

Build one self-contained file. Because it is ~600–800 lines of UI, implement it in the
sub-steps below and verify by launching — there is no unit test. Reuse the route_explorer
**ft-system** tokens verbatim for visual continuity.

- [ ] **Step 1: Shell, tokens, theme.** Add `<style>` with the ft-system `:root` (light)
  and `[data-theme="dark"]` token blocks (indigo `--accent:#5B4DE0`; dark
  `--accent:#8b7cf0`; backgrounds/panels/lines/text/red/green/amber per the spec's
  reference report), system font, 13px, tabular-nums. Layout: a 48px topbar
  (wordmark `ADSB HISTORICAL ROUTE BUILDER` in accent + a status chip + theme toggle)
  over a two-column grid (`form` | `run panel`), single-column below 900px. Persist
  theme in `localStorage["rb-theme"]`. **Verify:** open the file directly — chrome
  renders in both themes.

- [ ] **Step 2: Boot + state.** On load, `fetch('/api/config')`; keep a JS `params`
  object seeded from `defaults`. Render the **Presets bar**: a `<select>` of
  `config.presets` (built-ins + user), applying a preset overlays its `params` onto
  `defaults`; *Save as preset* prompts a name → `POST /api/presets` → refresh list;
  *Delete* → `DELETE /api/presets?name=` (hidden for built-ins). **Verify:** presets list
  loads; applying one updates the form.

- [ ] **Step 3: Form generation.** Render controls grouped by `parameters[].group` in the
  section order `input, output, detection, styling, stitching, positions`, with
  `detection_advanced` inside a collapsed `<details>` under Detection and
  `styling_altitude` shown only when `color_mode==="altitude"`. Map `type` →
  control: `folder/file/files`→a read-only field + **Browse** button (Step 4);
  `text`→input; `number`→number input; `slider`→range 0–100; `toggle`→a switch;
  `segmented`→a pill group; `color`→a native color input + opacity that reads/writes KML
  `AABBGGRR` (pure helpers `rgbaToKml(hex,opacity)` / `kmlToRgba(aabbggrr)` inline).
  Each control shows an ⓘ button whose tooltip is `help` + a line `Example: <example>`.
  **Verify:** every parameter appears with its tooltip; toggling `integrate_pos` reveals
  the positions fields; `color_mode=altitude` reveals the altitude section.

- [ ] **Step 4: Browse modal.** A modal listing `/api/browse?path=` results: a breadcrumb /
  Up (using `parent`), folders (with their `kml_count`) and files filtered by the field's
  kind (`.kml` for folders/files pickers, `.csv` for airports/routes/tail, any for output
  dir). Selecting a folder fills a `folder` field; a **Use this folder** button confirms
  a `--kml-folder`; multi-select checkboxes on `.kml` files fill a `files` field. Root
  defaults to `roots.input_default`. **Verify:** can navigate from the default root into
  `jat_2026_jun-jul` and pick it; airports picker lists `input/*.csv`.

- [ ] **Step 5: Smart defaults.** When an input folder is chosen, derive its basename; call
  a small client helper to prefill airports/routes by airline-prefix (query
  `/api/browse?path=<airports_dir>` and pick the file whose name starts with the airline
  code + that contains `airport`/`routes_time`); set `output_dir` to
  `roots.output_default` and `output_name` to a suggested name (mirror `suggest_name`
  logic in JS, or call `/api/validate` which returns `argv` and derive from output). Show
  the output folder's existing files via `/api/outputs` with a collision badge. **Verify:**
  picking the JAT batch prefills both CSVs and a sensible filename.

- [ ] **Step 6: Pre-flight card.** Debounced (300 ms) `POST /api/validate` on any change;
  render `N KML · N airports · N routes` (+ tail count when integrating), the resolved
  output path with a collision/warning line, the `errors` list, and the exact `argv`
  joined as a command line in a monospace block. Disable **Run** while `errors` non-empty.
  **Verify:** counts match the folder; a missing CSV disables Run with a clear message.

- [ ] **Step 7: Run + live console.** **Run** → `POST /api/run` → open
  `new EventSource('/api/run/'+job_id+'/stream')`. On `progress` update a bar + `ETA
  mm:ss` + `processed/total`; on `phase` show the milestone (indeterminate bar during
  "Loading position sources"); on `line` append to an auto-scrolling console
  (raw/errors-only toggle); on `done` stop the stream. Show a **Cancel** button
  (`POST …/cancel`). Update the topbar status chip (Running). **Verify:** running a small
  subset streams lines and advances the bar.

- [ ] **Step 8: Result + reset.** On `done.success` show a large green ✓ with the parsed
  summary (valid flights; filled fixes/flights when present; data reduction; connectors;
  output name + humanized size) and buttons **Reveal in Finder** (`POST …/reveal`) and
  **Run another batch** (reset only the run panel, keep `params`, clear `output_name` to
  re-suggest). On failure show a red ✗ with the stderr/tail. Update the status chip.
  **Verify:** success and a deliberately-broken run (bad CSV) both render correctly.

- [ ] **Step 9: Altitude legend.** In the styling section when `color_mode==="altitude"`,
  draw the ADS-B Exchange legend: a CSS linear-gradient bar built from the palette stops
  (compute stop colors in JS via an `hslToHex` helper matching the server hues) with tick
  labels `0 500 1000 2000 4000 6000 8000 10000 20000 30000 40000+`. **Verify:** the legend
  matches the screenshot ramp.

- [ ] **Step 10: Full manual run.** Launch `python3 route_builder/server.py --open`; pick
  the JAT batch (whole folder), confirm prefilled CSVs, choose a small subset via
  file-select to keep it fast, Run, watch progress → ✓, Reveal, then Run another batch.
  Fix any issues found. **Commit:**

```bash
git add route_builder/index.html
git commit -m "feat: add Route Builder frontend (form, pickers, run console, result)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 12: Launcher + gitignore + first-run polish

**Files:**
- Create: `route_builder/Route Builder.command`
- Modify: `.gitignore`

- [ ] **Step 1: Write the launcher**

`route_builder/Route Builder.command`:
```sh
#!/bin/sh
cd "$(dirname "$0")/.." || exit 1
exec python3 route_builder/server.py --open "$@"
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x "route_builder/Route Builder.command"`

- [ ] **Step 3: Ignore the runtime manifest**

Append to `.gitignore`:
```
route_builder/manifest.json
```

- [ ] **Step 4: Verify launch**

Run: `"./route_builder/Route Builder.command" --port 8790` (Ctrl-C to stop). Expected:
prints the URL, opens the browser, serves the page. Confirm `route_builder/manifest.json`
was created and is git-ignored (`git status` shows it untracked-ignored).

- [ ] **Step 5: Commit**

```bash
git add "route_builder/Route Builder.command" .gitignore
git commit -m "feat: add double-click launcher; ignore runtime manifest

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 13: Extension spec `EXTENSION_SPEC.md`

**Files:**
- Create: `route_builder/EXTENSION_SPEC.md`

- [ ] **Step 1: Write the contract doc**

Document the tool-shell contract (from spec §11) so future sibling tools (airport-file
builder, route-time builder, icao↔hex matcher) reuse this shell. Cover: (a) a tool =
`{id, title, parameters catalog, command template (script + build_argv mapping), manifest
section}`; (b) the server endpoints are tool-agnostic — a new tool registers a catalog +
an argv mapping + its own summary-parse regexes; (c) the frontend shell (topbar +
form-from-catalog + browse modal + run console) is reused, gaining a tool tab when a
second tool is added; (d) `parameters.json` schema (the field keys used in Task 3);
(e) how `manifest.default.json` gains a per-tool section. Point to `server.py` functions
as the reference implementation.

- [ ] **Step 2: Commit**

```bash
git add route_builder/EXTENSION_SPEC.md
git commit -m "docs: add tool-shell extension spec for future sibling tools

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 14: README + final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the GUI** — add a top-level "GUI: ADSB Historical Route Builder"
  section: what it is, how to launch (double-click `Route Builder.command` **or**
  `python3 route_builder/server.py --open`, `--port` to change port), the run workflow,
  presets, and that it drives the same script/flags.

- [ ] **Step 2: Document altitude coloring** — in the parameter reference and the styling
  section, add `--color-mode {source,altitude}`, `--altitude-palette adsbex`,
  `--altitude-bands` with the ADS-B Exchange legend description.

- [ ] **Step 3: Full test suite**

Run: `python3 -m pytest tests/ -q`
Expected: all PASS (script 85 + server tests), no skips beyond the known 1.

- [ ] **Step 4: Syntax/import check**

Run: `python3 -c "import sys; sys.path.insert(0,'route_builder'); import server; print('ok')"`
Expected: `ok` (module imports without starting the server).

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document Route Builder GUI + altitude color mode

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review (completed while writing)

- **Spec coverage:** §3 architecture → Tasks 4–12; §4 endpoints → Tasks 8–10; §4.4
  progress/summary → Tasks 7, 9; §5 frontend → Task 11; §6 manifest/presets → Tasks 4, 10;
  §7 parameter catalog → Task 3; §8 altitude coloring → Tasks 1, 2; §9 launcher → Task 12;
  §10 security (confinement, localhost, no-shell) → Tasks 5, 9, 10; §11 extension →
  Task 13; §12 files & §13 testing → all tasks + Task 14. No gaps.
- **Placeholder scan:** all code steps contain real code; no TBD/TODO.
- **Type consistency:** `build_argv`/`validate`/`RunManager`/`parse_summary`/`classify_line`/
  `confine_path`/`load_manifest`/`suggest_name`/`make_server` names and signatures are used
  identically across tasks; SSE event names (`line/progress/phase/done/error`) match between
  Task 9 (emit) and Tasks 10–11 (consume); param keys match `parameters.json` (Task 3),
  the manifest defaults (Task 4), and `build_argv` (Task 6).
