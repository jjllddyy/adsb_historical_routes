# Cirium Input Automation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `build_cirium_inputs.py`, which converts a raw Cirium schedule CSV into the `*_routes_time.csv` and `*_airports.csv` inputs required by `adsb_historical_routes.py`, using the shared `airport_lookup` tool for IATA→ICAO + coordinates.

**Architecture:** A stdlib-only script of small pure functions (parse → aggregate → convert → write → report) plus a thin lazy adapter over `conversion_tools/airport_lookup`. Pure functions take an injected `resolve(iata)` callable so all core logic is testable without the external tool. A first task enhances `airport_lookup` to expose coordinates and pin its cache.

**Tech Stack:** Python 3 standard library only (`csv`, `argparse`, `os`, `sys`, `collections`). Tests: `pytest` (existing repo setup). External: `conversion_tools/airport_lookup/airport_converter.py` (OurAirports-backed).

## Global Constraints

- Standard library only in `build_cirium_inputs.py` (matches the repo's zero-dependency policy).
- Output header rows, verbatim: `origin,destination,avg_enroute_min` and `airport,latitude,longitude,elevation_ft`.
- Codes in both output files are **ICAO**. `avg_enroute_min` rounded to 1 decimal.
- routes_time sorted by `(origin, destination)`; airports sorted by ICAO, deduped.
- Route-time rule: per (Orig,Dest) → group rows by `report_source` month → per month pool aircraft types as `Σ Block Mins ÷ Σ Flights` → take the **max** month.
- `airport_lookup` must be used with `auto_update=False` (pin to existing cache; no network).
- Guard/validation report is written to **both stdout and `<prefix>_error.txt`** (always written).
- CLI: `--input <cirium.csv>` and `--output <folder/prefix>` →
  `<prefix>_routes_time.csv`, `<prefix>_airports.csv`, `<prefix>_error.txt`.

---

## File Structure

- **Modify:** `~/Git/conversion_tools/airport_lookup/airport_converter.py` — expose lat/lon/elevation; add `auto_update`.
- **Create:** `build_cirium_inputs.py` (repo root) — the converter script.
- **Create:** `tests/test_build_cirium_inputs.py` — unit + integration tests.
- **Modify:** `tests/conftest.py` — add a `bci` fixture that loads `build_cirium_inputs`.

---

### Task 1: Enhance `airport_lookup` to expose coordinates + pin-to-cache

**Files:**
- Modify: `~/Git/conversion_tools/airport_lookup/airport_converter.py` (`__init__` ~37-51, `should_update` ~76-92, `_index_airport` ~190-199, `load_database` ~321-329)
- Test: `tests/test_build_cirium_inputs.py`

**Interfaces:**
- Produces: `AirportDatabase(cache_dir=..., auto_update=False)`; `convert_iata_to_icao(iata)` returns a dict now including keys `latitude`, `longitude`, `elevation_ft` (strings from OurAirports, possibly empty).

- [ ] **Step 1: Write the failing test** in `tests/test_build_cirium_inputs.py`

```python
import os, sys, importlib.util
import pytest

AL_DIR = os.path.expanduser("~/Git/conversion_tools/airport_lookup")
_HAS_AL = os.path.exists(os.path.join(AL_DIR, "airport_converter.py"))
al_required = pytest.mark.skipif(not _HAS_AL, reason="airport_lookup not available")


@al_required
def test_airport_lookup_exposes_coordinates():
    if AL_DIR not in sys.path:
        sys.path.insert(0, AL_DIR)
    from airport_converter import AirportDatabase
    db = AirportDatabase(cache_dir=os.path.join(AL_DIR, ".airport_cache"), auto_update=False)
    db.load_database(min_type="small_airport")
    rec = db.convert_iata_to_icao("GRU")
    assert rec is not None and rec["icao"] == "SBGR"
    assert rec.get("latitude") not in (None, "")
    assert rec.get("longitude") not in (None, "")
    assert rec.get("elevation_ft") not in (None, "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_build_cirium_inputs.py::test_airport_lookup_exposes_coordinates -v`
Expected: FAIL — `rec.get("latitude")` is `None` (coords not exposed yet).

- [ ] **Step 3: Add `auto_update` to `AirportDatabase.__init__`**

In `__init__` (after `self.verbose = verbose`), change the signature and store the flag:

```python
    def __init__(self, database_file: Optional[str] = None, cache_dir: str = ".airport_cache",
                 verbose: bool = False, update: bool = False, auto_update: bool = True):
```
```python
        self.update_flag = update
        self.auto_update = auto_update
```

- [ ] **Step 4: Make `should_update` respect the pin**

At the top of `should_update` (after the `if self.update_flag: return True` block), add:

```python
        # Pinned to existing cache: never refresh unless the file is missing entirely.
        if not self.auto_update:
            return not self.database_file.exists()
```

- [ ] **Step 5: Carry coordinates through `load_database`**

In the `airport_dict = { ... }` literal (~321-329), add three keys:

```python
                    airport_dict = {
                        'iata': iata,
                        'icao': icao,
                        'name': row.get('name', '').strip(),
                        'city': row.get('municipality', '').strip(),
                        'country': country,
                        'continent': continent,
                        'type': airport_type,
                        'latitude': row.get('latitude_deg', '').strip(),
                        'longitude': row.get('longitude_deg', '').strip(),
                        'elevation_ft': row.get('elevation_ft', '').strip(),
                    }
```

- [ ] **Step 6: Store coordinates in `_index_airport`**

In the `airport_data = { ... }` literal (~190-199), add:

```python
        airport_data = {
            'iata': iata,
            'icao': icao,
            'name': airport_dict.get('name', '').strip(),
            'city': airport_dict.get('city', airport_dict.get('municipality', '')).strip(),
            'country': airport_dict.get('country', airport_dict.get('iso_country', '')).strip().upper(),
            'continent': continent,
            'region': region,
            'type': airport_dict.get('type', '').strip(),
            'latitude': airport_dict.get('latitude', ''),
            'longitude': airport_dict.get('longitude', ''),
            'elevation_ft': airport_dict.get('elevation_ft', ''),
        }
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python3 -m pytest tests/test_build_cirium_inputs.py::test_airport_lookup_exposes_coordinates -v`
Expected: PASS.

- [ ] **Step 8: Commit** (only the test — `conversion_tools` is not a git repo, so its change is applied in place and noted, not committed)

```bash
git add tests/test_build_cirium_inputs.py
git commit -m "test: airport_lookup exposes coordinates (enhancement applied in conversion_tools)"
```

---

### Task 2: Cirium reader + number parsing + `bci` fixture

**Files:**
- Create: `build_cirium_inputs.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_build_cirium_inputs.py`

**Interfaces:**
- Produces:
  - `parse_number(raw: str) -> Optional[float]` — strip thousands-commas/whitespace/quotes; `None` if blank/unparseable.
  - `read_cirium(path: str) -> Tuple[List[dict], bool]` — returns `(rows, has_report_source)`. Each row dict has keys `report_source` (str, `''` if absent), `orig` (upper), `dest` (upper), `flights` (float|None), `block_mins` (float|None).

- [ ] **Step 1: Add the `bci` fixture** to `tests/conftest.py`

```python
@pytest.fixture(scope="session")
def bci():
    """The build_cirium_inputs module (loaded from repo root)."""
    spec = importlib.util.spec_from_file_location(
        "build_cirium_inputs", PROJECT_ROOT / "build_cirium_inputs.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_cirium_inputs"] = module
    spec.loader.exec_module(module)
    return module
```

- [ ] **Step 2: Write the failing tests**

```python
def test_parse_number_strips_commas(bci):
    assert bci.parse_number('"5,100"') == 5100.0
    assert bci.parse_number("28,440") == 28440.0
    assert bci.parse_number("30") == 30.0
    assert bci.parse_number("") is None
    assert bci.parse_number("   ") is None
    assert bci.parse_number("n/a") is None


def test_read_cirium_with_report_source(bci, tmp_path):
    p = tmp_path / "src.csv"
    p.write_text(
        "report_source,Mkt Al,Op Al,Orig,Dest,Manu,Type,Aircraft Family,"
        "Aircraft Type,Equip,Flights,Seats,ASMs,Block Mins\n"
        '2026-06,LA,JJ,aep,gig,Airbus,Narrow,A320,A320,320,30,"5,220","6,389,280","5,100"\n'
    )
    rows, has_rs = bci.read_cirium(str(p))
    assert has_rs is True
    assert len(rows) == 1
    r = rows[0]
    assert (r["orig"], r["dest"]) == ("AEP", "GIG")
    assert r["report_source"] == "2026-06"
    assert r["flights"] == 30.0 and r["block_mins"] == 5100.0


def test_read_cirium_without_report_source(bci, tmp_path):
    p = tmp_path / "src.csv"
    p.write_text(
        "Mkt Al,Op Al,Orig,Dest,Manu,Type,Aircraft Family,Aircraft Type,"
        "Equip,Flights,Seats,ASMs,Block Mins\n"
        "LA,JJ,GRU,SCL,Airbus,Narrow,A320,A320,320,100,17000,1000000,18000\n"
    )
    rows, has_rs = bci.read_cirium(str(p))
    assert has_rs is False
    assert rows[0]["report_source"] == ""
    assert rows[0]["orig"] == "GRU" and rows[0]["flights"] == 100.0
```

- [ ] **Step 3: Run to verify failure**

Run: `python3 -m pytest tests/test_build_cirium_inputs.py -k "parse_number or read_cirium" -v`
Expected: FAIL — `build_cirium_inputs.py` does not exist yet.

- [ ] **Step 4: Create `build_cirium_inputs.py` with the module header + these two functions**

```python
#!/usr/bin/env python3
"""Build adsb_historical_routes input files (routes_time + airports) from a raw Cirium CSV."""

import argparse
import csv
import os
import sys
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Tuple


def parse_number(raw: str) -> Optional[float]:
    """Parse a Cirium numeric cell, stripping thousands-commas/quotes/whitespace.

    Returns None for blank or non-numeric values.
    """
    if raw is None:
        return None
    s = raw.strip().strip('"').replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def read_cirium(path: str) -> Tuple[List[dict], bool]:
    """Read a Cirium CSV into normalized row dicts.

    Returns (rows, has_report_source). Each row: report_source (str, '' if column
    absent), orig/dest (upper IATA), flights/block_mins (float or None).
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        has_rs = "report_source" in fields
        rows = []
        for row in reader:
            rows.append({
                "report_source": (row.get("report_source") or "").strip() if has_rs else "",
                "orig": (row.get("Orig") or "").strip().upper(),
                "dest": (row.get("Dest") or "").strip().upper(),
                "flights": parse_number(row.get("Flights", "")),
                "block_mins": parse_number(row.get("Block Mins", "")),
            })
    return rows, has_rs
```

- [ ] **Step 5: Run to verify pass**

Run: `python3 -m pytest tests/test_build_cirium_inputs.py -k "parse_number or read_cirium" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add build_cirium_inputs.py tests/conftest.py tests/test_build_cirium_inputs.py
git commit -m "feat: Cirium CSV reader and number parsing for input builder"
```

---

### Task 3: Route-time aggregation (per-route pooled-months-max)

**Files:**
- Modify: `build_cirium_inputs.py`
- Test: `tests/test_build_cirium_inputs.py`

**Interfaces:**
- Consumes: normalized rows from `read_cirium`.
- Produces: `aggregate_route_times(rows: List[dict]) -> Tuple[Dict[Tuple[str,str], float], List[Tuple[str,str]]]` — returns `(route_times, dropped_no_flights)`. `route_times` maps `(orig_iata, dest_iata) -> minutes`. `dropped_no_flights` lists routes seen but with no valid flight rows in any month.

- [ ] **Step 1: Write the failing tests**

```python
def _row(orig, dest, flights, block, rs=""):
    return {"report_source": rs, "orig": orig, "dest": dest,
            "flights": flights, "block_mins": block}


def test_aggregate_single_group_pools_aircraft(bci):
    # Two aircraft types, same route/no report month: Σblock/Σflights.
    rows = [_row("GRU", "SCL", 10, 1000), _row("GRU", "SCL", 10, 1400)]
    times, dropped = bci.aggregate_route_times(rows)
    assert times[("GRU", "SCL")] == (1000 + 1400) / (10 + 10)  # 120.0
    assert dropped == []


def test_aggregate_max_across_months(bci):
    # June avg = 100, July avg = 130 -> take the larger (130).
    rows = [
        _row("AEP", "GIG", 10, 1000, rs="2026-06"),
        _row("AEP", "GIG", 10, 1300, rs="2026-07"),
    ]
    times, _ = bci.aggregate_route_times(rows)
    assert times[("AEP", "GIG")] == 130.0


def test_aggregate_pools_within_month_then_maxes(bci):
    # 2026-06 pooled avg = (1000+500)/(10+10)=75 ; 2026-07 = 2000/20=100 -> 100.
    rows = [
        _row("A", "B", 10, 1000, rs="2026-06"),
        _row("A", "B", 10, 500, rs="2026-06"),
        _row("A", "B", 20, 2000, rs="2026-07"),
    ]
    times, _ = bci.aggregate_route_times(rows)
    assert times[("A", "B")] == 100.0


def test_aggregate_skips_zero_flights_and_self_loops(bci):
    rows = [
        _row("A", "B", 0, 500),      # zero flights -> skipped
        _row("A", "B", 5, 400),      # valid -> 80
        _row("C", "C", 5, 400),      # self loop -> skipped, not seen as route
    ]
    times, dropped = bci.aggregate_route_times(rows)
    assert times == {("A", "B"): 80.0}
    assert ("C", "C") not in times


def test_aggregate_route_with_no_valid_flights_dropped(bci):
    rows = [_row("A", "B", 0, 500), _row("A", "B", None, 500)]
    times, dropped = bci.aggregate_route_times(rows)
    assert times == {}
    assert dropped == [("A", "B")]
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_build_cirium_inputs.py -k aggregate -v`
Expected: FAIL — `aggregate_route_times` not defined.

- [ ] **Step 3: Implement `aggregate_route_times`**

```python
def aggregate_route_times(rows: List[dict]) -> Tuple[Dict[Tuple[str, str], float], List[Tuple[str, str]]]:
    """Per (orig,dest): pool aircraft types per report month (Σblock/Σflights), take max month.

    Rows with flights<=0/None, blank block_mins, or orig==dest are skipped. Routes seen but
    with no valid flight rows in any month are returned in dropped_no_flights.
    """
    # route -> month -> [sum_block, sum_flights]
    acc: Dict[Tuple[str, str], Dict[str, List[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    seen = set()
    for r in rows:
        o, d = r["orig"], r["dest"]
        if not o or not d or o == d:
            continue
        seen.add((o, d))
        f, b = r["flights"], r["block_mins"]
        if f is None or b is None or f <= 0:
            continue
        month = r["report_source"] or "_single"
        bucket = acc[(o, d)][month]
        bucket[0] += b
        bucket[1] += f

    route_times: Dict[Tuple[str, str], float] = {}
    for route, months in acc.items():
        monthly = [blk / fl for (blk, fl) in months.values() if fl > 0]
        if monthly:
            route_times[route] = max(monthly)
    dropped_no_flights = sorted(seen - set(route_times))
    return route_times, dropped_no_flights
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_build_cirium_inputs.py -k aggregate -v`
Expected: PASS (all 5).

- [ ] **Step 5: Commit**

```bash
git add build_cirium_inputs.py tests/test_build_cirium_inputs.py
git commit -m "feat: per-route pooled-months-max block-time aggregation"
```

---

### Task 4: IATA→ICAO conversion + output assembly

**Files:**
- Modify: `build_cirium_inputs.py`
- Test: `tests/test_build_cirium_inputs.py`

**Interfaces:**
- Consumes: `route_times` from Task 3; a `resolve(iata) -> Optional[dict]` callable returning keys `icao`, `latitude`, `longitude`, `elevation_ft`.
- Produces: `convert_routes_to_icao(route_times, resolve) -> dict` with keys:
  `routes` (list of `(orig_icao, dest_icao, minutes)`, sorted), `airports` (dict `icao -> {airport,latitude,longitude,elevation_ft}`), `unmapped` (sorted list of IATA with no ICAO), `missing_coords` (sorted list of IATA mapped but lacking lat/lon), `elev_defaulted` (sorted list of ICAO whose elevation defaulted to 0), `dropped_routes` (list of `(orig_iata,dest_iata,reason)`).

- [ ] **Step 1: Write the failing tests**

```python
def _fake_resolver(mapping):
    return lambda iata: mapping.get(iata)


def test_convert_maps_and_collects_airports(bci):
    mapping = {
        "AEP": {"icao": "SABE", "latitude": "-34.55", "longitude": "-58.41", "elevation_ft": "18"},
        "GIG": {"icao": "SBGL", "latitude": "-22.81", "longitude": "-43.25", "elevation_ft": "28"},
    }
    out = bci.convert_routes_to_icao({("AEP", "GIG"): 170.5}, _fake_resolver(mapping))
    assert out["routes"] == [("SABE", "SBGL", 170.5)]
    assert set(out["airports"]) == {"SABE", "SBGL"}
    assert out["airports"]["SABE"]["elevation_ft"] == "18"
    assert out["unmapped"] == [] and out["missing_coords"] == []


def test_convert_drops_route_with_unmapped_code(bci):
    mapping = {"AEP": {"icao": "SABE", "latitude": "-34.55", "longitude": "-58.41", "elevation_ft": "18"}}
    out = bci.convert_routes_to_icao({("AEP", "ZZZ"): 120.0}, _fake_resolver(mapping))
    assert out["routes"] == []
    assert out["unmapped"] == ["ZZZ"]
    assert out["dropped_routes"] and out["dropped_routes"][0][:2] == ("AEP", "ZZZ")


def test_convert_defaults_missing_elevation_to_zero(bci):
    mapping = {
        "A": {"icao": "AAAA", "latitude": "1.0", "longitude": "2.0", "elevation_ft": ""},
        "B": {"icao": "BBBB", "latitude": "3.0", "longitude": "4.0", "elevation_ft": "50"},
    }
    out = bci.convert_routes_to_icao({("A", "B"): 60.0}, _fake_resolver(mapping))
    assert out["airports"]["AAAA"]["elevation_ft"] == 0
    assert out["elev_defaulted"] == ["AAAA"]


def test_convert_drops_route_missing_coordinates(bci):
    mapping = {
        "A": {"icao": "AAAA", "latitude": "", "longitude": "", "elevation_ft": "10"},
        "B": {"icao": "BBBB", "latitude": "3.0", "longitude": "4.0", "elevation_ft": "50"},
    }
    out = bci.convert_routes_to_icao({("A", "B"): 60.0}, _fake_resolver(mapping))
    assert out["routes"] == []
    assert out["missing_coords"] == ["A"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_build_cirium_inputs.py -k convert -v`
Expected: FAIL — `convert_routes_to_icao` not defined.

- [ ] **Step 3: Implement `convert_routes_to_icao`**

```python
def convert_routes_to_icao(route_times: Dict[Tuple[str, str], float],
                           resolve: Callable[[str], Optional[dict]]) -> dict:
    """Map IATA route endpoints to ICAO (+coords) and assemble output records.

    Routes whose endpoints cannot be mapped to ICAO or lack coordinates are dropped and
    reported. Missing elevation defaults to 0 (recorded in elev_defaulted).
    """
    cache: Dict[str, Optional[dict]] = {}

    def look(iata):
        if iata not in cache:
            cache[iata] = resolve(iata)
        return cache[iata]

    routes: List[Tuple[str, str, float]] = []
    airports: Dict[str, dict] = {}
    unmapped, missing_coords, elev_defaulted = set(), set(), set()
    dropped_routes: List[Tuple[str, str, str]] = []

    for (o, d), minutes in route_times.items():
        recs = {o: look(o), d: look(d)}
        bad = []
        for iata, rec in recs.items():
            if rec is None or not rec.get("icao"):
                unmapped.add(iata)
                bad.append(iata)
            elif rec.get("latitude") in (None, "") or rec.get("longitude") in (None, ""):
                missing_coords.add(iata)
                bad.append(iata)
        if bad:
            dropped_routes.append((o, d, "no ICAO/coords for " + ",".join(sorted(set(bad)))))
            continue
        for iata in (o, d):
            rec = recs[iata]
            icao = rec["icao"]
            if icao not in airports:
                elev = rec.get("elevation_ft")
                if elev in (None, ""):
                    elev = 0
                    elev_defaulted.add(icao)
                airports[icao] = {
                    "airport": icao,
                    "latitude": rec["latitude"],
                    "longitude": rec["longitude"],
                    "elevation_ft": elev,
                }
        routes.append((recs[o]["icao"], recs[d]["icao"], minutes))

    routes.sort(key=lambda t: (t[0], t[1]))
    return {
        "routes": routes,
        "airports": airports,
        "unmapped": sorted(unmapped),
        "missing_coords": sorted(missing_coords),
        "elev_defaulted": sorted(elev_defaulted),
        "dropped_routes": dropped_routes,
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_build_cirium_inputs.py -k convert -v`
Expected: PASS (all 4).

- [ ] **Step 5: Commit**

```bash
git add build_cirium_inputs.py tests/test_build_cirium_inputs.py
git commit -m "feat: IATA->ICAO conversion and output-record assembly"
```

---

### Task 5: Writers + validation report (+ `_error.txt`) + verdict

**Files:**
- Modify: `build_cirium_inputs.py`
- Test: `tests/test_build_cirium_inputs.py`

**Interfaces:**
- Consumes: the `convert_routes_to_icao` result dict; `dropped_no_flights` from Task 3; `n_rows` (int).
- Produces:
  - `write_routes_time(path, routes)` and `write_airports(path, airports)`.
  - `build_report(result, dropped_no_flights, n_rows) -> Tuple[str, bool]` — returns `(report_text, ready)`. `ready` is True iff routes non-empty, no `unmapped`, no `missing_coords`, and the airports set covers every route endpoint.
  - `write_report(path, report_text)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_writers_produce_expected_files(bci, tmp_path):
    result = {
        "routes": [("SABE", "SBGL", 170.53), ("SBGL", "SABE", 168.4)],
        "airports": {
            "SABE": {"airport": "SABE", "latitude": "-34.55", "longitude": "-58.41", "elevation_ft": "18"},
            "SBGL": {"airport": "SBGL", "latitude": "-22.81", "longitude": "-43.25", "elevation_ft": 0},
        },
    }
    rt = tmp_path / "x_routes_time.csv"
    ap = tmp_path / "x_airports.csv"
    bci.write_routes_time(str(rt), result["routes"])
    bci.write_airports(str(ap), result["airports"])
    rt_lines = rt.read_text().splitlines()
    assert rt_lines[0] == "origin,destination,avg_enroute_min"
    assert rt_lines[1] == "SABE,SBGL,170.5"   # rounded to 1 dp
    ap_lines = ap.read_text().splitlines()
    assert ap_lines[0] == "airport,latitude,longitude,elevation_ft"
    assert ap_lines[1].startswith("SABE,")     # sorted by ICAO


def test_report_ready_when_clean(bci):
    result = {
        "routes": [("SABE", "SBGL", 170.5)],
        "airports": {"SABE": {}, "SBGL": {}},
        "unmapped": [], "missing_coords": [], "elev_defaulted": [], "dropped_routes": [],
    }
    text, ready = bci.build_report(result, [], n_rows=1)
    assert ready is True
    assert "READY" in text


def test_report_needs_input_when_unmapped(bci):
    result = {
        "routes": [("SABE", "SBGL", 170.5)],
        "airports": {"SABE": {}, "SBGL": {}},
        "unmapped": ["ZZZ"], "missing_coords": [], "elev_defaulted": [],
        "dropped_routes": [("AEP", "ZZZ", "no ICAO/coords for ZZZ")],
    }
    text, ready = bci.build_report(result, [], n_rows=2)
    assert ready is False
    assert "NEEDS INPUT" in text and "ZZZ" in text
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_build_cirium_inputs.py -k "writers or report" -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement writers, report, and report-writer**

```python
def write_routes_time(path: str, routes: List[Tuple[str, str, float]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["origin", "destination", "avg_enroute_min"])
        for orig, dest, minutes in routes:
            w.writerow([orig, dest, round(minutes, 1)])


def write_airports(path: str, airports: Dict[str, dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["airport", "latitude", "longitude", "elevation_ft"])
        for icao in sorted(airports):
            a = airports[icao]
            w.writerow([a["airport"], a["latitude"], a["longitude"], a["elevation_ft"]])


def build_report(result: dict, dropped_no_flights: List[Tuple[str, str]], n_rows: int) -> Tuple[str, bool]:
    """Compose the human-readable validation report and compute the READY verdict."""
    endpoints = set()
    for orig, dest, _ in result["routes"]:
        endpoints.update((orig, dest))
    missing_in_airports = sorted(endpoints - set(result["airports"]))

    ready = bool(result["routes"]) and not result["unmapped"] \
        and not result["missing_coords"] and not missing_in_airports

    lines = ["=" * 60, "Cirium input build report", "=" * 60,
             f"Source rows read:        {n_rows}",
             f"Routes written:          {len(result['routes'])}",
             f"Airports written:        {len(result['airports'])}",
             f"Routes dropped (no map): {len(result['dropped_routes'])}",
             f"Routes dropped (no flts):{len(dropped_no_flights)}", ""]

    def section(title, items):
        if items:
            lines.append(f"{title} ({len(items)}):")
            lines.extend(f"    {x}" for x in items)
            lines.append("")

    section("Unmapped IATA codes (need ICAO mapping in airport_lookup)", result["unmapped"])
    section("Airports missing coordinates", result["missing_coords"])
    section("Routes dropped for unmapped/missing endpoints",
            [f"{o}-{d}: {why}" for o, d, why in result["dropped_routes"]])
    section("Routes dropped for no valid flights", [f"{o}-{d}" for o, d in dropped_no_flights])
    section("Elevations defaulted to 0 (verify)", result["elev_defaulted"])
    section("Route endpoints missing from airports file (INVARIANT BREACH)", missing_in_airports)

    if ready:
        lines.append("VERDICT: READY — both files complete; adsb_historical_routes.py can run.")
    else:
        reasons = []
        if not result["routes"]:
            reasons.append("no routes produced")
        if result["unmapped"]:
            reasons.append(f"{len(result['unmapped'])} unmapped IATA code(s)")
        if result["missing_coords"]:
            reasons.append(f"{len(result['missing_coords'])} airport(s) without coordinates")
        if missing_in_airports:
            reasons.append("airports-file invariant breached")
        lines.append("VERDICT: NEEDS INPUT — " + "; ".join(reasons) + ".")
    lines.append("=" * 60)
    return "\n".join(lines), ready


def write_report(path: str, report_text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(report_text + "\n")
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_build_cirium_inputs.py -k "writers or report" -v`
Expected: PASS (all 3).

- [ ] **Step 5: Commit**

```bash
git add build_cirium_inputs.py tests/test_build_cirium_inputs.py
git commit -m "feat: output writers and READY/NEEDS-INPUT validation report"
```

---

### Task 6: CLI `main()`, real `airport_lookup` wiring, end-to-end

**Files:**
- Modify: `build_cirium_inputs.py`
- Test: `tests/test_build_cirium_inputs.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `make_resolver(airport_lookup_dir: str) -> Callable[[str], Optional[dict]]` — lazy-imports `AirportDatabase`, pins cache (`auto_update=False`), returns a resolver mapping IATA→`{icao,latitude,longitude,elevation_ft}` or None.
  - `run(input_path, output_prefix, resolve) -> bool` — full pipeline; writes the three files; returns `ready`.
  - `main(argv=None) -> int` — argparse CLI; exit 0 if READY else 1.

- [ ] **Step 1: Write the failing tests** (unit `run` with a stub resolver; integration with the real tool)

```python
def test_run_end_to_end_with_stub(bci, tmp_path):
    src = tmp_path / "src.csv"
    src.write_text(
        "report_source,Mkt Al,Op Al,Orig,Dest,Manu,Type,Aircraft Family,"
        "Aircraft Type,Equip,Flights,Seats,ASMs,Block Mins\n"
        "2026-06,LA,JJ,AEP,GIG,Airbus,Narrow,A320,A320,320,10,1000,1000,1705\n"
        "2026-07,LA,JJ,AEP,GIG,Airbus,Narrow,A320,A320,320,10,1000,1000,1600\n"
    )
    mapping = {
        "AEP": {"icao": "SABE", "latitude": "-34.55", "longitude": "-58.41", "elevation_ft": "18"},
        "GIG": {"icao": "SBGL", "latitude": "-22.81", "longitude": "-43.25", "elevation_ft": "28"},
    }
    prefix = str(tmp_path / "lan_test")
    ready = bci.run(str(src), prefix, resolve=lambda i: mapping.get(i))
    assert ready is True
    rt = (tmp_path / "lan_test_routes_time.csv").read_text().splitlines()
    assert rt[1] == "SABE,SBGL,170.5"                       # max month (June 170.5 > July 160.0)
    assert (tmp_path / "lan_test_airports.csv").exists()
    assert "READY" in (tmp_path / "lan_test_error.txt").read_text()


@al_required
def test_integration_real_lan_cirium(bci, tmp_path):
    src = os.path.join(os.path.dirname(__file__), "..", "input", "lan_route_cirium_202606-2020607.csv")
    if not os.path.exists(src):
        pytest.skip("LAN Cirium sample not present")
    resolve = bci.make_resolver(AL_DIR)
    prefix = str(tmp_path / "lan")
    bci.run(src, prefix, resolve)
    rt = (tmp_path / "lan_routes_time.csv").read_text().splitlines()
    ap = (tmp_path / "lan_airports.csv").read_text().splitlines()
    assert rt[0] == "origin,destination,avg_enroute_min" and len(rt) > 100
    assert ap[0] == "airport,latitude,longitude,elevation_ft" and len(ap) > 10
    # invariant: every route endpoint is in the airports file
    airports = {ln.split(",")[0] for ln in ap[1:]}
    for ln in rt[1:]:
        o, d, _ = ln.split(",")
        assert o in airports and d in airports
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_build_cirium_inputs.py -k "end_to_end or integration" -v`
Expected: FAIL — `run`/`make_resolver` not defined.

- [ ] **Step 3: Implement `make_resolver`, `run`, `main`**

```python
DEFAULT_AIRPORT_LOOKUP_DIR = os.path.expanduser("~/Git/conversion_tools/airport_lookup")


def make_resolver(airport_lookup_dir: str) -> Callable[[str], Optional[dict]]:
    """Return an iata->record resolver backed by the shared airport_lookup tool (cache pinned)."""
    if airport_lookup_dir not in sys.path:
        sys.path.insert(0, airport_lookup_dir)
    from airport_converter import AirportDatabase  # lazy: only needed for real runs
    db = AirportDatabase(
        cache_dir=os.path.join(airport_lookup_dir, ".airport_cache"),
        auto_update=False,
    )
    db.load_database(min_type="small_airport")

    def resolve(iata: str) -> Optional[dict]:
        rec = db.convert_iata_to_icao(iata)
        if not rec:
            return None
        return {
            "icao": rec.get("icao"),
            "latitude": rec.get("latitude"),
            "longitude": rec.get("longitude"),
            "elevation_ft": rec.get("elevation_ft"),
        }
    return resolve


def run(input_path: str, output_prefix: str, resolve: Callable[[str], Optional[dict]]) -> bool:
    """Full pipeline: read -> aggregate -> convert -> write 3 files. Returns the READY verdict."""
    out_dir = os.path.dirname(os.path.abspath(output_prefix))
    os.makedirs(out_dir, exist_ok=True)

    rows, _ = read_cirium(input_path)
    route_times, dropped_no_flights = aggregate_route_times(rows)
    result = convert_routes_to_icao(route_times, resolve)

    write_routes_time(output_prefix + "_routes_time.csv", result["routes"])
    write_airports(output_prefix + "_airports.csv", result["airports"])
    report_text, ready = build_report(result, dropped_no_flights, n_rows=len(rows))
    write_report(output_prefix + "_error.txt", report_text)
    print(report_text)
    return ready


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Build routes_time + airports inputs for adsb_historical_routes.py from a Cirium CSV."
    )
    parser.add_argument("--input", required=True, help="Raw Cirium CSV file")
    parser.add_argument("--output", required=True,
                        help="Output folder+prefix (e.g. input/lan_202606-202607)")
    parser.add_argument("--airport-lookup-dir", default=DEFAULT_AIRPORT_LOOKUP_DIR,
                        help="Path to the airport_lookup tool (default: %(default)s)")
    args = parser.parse_args(argv)

    if not os.path.exists(args.input):
        print(f"Error: input file not found: {args.input}")
        return 1
    resolve = make_resolver(args.airport_lookup_dir)
    ready = run(args.input, args.output, resolve)
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_build_cirium_inputs.py -k "end_to_end or integration" -v`
Expected: PASS (integration skips if `airport_lookup` absent).

- [ ] **Step 5: Run the full suite + a real invocation**

Run:
```bash
python3 -m pytest tests/ -q
python3 build_cirium_inputs.py --input input/lan_route_cirium_202606-2020607.csv --output input/lan_202606-202607
```
Expected: all tests pass; three `input/lan_202606-202607_*` files created; stdout ends with a READY/NEEDS INPUT verdict.

- [ ] **Step 6: Commit + push the branch**

```bash
git add build_cirium_inputs.py tests/test_build_cirium_inputs.py
git commit -m "feat: CLI, airport_lookup wiring, and end-to-end Cirium input build"
git push -u origin cirium-input-automation
```

---

## Self-Review

**Spec coverage:**
- Optional `report_source` → Task 2 (`has_report_source`) + Task 3 (month grouping / `_single`). ✓
- Comma numbers → Task 2 `parse_number`. ✓
- Per-route pooled-months-max → Task 3. ✓
- IATA→ICAO via airport_lookup, coords/elevation → Task 1 (enhancement) + Task 4/6. ✓
- Two output files + `_error.txt`, exact headers, ICAO, 1 dp, sorted → Tasks 4/5. ✓
- Airports = union of output route endpoints → Task 4. ✓
- Guards (unmapped/missing coords/elevation default/self-loop/zero-flights) → Tasks 3/4/5. ✓
- Guard output to stdout + `_error.txt`; READY/NEEDS INPUT verdict → Task 5/6. ✓
- CLI `--input`/`--output` prefix → Task 6. ✓
- Pin-to-cache (offline) → Task 1 (`auto_update`) + Task 6. ✓

**Placeholder scan:** none — every step has runnable code. ✓
**Type consistency:** `resolve(iata) -> {icao,latitude,longitude,elevation_ft}` used identically in Tasks 4 and 6; `convert_routes_to_icao` result keys consistent across Tasks 4/5. ✓

## Execution Handoff

Two execution options — see below.
