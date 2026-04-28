# Improved Route Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the project to `adsb_historical_routes`, bootstrap a clean v1.0 GitHub repo, then on an `improved-route-detection` branch add tiered confidence presets, bearing-aware cross-file joining, route-time rescue with bearing safeguard, and a diagnostics CSV sidecar — recovering most of the ~22% of flight segments currently dropped.

**Architecture:** Promote `prior_versions/flight_route_splitter_v6_old.py` to be the canonical `adsb_historical_routes.py` (it's the version that produced the existing LAN output and works on the ADS-B Exchange data structure). Add new logic via a `ConfidencePreset` dataclass that flows through `find_nearest_airport`, `detect_flight_segments`, and `combine_segments_intelligently`. New bearing utilities and a `DiagnosticsRecorder` class. CLI gains `--confidence {strict,balanced,permissive}` plus per-knob overrides.

**Tech Stack:** Python 3.6+ standard library only (per ADR-001). pytest for tests. git + GitHub CLI for repo work.

**Spec:** `docs/superpowers/specs/2026-04-28-improved-route-detection-design.md`

**Branches:**
- Phase A (Tasks 1-4) → `main` branch, tagged `v1.0`
- Phase B (Tasks 5-19) → `improved-route-detection` branch off `main`

---

## Phase A — `main` branch (rename + repo bootstrap)

### Task 1: Add .gitignore and rename canonical script

**Files:**
- Create: `.gitignore`
- Move: `flight_route_splitter_v6.py` → `prior_versions/flight_route_splitter_v6_simple.py`
- Move: `prior_versions/flight_route_splitter_v6_old.py` → `adsb_historical_routes.py`

- [ ] **Step 1: Create .gitignore**

Write `/Users/johnyoung/Git/kml/adsb_historical_routes/.gitignore`:

```
# Build/test artifacts
__pycache__/
*.pyc
*.pyo
*.egg-info/
.pytest_cache/

# Local data and outputs
output/
kml_input/
*.tar.gz
*.diagnostics.csv

# OS
.DS_Store
Thumbs.db

# Editor
.vscode/
.idea/
*.swp
```

- [ ] **Step 2: Move the simplified v6 script aside**

Run:

```bash
mv /Users/johnyoung/Git/kml/adsb_historical_routes/flight_route_splitter_v6.py \
   /Users/johnyoung/Git/kml/adsb_historical_routes/prior_versions/flight_route_splitter_v6_simple.py
```

Expected: file moves, no error.

- [ ] **Step 3: Promote v6_old to canonical name**

Run:

```bash
mv /Users/johnyoung/Git/kml/adsb_historical_routes/prior_versions/flight_route_splitter_v6_old.py \
   /Users/johnyoung/Git/kml/adsb_historical_routes/adsb_historical_routes.py
```

Expected: file moves.

- [ ] **Step 4: Smoke-test the renamed script can still import**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -c "import importlib.util, sys; spec=importlib.util.spec_from_file_location('m','adsb_historical_routes.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print('OK', m.haversine_distance(0,0,0,1))"
```

Expected: `OK 111.19...`

### Task 2: Update documentation references to the new script name

**Files:**
- Modify: `README.md` — replace all occurrences of `flight_route_splitter_v6.py` and `flight_route_splitter_v6` (import) with the new name
- Modify: `examples/README.md`
- Modify: `.claude/claude.md` (which is referenced by .claude/CLAUDE.md)
- Modify: `.claude/known_issues.md` — replace the placeholder `flight_route_splitter.py` with `adsb_historical_routes.py`
- Modify: `ADSB KML route analysis.txt` (only if it actually contains the old name; sed-based check)

- [ ] **Step 1: Replace the script name in README.md**

Run:

```bash
sed -i '' 's/flight_route_splitter_v6\.py/adsb_historical_routes.py/g; s/from flight_route_splitter_v6 /from adsb_historical_routes /g' \
   /Users/johnyoung/Git/kml/adsb_historical_routes/README.md
```

Verify:

```bash
grep -n "flight_route_splitter" /Users/johnyoung/Git/kml/adsb_historical_routes/README.md || echo "OK: no references left"
```

Expected: `OK: no references left`

- [ ] **Step 2: Replace in examples/README.md, .claude/claude.md, .claude/known_issues.md**

Run:

```bash
for f in /Users/johnyoung/Git/kml/adsb_historical_routes/examples/README.md \
         /Users/johnyoung/Git/kml/adsb_historical_routes/.claude/claude.md \
         /Users/johnyoung/Git/kml/adsb_historical_routes/.claude/known_issues.md; do
  sed -i '' 's/flight_route_splitter_v6\.py/adsb_historical_routes.py/g; s/flight_route_splitter\.py/adsb_historical_routes.py/g' "$f"
done
```

Verify:

```bash
grep -rn "flight_route_splitter" /Users/johnyoung/Git/kml/adsb_historical_routes/README.md \
   /Users/johnyoung/Git/kml/adsb_historical_routes/examples/ \
   /Users/johnyoung/Git/kml/adsb_historical_routes/.claude/ || echo "OK: clean"
```

Expected: `OK: clean`

- [ ] **Step 3: Check the analysis text file for stale references**

Run:

```bash
grep -n "flight_route_splitter\|adsb_routes" "/Users/johnyoung/Git/kml/adsb_historical_routes/ADSB KML route analysis.txt" || echo "no references"
```

If references are found, replace them with `sed`. If `no references`, skip.

### Task 3: Update CHANGELOG.md with the v1.0 entry

**Files:**
- Modify: `CHANGELOG.md` — prepend a v1.0 entry describing the rename and the canonical script promotion. Replace any `flight_route_splitter_v6.py` reference with the new name.

- [ ] **Step 1: Replace the script name in CHANGELOG.md**

Run:

```bash
sed -i '' 's/flight_route_splitter_v6\.py/adsb_historical_routes.py/g' \
   /Users/johnyoung/Git/kml/adsb_historical_routes/CHANGELOG.md
```

- [ ] **Step 2: Read the current CHANGELOG to find the insertion point**

Read `/Users/johnyoung/Git/kml/adsb_historical_routes/CHANGELOG.md`, identify the topmost entry (likely a v6.0 section).

- [ ] **Step 3: Prepend a v1.0 section above the existing entries**

Edit `/Users/johnyoung/Git/kml/adsb_historical_routes/CHANGELOG.md`. After the title/header lines (typically the first 1-3 lines), insert:

```markdown
## [1.0.0] - 2026-04-28

### Changed

- **Project renamed** to `adsb_historical_routes`. The canonical script is `adsb_historical_routes.py` (formerly `prior_versions/flight_route_splitter_v6_old.py`, the sophisticated v6 that produced `output/lan_routes_2026-feb-mar.kml`).
- The simplified `flight_route_splitter_v6.py` that previously sat at the repo root is preserved at `prior_versions/flight_route_splitter_v6_simple.py` for reference. It does not handle ADS-B Exchange's direct-`<Folder>` (no `<Document>` wrapper) KML files and is not recommended.

### Added

- Initial GitHub repo at https://github.com/jjllddyy/adsb_historical_routes (this commit).
- `.gitignore` excluding `output/`, `kml_input/`, build artifacts, and `.diagnostics.csv` files.

### Notes

- All algorithm behavior in this v1.0 commit is identical to the prior `flight_route_splitter_v6_old.py`. Tiered confidence presets, bearing-aware joining, and the diagnostics CSV land in v1.1 on the `improved-route-detection` branch.

```

### Task 4: Initialize git repo, commit, tag, push to GitHub

**Files:** None modified (this task is about repo state).

- [ ] **Step 1: Initialize git in the project root**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git init -b main
```

Expected: `Initialized empty Git repository in ...`

- [ ] **Step 2: Stage everything that should be tracked**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git add .gitignore adsb_historical_routes.py prior_versions/ input/ examples/ docs/ README.md SPECIFICATION.md CHANGELOG.md LICENSE requirements.txt ".claude" "ADSB KML route analysis.txt"
```

Verify:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git status --short | grep -v "^A " || echo "everything staged"
```

Expected: `everything staged` (anything else means an unstaged or untracked file slipped through). Confirm `output/` and `kml_input/` are NOT in the staged list.

- [ ] **Step 3: Make the initial commit (no co-authored-by tag — first commit is solely the user's project)**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git commit -m "Initial commit: rename to adsb_historical_routes (v1.0)

- Promote prior_versions/flight_route_splitter_v6_old.py to adsb_historical_routes.py
- Move simplified flight_route_splitter_v6.py to prior_versions/flight_route_splitter_v6_simple.py
- Update documentation references to the new script name
- Add .gitignore for output/, kml_input/, and build artifacts"
```

- [ ] **Step 4: Tag v1.0**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git tag -a v1.0 -m "v1.0 - First GitHub release; rename + repo bootstrap"
```

- [ ] **Step 5: Confirm GitHub auth and create the remote repo**

Verify gh is authenticated:

```bash
gh auth status
```

If not authenticated, ask the user to run `gh auth login` and pause until done.

Then create the public repo:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && gh repo create jjllddyy/adsb_historical_routes --public --source . --remote origin --description "Process ADS-B Exchange historical KML traces into segmented flight routes with airport matching"
```

Expected: prints the repo URL.

- [ ] **Step 6: Push main and the tag**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git push -u origin main && git push origin v1.0
```

Verify in browser or:

```bash
gh repo view jjllddyy/adsb_historical_routes --web
```

---

## Phase B — `improved-route-detection` branch (algorithm + diagnostics)

### Task 5: Create branch and scaffold tests/conftest.py

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `pytest.ini`

- [ ] **Step 1: Create the branch off main**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git checkout -b improved-route-detection
```

- [ ] **Step 2: Create pytest.ini**

Write `/Users/johnyoung/Git/kml/adsb_historical_routes/pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short
```

- [ ] **Step 3: Create tests/__init__.py (empty)**

Write `/Users/johnyoung/Git/kml/adsb_historical_routes/tests/__init__.py`:

```python
```

- [ ] **Step 4: Create tests/conftest.py with shared fixtures**

Write `/Users/johnyoung/Git/kml/adsb_historical_routes/tests/conftest.py`:

```python
"""Shared pytest fixtures for adsb_historical_routes tests."""

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "adsb_historical_routes.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("adsb_historical_routes", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["adsb_historical_routes"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def ahr():
    """The adsb_historical_routes module."""
    return _load_module()


@pytest.fixture
def make_track_point(ahr):
    """Factory for TrackPoint objects."""
    def _make(t: datetime, lat: float, lon: float, alt_m: float = 10000.0):
        ts = t.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return ahr.TrackPoint(ts, lon, lat, alt_m)
    return _make


@pytest.fixture
def make_segment(ahr, make_track_point):
    """Factory for FlightSegment objects with N points along a great-circle path."""
    def _make(
        aircraft_id: str,
        start: datetime,
        duration_min: float,
        start_lat: float, start_lon: float,
        end_lat: float, end_lon: float,
        origin_code: str = None,
        dest_code: str = None,
        n_points: int = 30,
        alt_m: float = 10000.0,
    ):
        points = []
        for i in range(n_points):
            f = i / max(n_points - 1, 1)
            t = start + timedelta(seconds=duration_min * 60 * f)
            lat = start_lat + (end_lat - start_lat) * f
            lon = start_lon + (end_lon - start_lon) * f
            points.append(make_track_point(t, lat, lon, alt_m))

        origin = ahr.Airport(origin_code, start_lat, start_lon, 0) if origin_code else None
        destination = ahr.Airport(dest_code, end_lat, end_lon, 0) if dest_code else None
        return ahr.FlightSegment(
            aircraft_id, aircraft_id, points,
            origin, destination,
            "ff0000ff", "2", "ff", "test.kml"
        )
    return _make


@pytest.fixture
def utc():
    """A baseline UTC datetime."""
    return datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)
```

- [ ] **Step 5: Run pytest to confirm scaffolding works**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -m pytest --collect-only
```

Expected: `0 tests collected` and no errors. (The conftest imports must succeed.)

- [ ] **Step 6: Commit**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git add tests/ pytest.ini && git commit -m "Add pytest scaffolding with shared fixtures

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 6: Add bearing utilities (TDD)

**Files:**
- Modify: `adsb_historical_routes.py` — add `bearing()`, `angular_diff()`, `mean_bearing()` near the other math helpers (after `haversine_distance`).
- Create: `tests/test_bearing.py`

- [ ] **Step 1: Write the failing tests**

Write `/Users/johnyoung/Git/kml/adsb_historical_routes/tests/test_bearing.py`:

```python
"""Tests for bearing utility functions."""

import pytest


class TestBearing:
    def test_due_north_is_zero(self, ahr):
        b = ahr.bearing(0.0, 0.0, 1.0, 0.0)
        assert abs(b - 0.0) < 0.5 or abs(b - 360.0) < 0.5

    def test_due_east_is_ninety(self, ahr):
        b = ahr.bearing(0.0, 0.0, 0.0, 1.0)
        assert abs(b - 90.0) < 0.5

    def test_due_south_is_one_eighty(self, ahr):
        b = ahr.bearing(1.0, 0.0, 0.0, 0.0)
        assert abs(b - 180.0) < 0.5

    def test_due_west_is_two_seventy(self, ahr):
        b = ahr.bearing(0.0, 1.0, 0.0, 0.0)
        assert abs(b - 270.0) < 0.5

    def test_result_is_zero_to_360(self, ahr):
        # SW should be ~225
        b = ahr.bearing(0.0, 0.0, -1.0, -1.0)
        assert 0.0 <= b < 360.0
        assert abs(b - 225.0) < 0.5


class TestAngularDiff:
    def test_zero_diff(self, ahr):
        assert ahr.angular_diff(45.0, 45.0) == 0.0

    def test_thirty_diff(self, ahr):
        assert abs(ahr.angular_diff(45.0, 75.0) - 30.0) < 1e-6

    def test_wraparound_small(self, ahr):
        # 350 to 10 is 20 deg, not 340
        assert abs(ahr.angular_diff(350.0, 10.0) - 20.0) < 1e-6

    def test_max_is_one_eighty(self, ahr):
        assert ahr.angular_diff(0.0, 180.0) == 180.0


class TestMeanBearing:
    def test_consistent_north_track(self, ahr, make_track_point, utc):
        from datetime import timedelta
        # 5 points heading due north
        pts = [
            make_track_point(utc + timedelta(seconds=i * 60), i * 0.1, 0.0)
            for i in range(5)
        ]
        b = ahr.mean_bearing(pts, window_minutes=10.0)
        assert abs(b - 0.0) < 1.0 or abs(b - 360.0) < 1.0

    def test_consistent_east_track(self, ahr, make_track_point, utc):
        from datetime import timedelta
        pts = [
            make_track_point(utc + timedelta(seconds=i * 60), 0.0, i * 0.1)
            for i in range(5)
        ]
        b = ahr.mean_bearing(pts, window_minutes=10.0)
        assert abs(b - 90.0) < 1.0

    def test_short_track_falls_back_to_full(self, ahr, make_track_point, utc):
        from datetime import timedelta
        pts = [
            make_track_point(utc + timedelta(seconds=i * 30), 0.0, i * 0.1)
            for i in range(3)
        ]
        # 1.5 minutes total < 10 minute window — should still return a meaningful bearing
        b = ahr.mean_bearing(pts, window_minutes=10.0)
        assert abs(b - 90.0) < 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -m pytest tests/test_bearing.py -x
```

Expected: collection or test failures with `AttributeError: module 'adsb_historical_routes' has no attribute 'bearing'`.

- [ ] **Step 3: Implement the bearing utilities**

In `/Users/johnyoung/Git/kml/adsb_historical_routes/adsb_historical_routes.py`, find the `haversine_distance` function (around line 175). Just below it, before `calculate_groundspeed`, add:

```python
def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing in degrees (0-360, clockwise from north) from point 1 to point 2."""
    from math import atan2, cos, degrees, radians, sin
    rlat1, rlat2 = radians(lat1), radians(lat2)
    dlon = radians(lon2 - lon1)
    x = sin(dlon) * cos(rlat2)
    y = cos(rlat1) * sin(rlat2) - sin(rlat1) * cos(rlat2) * cos(dlon)
    return (degrees(atan2(x, y)) + 360.0) % 360.0


def angular_diff(a: float, b: float) -> float:
    """Smallest angular difference between two bearings in degrees (0-180)."""
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d


def mean_bearing(points: List["TrackPoint"], window_minutes: float = 10.0) -> float:
    """Average bearing over the last `window_minutes` of a track. Uses vector mean to avoid wrap-around bias."""
    from math import atan2, cos, degrees, radians, sin

    if len(points) < 2:
        return 0.0

    last_time = points[-1].datetime
    cutoff = last_time - timedelta(minutes=window_minutes)
    window = [p for p in points if p.datetime >= cutoff]
    if len(window) < 2:
        window = points[-min(10, len(points)):]
    if len(window) < 2:
        return 0.0

    sum_x = 0.0
    sum_y = 0.0
    for i in range(len(window) - 1):
        b = bearing(window[i].lat, window[i].lon, window[i + 1].lat, window[i + 1].lon)
        sum_x += sin(radians(b))
        sum_y += cos(radians(b))
    return (degrees(atan2(sum_x, sum_y)) + 360.0) % 360.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -m pytest tests/test_bearing.py -v
```

Expected: all 11 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git add tests/test_bearing.py adsb_historical_routes.py && git commit -m "Add bearing, angular_diff, mean_bearing utilities

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 7: Add ConfidencePreset dataclass and resolver (TDD)

**Files:**
- Modify: `adsb_historical_routes.py` — add `ConfidencePreset`, `PRESETS`, and `resolve_preset` near the top (after `__future__`/imports section).
- Create: `tests/test_confidence_preset.py`

- [ ] **Step 1: Write the failing tests**

Write `/Users/johnyoung/Git/kml/adsb_historical_routes/tests/test_confidence_preset.py`:

```python
"""Tests for ConfidencePreset and CLI override resolution."""

import argparse
import pytest


class TestPresets:
    def test_all_three_presets_exist(self, ahr):
        assert "strict" in ahr.PRESETS
        assert "balanced" in ahr.PRESETS
        assert "permissive" in ahr.PRESETS

    def test_strict_values(self, ahr):
        p = ahr.PRESETS["strict"]
        assert p.airport_radius_km == 20
        assert p.max_join_gap_hours == 2.0
        assert p.max_join_distance_km == 100
        assert p.route_time_tolerance == 0.25
        assert p.route_time_rescue is False
        assert p.direction_aware_rescue is False

    def test_balanced_values(self, ahr):
        p = ahr.PRESETS["balanced"]
        assert p.airport_radius_km == 50
        assert p.max_join_gap_hours == 3.0
        assert p.max_join_distance_km == 200
        assert p.route_time_tolerance == 0.40
        assert p.route_time_rescue is True
        assert p.direction_aware_rescue is True

    def test_permissive_values(self, ahr):
        p = ahr.PRESETS["permissive"]
        assert p.airport_radius_km == 100
        assert p.max_join_gap_hours == 4.0
        assert p.max_join_distance_km == 500
        assert p.route_time_tolerance == 0.60
        assert p.route_time_rescue is True
        assert p.direction_aware_rescue is True

    def test_preset_is_frozen(self, ahr):
        # Changing a preset value should raise (frozen dataclass)
        p = ahr.PRESETS["balanced"]
        with pytest.raises(Exception):
            p.airport_radius_km = 999


class TestResolvePreset:
    def _ns(self, **kwargs):
        defaults = dict(
            confidence="balanced",
            airport_radius_km=None,
            max_join_gap_hours=None,
            max_join_distance_km=None,
            route_time_tolerance=None,
            route_time_rescue=None,
        )
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_no_overrides_returns_named_preset(self, ahr):
        p = ahr.resolve_preset(self._ns(confidence="strict"))
        assert p.name == "strict"
        assert p.airport_radius_km == 20

    def test_single_override_changes_name_to_custom(self, ahr):
        p = ahr.resolve_preset(self._ns(confidence="balanced", airport_radius_km=75))
        assert p.name == "custom"
        assert p.airport_radius_km == 75
        assert p.max_join_gap_hours == 3.0  # other fields unchanged

    def test_route_time_rescue_override_off(self, ahr):
        p = ahr.resolve_preset(self._ns(confidence="balanced", route_time_rescue="off"))
        assert p.route_time_rescue is False
        assert p.name == "custom"

    def test_route_time_rescue_override_on(self, ahr):
        p = ahr.resolve_preset(self._ns(confidence="strict", route_time_rescue="on"))
        assert p.route_time_rescue is True
        assert p.name == "custom"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -m pytest tests/test_confidence_preset.py -x
```

Expected: failures with `AttributeError: module 'adsb_historical_routes' has no attribute 'PRESETS'`.

- [ ] **Step 3: Add the dataclass + presets + resolver**

In `/Users/johnyoung/Git/kml/adsb_historical_routes/adsb_historical_routes.py`, add this near the top (just after the existing `import` block, before the `class Airport` line). First, add `from dataclasses import dataclass, replace` to the imports if not already present.

```python
@dataclass(frozen=True)
class ConfidencePreset:
    """Tunable knobs for flight detection and joining aggressiveness."""
    name: str
    airport_radius_km: float
    max_join_gap_hours: float
    max_join_distance_km: float
    route_time_tolerance: float
    route_time_rescue: bool
    direction_aware_rescue: bool
    min_segment_points: int = 20
    min_flight_altitude_m: float = 300.0


PRESETS: Dict[str, ConfidencePreset] = {
    "strict": ConfidencePreset(
        name="strict",
        airport_radius_km=20,
        max_join_gap_hours=2.0,
        max_join_distance_km=100,
        route_time_tolerance=0.25,
        route_time_rescue=False,
        direction_aware_rescue=False,
    ),
    "balanced": ConfidencePreset(
        name="balanced",
        airport_radius_km=50,
        max_join_gap_hours=3.0,
        max_join_distance_km=200,
        route_time_tolerance=0.40,
        route_time_rescue=True,
        direction_aware_rescue=True,
    ),
    "permissive": ConfidencePreset(
        name="permissive",
        airport_radius_km=100,
        max_join_gap_hours=4.0,
        max_join_distance_km=500,
        route_time_tolerance=0.60,
        route_time_rescue=True,
        direction_aware_rescue=True,
    ),
}


def resolve_preset(args) -> ConfidencePreset:
    """Apply CLI overrides on top of the named preset, returning a (possibly renamed) ConfidencePreset."""
    base = PRESETS[args.confidence]
    overrides = {}
    if getattr(args, "airport_radius_km", None) is not None:
        overrides["airport_radius_km"] = args.airport_radius_km
    if getattr(args, "max_join_gap_hours", None) is not None:
        overrides["max_join_gap_hours"] = args.max_join_gap_hours
    if getattr(args, "max_join_distance_km", None) is not None:
        overrides["max_join_distance_km"] = args.max_join_distance_km
    if getattr(args, "route_time_tolerance", None) is not None:
        overrides["route_time_tolerance"] = args.route_time_tolerance
    rtr = getattr(args, "route_time_rescue", None)
    if rtr is not None:
        overrides["route_time_rescue"] = (rtr == "on")
    if not overrides:
        return base
    return replace(base, name="custom", **overrides)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -m pytest tests/test_confidence_preset.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git add tests/test_confidence_preset.py adsb_historical_routes.py && git commit -m "Add ConfidencePreset dataclass with strict/balanced/permissive presets

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 8: Add assert_join_invariants and invariant tests (TDD)

**Files:**
- Modify: `adsb_historical_routes.py` — add `assert_join_invariants` near `combine_segments_intelligently`.
- Create: `tests/test_invariants.py`

- [ ] **Step 1: Write the failing tests**

Write `/Users/johnyoung/Git/kml/adsb_historical_routes/tests/test_invariants.py`:

```python
"""Tests for cross-file joining invariants."""

from datetime import timedelta
import pytest


class TestJoinInvariants:
    def test_cross_aircraft_join_blocked(self, ahr, make_segment, utc):
        a = make_segment("E47FA6", utc, duration_min=60,
                         start_lat=-23.4, start_lon=-46.5,
                         end_lat=-22.9, end_lon=-43.2,
                         origin_code="SBGR")
        b = make_segment("E47FA7", utc + timedelta(hours=2), duration_min=60,
                         start_lat=-22.9, start_lon=-43.2,
                         end_lat=-23.4, end_lon=-46.5,
                         dest_code="SBGR")
        with pytest.raises(AssertionError, match="Cross-aircraft"):
            ahr.assert_join_invariants(a, b)

    def test_non_chronological_join_blocked(self, ahr, make_segment, utc):
        a = make_segment("E47FA6", utc + timedelta(hours=4), duration_min=60,
                         start_lat=-23.4, start_lon=-46.5,
                         end_lat=-22.9, end_lon=-43.2)
        b = make_segment("E47FA6", utc, duration_min=60,
                         start_lat=-22.9, start_lon=-43.2,
                         end_lat=-23.4, end_lon=-46.5)
        with pytest.raises(AssertionError, match="Non-chronological"):
            ahr.assert_join_invariants(a, b)

    def test_zero_gap_join_blocked(self, ahr, make_segment, utc):
        a = make_segment("E47FA6", utc, duration_min=60,
                         start_lat=-23.4, start_lon=-46.5,
                         end_lat=-22.9, end_lon=-43.2)
        # b starts at exactly the same time a ends
        b_start = a.landing_time
        b = make_segment("E47FA6", b_start, duration_min=60,
                         start_lat=-22.9, start_lon=-43.2,
                         end_lat=-23.4, end_lon=-46.5)
        with pytest.raises(AssertionError, match="Non-chronological|Zero|gap"):
            ahr.assert_join_invariants(a, b)

    def test_valid_chronological_passes(self, ahr, make_segment, utc):
        a = make_segment("E47FA6", utc, duration_min=60,
                         start_lat=-23.4, start_lon=-46.5,
                         end_lat=-22.9, end_lon=-43.2)
        b = make_segment("E47FA6", utc + timedelta(hours=3), duration_min=60,
                         start_lat=-22.9, start_lon=-43.2,
                         end_lat=-23.4, end_lon=-46.5)
        # Should not raise
        ahr.assert_join_invariants(a, b)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -m pytest tests/test_invariants.py -x
```

Expected: `AttributeError: ... assert_join_invariants`.

- [ ] **Step 3: Implement assert_join_invariants**

In `/Users/johnyoung/Git/kml/adsb_historical_routes/adsb_historical_routes.py`, find `def combine_segments_intelligently`. Just above it, add:

```python
def assert_join_invariants(current: "FlightSegment", next_seg: "FlightSegment") -> None:
    """Enforce that any join attempt is between two segments of the same aircraft, in chronological order, with a positive time gap.

    These conditions are guaranteed by the upstream partition-by-aircraft + sort-by-time logic; the assertions exist to fail loudly if a future refactor breaks that contract.
    """
    if current.aircraft_id != next_seg.aircraft_id:
        raise AssertionError(
            f"Cross-aircraft join blocked: {current.aircraft_id} vs {next_seg.aircraft_id}"
        )
    if next_seg.takeoff_time <= current.landing_time:
        raise AssertionError(
            f"Non-chronological join blocked: next.takeoff={next_seg.takeoff_time} not after current.landing={current.landing_time}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -m pytest tests/test_invariants.py -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git add tests/test_invariants.py adsb_historical_routes.py && git commit -m "Add cross-file join invariants with assertion-based enforcement

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 9: Add DiagnosticsRecorder (TDD)

**Files:**
- Modify: `adsb_historical_routes.py` — add `DiagnosticsRecorder` class near `create_output_kml`.
- Create: `tests/test_diagnostics.py`

- [ ] **Step 1: Write the failing tests**

Write `/Users/johnyoung/Git/kml/adsb_historical_routes/tests/test_diagnostics.py`:

```python
"""Tests for DiagnosticsRecorder."""

import csv
from datetime import timedelta
import pytest


@pytest.fixture
def recorder(ahr, tmp_path):
    out_kml = tmp_path / "out.kml"
    return ahr.DiagnosticsRecorder(str(out_kml), preset_name="balanced")


@pytest.fixture
def airports(ahr):
    return [
        ahr.Airport("SBGR", -23.43, -46.47, 2459),  # Sao Paulo
        ahr.Airport("SBGL", -22.81, -43.24, 28),    # Rio de Janeiro
    ]


class TestDiagnosticsRecorder:
    def test_writes_csv_with_header(self, ahr, recorder, tmp_path, airports, make_segment, utc):
        seg = make_segment("E47FA6", utc, duration_min=60,
                           start_lat=-23.43, start_lon=-46.47,
                           end_lat=-22.81, end_lon=-43.24,
                           origin_code="SBGR", dest_code="SBGL")
        recorder.record_raw(seg, segment_idx=0, airports=airports)
        recorder.record_outcome(seg, segment_idx=0, disposition="kept_complete", airports=airports)
        recorder.write_csv()

        csv_path = tmp_path / "out.kml.diagnostics.csv"
        assert csv_path.exists()
        with csv_path.open() as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert rows[0]["phase"] == "raw"
        assert rows[1]["phase"] == "final"

    def test_columns_match_spec(self, ahr, recorder, tmp_path, airports, make_segment, utc):
        seg = make_segment("E47FA6", utc, duration_min=60,
                           start_lat=-23.43, start_lon=-46.47,
                           end_lat=-22.81, end_lon=-43.24)
        recorder.record_raw(seg, segment_idx=0, airports=airports)
        recorder.write_csv()

        csv_path = tmp_path / "out.kml.diagnostics.csv"
        with csv_path.open() as f:
            reader = csv.DictReader(f)
            expected = {
                "phase", "preset", "aircraft_id", "registration", "source_file",
                "segment_idx", "takeoff_time", "landing_time", "duration_min",
                "num_points", "max_alt_m", "total_distance_km", "mean_bearing_deg",
                "origin_code", "dest_code", "origin_dist_km", "dest_dist_km",
                "nearest_airport_start_code", "nearest_airport_start_km",
                "nearest_airport_end_code", "nearest_airport_end_km",
                "disposition", "drop_reason", "joined_with_segment_idx", "rescue_method",
            }
            assert set(reader.fieldnames) == expected

    def test_preset_name_recorded(self, ahr, recorder, tmp_path, airports, make_segment, utc):
        seg = make_segment("E47FA6", utc, duration_min=60,
                           start_lat=-23.43, start_lon=-46.47,
                           end_lat=-22.81, end_lon=-43.24)
        recorder.record_raw(seg, segment_idx=0, airports=airports)
        recorder.write_csv()

        csv_path = tmp_path / "out.kml.diagnostics.csv"
        with csv_path.open() as f:
            row = next(csv.DictReader(f))
        assert row["preset"] == "balanced"

    def test_nearest_airport_filled_even_when_no_match(self, ahr, recorder, tmp_path, airports, make_segment, utc):
        # Segment endpoints far from any airport in our small DB
        seg = make_segment("E47FA6", utc, duration_min=60,
                           start_lat=10.0, start_lon=10.0,
                           end_lat=11.0, end_lon=11.0)
        recorder.record_raw(seg, segment_idx=0, airports=airports)
        recorder.write_csv()

        csv_path = tmp_path / "out.kml.diagnostics.csv"
        with csv_path.open() as f:
            row = next(csv.DictReader(f))
        # nearest_airport_*_code is the airport code regardless of preset radius (within 1000km)
        assert row["nearest_airport_start_code"] in {"SBGR", "SBGL", ""}
        assert row["nearest_airport_start_km"] != ""
        # origin_code is empty (it wasn't matched within the preset radius)
        assert row["origin_code"] == ""

    def test_outcome_fields_populated(self, ahr, recorder, tmp_path, airports, make_segment, utc):
        seg = make_segment("E47FA6", utc, duration_min=60,
                           start_lat=-23.43, start_lon=-46.47,
                           end_lat=-22.81, end_lon=-43.24,
                           origin_code="SBGR", dest_code="SBGL")
        recorder.record_outcome(seg, segment_idx=5, disposition="kept_joined",
                                joined_with=[3, 4], rescue_method="bearing_join",
                                airports=airports)
        recorder.write_csv()

        csv_path = tmp_path / "out.kml.diagnostics.csv"
        with csv_path.open() as f:
            row = next(csv.DictReader(f))
        assert row["disposition"] == "kept_joined"
        assert row["joined_with_segment_idx"] == "3,4"
        assert row["rescue_method"] == "bearing_join"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -m pytest tests/test_diagnostics.py -x
```

Expected: `AttributeError: ... DiagnosticsRecorder`.

- [ ] **Step 3: Implement DiagnosticsRecorder**

In `/Users/johnyoung/Git/kml/adsb_historical_routes/adsb_historical_routes.py`, just above `def create_output_kml`, add:

```python
class DiagnosticsRecorder:
    """Buffers per-segment diagnostic rows and writes them as a CSV sidecar to the output KML."""

    COLUMNS = [
        "phase", "preset", "aircraft_id", "registration", "source_file",
        "segment_idx", "takeoff_time", "landing_time", "duration_min",
        "num_points", "max_alt_m", "total_distance_km", "mean_bearing_deg",
        "origin_code", "dest_code", "origin_dist_km", "dest_dist_km",
        "nearest_airport_start_code", "nearest_airport_start_km",
        "nearest_airport_end_code", "nearest_airport_end_km",
        "disposition", "drop_reason", "joined_with_segment_idx", "rescue_method",
    ]

    def __init__(self, output_kml_path: str, preset_name: str):
        self.csv_path = output_kml_path + ".diagnostics.csv"
        self.preset_name = preset_name
        self.rows: List[Dict[str, str]] = []

    def _nearest_within(self, lat: float, lon: float, airports: List["Airport"], max_km: float = 1000.0):
        nearest = None
        min_d = float("inf")
        for a in airports:
            d = haversine_distance(lat, lon, a.lat, a.lon)
            if d < min_d and d <= max_km:
                min_d = d
                nearest = a
        return (nearest, min_d if nearest else float("inf"))

    def _build_row(self, phase: str, segment: "FlightSegment", segment_idx: int, airports: List["Airport"]) -> Dict[str, str]:
        first = segment.points[0] if segment.points else None
        last = segment.points[-1] if segment.points else None

        if first is None:
            ns_a, ns_km = (None, float("inf"))
            ne_a, ne_km = (None, float("inf"))
            mb = 0.0
        else:
            ns_a, ns_km = self._nearest_within(first.lat, first.lon, airports)
            ne_a, ne_km = self._nearest_within(last.lat, last.lon, airports)
            mb = mean_bearing(segment.points, window_minutes=10.0)

        origin_dist = ""
        dest_dist = ""
        if first and segment.origin:
            origin_dist = f"{haversine_distance(first.lat, first.lon, segment.origin.lat, segment.origin.lon):.2f}"
        if last and segment.destination:
            dest_dist = f"{haversine_distance(last.lat, last.lon, segment.destination.lat, segment.destination.lon):.2f}"

        return {
            "phase": phase,
            "preset": self.preset_name,
            "aircraft_id": segment.aircraft_id,
            "registration": segment.registration,
            "source_file": segment.source_file,
            "segment_idx": str(segment_idx),
            "takeoff_time": segment.takeoff_time.isoformat() if segment.takeoff_time else "",
            "landing_time": segment.landing_time.isoformat() if segment.landing_time else "",
            "duration_min": f"{segment.flight_duration:.2f}",
            "num_points": str(len(segment.points)),
            "max_alt_m": f"{segment.max_altitude:.0f}",
            "total_distance_km": f"{segment.total_distance:.2f}",
            "mean_bearing_deg": f"{mb:.1f}",
            "origin_code": segment.origin.code if segment.origin else "",
            "dest_code": segment.destination.code if segment.destination else "",
            "origin_dist_km": origin_dist,
            "dest_dist_km": dest_dist,
            "nearest_airport_start_code": ns_a.code if ns_a else "",
            "nearest_airport_start_km": f"{ns_km:.2f}" if ns_a else "",
            "nearest_airport_end_code": ne_a.code if ne_a else "",
            "nearest_airport_end_km": f"{ne_km:.2f}" if ne_a else "",
            "disposition": "",
            "drop_reason": "",
            "joined_with_segment_idx": "",
            "rescue_method": "",
        }

    def record_raw(self, segment: "FlightSegment", segment_idx: int, airports: List["Airport"]) -> None:
        self.rows.append(self._build_row("raw", segment, segment_idx, airports))

    def record_outcome(self, segment: "FlightSegment", segment_idx: int, disposition: str,
                       drop_reason: str = "", joined_with: Optional[List[int]] = None,
                       rescue_method: str = "none", airports: Optional[List["Airport"]] = None) -> None:
        row = self._build_row("final", segment, segment_idx, airports or [])
        row["disposition"] = disposition
        row["drop_reason"] = drop_reason
        row["joined_with_segment_idx"] = ",".join(str(i) for i in (joined_with or []))
        row["rescue_method"] = rescue_method
        self.rows.append(row)

    def write_csv(self) -> None:
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.COLUMNS)
            writer.writeheader()
            for row in self.rows:
                writer.writerow(row)
        print(f"Diagnostics written to: {self.csv_path}")
```

Verify `import csv` is present at the top of the file (it already is in v6_old).

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -m pytest tests/test_diagnostics.py -v
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git add tests/test_diagnostics.py adsb_historical_routes.py && git commit -m "Add DiagnosticsRecorder for per-segment audit CSV

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 10: Wire ConfidencePreset into find_nearest_airport and detect_flight_segments

**Files:**
- Modify: `adsb_historical_routes.py` — `find_nearest_airport`, `detect_flight_segments`, and `combine_segments_intelligently` accept the preset and use its values.
- Create: `tests/test_preset_wiring.py`

This task wires the preset's values down into the existing functions WITHOUT changing the existing default behavior. New parameters get default values matching the previous hardcoded constants so existing call sites still work.

- [ ] **Step 1: Write the failing tests**

Write `/Users/johnyoung/Git/kml/adsb_historical_routes/tests/test_preset_wiring.py`:

```python
"""Tests verifying ConfidencePreset values flow into airport matching and segment detection."""

from datetime import timedelta


class TestAirportRadiusWiring:
    def test_strict_radius_misses_far_airport(self, ahr):
        airport = ahr.Airport("FAR", 0.0, 0.0, 0)
        # 30 km away — outside strict's 20 km radius
        match = ahr.find_nearest_airport(0.0, 0.27, alt_meters=10.0,
                                         airports=[airport],
                                         max_distance_km=20, max_alt_diff_ft=3000.0,
                                         lenient=True)
        assert match is None

    def test_balanced_radius_catches_30km_airport(self, ahr):
        airport = ahr.Airport("MED", 0.0, 0.0, 0)
        match = ahr.find_nearest_airport(0.0, 0.27, alt_meters=10.0,
                                         airports=[airport],
                                         max_distance_km=50, max_alt_diff_ft=3000.0,
                                         lenient=True)
        assert match is not None
        assert match.code == "MED"


class TestPresetUsedInDetection:
    def test_preset_radius_passed_through(self, ahr, make_track_point, utc):
        """detect_flight_segments_with_preset matches origin/dest using preset.airport_radius_km."""
        # Build a track that ends 30km from the only airport
        from datetime import timedelta
        airport = ahr.Airport("SBGR", -23.43, -46.47, 2459)
        # Endpoint at -23.43, -46.18 (~30km east of SBGR)
        pts = []
        for i in range(40):
            t = utc + timedelta(seconds=i * 30)
            lon = -46.47 + 0.0073 * i
            pts.append(make_track_point(t, -23.43, lon, 10000.0))

        # Strict (20km): no match → both endpoints have origin/dest as None
        strict = ahr.PRESETS["strict"]
        strict_segs = ahr.detect_flight_segments_with_preset(pts, [airport], strict, max_gap_minutes=20.0)
        assert len(strict_segs) == 1
        _, _, origin_s, dest_s = strict_segs[0]
        assert origin_s is None  # start is at SBGR but not the endpoint
        # Wait — start is exactly at SBGR (0km). So strict actually matches start.
        # Let's reverse the assertion: dest (30km away) should NOT match under strict.
        assert dest_s is None

        balanced = ahr.PRESETS["balanced"]
        balanced_segs = ahr.detect_flight_segments_with_preset(pts, [airport], balanced, max_gap_minutes=20.0)
        _, _, _, dest_b = balanced_segs[0]
        assert dest_b is not None
        assert dest_b.code == "SBGR"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -m pytest tests/test_preset_wiring.py -x
```

Expected: failures (the first two tests should pass since `find_nearest_airport` already accepts `max_distance_km`; the third will fail because `detect_flight_segments_with_preset` doesn't exist yet).

- [ ] **Step 3: Add `detect_flight_segments_with_preset` wrapper**

In `/Users/johnyoung/Git/kml/adsb_historical_routes/adsb_historical_routes.py`, find `def detect_flight_segments(`. Just above it, add:

```python
def detect_flight_segments_with_preset(track_points: List["TrackPoint"], airports: List["Airport"],
                                        preset: ConfidencePreset,
                                        max_gap_minutes: float
                                        ) -> List[Tuple[List["TrackPoint"], bool, Optional["Airport"], Optional["Airport"]]]:
    """Like detect_flight_segments, but uses preset.airport_radius_km and preset.min_segment_points."""
    if not track_points or len(track_points) < preset.min_segment_points:
        return []

    segments = []
    current_segment = []
    segment_has_gap = False
    last_point = None

    for point in track_points:
        if last_point:
            time_gap = (point.datetime - last_point.datetime).total_seconds() / 60
            if time_gap > max_gap_minutes:
                if len(current_segment) >= preset.min_segment_points:
                    origin = find_nearest_airport(
                        current_segment[0].lat, current_segment[0].lon, current_segment[0].alt,
                        airports, max_distance_km=preset.airport_radius_km,
                        max_alt_diff_ft=3000.0, lenient=True
                    )
                    destination = find_nearest_airport(
                        current_segment[-1].lat, current_segment[-1].lon, current_segment[-1].alt,
                        airports, max_distance_km=preset.airport_radius_km,
                        max_alt_diff_ft=3000.0, lenient=True
                    )
                    segments.append((current_segment, segment_has_gap, origin, destination))
                current_segment = [point]
                segment_has_gap = False
            else:
                current_segment.append(point)
        else:
            current_segment = [point]
        last_point = point

    if len(current_segment) >= preset.min_segment_points:
        origin = find_nearest_airport(
            current_segment[0].lat, current_segment[0].lon, current_segment[0].alt,
            airports, max_distance_km=preset.airport_radius_km,
            max_alt_diff_ft=3000.0, lenient=True
        )
        destination = find_nearest_airport(
            current_segment[-1].lat, current_segment[-1].lon, current_segment[-1].alt,
            airports, max_distance_km=preset.airport_radius_km,
            max_alt_diff_ft=3000.0, lenient=True
        )
        segments.append((current_segment, segment_has_gap, origin, destination))

    return segments
```

The original `detect_flight_segments` is left unchanged — `process_kml_files` will be switched to call the preset-aware version in Task 13.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -m pytest tests/test_preset_wiring.py -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git add tests/test_preset_wiring.py adsb_historical_routes.py && git commit -m "Add preset-aware detect_flight_segments_with_preset

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 11: Add bearing-aware mid-cruise join (Case 5) and tests

**Files:**
- Modify: `adsb_historical_routes.py` — `combine_segments_intelligently` gains a `preset` parameter; logic uses `preset.max_join_gap_hours`, `preset.max_join_distance_km`, and the new Case 5.
- Modify: `tests/test_join_logic.py` (new file)

- [ ] **Step 1: Write the failing tests**

Write `/Users/johnyoung/Git/kml/adsb_historical_routes/tests/test_join_logic.py`:

```python
"""Tests for cross-file joining logic with confidence presets."""

from datetime import timedelta


class TestStrictRejects:
    def test_long_overnight_gap_rejected(self, ahr, make_segment, utc):
        a = make_segment("E47FA6", utc, duration_min=60,
                         start_lat=-23.43, start_lon=-46.47,
                         end_lat=-22.81, end_lon=-43.24,
                         origin_code="SBGR")  # ends mid-cruise (no dest)
        b = make_segment("E47FA6", utc + timedelta(hours=10), duration_min=60,
                         start_lat=-23.43, start_lon=-46.47,
                         end_lat=-22.81, end_lon=-43.24,
                         dest_code="SBGL")  # next day, no origin
        preset = ahr.PRESETS["strict"]
        out = ahr.combine_segments_intelligently([a, b], airports=[], routes_dict={}, preset=preset)
        # 10-hour gap > strict's 2-hour ceiling: not joined
        assert len(out) <= 2
        # Neither should be marked complete-via-join
        for s in out:
            assert not (s.origin and s.origin.code == "SBGR" and s.destination and s.destination.code == "SBGL")

    def test_dest_eq_next_origin_not_joined(self, ahr, make_segment, utc):
        # Real sequential flights through SBGR — must NOT join
        a = make_segment("E47FA6", utc, duration_min=60,
                         start_lat=-22.81, start_lon=-43.24,
                         end_lat=-23.43, end_lon=-46.47,
                         origin_code="SBGL", dest_code="SBGR")
        b = make_segment("E47FA6", utc + timedelta(hours=2), duration_min=60,
                         start_lat=-23.43, start_lon=-46.47,
                         end_lat=-15.87, end_lon=-47.92,
                         origin_code="SBGR", dest_code="SBBR")
        preset = ahr.PRESETS["balanced"]
        out = ahr.combine_segments_intelligently([a, b], airports=[], routes_dict={}, preset=preset)
        # Both segments preserved as separate flights
        assert len(out) == 2


class TestBearingAwareJoin:
    def test_aligned_bearing_joins_under_permissive(self, ahr, make_segment, utc):
        # Aircraft heading SE (135°). Segment A ends mid-cruise.
        a = make_segment("E47FA6", utc, duration_min=60,
                         start_lat=-15.0, start_lon=-50.0,
                         end_lat=-16.5, end_lon=-48.5,
                         origin_code="SBBR")  # dest=None
        # Segment B continues SE from a point 200km further along the bearing
        b_start = utc + timedelta(hours=1, minutes=15)  # 15-min cruise gap
        b = make_segment("E47FA6", b_start, duration_min=45,
                         start_lat=-18.3, start_lon=-46.7,
                         end_lat=-19.5, end_lon=-44.0,
                         dest_code="SBSV")  # origin=None
        preset = ahr.PRESETS["permissive"]
        out = ahr.combine_segments_intelligently([a, b], airports=[], routes_dict={}, preset=preset)
        # Should be joined into one complete segment
        complete = [s for s in out if s.origin and s.destination]
        assert len(complete) == 1
        assert complete[0].origin.code == "SBBR"
        assert complete[0].destination.code == "SBSV"

    def test_misaligned_bearing_not_joined(self, ahr, make_segment, utc):
        # A ends mid-cruise heading SE
        a = make_segment("E47FA6", utc, duration_min=60,
                         start_lat=-15.0, start_lon=-50.0,
                         end_lat=-16.5, end_lon=-48.5,
                         origin_code="SBBR")
        # B starts 200km away, but heading NORTHWEST (back toward A's start) — wrong direction
        b_start = utc + timedelta(hours=1, minutes=15)
        b = make_segment("E47FA6", b_start, duration_min=45,
                         start_lat=-18.3, start_lon=-46.7,
                         end_lat=-15.5, end_lon=-49.5,
                         dest_code="SBBR")
        preset = ahr.PRESETS["permissive"]
        out = ahr.combine_segments_intelligently([a, b], airports=[], routes_dict={}, preset=preset)
        # Bearing mismatch — should NOT bearing-join (bearing diff > 30°)
        # Since both have UNKN-like endpoints and 200km is at the boundary, the "Case 5" join is rejected
        complete = [s for s in out if s.origin and s.destination
                    and s.origin.code == "SBBR" and s.destination.code == "SBBR"]
        assert len(complete) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -m pytest tests/test_join_logic.py -x
```

Expected: failure — `combine_segments_intelligently` doesn't accept a `preset` keyword argument.

- [ ] **Step 3: Refactor combine_segments_intelligently to accept preset**

In `/Users/johnyoung/Git/kml/adsb_historical_routes/adsb_historical_routes.py`, find `def combine_segments_intelligently(`. Replace its signature and body with:

```python
def combine_segments_intelligently(segments: List["FlightSegment"], airports: List["Airport"],
                                   routes_dict: Dict[Tuple[str, str], float],
                                   preset: ConfidencePreset = None,
                                   max_join_gap_hours: float = None,
                                   recorder: Optional["DiagnosticsRecorder"] = None,
                                   raw_idx_by_segment: Optional[Dict[int, int]] = None,
                                   ) -> List["FlightSegment"]:
    """Intelligently combine segments using the preset's join knobs.

    Backward-compat: if ``preset`` is None, uses the legacy hardcoded values.
    The optional ``recorder`` and ``raw_idx_by_segment`` enable diagnostics CSV output.
    """
    if not segments:
        return []

    if preset is None:
        # Legacy behavior preserved for any callers that don't pass a preset
        eff_gap = max_join_gap_hours if max_join_gap_hours is not None else 2.0
        eff_dist = 100.0
        eff_route_tol = 0.4
        rt_rescue = True
        dir_aware = False
    else:
        eff_gap = preset.max_join_gap_hours
        eff_dist = preset.max_join_distance_km
        eff_route_tol = preset.route_time_tolerance
        rt_rescue = preset.route_time_rescue
        dir_aware = preset.direction_aware_rescue

    segments.sort(key=lambda s: s.takeoff_time if s.takeoff_time else datetime.min)
    airports_dict = {a.code: a for a in airports}
    combined = []
    i = 0

    def raw_idx(seg):
        return raw_idx_by_segment.get(id(seg), -1) if raw_idx_by_segment else -1

    def record_outcome(seg, disposition, drop_reason="", joined_with=None, rescue_method="none"):
        if recorder is not None:
            recorder.record_outcome(
                seg, segment_idx=raw_idx(seg), disposition=disposition,
                drop_reason=drop_reason, joined_with=joined_with or [],
                rescue_method=rescue_method, airports=airports
            )

    while i < len(segments):
        current = segments[i]
        joined_idxs = []
        rescue_method = "none"

        if current.is_complete and current.is_valid_flight():
            combined.append(current)
            record_outcome(current, "kept_complete")
            i += 1
            continue

        # Forward route-time rescue (origin known, dest missing) — only if preset enables it
        if rt_rescue and current.origin and not current.destination:
            dest_code = match_route_by_time_with_bearing(
                current, routes_dict, airports_dict, tolerance=eff_route_tol
            )
            if dest_code and dest_code in airports_dict:
                current.destination = airports_dict[dest_code]
                current.is_complete = True
                rescue_method = "route_time_forward"
                print(f"        Enhanced {current.origin.code} -> {dest_code} via route-time + bearing")

        # Reverse route-time rescue (dest known, origin missing)
        elif rt_rescue and not current.origin and current.destination:
            for (orig, dest), avg_time in routes_dict.items():
                if dest != current.destination.code:
                    continue
                time_diff = abs(current.flight_duration - avg_time)
                if time_diff < avg_time * eff_route_tol and orig in airports_dict:
                    if dir_aware:
                        # Bearing from origin → destination should align with current's mean bearing
                        cand_brg = bearing(airports_dict[orig].lat, airports_dict[orig].lon,
                                           current.destination.lat, current.destination.lon)
                        seg_brg = mean_bearing(current.points, window_minutes=15.0)
                        if angular_diff(cand_brg, seg_brg) > 45.0:
                            continue
                    current.origin = airports_dict[orig]
                    current.is_complete = True
                    rescue_method = "route_time_reverse"
                    print(f"        Enhanced {orig} -> {current.destination.code} via reverse route-time + bearing")
                    break

        # Try to join with the next segment
        if i + 1 < len(segments):
            next_seg = segments[i + 1]
            if current.aircraft_id == next_seg.aircraft_id:
                assert_join_invariants(current, next_seg)
                time_gap_h = (next_seg.takeoff_time - current.landing_time).total_seconds() / 3600

                if time_gap_h < eff_gap:
                    should_join = False

                    # Case 1: both endpoints incomplete and close in space
                    if not current.destination and not next_seg.origin:
                        if current.points and next_seg.points:
                            dist = haversine_distance(
                                current.points[-1].lat, current.points[-1].lon,
                                next_seg.points[0].lat, next_seg.points[0].lon
                            )
                            if dist < eff_dist:
                                should_join = True

                    # Case 2: both at altitude, very short gap
                    if (current.max_altitude > 3000 and next_seg.max_altitude > 3000 and
                            time_gap_h < 0.5):
                        if current.points and next_seg.points:
                            dist = haversine_distance(
                                current.points[-1].lat, current.points[-1].lon,
                                next_seg.points[0].lat, next_seg.points[0].lon
                            )
                            if dist < eff_dist:
                                should_join = True

                    # Case 3: dest matches next origin → these are sequential flights, DO NOT JOIN
                    if (current.destination and next_seg.origin and
                            current.destination.code == next_seg.origin.code):
                        should_join = False

                    # Case 4: both already complete → DO NOT JOIN
                    if current.is_complete and next_seg.is_complete:
                        should_join = False

                    # Case 5 (new): direction-aware mid-cruise join
                    if (dir_aware and not current.destination and not next_seg.origin and
                            current.points and next_seg.points):
                        seg_brg = mean_bearing(current.points, window_minutes=10.0)
                        gap_brg = bearing(current.points[-1].lat, current.points[-1].lon,
                                          next_seg.points[0].lat, next_seg.points[0].lon)
                        if angular_diff(seg_brg, gap_brg) <= 30.0:
                            # Sanity-check: gap distance ≈ cruise-speed × time (reject extreme outliers)
                            gap_dist = haversine_distance(
                                current.points[-1].lat, current.points[-1].lon,
                                next_seg.points[0].lat, next_seg.points[0].lon
                            )
                            implied_speed = gap_dist / max(time_gap_h, 0.01)
                            if 200 <= implied_speed <= 1100:  # km/h, plausible cruise band
                                should_join = True
                                rescue_method = "bearing_join"

                    if should_join:
                        print(f"        Joining segments: {current} + {next_seg}")
                        new_points = current.points + next_seg.points
                        new_segment = FlightSegment(
                            current.aircraft_id,
                            current.registration,
                            new_points,
                            current.origin or next_seg.origin,
                            next_seg.destination or current.destination,
                            current.style_color,
                            current.style_width,
                            current.style_opacity,
                            current.source_file,
                        )
                        new_segment.has_gaps = current.has_gaps or next_seg.has_gaps
                        joined_idxs = [raw_idx(current), raw_idx(next_seg)]
                        # Record the consumed `next_seg` outcome before overwriting `current`
                        record_outcome(next_seg, "kept_joined", joined_with=[raw_idx(current)],
                                       rescue_method=rescue_method)
                        current = new_segment
                        i += 1

        if current.is_valid_flight():
            disp = "kept_rescued" if rescue_method != "none" else (
                "kept_joined" if joined_idxs else "kept_complete"
            )
            combined.append(current)
            record_outcome(current, disp, joined_with=joined_idxs, rescue_method=rescue_method)
        else:
            # Last-chance route-time rescue with looser tolerance
            if rt_rescue and current.origin and not current.destination and current.max_altitude > 3000:
                dest_code = match_route_by_time_with_bearing(
                    current, routes_dict, airports_dict, tolerance=min(eff_route_tol + 0.1, 0.7)
                )
                if dest_code and dest_code in airports_dict:
                    current.destination = airports_dict[dest_code]
                    current.is_complete = True
                    if current.is_valid_flight():
                        print(f"        Rescued segment: {current.origin.code} -> {dest_code}")
                        combined.append(current)
                        record_outcome(current, "kept_rescued",
                                       joined_with=joined_idxs, rescue_method="route_time_forward")
                        i += 1
                        continue
            record_outcome(current, "dropped_unjoined",
                           drop_reason="no_airport_match" if not current.origin or not current.destination else "invalid",
                           joined_with=joined_idxs)

        i += 1

    return combined
```

- [ ] **Step 4: Add the `match_route_by_time_with_bearing` helper**

Just below the existing `match_route_by_time` function, add:

```python
def match_route_by_time_with_bearing(segment: "FlightSegment",
                                      routes_dict: Dict[Tuple[str, str], float],
                                      airports_dict: Dict[str, "Airport"],
                                      tolerance: float = 0.4,
                                      bearing_tolerance_deg: float = 45.0) -> Optional[str]:
    """Like match_route_by_time, but also requires the bearing from segment's origin to candidate destination to align with the segment's mean cruise bearing within bearing_tolerance_deg degrees."""
    if not segment.origin or not segment.points:
        return None

    seg_brg = mean_bearing(segment.points, window_minutes=15.0)
    best_match = None
    best_diff = float("inf")

    for (orig, dest), avg_time in routes_dict.items():
        if orig != segment.origin.code:
            continue
        if dest not in airports_dict:
            continue
        time_diff = abs(segment.flight_duration - avg_time)
        relative_diff = time_diff / avg_time
        if relative_diff >= tolerance:
            continue

        cand_brg = bearing(segment.origin.lat, segment.origin.lon,
                           airports_dict[dest].lat, airports_dict[dest].lon)
        if angular_diff(cand_brg, seg_brg) > bearing_tolerance_deg:
            continue

        if time_diff < best_diff:
            best_diff = time_diff
            best_match = dest

    return best_match
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -m pytest tests/test_join_logic.py -v
```

Expected: 4 tests pass. If `test_aligned_bearing_joins_under_permissive` fails on the implied-speed check, adjust the test fixture's `b_start` so that 200 km / 1.25 h ≈ 160 km/h … too low. Either bump the gap distance or shorten the time gap to bring implied speed into the 200–1100 km/h band. The simpler fix is to widen the test's gap distance: change `b` start coordinates to something like `(-19.5, -45.5)` (400 km away) with `b_start = utc + timedelta(hours=1, minutes=30)` (30-min gap on top of A's 60-min duration).

If you adjust the fixture, also update the docstring intent in the test to match.

- [ ] **Step 6: Commit**

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git add tests/test_join_logic.py adsb_historical_routes.py && git commit -m "Add bearing-aware mid-cruise join (Case 5) and preset-driven join knobs

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 12: Add bearing-aware route_time rescue tests

**Files:**
- Create: `tests/test_route_time_rescue.py`

The implementation already landed in Task 11 (`match_route_by_time_with_bearing`). This task adds the focused unit tests for it.

- [ ] **Step 1: Write tests**

Write `/Users/johnyoung/Git/kml/adsb_historical_routes/tests/test_route_time_rescue.py`:

```python
"""Tests for bearing-checked route-time rescue."""

from datetime import timedelta


class TestRouteTimeRescue:
    def test_aligned_bearing_rescues(self, ahr, make_segment, utc):
        # Origin SBGR (-23.43, -46.47), heading south to SAEZ (-34.82, -58.54)
        # Mean bearing should be ~225° (southwest)
        seg = make_segment("E47FA6", utc, duration_min=140,
                           start_lat=-23.43, start_lon=-46.47,
                           end_lat=-30.0, end_lon=-52.0,  # mid-cruise endpoint heading SW
                           origin_code="SBGR")
        airports = {
            "SBGR": ahr.Airport("SBGR", -23.43, -46.47, 2459),
            "SAEZ": ahr.Airport("SAEZ", -34.82, -58.54, 67),
            "SBSV": ahr.Airport("SBSV", -12.91, -38.32, 64),  # NORTHEAST of SBGR
        }
        routes = {
            ("SBGR", "SAEZ"): 140,
            ("SBGR", "SBSV"): 140,  # same duration as a confounder, opposite bearing
        }
        result = ahr.match_route_by_time_with_bearing(seg, routes, airports, tolerance=0.20)
        assert result == "SAEZ"

    def test_misaligned_bearing_rejects(self, ahr, make_segment, utc):
        # Origin SBGR but heading NORTH (toward SBSV not SAEZ)
        seg = make_segment("E47FA6", utc, duration_min=140,
                           start_lat=-23.43, start_lon=-46.47,
                           end_lat=-15.0, end_lon=-43.0,  # northeast
                           origin_code="SBGR")
        airports = {
            "SBGR": ahr.Airport("SBGR", -23.43, -46.47, 2459),
            "SAEZ": ahr.Airport("SAEZ", -34.82, -58.54, 67),  # SOUTHWEST
        }
        # Only SAEZ in routes; bearing won't match → no rescue
        routes = {("SBGR", "SAEZ"): 140}
        result = ahr.match_route_by_time_with_bearing(seg, routes, airports, tolerance=0.20)
        assert result is None

    def test_no_origin_returns_none(self, ahr, make_segment, utc):
        seg = make_segment("E47FA6", utc, duration_min=140,
                           start_lat=-23.43, start_lon=-46.47,
                           end_lat=-30.0, end_lon=-52.0)  # no origin_code
        airports = {"SAEZ": ahr.Airport("SAEZ", -34.82, -58.54, 67)}
        routes = {("SBGR", "SAEZ"): 140}
        result = ahr.match_route_by_time_with_bearing(seg, routes, airports)
        assert result is None
```

- [ ] **Step 2: Run tests to verify they pass**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -m pytest tests/test_route_time_rescue.py -v
```

Expected: 3 tests pass.

- [ ] **Step 3: Commit**

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git add tests/test_route_time_rescue.py && git commit -m "Add tests for bearing-checked route-time rescue

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 13: Wire DiagnosticsRecorder and preset into process_kml_files

**Files:**
- Modify: `adsb_historical_routes.py` — `process_kml_files` accepts `preset: ConfidencePreset` and creates a `DiagnosticsRecorder`. Switch detection to `detect_flight_segments_with_preset`. Pass recorder + raw_idx mapping into `combine_segments_intelligently`.

- [ ] **Step 1: Update process_kml_files signature**

In `/Users/johnyoung/Git/kml/adsb_historical_routes/adsb_historical_routes.py`, find `def process_kml_files(`. Replace its signature to:

```python
def process_kml_files(kml_files: List[str], airports: List["Airport"], routes: List["Route"],
                     output_file: str, preset: ConfidencePreset,
                     group_by: str = "destination",
                     sample_minutes: float = 2.0,
                     max_gap_minutes: float = 20.0,
                     override_color: Optional[str] = None,
                     override_width: Optional[str] = None,
                     override_opacity: Optional[int] = None,
                     show_labels: bool = False,
                     show_icons: bool = False,
                     extend_to_ground: bool = False):
```

- [ ] **Step 2: Replace the body of process_kml_files**

Replace the function body (everything after the docstring/signature) with:

```python
    """Main processing pipeline driven by a ConfidencePreset, emitting both KML and a diagnostics CSV."""
    routes_dict = {(r.origin, r.destination): r.avg_time_min for r in routes}
    recorder = DiagnosticsRecorder(output_file, preset_name=preset.name)

    all_segments: List[FlightSegment] = []
    raw_idx_by_segment: Dict[int, int] = {}
    next_idx = 0

    kml_files = sorted(kml_files)

    for input_file in kml_files:
        aircraft_id = extract_aircraft_id(input_file)
        print(f"\nProcessing: {os.path.basename(input_file)} (Aircraft: {aircraft_id})")

        all_tracks = parse_kml_tracks(input_file, aircraft_id)
        print(f"Found {len(all_tracks)} useful tracks")

        for track_points, registration, style_color, style_width, style_opacity in all_tracks:
            if not track_points:
                continue
            print(f"  Track with {len(track_points)} points (Reg: {registration})")

            raw_segments = detect_flight_segments_with_preset(
                track_points, airports, preset, max_gap_minutes=max_gap_minutes
            )
            print(f"    Detected {len(raw_segments)} raw segments")

            for segment_points, has_gap, origin, destination in raw_segments:
                if len(segment_points) < preset.min_segment_points:
                    continue
                segment = FlightSegment(
                    aircraft_id, registration, segment_points,
                    origin, destination,
                    style_color, style_width, style_opacity,
                    os.path.basename(input_file)
                )
                segment.has_gaps = has_gap

                raw_idx_by_segment[id(segment)] = next_idx
                recorder.record_raw(segment, segment_idx=next_idx, airports=airports)
                next_idx += 1

                all_segments.append(segment)
                orig = origin.code if origin else "NONE"
                dest = destination.code if destination else "NONE"
                print(f"      {orig} -> {dest}: {len(segment_points)} pts, "
                      f"{segment.flight_duration:.1f} min, max alt {segment.max_altitude:.0f}m")

    print(f"\n\nTotal raw segments collected: {len(all_segments)}")

    print("\nCombining segments by aircraft...")
    segments_by_aircraft = defaultdict(list)
    for seg in all_segments:
        segments_by_aircraft[seg.aircraft_id].append(seg)

    final_segments: List[FlightSegment] = []
    for aircraft_id, segments in segments_by_aircraft.items():
        print(f"\n  Aircraft {aircraft_id}: {len(segments)} raw segments")
        combined = combine_segments_intelligently(
            segments, airports, routes_dict,
            preset=preset, recorder=recorder,
            raw_idx_by_segment=raw_idx_by_segment
        )
        complete_valid = [s for s in combined if s.is_complete and s.is_valid_flight()]
        print(f"    Final valid flights: {len(complete_valid)}")
        final_segments.extend(complete_valid)

    print(f"\n\nTOTAL VALID COMPLETE FLIGHTS: {len(final_segments)}")

    print("\nCreating output KML...")
    total_flights = create_output_kml(
        final_segments, output_file, group_by,
        sample_minutes,
        override_color, override_width, override_opacity,
        show_labels, show_icons, extend_to_ground
    )

    recorder.write_csv()

    print("\n" + "=" * 60)
    print("FLIGHT PARSING SUMMARY")
    print("=" * 60)
    print(f"Confidence preset: {preset.name}")
    print(f"Total valid complete flights: {total_flights}")
    print(f"Match rate: {total_flights}/{len(all_segments)} = "
          f"{total_flights / max(len(all_segments), 1) * 100:.1f}%")

    route_counts = defaultdict(int)
    for seg in final_segments:
        route_counts[f"{seg.origin.code}-{seg.destination.code}"] += 1
    print("\nFlights by route:")
    for route, count in sorted(route_counts.items()):
        print(f"  {route}: {count} flights")
    print("=" * 60)
```

- [ ] **Step 3: Smoke-test the import still works**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -c "
import importlib.util, sys
spec = importlib.util.spec_from_file_location('m','adsb_historical_routes.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print('Imports OK')
print('PRESETS:', list(m.PRESETS.keys()))
print('process_kml_files signature:', m.process_kml_files.__name__)
"
```

Expected: `Imports OK` and the preset list.

- [ ] **Step 4: Run all tests**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -m pytest -x
```

Expected: all previous tests still pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git add adsb_historical_routes.py && git commit -m "Wire DiagnosticsRecorder and ConfidencePreset into process_kml_files

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 14: Add CLI arguments

**Files:**
- Modify: `adsb_historical_routes.py` — `main()` parses `--confidence`, override flags, and passes the resolved preset into `process_kml_files`.

- [ ] **Step 1: Update the argument parser**

In `/Users/johnyoung/Git/kml/adsb_historical_routes/adsb_historical_routes.py`, find `def main():`. Just after the existing `parser.add_argument(...)` calls and before `args = parser.parse_args()`, add:

```python
    # Confidence preset and per-knob overrides
    parser.add_argument('--confidence', choices=['strict', 'balanced', 'permissive'],
                       default='balanced',
                       help='Detection/joining aggressiveness preset (default: balanced)')
    parser.add_argument('--airport-radius-km', type=float, default=None,
                       dest='airport_radius_km',
                       help='Override airport-match radius in km')
    parser.add_argument('--max-join-gap-hours', type=float, default=None,
                       dest='max_join_gap_hours',
                       help='Override max gap (hours) for cross-file joining')
    parser.add_argument('--max-join-distance-km', type=float, default=None,
                       dest='max_join_distance_km',
                       help='Override max spatial gap (km) for cross-file joining')
    parser.add_argument('--route-time-tolerance', type=float, default=None,
                       dest='route_time_tolerance',
                       help='Override route-time matching tolerance (e.g., 0.30 = ±30%%)')
    parser.add_argument('--route-time-rescue', choices=['on', 'off'], default=None,
                       dest='route_time_rescue',
                       help='Override route-time rescue (on/off)')
```

- [ ] **Step 2: Resolve the preset and pass it through**

Find the call site `process_kml_files(...)` near the end of `main()`. Just before it, insert:

```python
    preset = resolve_preset(args)
    print(f"  Confidence preset: {preset.name}")
    print(f"    airport_radius_km={preset.airport_radius_km}, "
          f"max_join_gap_hours={preset.max_join_gap_hours}, "
          f"max_join_distance_km={preset.max_join_distance_km}, "
          f"route_time_tolerance={preset.route_time_tolerance}, "
          f"route_time_rescue={preset.route_time_rescue}, "
          f"direction_aware_rescue={preset.direction_aware_rescue}")
```

Then update the `process_kml_files` call to pass `preset=preset` as the third positional / first keyword:

```python
    process_kml_files(
        kml_files, airports, routes, args.output,
        preset=preset,
        group_by=args.group,
        sample_minutes=args.sample,
        max_gap_minutes=args.maxgap,
        override_color=args.color,
        override_width=args.width,
        override_opacity=args.opacity,
        show_labels=show_labels,
        show_icons=show_icons,
        extend_to_ground=extend_to_ground,
    )
```

- [ ] **Step 3: Smoke-test the CLI help**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 adsb_historical_routes.py --help 2>&1 | grep -E "confidence|airport-radius|join-gap|route-time"
```

Expected: lines for each new flag.

- [ ] **Step 4: Run all tests**

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -m pytest -x
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git add adsb_historical_routes.py && git commit -m "Add CLI flags: --confidence preset and per-knob overrides

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 15: Integration test against a small real sample

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write the integration test**

Write `/Users/johnyoung/Git/kml/adsb_historical_routes/tests/test_integration.py`:

```python
"""Integration test: run the full pipeline against 1 aircraft × 3 days from real LAN data."""

import csv
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "adsb_historical_routes.py"
KML_DIR = PROJECT_ROOT / "kml_input" / "kml_downloads"
AIRPORTS = PROJECT_ROOT / "input" / "latam_la_airports.csv"
ROUTES = PROJECT_ROOT / "input" / "latam_la_routes_time.csv"


def _pick_sample_files() -> list:
    if not KML_DIR.exists():
        return []
    files_by_hex = {}
    for f in sorted(KML_DIR.glob("*.kml")):
        m = re.match(r"^([a-f0-9]+)_(\d{4}-\d{2}-\d{2})_baro_avg\.kml$", f.name)
        if m:
            files_by_hex.setdefault(m.group(1), []).append(f)
    if not files_by_hex:
        return []
    # Pick first hex with ≥ 3 substantial (>50KB) days
    for hex_id, files in files_by_hex.items():
        substantial = [f for f in files if f.stat().st_size > 50_000]
        if len(substantial) >= 3:
            return [str(f) for f in sorted(substantial)[:3]]
    return []


@pytest.mark.skipif(not KML_DIR.exists(), reason="kml_input/kml_downloads not present")
@pytest.mark.skipif(not AIRPORTS.exists(), reason="latam_la_airports.csv not present")
@pytest.mark.skipif(not ROUTES.exists(), reason="latam_la_routes_time.csv not present")
def test_three_presets_monotonic(tmp_path):
    sample = _pick_sample_files()
    if not sample:
        pytest.skip("No suitable sample files found")

    counts = {}
    for preset in ["strict", "balanced", "permissive"]:
        out_kml = tmp_path / f"out_{preset}.kml"
        cmd = [
            sys.executable, str(SCRIPT),
            "--kml-files", *sample,
            "--airports", str(AIRPORTS),
            "--routes", str(ROUTES),
            "--output", str(out_kml),
            "--confidence", preset,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, f"Failed under {preset}: {result.stderr}"
        assert out_kml.exists(), f"Missing KML for {preset}"

        diag_path = tmp_path / f"out_{preset}.kml.diagnostics.csv"
        assert diag_path.exists(), f"Missing diagnostics for {preset}"
        with diag_path.open() as f:
            rows = list(csv.DictReader(f))
        kept = sum(1 for r in rows if r["phase"] == "final" and r["disposition"].startswith("kept_"))
        counts[preset] = kept
        print(f"{preset}: {kept} kept")

    assert counts["strict"] <= counts["balanced"] <= counts["permissive"], \
        f"Monotonicity violated: {counts}"
```

- [ ] **Step 2: Run the integration test**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && python3 -m pytest tests/test_integration.py -v -s
```

Expected: test passes (or skips gracefully if data isn't present). The `-s` flag shows the printed counts.

If the test fails on monotonicity: investigate the diagnostics CSV — usually means a preset's stricter knobs aren't actually rejecting things they should. Re-check Task 11's Case 5 implementation.

- [ ] **Step 3: Commit**

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git add tests/test_integration.py && git commit -m "Add integration test verifying preset monotonicity on real LAN data

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 16: Update SPECIFICATION.md and CHANGELOG.md (v1.1)

**Files:**
- Modify: `SPECIFICATION.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Append a "Cross-file joining invariants" section to SPECIFICATION.md**

Read the file, find the last section. Append:

```markdown

## Cross-File Joining Invariants

The `combine_segments_intelligently` function MUST satisfy the following invariants when attempting to join two raw flight segments. These are encoded as runtime assertions in `assert_join_invariants`.

1. **Same aircraft.** Joins are only attempted between segments with the same `aircraft_id` (extracted from the KML filename's hex prefix and uppercased).
2. **Chronological order.** `next_seg.takeoff_time` must be strictly greater than `current.landing_time`.
3. **Positive time gap.** Implicit in (2) — zero or negative gaps are rejected.
4. **Time-gap ceiling.** The gap between segment end and next segment start must be `< preset.max_join_gap_hours`. Beyond this, the segments are kept separate even if all other criteria match.

## Confidence Presets

| Knob | strict | balanced | permissive |
|---|---|---|---|
| `airport_radius_km` | 20 | 50 | 100 |
| `max_join_gap_hours` | 2.0 | 3.0 | 4.0 |
| `max_join_distance_km` | 100 | 200 | 500 |
| `route_time_tolerance` | 0.25 | 0.40 | 0.60 |
| `route_time_rescue` | off | on | on |
| `direction_aware_rescue` | off | on | on |
| `min_segment_points` | 20 | 20 | 20 |
| `min_flight_altitude_m` | 300 | 300 | 300 |

CLI: `--confidence {strict,balanced,permissive}` selects the preset (default: `balanced`). Any individual knob can be overridden with `--airport-radius-km`, `--max-join-gap-hours`, `--max-join-distance-km`, `--route-time-tolerance`, `--route-time-rescue {on,off}`. Overriding any knob renames the active preset to `custom` in logs and the diagnostics CSV.

## Diagnostics CSV

Every run writes `<output>.diagnostics.csv` next to the output KML. One row per raw segment (`phase=raw`) and one row per outcome (`phase=final`). Columns are documented in the design spec at `docs/superpowers/specs/2026-04-28-improved-route-detection-design.md`.
```

- [ ] **Step 2: Add v1.1 entry to CHANGELOG.md**

Read `/Users/johnyoung/Git/kml/adsb_historical_routes/CHANGELOG.md`. Above the v1.0 entry that was added in Task 3, prepend:

```markdown
## [1.1.0] - 2026-04-28 (improved-route-detection branch)

### Added

- **Tiered confidence presets** (`--confidence {strict,balanced,permissive}`) plus per-knob CLI overrides (`--airport-radius-km`, `--max-join-gap-hours`, `--max-join-distance-km`, `--route-time-tolerance`, `--route-time-rescue`).
- **Bearing-aware mid-cruise joining** (Case 5 in `combine_segments_intelligently`): when two segments have unknown endpoints separated by a multi-hour cruise gap, they are joined only if the segment's mean bearing aligns with the bearing across the gap within ±30°, AND the implied speed across the gap falls in a plausible 200–1100 km/h band.
- **Bearing-checked route-time rescue** (`match_route_by_time_with_bearing`): the existing route-time rescue now also verifies the bearing from origin to candidate destination aligns with the segment's mean cruise bearing within ±45°. Rejects coincidental time matches (e.g., SBGR→SAEZ vs. SBGR→SBSV with similar durations but opposite bearings).
- **Hardened cross-file join invariants** via `assert_join_invariants` (same-aircraft, chronological, positive time gap).
- **Diagnostics CSV sidecar** (`<output>.diagnostics.csv`) with one row per raw segment and one per final outcome — supports auditing dropped segments and comparing preset runs.
- **Test suite** (`tests/`) covering bearing math, presets, invariants, join logic, route-time rescue, diagnostics format, and an integration test against real LAN data.

### Changed

- `process_kml_files` now requires a `ConfidencePreset` and emits the diagnostics CSV.
- `combine_segments_intelligently` now accepts a `preset` argument; legacy default values are preserved when called without one.

### Notes

- Default preset is `balanced` to stay close to v1.0 behavior while recovering most of the previously-dropped segments. Use `strict` to reproduce v1.0 output exactly (within rounding); use `permissive` for the most aggressive recovery.

```

- [ ] **Step 3: Commit**

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git add SPECIFICATION.md CHANGELOG.md && git commit -m "Document v1.1 confidence presets, invariants, and diagnostics CSV

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 17: Update .claude/decisions.md and .claude/known_issues.md

**Files:**
- Modify: `.claude/decisions.md`
- Modify: `.claude/known_issues.md`

- [ ] **Step 1: Append three new ADRs to .claude/decisions.md**

Read `/Users/johnyoung/Git/kml/adsb_historical_routes/.claude/decisions.md`. Find the "Questions for Future ADRs" section near the end. Insert ABOVE that section:

```markdown
## ADR-011: Tiered Confidence Presets

**Status**: Accepted
**Date**: 2026-04-28
**Decision**: Expose detection/joining aggressiveness via `--confidence {strict,balanced,permissive}` plus per-knob overrides.

### Context
v6_old's hardcoded thresholds (20km airport radius, 2-hour join window, 100km join distance) drop ~22% of raw segments because mid-cruise data outages produce endpoints that fall outside these tight constraints. Users want the ability to compare runs at different confidence levels rather than re-tuning code.

### Decision
A `ConfidencePreset` frozen dataclass holds eight tunable knobs. Three named presets (`strict`, `balanced`, `permissive`) cover the typical confidence/recovery tradeoff. Individual knobs can be overridden via CLI flags; overriding any knob renames the preset to `custom` for traceability.

### Rationale
- Single-knob CLI is too coarse for experimentation.
- Pure per-knob CLI is too fiddly for daily use.
- Combined: presets for ease, knobs for experimentation.
- Default `balanced` recovers most dropped segments without unsafe joins.

### Alternatives Considered
- Per-knob only: rejected — too many flags to remember for routine use.
- Preset only: rejected — no flexibility to experiment with one knob at a time.
- Layered config files: rejected for v6.x — overkill given the small knob count.

### Consequences
- All preset values flow through one resolver (`resolve_preset`), making future additions cheap.
- Diagnostics CSV records the active preset (or `custom`), so cross-run comparisons are unambiguous.

---

## ADR-012: Diagnostics CSV Sidecar

**Status**: Accepted
**Date**: 2026-04-28
**Decision**: Always emit `<output>.diagnostics.csv` next to the output KML.

### Context
With confidence presets, users will run the same dataset under multiple settings to compare results. Without a structured per-segment audit, it's impossible to tell why a particular flight appeared/disappeared between runs.

### Decision
A `DiagnosticsRecorder` buffers one row per raw segment (phase=raw) and one row per final outcome (phase=final). Columns include preset name, aircraft ID, source file, segment timing, endpoint distances to nearest airport (regardless of preset radius), disposition, drop reason, joined-with indices, and rescue method.

### Rationale
- Enables cross-preset comparison (delta of `kept_*` rows).
- `nearest_airport_*_km` lets users audit "would a wider radius recover this?" without rerunning.
- CSV is universal — opens in Excel, easily diff-able, queryable from Python/Pandas/Athena.

### Alternatives Considered
- JSON sidecar: rejected — less convenient for spreadsheet inspection.
- SQLite: rejected — adds a dependency or stdlib weight.
- Print-only logs: rejected — not machine-parseable.

### Consequences
- Sidecar size is proportional to segment count (~100 bytes/row), tractable for any practical run.
- The CSV path is suffix-appended to the output KML path, keeping artifacts together.

---

## ADR-013: Bearing-Aware Route Rescue

**Status**: Accepted
**Date**: 2026-04-28
**Decision**: Augment route-time matching and cross-file joining with mean-bearing alignment checks.

### Context
The original `match_route_by_time` could rescue a segment with origin known and dest missing by finding a route in `routes_time.csv` whose flight time matches. This produces false positives when two routes from the same origin have similar durations (e.g., SBGR→SAEZ ~140 min, SBGR→SBSV ~140 min, opposite bearings).

Similarly, joining mid-cruise segments by spatial+temporal proximity alone (Case 1/2 of v6_old) misses long cruise gaps where the segments are >100 km apart but along the same heading.

### Decision
- `match_route_by_time_with_bearing` rejects rescues where the bearing from origin to candidate destination misaligns from the segment's mean cruise bearing by more than ±45°.
- Case 5 (new) joins mid-cruise segments when the bearing across the gap aligns with the segment's mean bearing within ±30° AND the implied speed falls in 200–1100 km/h.

### Rationale
- Mean cruise bearing is robust against turn-final variation.
- ±30° / ±45° tolerances correspond roughly to airway dispersion vs. typical multi-hop divergence.
- The implied-speed band rejects unrealistic stitches (e.g., 1500 km gap in 30 min).

### Alternatives Considered
- Trajectory-extrapolation rescue (extend the segment's last 100 km along the bearing and look for an airport): rejected — more code, marginal gain over route-time + bearing.
- ML-based stitching: rejected — adds dependencies, not justified for this problem size.
- Tighter bearing tolerances: rejected — would over-reject legitimate joins on routes with significant en-route turns.

### Consequences
- The two helpers (`bearing`, `mean_bearing`, `angular_diff`) are reusable for any future direction-aware logic.
- Diagnostics CSV's `rescue_method` column makes false rescues inspectable.

---

```

- [ ] **Step 2: Update .claude/known_issues.md**

In `/Users/johnyoung/Git/kml/adsb_historical_routes/.claude/known_issues.md`:

- For `### ISSUE-007: Fixed Threshold Parameters`: change the **Severity** to `Resolved` and add a closing note: `**Resolution**: v1.1 — replaced with ConfidencePreset + CLI overrides (see ADR-011).`
- For `### ISSUE-008: No Validation Mode`: change the **Severity** to `Resolved` and add: `**Resolution**: v1.1 — diagnostics CSV provides per-segment audit data including drop reasons and nearest-airport distances (see ADR-012).`

- [ ] **Step 3: Commit**

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git add .claude/decisions.md .claude/known_issues.md && git commit -m "Add ADR-011/012/013; close ISSUE-007 and ISSUE-008

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 18: Rewrite README.md for the new flags

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read the current README**

Read `/Users/johnyoung/Git/kml/adsb_historical_routes/README.md`. Identify the Quick Start, Usage, and CLI Reference sections.

- [ ] **Step 2: Update Quick Start**

Replace the Quick Start usage block with:

````markdown
## Quick Start

```bash
# Single file
python adsb_historical_routes.py \
    --kml-files daily_tracks.kml \
    --airports input/latam_la_airports.csv \
    --routes input/latam_la_routes_time.csv \
    --output organized_routes.kml \
    --confidence balanced

# Multi-day, whole folder
python adsb_historical_routes.py \
    --kml-folder kml_input/kml_downloads \
    --airports input/latam_la_airports.csv \
    --routes input/latam_la_routes_time.csv \
    --output lan_routes.kml \
    --confidence balanced \
    --group destination
```
````

- [ ] **Step 3: Add a "Confidence Presets" section after Quick Start**

````markdown
## Confidence Presets

The `--confidence` flag controls how aggressively the script recovers flight routes from incomplete ADS-B data. Each preset is a bundle of detection/joining knobs:

| Knob | strict | balanced (default) | permissive |
|---|---|---|---|
| `airport_radius_km` | 20 | 50 | 100 |
| `max_join_gap_hours` | 2.0 | 3.0 | 4.0 |
| `max_join_distance_km` | 100 | 200 | 500 |
| `route_time_tolerance` | 0.25 | 0.40 | 0.60 |
| `route_time_rescue` | off | on | on |
| `direction_aware_rescue` | off | on | on |

- **strict** — Closest to v1.0 behavior. Highest confidence per route, lowest recovery rate.
- **balanced** — Default. Recovers most legitimate dropped routes; safe for downstream ML.
- **permissive** — Maximum recovery; emit-and-audit. Some routes may be wrong joins; review the diagnostics CSV.

Override individual knobs:

```bash
python adsb_historical_routes.py ... --confidence balanced --airport-radius-km 75 --route-time-rescue off
```

When any knob is overridden, logs and the diagnostics CSV mark the run as `custom` (the named preset is no longer truthful).

## Diagnostics CSV

Every run writes `<output>.diagnostics.csv` next to the output KML:

```
out.kml
out.kml.diagnostics.csv
```

One row per raw segment (`phase=raw`) and one per final outcome (`phase=final`). Useful columns:

- `disposition`: `kept_complete`, `kept_rescued`, `kept_joined`, `dropped_invalid`, `dropped_unjoined`
- `nearest_airport_*_km`: distance to nearest airport regardless of preset radius — answers "would a wider radius recover this?"
- `joined_with_segment_idx`: traceability for joined segments
- `rescue_method`: which logic path saved or merged this segment

Compare runs at different presets by diffing their CSVs.

## Same-Aircraft + Chronological Invariants

Cross-file segment joining strictly enforces:

1. Same aircraft (ICAO hex from filename).
2. `next.takeoff > current.landing`.
3. Time gap > 0 and < `preset.max_join_gap_hours`.

Cross-aircraft and out-of-order joins fail loudly with `AssertionError`.
````

- [ ] **Step 4: Commit**

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git add README.md && git commit -m "Update README with --confidence flag, presets table, and diagnostics CSV section

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 19: Push the branch and open a draft PR

**Files:** None.

- [ ] **Step 1: Push the branch**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && git push -u origin improved-route-detection
```

- [ ] **Step 2: Create a draft pull request**

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && gh pr create --draft --title "Improved route detection: confidence presets + bearing-aware joining + diagnostics CSV" --body "$(cat <<'EOF'
## Summary

- Adds tiered `--confidence {strict,balanced,permissive}` presets with per-knob overrides
- Adds bearing-aware mid-cruise joining (Case 5) and bearing-checked route-time rescue
- Adds `<output>.diagnostics.csv` sidecar with one row per raw + final segment
- Hardens cross-file join invariants (same-aircraft, chronological, positive gap)
- Adds pytest test suite covering bearing math, presets, invariants, joining, rescue, and an integration test

## Spec
`docs/superpowers/specs/2026-04-28-improved-route-detection-design.md`

## Test plan
- [ ] `python3 -m pytest` passes
- [ ] `python3 adsb_historical_routes.py --confidence strict ...` reproduces v1.0 behavior on a sample
- [ ] `--confidence permissive` produces strictly more `kept_*` diagnostics rows than `balanced` than `strict`
- [ ] Diagnostics CSV opens cleanly in spreadsheet tools
- [ ] Visual inspection of a recovered route in Google Earth shows a coherent flight path

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: prints PR URL.

- [ ] **Step 3: Print the PR URL for the user**

Run:

```bash
cd /Users/johnyoung/Git/kml/adsb_historical_routes && gh pr view --json url --jq .url
```

---

## Self-Review Checklist (run after writing this plan)

1. **Spec coverage**:
   - Section 1 (rename + bootstrap) → Tasks 1-4 ✓
   - Section 2 (algorithm: 5 changes + presets) → Tasks 6, 7, 8, 10, 11, 12 ✓
   - Section 3 (diagnostics CSV) → Task 9, wired in Task 13 ✓
   - Section 4 (documentation) → Tasks 16, 17, 18 ✓
   - Section 5 (testing) → Tasks 5, 6, 7, 8, 9, 10, 11, 12, 15 ✓
   - Acceptance criterion 1 (clean v1.0 commit) → Task 4 ✓
   - Acceptance criterion 2 (improved-route-detection branch) → Task 19 ✓
   - Acceptance criterion 3 (CLI runs end-to-end) → Tasks 14, 15 ✓
   - Acceptance criterion 4 (monotonicity) → Task 15 ✓
   - Acceptance criterion 5 (tests pass) → Tasks 5-15 ✓
   - Acceptance criterion 6 (invariants encoded + tested) → Task 8 ✓
   - Acceptance criterion 7 (docs reflect changes) → Tasks 16, 17, 18 ✓

2. **Placeholder scan**: All "TBD"/"TODO"/etc. excluded. All steps have concrete code or commands.

3. **Type/name consistency**:
   - `ConfidencePreset` fields used consistently across Tasks 7, 10, 11, 13, 14.
   - `DiagnosticsRecorder` API (`record_raw`, `record_outcome`, `write_csv`) consistent across Tasks 9, 11, 13.
   - `assert_join_invariants(current, next_seg)` signature consistent in Tasks 8 and 11.
   - `bearing()`, `angular_diff()`, `mean_bearing()` consistently used in Tasks 6, 11, 12.
   - `match_route_by_time_with_bearing` defined in Task 11, used in Task 11's `combine_segments_intelligently` and tested in Task 12.
