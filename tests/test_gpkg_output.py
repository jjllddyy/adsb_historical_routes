"""GeoPackage output (flown_routes v1) and the KML-from-GeoPackage equivalence (spec §6).

Runs the real CLI on the synthetic ``flown_fixture`` (4 flights, one joined from two
segments, one with an interior coverage gap, firehose fixes) in every mode the spec
names, and checks that KML written directly and KML rendered from the GeoPackage match.
"""

import csv
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import kml_compare

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "adsb_historical_routes.py"
EXPORTER = PROJECT_ROOT / "gpkg_to_kml.py"

MODES = {
    "default": [],
    "altitude": ["--color-mode", "altitude"],
    "stitch": ["--stitch-gaps", "true"],
    "integrate_pos": ["--integrate-pos", "true"],
    "group_origin": ["--group", "origin"],
    "pos_stitch_labels": ["--integrate-pos", "true", "--stitch-gaps", "true",
                          "--labels", "true", "--width", "3", "--opacity", "50"],
}


def run(fx, output, *extra):
    cmd = [sys.executable, str(SCRIPT), "--kml-folder", str(fx["kml_dir"]),
           "--airports", str(fx["airports"]), "--routes", str(fx["routes"]),
           "--confidence", "permissive", "--airline", "TST", "--output", str(output), *extra]
    if "--integrate-pos" in extra:
        cmd += ["--firehose-folder", str(fx["firehose"])]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stdout[-3000:] + res.stderr[-3000:]
    return res.stdout


def placemarks(kml_path):
    return Path(kml_path).read_text().count("<Placemark")


@pytest.mark.parametrize("mode", sorted(MODES))
def test_kml_via_gpkg_equivalent_to_direct(flown_fixture, tmp_path, mode):
    (tmp_path / "direct").mkdir()
    (tmp_path / "via").mkdir()
    direct = tmp_path / "direct" / "routes.kml"
    via = tmp_path / "via" / "routes.kml"
    run(flown_fixture, direct, "--format", "kml", "--legacy-kml", *MODES[mode])
    run(flown_fixture, via, "--format", "both", *MODES[mode])
    diffs, stats = kml_compare.compare_kml(str(direct), str(via))
    assert diffs == [], diffs
    assert stats["placemarks"] >= 4
    # one flights row per flight, however many placemarks the mode draws
    conn = sqlite3.connect(str(tmp_path / "via" / "routes.gpkg"))
    assert conn.execute("SELECT count(*) FROM flights").fetchone()[0] == 4


def test_placemarks_are_not_flights(flown_fixture, tmp_path):
    out = tmp_path / "r.kml"
    run(flown_fixture, out, "--integrate-pos", "true", "--stitch-gaps", "true")
    conn = sqlite3.connect(str(tmp_path / "r.gpkg"))
    assert placemarks(out) > 4
    assert conn.execute("SELECT count(*) FROM flights").fetchone()[0] == 4
    assert conn.execute("SELECT count(*) FROM tracks_full").fetchone()[0] == 4
    # sources live on the points, and in flights.sources
    srcs = dict(conn.execute("SELECT aircraft_id, sources FROM flights WHERE "
                             "aircraft_id = 'ABC002'"))
    assert srcs["ABC002"] == "ADSB,FIREHOSE"
    n_fh = conn.execute("SELECT count(*) FROM track_points WHERE source = 'FIREHOSE'"
                        ).fetchone()[0]
    assert n_fh > 0


class TestFlownSchema:
    @pytest.fixture(scope="class")
    def built(self, flown_fixture, tmp_path_factory):
        d = tmp_path_factory.mktemp("built")
        stdout = run(flown_fixture, d / "tst_flown_{from}_{to}.kml", "--gpkg-points")
        gpkg = d / "tst_flown_2026-10-01_2026-10-02.gpkg"
        return {"dir": d, "gpkg": gpkg, "stdout": stdout,
                "conn": sqlite3.connect(str(gpkg))}

    def test_outputs_named_from_flight_dates(self, built):
        names = sorted(p.name for p in built["dir"].iterdir())
        assert names == ["tst_flown_2026-10-01_2026-10-02.gpkg",
                         "tst_flown_2026-10-01_2026-10-02.kml",
                         "tst_flown_2026-10-01_2026-10-02.kml.diagnostics.csv"]

    def test_meta(self, built):
        meta = dict(built["conn"].execute("SELECT key, value FROM meta"))
        assert meta["schema_name"] == "flown_routes" and meta["schema_version"] == "3"
        assert meta["tool"] == "adsb_historical_routes" and meta["airline"] == "TST"
        assert (meta["date_from"], meta["date_to"]) == ("2026-10-01", "2026-10-02")
        assert meta["source_count"] == "3"
        assert "--gpkg-points" in json.loads(meta["argv"])
        assert json.loads(meta["kml_params"])["sample_minutes"] == 2.0
        assert meta["created_utc"].endswith("Z")

    def test_flights_rows(self, built):
        c = built["conn"]
        rows = c.execute("SELECT flight_id, airline, origin, dest, city_pair, group_key, "
                         "flight_date, n_points_display < n_points_full, callsign, "
                         "disposition, joined_segment_count, rescue_method, "
                         "style_color, style_width FROM flights ORDER BY takeoff_time").fetchall()
        assert [r[0] for r in rows] == ["ABC001_20261001T100000Z", "ABC002_20261001T100700Z",
                                        "ABC001_20261001T120000Z", "ABC001_20261002T093000Z"]
        joined = rows[1]
        assert joined[2:7] == ("AAA", "CCC", "AAA-CCC", "CCC", "2026-10-01")
        assert joined[9:12] == ("kept_rescued", 2, "bearing_join")
        assert rows[0][9:12] == ("kept_complete", 1, None)
        assert all(r[1] == "TST" and r[7] == 1 and r[8] is None for r in rows)
        assert rows[0][12:] == ("ff1c1ae3", 4.0)
        t = c.execute("SELECT takeoff_time FROM flights WHERE fid = 1").fetchone()[0]
        assert t == "2026-10-01T10:00:00.250Z"

    def test_gaps_measured(self, built):
        gaps = dict(built["conn"].execute(
            "SELECT flight_id, gap_duration_min FROM flights WHERE has_gaps = 1"))
        assert set(gaps) == {"ABC001_20261001T120000Z", "ABC002_20261001T100700Z"}
        assert gaps["ABC001_20261001T120000Z"] == pytest.approx(8.0)   # minutes 30 -> 38

    def test_od_check(self, built):
        for gc, ratio, suspect in built["conn"].execute(
                "SELECT od_gc_km, od_distance_ratio, suspect_od FROM flights"):
            assert gc > 400 and ratio == pytest.approx(1.0, abs=0.01) and suspect == 0

    def test_tracks_full_keeps_every_point_with_time(self, built, ahr):
        c = built["conn"]
        blob, n = c.execute("SELECT t.geom, f.n_points_full FROM tracks_full t JOIN flights f "
                            "USING (flight_id) WHERE flight_id = 'ABC001_20261001T100000Z'"
                            ).fetchone()
        kind, has_z, has_m, parts = ahr.gpkg_writer.decode_geometry(blob)
        assert (kind, has_z, has_m, len(parts)) == ("MULTILINESTRING", True, True, 1)
        verts = parts[0]
        assert len(verts) == n
        first = datetime(2026, 10, 1, 10, 0, 0, 250000, tzinfo=timezone.utc).timestamp()
        assert verts[0][3] == pytest.approx(first, abs=1e-6)
        assert verts[1][3] - verts[0][3] == pytest.approx(20.0, abs=1e-6)

    def test_track_points_motion(self, built):
        rows = built["conn"].execute(
            "SELECT seq, t, gs_kt, vs_fpm, track_deg, source FROM track_points "
            "WHERE flight_id = 'ABC001_20261001T100000Z' ORDER BY seq LIMIT 3").fetchall()
        assert rows[0][0] == 0 and rows[0][2:5] == (None, None, None)
        assert rows[1][1] == "2026-10-01T10:00:20.250Z"
        # 5 deg of longitude on the equator in 60 min = ~300 kt, heading east, climbing
        assert rows[1][2] == pytest.approx(300.2, abs=1.0)
        assert rows[1][4] == pytest.approx(90.0, abs=0.01)
        assert rows[1][3] > 0 and rows[1][5] == "ADSB"

    def test_diagnostics_linked(self, built):
        c = built["conn"]
        n_csv = sum(1 for _ in csv.DictReader(open(
            built["dir"] / "tst_flown_2026-10-01_2026-10-02.kml.diagnostics.csv")))
        assert c.execute("SELECT count(*) FROM diagnostics").fetchone()[0] == n_csv
        # flights = distinct flight ids in diagnostics (spec §8)
        assert c.execute("SELECT count(DISTINCT flight_id) FROM diagnostics").fetchone()[0] == 4
        # the absorbed fragment of the joined flight carries the flight's id
        linked = c.execute("SELECT segment_idx, suspect_od FROM diagnostics WHERE phase='final' "
                           "AND flight_id = 'ABC002_20261001T100700Z'").fetchall()
        assert len(linked) == 2
        assert sorted(s is None for _, s in linked) == [False, True]

    def test_indexes(self, built):
        idx = {r[0] for r in built["conn"].execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%'")}
        assert {"idx_flights_city_pair", "idx_flights_flight_id", "idx_tracks_full_flight_id",
                "idx_track_points_flight_id_seq", "idx_track_points_city_pair"} <= idx


def test_format_gpkg_writes_no_kml(flown_fixture, tmp_path):
    run(flown_fixture, tmp_path / "g.kml", "--format", "gpkg")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["g.gpkg", "g.kml.diagnostics.csv"]
    conn = sqlite3.connect(str(tmp_path / "g.gpkg"))
    assert conn.execute("SELECT count(*) FROM gpkg_contents WHERE table_name = 'track_points'"
                        ).fetchone()[0] == 0


def test_format_kml_leaves_no_gpkg(flown_fixture, tmp_path):
    run(flown_fixture, tmp_path / "k.kml", "--format", "kml")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["k.kml", "k.kml.diagnostics.csv"]


def test_gpkg_alias_conflicts_with_kml_format(flown_fixture, tmp_path):
    cmd = [sys.executable, str(SCRIPT), "--kml-folder", str(flown_fixture["kml_dir"]),
           "--airports", str(flown_fixture["airports"]), "--routes", str(flown_fixture["routes"]),
           "--output", str(tmp_path / "x.kml"), "--gpkg", "--format", "kml"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert res.returncode == 1 and "conflicts" in res.stdout


def test_standalone_export_applies_overrides(flown_fixture, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    run(flown_fixture, tmp_path / "a" / "r.kml", "--format", "gpkg")
    run(flown_fixture, tmp_path / "b" / "r.kml", "--format", "kml", "--legacy-kml",
        "--color-mode", "altitude", "--sample", "1")
    res = subprocess.run([sys.executable, str(EXPORTER), str(tmp_path / "a" / "r.gpkg"),
                          "-o", str(tmp_path / "a" / "r.kml"),
                          "--color-mode", "altitude", "--sample", "1"],
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr
    diffs, _ = kml_compare.compare_kml(str(tmp_path / "b" / "r.kml"), str(tmp_path / "a" / "r.kml"))
    assert diffs == []


def test_timestamp_rebuild(ahr):
    m = datetime(2026, 10, 19, 14, 34, 55, 437000, tzinfo=timezone.utc).timestamp()
    assert ahr._timestamp_from_epoch(m, "ADSB") == "2026-10-19T14:34:55.437Z"
    whole = datetime(2026, 10, 19, 14, 35, tzinfo=timezone.utc).timestamp()
    assert ahr._timestamp_from_epoch(whole, "FIREHOSE") == "2026-10-19T14:35:00Z"
    assert ahr._timestamp_from_epoch(whole, "ADSB") == "2026-10-19T14:35:00.000Z"


def test_flight_id_repeats_suffixed(ahr, make_segment, utc):
    a = make_segment("AC1", utc, 60, 0.0, 0.0, 0.0, 3.0, "A", "B")
    b = make_segment("AC1", utc, 60, 0.0, 0.0, 0.0, 3.0, "A", "B")
    assert ahr.annotate_flights([a, b], gap_min=5.0) == 1
    assert (a.flight_id, b.flight_id) == ("AC1_20260201T120000Z", "AC1_20260201T120000Z_2")


def test_suspect_od_threshold(ahr, make_segment, utc):
    seg = make_segment("AC1", utc, 60, 0.0, 0.0, 0.0, 3.0, "A", "B")
    seg.destination = ahr.Airport("B", 0.0, 1.0, 0)   # 111 km away, flew 334 km
    ahr.annotate_flights([seg], gap_min=5.0, suspect_ratio=2.5)
    assert seg.od_distance_ratio == pytest.approx(3.0, abs=0.01) and seg.suspect_od
    ahr.annotate_flights([seg], gap_min=5.0, suspect_ratio=3.5)
    assert not seg.suspect_od


def test_output_placeholders(ahr):
    assert ahr.resolve_output_path("o/viv_{from}_{to}.kml", "2026-09-30", "2026-10-19") == \
        "o/viv_2026-09-30_2026-10-19.kml"
    assert ahr.gpkg_path_for("o/viv.kml") == "o/viv.gpkg"
    assert ahr.gpkg_path_for("o/viv") == "o/viv.gpkg"


def test_takeoff_date_filter(flown_fixture, tmp_path):
    stdout = run(flown_fixture, tmp_path / "f_{from}_{to}.kml", "--format", "gpkg",
                 "--takeoff-from", "2026-10-02", "--takeoff-to", "2026-10-02")
    assert "dropped 3 flight(s), 1 remain" in stdout
    gpkg = tmp_path / "f_2026-10-02_2026-10-02.gpkg"
    conn = sqlite3.connect(str(gpkg))
    assert conn.execute("SELECT flight_date FROM flights").fetchall() == [("2026-10-02",)]
    meta = dict(conn.execute("SELECT key, value FROM meta"))
    assert (meta["date_from"], meta["date_to"]) == ("2026-10-02", "2026-10-02")
    rows = conn.execute("SELECT disposition, drop_reason, flight_id FROM diagnostics "
                        "WHERE disposition = 'dropped_date_range'").fetchall()
    assert rows and all(r[1] == "takeoff outside 2026-10-02..2026-10-02" and r[2] is None
                        for r in rows)
    linked = {r[0] for r in conn.execute("SELECT flight_id FROM diagnostics "
                                         "WHERE flight_id IS NOT NULL")}
    assert linked == {r[0] for r in conn.execute("SELECT flight_id FROM flights")}


def test_takeoff_date_rejects_bad_date(flown_fixture, tmp_path):
    cmd = [sys.executable, str(SCRIPT), "--kml-folder", str(flown_fixture["kml_dir"]),
           "--airports", str(flown_fixture["airports"]), "--routes", str(flown_fixture["routes"]),
           "--output", str(tmp_path / "x.kml"), "--takeoff-from", "2026-13-01"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert res.returncode == 1 and "must be YYYY-MM-DD" in res.stdout


def test_output_named_gpkg(flown_fixture, tmp_path):
    run(flown_fixture, tmp_path / "lan.gpkg", "--format", "gpkg")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["lan.gpkg", "lan.kml.diagnostics.csv"]


def test_filter_kml_files_by_date(ahr):
    files = [f"/x/e4_2026-07-{d:02d}_baro_avg.kml" for d in (12, 13, 14, 30, 31)] + ["/x/a.kml"]
    kept, skipped = ahr.filter_kml_files_by_date(files, "2026-07-14", "2026-07-29")
    assert [f.split("_")[1] if "_" in f else f for f in kept] == [
        "2026-07-13", "2026-07-14", "2026-07-30", "/x/a.kml"]
    assert skipped == 2


def test_gpkg_summary(flown_fixture, tmp_path):
    run(flown_fixture, tmp_path / "s.gpkg", "--format", "gpkg", "--integrate-pos", "true")
    out = tmp_path / "s_summary.txt"
    res = subprocess.run([sys.executable, str(PROJECT_ROOT / "gpkg_summary.py"),
                          str(tmp_path / "s.gpkg"), "-o", str(out)],
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    text = out.read_text()
    assert text == res.stdout
    assert "Flights: 4  aircraft: 2" in text
    assert "flight dates: 2026-10-01 -> 2026-10-02" in text
    assert "Track points by source:" in text and "FIREHOSE" in text
