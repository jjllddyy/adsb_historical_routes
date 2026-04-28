# Improved Route Detection — Design

**Project**: adsb_historical_routes (formerly Flight Route Splitter)
**Branch**: `improved-route-detection`
**Author**: John Young (jjllddyy)
**Date**: 2026-04-28
**Status**: Approved (pending spec review)

## Problem

ADS-B Exchange exports flight tracks as one KML file per (ICAO hex, date). Each file contains one or more `<Placemark>` tracks for that aircraft on that day. The current production script (`prior_versions/flight_route_splitter_v6_old.py`, the algorithm that generated `output/lan_routes_2026-feb-mar.kml`) detects flight segments by time-gap and matches origin/destination by airport proximity, then attempts to combine incomplete segments across files for the same aircraft.

In practice, ~22% of raw segments per aircraft are dropped because either (a) endpoints fall outside the 20 km airport-match radius (e.g., flight ended in cruise when the data file cut off), or (b) the `combine_segments_intelligently` join logic rejects valid cross-file continuations because of overly tight 100 km / 2-hour join constraints.

Empirical analysis on aircraft `E47FA6` over 14 days:

| Metric | Count |
|---|---|
| Raw segments detected | 94 |
| Complete (origin + dest) on first pass | 43 (46%) |
| Origin only | 24 (26%) |
| Destination only | 22 (23%) |
| Neither | 5 (5%) |
| Final valid flights output | 73 (78%) |
| **Dropped** | **21 (22%)** |

Endpoint distance to nearest airport in the regional 164-airport DB (incomplete endpoints, n=56):

| Radius | Recoverable |
|---|---|
| ≤ 20 km (current) | 16% |
| ≤ 50 km | 38% |
| ≤ 100 km | 54% |
| ≤ 200 km | 80% |
| ≤ 500 km | 98% |

~62% of incomplete endpoints are genuinely mid-cruise (>50 km from any airport). These cannot be recovered by relaxing the airport radius alone — they require correctly joining the segment with its continuation in the next file for the same aircraft. The current join logic is too restrictive for these cases.

## Goals

1. Recover the bulk of the dropped 22% by relaxing detection knobs in a controlled, tunable way.
2. Preserve the strict same-aircraft + chronological invariants — no joining across aircraft, no joining non-adjacent flights for the same aircraft.
3. Make confidence levels selectable at runtime so multiple confidence-tier outputs can be compared without re-coding.
4. Produce a diagnostics sidecar so the user can audit which segments were dropped, joined, or rescued, and why.
5. Bring the project to a clean v1 GitHub state with a renamed canonical script.

## Non-Goals

- Spatial indexing for airport lookups (still O(n); known issue, deferred).
- GUI, streaming processing, alternative export formats.
- Refactoring the v6_old codebase beyond what the new logic requires.
- Removing or rewriting unchanged behavior of `v6_old`.

## Architecture

### Repository state after this change

```
adsb_historical_routes/
├── adsb_historical_routes.py     (renamed from v6_old, canonical script)
├── prior_versions/
│   ├── flight_route_splitter_v6.py        (the simplified v6 — moved here)
│   ├── flight_route_splitter_v6_old.py    (kept as historical reference)
│   ├── flight_route_splitter_v5.py
│   ├── flight_route_splitter_v3.py
│   ├── flight_route_splitter_v2.py
│   └── flight_route_splitter_v1.py
├── input/                        (CSVs — kept in repo)
├── kml_input/                    (symlink — gitignored)
├── output/                       (gitignored)
├── examples/
├── tests/
│   ├── test_invariants.py
│   ├── test_join_logic.py
│   └── test_route_time_rescue.py
├── docs/superpowers/specs/2026-04-28-improved-route-detection-design.md
├── README.md
├── SPECIFICATION.md
├── CHANGELOG.md
├── .claude/
│   ├── CLAUDE.md
│   ├── decisions.md         (adds ADR-011, ADR-012)
│   ├── known_issues.md      (updates)
│   └── ...
└── .gitignore
```

### Module structure of `adsb_historical_routes.py`

Existing v6_old structure preserved. New additions:

- `ConfidencePreset` dataclass with the tunable knobs (airport_radius_km, max_join_gap_hours, max_join_distance_km, route_time_tolerance, route_time_rescue, direction_aware_rescue).
- `PRESETS: Dict[str, ConfidencePreset]` with `strict`, `balanced`, `permissive`.
- `resolve_preset(args) -> ConfidencePreset` — apply CLI overrides on top of the chosen preset.
- `bearing(p1, p2) -> float` — initial bearing in degrees from p1 to p2.
- `mean_bearing(points: List[TrackPoint], window_minutes: float) -> float` — mean bearing over the last `window_minutes` of a segment.
- `route_time_rescue_with_bearing(...)` — replaces inlined logic in `combine_segments_intelligently`; adds bearing check.
- `DiagnosticsRecorder` class that buffers per-segment rows and writes the sidecar CSV.
- `assert_join_invariants(current, next_seg)` — raises on cross-aircraft, non-chronological, or negative-gap attempts.

### Data flow

```
KML files (per hex × date)
  → parse_kml_tracks (per file)
    → useful-altitude tracks
      → detect_flight_segments (time-gap split)
        → raw FlightSegments
          → DiagnosticsRecorder.record_raw(...)
          → group by aircraft_id
            → combine_segments_intelligently (uses ConfidencePreset)
              ├─ assert_join_invariants
              ├─ route_time_rescue_with_bearing
              └─ DiagnosticsRecorder.record_outcome(...)
              → final FlightSegments
                → create_output_kml (group by destination|origin)
                → DiagnosticsRecorder.write_csv(<output>.diagnostics.csv)
```

## Detailed Design

### Section 1 — Project rename and repo bootstrap

- Rename `flight_route_splitter_v6.py` (the simplified version, currently at repo root) to `prior_versions/flight_route_splitter_v6_simple.py` to avoid a name clash with the file already in `prior_versions/`.
- Promote `prior_versions/flight_route_splitter_v6_old.py` to `adsb_historical_routes.py` at the repo root. The old path is removed once promoted (the file is preserved by the new path; git history will show the move).
- Replace `adsb_routes` → `adsb_historical_routes` in: `README.md`, `CHANGELOG.md`, `SPECIFICATION.md`, `.claude/*.md`, `examples/`, any `*.py` strings.
- Initialize git in the project root, add `.gitignore`, make initial commit on `main`, push to `https://github.com/jjllddyy/adsb_historical_routes` (public).
- Tag `v1.0` on the initial commit.

`.gitignore`:

```
output/
kml_input/
*.tar.gz
.DS_Store
__pycache__/
*.pyc
*.diagnostics.csv
```

### Section 2 — Algorithm improvements

**Confidence presets** (immutable dataclass):

| Knob | strict | balanced | permissive |
|---|---|---|---|
| `airport_radius_km` | 20 | 50 | 100 |
| `max_join_gap_hours` | 2.0 | 3.0 | 4.0 |
| `max_join_distance_km` | 100 | 200 | 500 |
| `route_time_tolerance` | 0.25 | 0.40 | 0.60 |
| `route_time_rescue` | False | True | True |
| `direction_aware_rescue` | False | True | True |
| `min_segment_points` | 20 | 20 | 20 |
| `min_flight_altitude_m` | 300 | 300 | 300 |

Default is `balanced`. CLI: `--confidence {strict,balanced,permissive}` plus per-knob overrides (`--airport-radius-km`, `--max-join-gap-hours`, `--max-join-distance-km`, `--route-time-tolerance`, `--route-time-rescue {on,off}`).

**Hardened invariants** in `combine_segments_intelligently`. Before each join attempt:

```python
def assert_join_invariants(current: FlightSegment, next_seg: FlightSegment) -> None:
    assert current.aircraft_id == next_seg.aircraft_id, \
        f"Cross-aircraft join blocked: {current.aircraft_id} vs {next_seg.aircraft_id}"
    assert next_seg.takeoff_time > current.landing_time, \
        f"Non-chronological join blocked: {next_seg.takeoff_time} not after {current.landing_time}"
    gap = (next_seg.takeoff_time - current.landing_time).total_seconds()
    assert gap > 0, f"Zero/negative gap in join: {gap}s"
```

These invariants cannot fail under correct input ordering; the assertions exist to fail loudly if a future refactor breaks the partition-then-sort upstream contract.

**Bearing-aware joining and rescue.** A new `bearing(p1, p2)` and `mean_bearing(points, window_minutes=10)` are used in two places:

1. **Direction-aware mid-cruise join (Case 5, new):** When the current segment lacks a destination and the next segment lacks an origin AND they are close in time but not space (e.g., 200 km apart), join only if the bearing from `current[-10min:]` aligns with the bearing from `current[-1]` toward `next_seg[0]` within ±30°. This catches cases where the data drops out for tens of minutes during cruise (segment ends NW-bound, gap, next segment continues NW-bound from a point 200 km NW of the previous endpoint).

2. **Route-time rescue safeguard:** `match_route_by_time` is wrapped: when a candidate destination is found from `routes_time.csv`, the bearing from the segment's origin to the candidate destination must align within ±45° of the segment's mean cruise bearing. This rejects coincidental time matches (e.g., SBGR→SAEZ and SBGR→SCEL have similar durations but opposite bearings).

**Updated `should_join` cases** in `combine_segments_intelligently`:

- Case 1 (existing) — both endpoints incomplete and within `max_join_distance_km` of each other.
- Case 2 (existing, retuned) — both at altitude > 3000 m, gap < 0.5 h, distance ≤ `max_join_distance_km`.
- Case 3 (existing) — `current.dest == next.origin` → DON'T JOIN (these are sequential flights through the same airport).
- Case 4 (existing) — both already complete → DON'T JOIN.
- **Case 5 (new) — direction-aware:** both endpoints incomplete, gap ≤ `max_join_gap_hours`, bearing alignment within ±30°. Distance can exceed `max_join_distance_km` if bearing aligns AND elapsed time × cruise speed (~800 km/h) approximates the distance. This catches multi-hour cruise data outages.

### Section 3 — Diagnostics CSV

`DiagnosticsRecorder` writes `<output_path>.diagnostics.csv`. Schema:

| Column | Type | Notes |
|---|---|---|
| `phase` | str | `raw` or `final` |
| `preset` | str | `strict`/`balanced`/`permissive`/`custom` |
| `aircraft_id` | str | ICAO hex (uppercase) |
| `registration` | str | Tail number from KML |
| `source_file` | str | KML basename (raw rows; final rows list comma-separated sources) |
| `segment_idx` | int | Per-aircraft index in raw order |
| `takeoff_time` | ISO 8601 | UTC |
| `landing_time` | ISO 8601 | UTC |
| `duration_min` | float | |
| `num_points` | int | |
| `max_alt_m` | float | |
| `total_distance_km` | float | |
| `mean_bearing_deg` | float | Last 10 min, or full track if shorter |
| `origin_code` | str | `""` if no match |
| `dest_code` | str | `""` if no match |
| `origin_dist_km` | float | Distance from first point to assigned origin |
| `dest_dist_km` | float | Distance from last point to assigned dest |
| `nearest_airport_start_code` | str | Within 1000 km, regardless of preset radius |
| `nearest_airport_start_km` | float | |
| `nearest_airport_end_code` | str | |
| `nearest_airport_end_km` | float | |
| `disposition` | str | `kept_complete`, `kept_rescued`, `kept_joined`, `dropped_invalid`, `dropped_unjoined` (final phase only; raw phase = `""`) |
| `drop_reason` | str | `same_airport`, `too_short`, `low_altitude`, `no_airport_match`, `unjoinable` |
| `joined_with_segment_idx` | str | Comma-separated list of raw segment_idx values that were merged |
| `rescue_method` | str | `route_time_forward`, `route_time_reverse`, `bearing_join`, `none` |

CSV is always written next to the output KML, regardless of preset. Header row included. Empty values are empty strings, not `null`.

### Section 4 — Documentation updates

- **README.md** — rewrite Quick Start with the new script name + `--confidence` flag. Keep the existing v6_old style-override docs (color, width, opacity, labels, icons, ground). Add a "Diagnostics CSV" subsection.
- **SPECIFICATION.md** — add a "Cross-file joining invariants" section listing the four rules. Add the confidence-preset table.
- **CHANGELOG.md** — add two entries:
  - `v1.0` (initial commit on `main`): rename to `adsb_historical_routes.py`, promote `v6_old` algorithm as canonical, repo bootstrap, tag.
  - `v1.1` (on `improved-route-detection` branch, unreleased until merged): tiered confidence presets, bearing-aware joining, route-time rescue with bearing safeguard, diagnostics CSV, hardened invariants.
- **.claude/decisions.md** — ADR-011 "Tiered Confidence Presets", ADR-012 "Diagnostics CSV Sidecar", ADR-013 "Bearing-Aware Route Rescue".
- **.claude/known_issues.md** — mark ISSUE-007 (fixed thresholds) and ISSUE-008 (no validation mode) as resolved by this branch. Close the open question about parallelization (out of scope, still deferred).

### Section 5 — Testing

`tests/conftest.py` — synthetic segment factory.

`tests/test_invariants.py`:
- `test_cross_aircraft_join_blocked` — two segments with different `aircraft_id` → `AssertionError`.
- `test_non_chronological_join_blocked` — `next.takeoff < current.landing` → `AssertionError`.
- `test_zero_gap_join_blocked` — `next.takeoff == current.landing` → `AssertionError`.

`tests/test_join_logic.py`:
- `test_midnight_crossing_joins` — segment ending 23:55Z + segment starting 00:05Z next day → joined.
- `test_long_overnight_gap_rejected` — 8-hour gap → not joined regardless of preset.
- `test_strict_preset_rejects_200km_gap` — 200 km mid-cruise gap → joined under `permissive`, rejected under `strict`.
- `test_dest_eq_next_origin_not_joined` — dest=SBGR, next.origin=SBGR → not joined (Case 3).

`tests/test_route_time_rescue.py`:
- `test_bearing_match_rescues` — origin SBGR, duration matches SBGR→SAEZ, mean bearing ~210° → rescued.
- `test_bearing_mismatch_rejects` — origin SBGR, duration matches SBGR→SAEZ but mean bearing ~30° (north) → not rescued.
- `test_disabled_under_strict` — same scenario as first, but `route_time_rescue=False` → not rescued.

Integration test (small): pick 1 hex × 3 substantial days from `kml_input/kml_downloads/`, run with each preset, assert the diagnostics CSV row counts are monotonic in `kept_*` rows (`strict ≤ balanced ≤ permissive`) and that no row violates the invariants.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Bearing-aware joins introduce false positives near airports where multiple routes share bearings | Bearing check is bounded to ±30° (joins) / ±45° (rescue). Diagnostics CSV makes false joins inspectable. |
| `permissive` preset produces visibly wrong routes | `--confidence` is a runtime knob, not a default. Default remains `balanced`. |
| Renaming breaks existing user workflows referencing `flight_route_splitter_v6.py` | The file remains accessible at `prior_versions/flight_route_splitter_v6_simple.py`; v6_old is preserved at `prior_versions/flight_route_splitter_v6_old.py`. README documents the rename. |
| Cross-file joining merges flights that were genuinely separate | Time-gap ceiling + same-aircraft + chronology assertions + bearing check together make this very unlikely under `strict`/`balanced`. |
| Diagnostics CSV bloats output for large runs | Acceptable: typical run is 100s of segments per aircraft; CSV is 1-10 MB even for full LAN runs. |

## Acceptance Criteria

1. `main` branch on the new GitHub repo shows a single clean initial commit (v1.0 tag) by jjllddyy with the renamed `adsb_historical_routes.py` and updated documentation.
2. `improved-route-detection` branch is pushed to GitHub with the algorithm changes, diagnostics CSV, tests, and CHANGELOG v1.1 entry.
3. CLI: `python adsb_historical_routes.py --confidence balanced --kml-folder ... --airports ... --routes ... --output ...` runs end-to-end and produces a KML output plus the `<output>.diagnostics.csv` sidecar.
4. Same command with `--confidence permissive` produces ≥ as many `kept_*` rows in the diagnostics CSV as `balanced`, which produces ≥ `strict` (monotonicity).
5. All tests in `tests/` pass under `pytest`.
6. The four cross-file joining invariants (same-aircraft, chronological, positive gap, time-gap ceiling) are encoded as runtime assertions and covered by tests.
7. Documentation in README, SPECIFICATION, CHANGELOG, and `.claude/decisions.md` reflects the new flags, presets, invariants, and diagnostics format.
