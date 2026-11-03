"""The endpoint-evidence flags end to end on the synthetic ``evidence_fixture`` (CLI)."""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "adsb_historical_routes.py"
EXPORTER = PROJECT_ROOT / "gpkg_to_kml.py"
ALL_FLAGS = ["--acars-identity", "true", "--pos-extend-ends", "true",
             "--pos-only-flights", "true", "--pos-fleets", "all",
             "--rotation-endpoints", "true", "--recover-dropped", "true"]


def _run(fx, out, *extra, check=True):
    cmd = [sys.executable, str(SCRIPT), "--kml-folder", str(fx["kml_dir"]),
           "--airports", str(fx["airports"]), "--routes", str(fx["routes"]),
           "--confidence", "permissive", "--airline", "TST",
           "--integrate-pos", "true", "--pos-folder", str(fx["pos"]),
           "--tail-icao", str(fx["tails"]), "--format", "gpkg", "--output", str(out), *extra]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         universal_newlines=True, timeout=300)
    if check:
        assert res.returncode == 0, res.stdout[-3000:] + res.stderr[-3000:]
    return res


@pytest.fixture(scope="module")
def built(evidence_fixture, tmp_path_factory):
    d = tmp_path_factory.mktemp("ev")
    gpkg = d / "ev.gpkg"
    res = _run(evidence_fixture, gpkg, *ALL_FLAGS, "--planned-legs", str(evidence_fixture["legs"]))
    conn = sqlite3.connect(str(gpkg))
    conn.row_factory = sqlite3.Row
    rows = {r["flight_id"]: dict(r) for r in conn.execute("SELECT * FROM flights")}
    return {"dir": d, "gpkg": gpkg, "stdout": res.stdout, "conn": conn, "rows": rows}


def test_truncated_flight_reaches_its_real_destination(built):
    f = built["rows"]["ABC101_20261001T100000Z"]
    assert (f["origin"], f["dest"], f["orig_source"], f["dest_source"]) == \
        ("AAA", "DDD", "observed", "pos_observed")
    assert (f["flight_number"], f["flight_number_source"]) == ("JJ1234", "acars_fi")
    assert f["planned_leg_key"] == "2026-10-01_TAM1234_AAA-DDD_b0"
    assert f["sources"] == "ADSB,POS" and f["n_pos_fixes"] == 7
    assert f["rescue_method"] == "route_time_forward"       # 1.4.0's meaning kept
    assert f["endpoint_confidence"] == "medium" and f["endpoint_conflict"] == 0


def test_pos_only_flights(built):
    rows = built["rows"]
    mapped = rows["ABC101_20261001T170000Z"]
    assert (mapped["origin"], mapped["dest"], mapped["disposition"], mapped["sources"]) == \
        ("AAA", "CCC", "pos_only", "POS")
    assert mapped["aircraft_id"] == "ABC101" and mapped["flight_number"] == "JJ1236"
    cc = rows["CCBMD_20261001T110000Z"]
    assert cc["aircraft_id"] is None and cc["registration"] == "CC-BMD"
    assert (cc["origin"], cc["dest"], cc["flight_number"]) == ("AAA", "BBB", "LA800")
    assert cc["planned_leg_key"] == "2026-10-01_LAN800_AAA-BBB_b0"


def test_meta_and_report(built):
    meta = dict(built["conn"].execute("SELECT key, value FROM meta"))
    assert meta["schema_version"] == "3"
    opts = json.loads(meta["evidence_options"])
    assert opts["pos_fleets"] == "all" and opts["planned_legs"] == ["legs.csv"]
    assert json.loads(meta["planned_legs"])[0]["rows"] == 4
    assert json.loads(meta["flight_number_prefixes"]) == {"JJ": {"TAM": 3}, "LA": {"LAN": 1}}
    report = (built["dir"] / "ev_evidence_report.md").read_text()
    assert "## Agreement with planned legs" in report
    assert "| CC | 1 |" in report
    ev = [tuple(r) for r in built["conn"].execute(
        "SELECT evidence_feature, endpoint, before_code, after_code "
        "FROM diagnostics WHERE phase = 'evidence'")]
    assert ("F2", "dest", "BBB", "DDD") in ev


def test_kml_export_of_v3_with_pos_only_flights(built, tmp_path):
    kml = tmp_path / "ev.kml"
    res = subprocess.run([sys.executable, str(EXPORTER), str(built["gpkg"]), "-o", str(kml)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         universal_newlines=True, timeout=300)
    assert res.returncode == 0, res.stderr
    text = kml.read_text()
    assert "CC-BMD" in text and "None-" not in text


def test_identity_needs_pos(evidence_fixture, tmp_path):
    cmd = [sys.executable, str(SCRIPT), "--kml-folder", str(evidence_fixture["kml_dir"]),
           "--airports", str(evidence_fixture["airports"]),
           "--routes", str(evidence_fixture["routes"]), "--acars-identity", "true",
           "--output", str(tmp_path / "x.gpkg")]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         universal_newlines=True, timeout=60)
    assert res.returncode == 1 and "--integrate-pos true" in res.stdout


def test_several_airport_files(evidence_fixture, tmp_path):
    extra = tmp_path / "extra.csv"
    extra.write_text("airport,latitude,longitude,elevation_ft\nEEE,9.0,9.0,10\n")
    res = _run(evidence_fixture, tmp_path / "a.gpkg", "--airports",
               str(evidence_fixture["airports"]), str(extra))
    assert "Loaded 5 airports" in res.stdout or "5 airports" in res.stdout


def test_report_counts_the_contradicted_1_4_0_guess(built):
    report = json.loads(dict(built["conn"].execute("SELECT key, value FROM meta"))[
        "evidence_report"])
    # 1.4.0 said AAA->BBB by route time; the plan (and now POS) say DDD
    assert report["contradicted"] == {"total": 1, "free_now_agrees": 1}
    assert report["agreement_1_4_0"]["dest"]["route_time"] == [0, 1]
    assert report["agreement"]["dest"]["pos_observed"] == [3, 0]


def test_gpkg_summary_v3_sections(built):
    sys.path.insert(0, str(PROJECT_ROOT))
    import gpkg_summary
    text = gpkg_summary.summarize(str(built["gpkg"]))
    assert "Endpoints by source (origin / dest):" in text
    assert "  pos_observed" in text
    assert "Endpoint confidence:" in text
    assert "Flights with a flight number: 4 (100.0%)" in text
    assert "POS-only flights by registration prefix:" in text and "  CC " in text
    assert "Planned-leg agreement (rule 1, plan-free answer):" in text
