"""With every endpoint-evidence flag off, the v3 build equals 1.4.0 (request §6.1).

``tests/test_data/flown_v14_golden.json`` holds digests of the 1.4.0 output (tool
1.4.0, commit fa49139) for the synthetic fixtures: every v2 column and geometry of
``flights`` / ``tracks_full`` / ``track_points``, the v2 diagnostics columns, the meta
values that do not depend on the run, and the KML text. Regenerate it only from a 1.4.0
checkout:

    python3 tests/test_v3_equivalence.py --write-golden
"""

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "adsb_historical_routes.py"
GOLDEN = Path(__file__).resolve().parent / "test_data" / "flown_v14_golden.json"

# The flown_routes v2 columns written by 1.4.0, in order.
V2_FLIGHTS = [
    "flight_id", "airline", "aircraft_id", "registration", "callsign", "origin", "dest",
    "city_pair", "group_key", "takeoff_time", "landing_time", "flight_date", "duration_min",
    "distance_km", "max_alt_ft", "n_points_full", "n_points_display", "has_gaps",
    "gap_duration_min", "disposition", "rescue_method", "joined_segment_count", "sources",
    "source_file", "preset", "style_color", "style_width", "style_opacity", "od_gc_km",
    "od_distance_ratio", "suspect_od", "geom",
]
V2_TRACKS_FULL = ["flight_id", "city_pair", "group_key", "origin", "dest", "flight_date", "geom"]
V2_TRACK_POINTS = ["flight_id", "city_pair", "group_key", "origin", "dest", "seq", "t",
                   "alt_ft", "gs_kt", "vs_fpm", "track_deg", "source", "geom"]
V2_DIAGNOSTICS = [
    "phase", "preset", "aircraft_id", "registration", "source_file", "segment_idx",
    "takeoff_time", "landing_time", "duration_min", "num_points", "max_alt_m",
    "total_distance_km", "mean_bearing_deg", "origin_code", "dest_code", "origin_dist_km",
    "dest_dist_km", "nearest_airport_start_code", "nearest_airport_start_km",
    "nearest_airport_end_code", "nearest_airport_end_km", "disposition", "drop_reason",
    "joined_with_segment_idx", "rescue_method", "flight_id", "od_gc_km", "od_distance_ratio",
    "suspect_od",
]
# Meta keys that change from run to run, or by design in v3.
META_SKIP = {"created_utc", "argv", "schema_version", "tool_version"}


def _cases(flown, evidence):
    base = ["--confidence", "permissive", "--airline", "TST"]
    flown_in = ["--kml-folder", str(flown["kml_dir"]), "--airports", str(flown["airports"]),
                "--routes", str(flown["routes"])]
    ev_in = ["--kml-folder", str(evidence["kml_dir"]), "--airports", str(evidence["airports"]),
             "--routes", str(evidence["routes"])]
    pos = ["--integrate-pos", "true", "--pos-folder", str(evidence["pos"]),
           "--tail-icao", str(evidence["tails"])]
    return {
        "flown_default": flown_in + base,
        "flown_firehose_stitch": flown_in + base + [
            "--integrate-pos", "true", "--firehose-folder", str(flown["firehose"]),
            "--stitch-gaps", "true"],
        "evidence_pos": ev_in + base + pos + ["--stitch-gaps", "true"],
    }


def _digest(conn, table, cols):
    h = hashlib.sha256()
    n = 0
    for row in conn.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY fid"):
        h.update(repr(tuple(bytes(v).hex() if isinstance(v, (bytes, memoryview)) else v
                            for v in row)).encode())
        n += 1
    return {"rows": n, "sha256": h.hexdigest()}


def build_digests(case_args, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    kml = out_dir / "routes.kml"
    res = subprocess.run([sys.executable, str(SCRIPT), *case_args, "--output", str(kml)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         universal_newlines=True, timeout=300)
    assert res.returncode == 0, res.stdout[-3000:] + res.stderr[-3000:]
    conn = sqlite3.connect(str(out_dir / "routes.gpkg"))
    meta = {k: v for k, v in conn.execute("SELECT key, value FROM meta")
            if k not in META_SKIP}
    has_points = conn.execute("SELECT 1 FROM gpkg_contents WHERE table_name = "
                              "'track_points'").fetchone() is not None
    out = {
        "flights": _digest(conn, "flights", V2_FLIGHTS),
        "tracks_full": _digest(conn, "tracks_full", V2_TRACKS_FULL),
        "track_points": _digest(conn, "track_points", V2_TRACK_POINTS) if has_points else None,
        "diagnostics": _digest(conn, "diagnostics", V2_DIAGNOSTICS),
        "meta": meta,
        "kml_sha256": hashlib.sha256(kml.read_bytes()).hexdigest(),
    }
    conn.close()
    return out


@pytest.mark.parametrize("case", ["flown_default", "flown_firehose_stitch", "evidence_pos"])
def test_flags_off_equals_1_4_0(flown_fixture, evidence_fixture, tmp_path, case):
    golden = json.loads(GOLDEN.read_text())[case]
    got = build_digests(_cases(flown_fixture, evidence_fixture)[case], tmp_path / case)
    for key in ("flights", "tracks_full", "track_points", "diagnostics", "kml_sha256"):
        assert got[key] == golden[key], key
    # every 1.4.0 meta value is unchanged (v3 only adds keys)
    assert {k: got["meta"].get(k) for k in golden["meta"]} == golden["meta"]


if __name__ == "__main__" and "--write-golden" in sys.argv:
    import tempfile
    sys.path.insert(0, str(PROJECT_ROOT))
    from tests import conftest

    class _Factory:
        def __init__(self, base):
            self.base = Path(base)

        def mktemp(self, name):
            p = self.base / name
            p.mkdir()
            return p

    with tempfile.TemporaryDirectory() as tmp:
        f = _Factory(tmp)
        flown = conftest.flown_fixture.__wrapped__(f)
        evidence = conftest.evidence_fixture.__wrapped__(f)
        result = {name: build_digests(args, Path(tmp) / "out" / name)
                  for name, args in _cases(flown, evidence).items()}
    GOLDEN.parent.mkdir(exist_ok=True)
    GOLDEN.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(f"wrote {GOLDEN}")
