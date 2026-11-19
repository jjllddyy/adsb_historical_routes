"""Trace JSON inputs (--trace-folder): ADS-B Exchange JSON, adsb.lol and the S3 trace builder.

The reader must give what parse_kml_tracks gives for the website's KML of the same day, so
the rest of the pipeline is unchanged (handoff 2026-11-19_trace_json_sources.md §4-§6).
"""

import gzip
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.conftest import flight_trace, readsb_point, write_readsb_trace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "adsb_historical_routes.py"
REAL = Path(__file__).resolve().parent / "fixtures" / "real"
DAY = datetime(2026, 10, 1, tzinfo=timezone.utc)


# ---- the reader --------------------------------------------------------------------

def test_reader_returns_the_airborne_sections(ahr, trace_fixture):
    tracks = ahr.parse_trace_json(
        str(trace_fixture["json_dir"] / "abc201_2026-10-01_trace.json"), "ABC201")
    assert len(tracks) == 2                                  # ground sections drop out
    (p1, reg, color, width, opacity), (p2, *_rest) = tracks
    assert (reg, color, width, opacity) == ("XA-JSN", "ff1c1ae3", "4", "ff")
    assert len(p1) == 181 - 3 and len(p2) == 181             # 2 without geometric, 1 without position
    assert p1[0].timestamp == "2026-10-01T10:00:00.000Z"
    assert p1[0].alt == pytest.approx((100 + 1900) * 0.3048)    # satellite altitude, metres
    assert p1[0].source == "ADSB" and p1[0].trace_source == "ADSBX"
    assert p1[1].groundspeed is not None                     # as parse_kml_tracks computes it
    assert (p1[0].gs_kt, p1[0].vrate_fpm, p1[0].pos_source) == (420.0, 0, "adsb_icao")


def test_reader_reads_gzip(ahr, tmp_path):
    pts, _ = flight_trace(DAY, DAY + timedelta(hours=10), 60, (0, 0), (0, 5))
    f = write_readsb_trace(tmp_path / "abc201_2026-10-01_trace.json", "abc201", "XA-JSN",
                           DAY, pts, gzip_it=True)
    assert f.read_bytes()[:2] == b"\x1f\x8b"
    assert len(ahr.parse_trace_json(str(f), "ABC201")[0][0]) == 181


def test_reader_uses_barometric_when_no_satellite_altitude(ahr, tmp_path):
    # the S3 builder: flags 0, feed code in index 9, timestamp = first point, details carry
    # the feed record (FlightAware: flight, orig, dest, flight_id), no geometric altitude
    first = DAY + timedelta(hours=10)
    pts = []
    for i in range(61):
        det = {"flight": "AMX123", "orig": "AAA", "dest": "BBB",
               "flight_id": "AMX123-1-airline-1p"} if i % 2 == 0 else None
        pts.append(readsb_point(first, first + timedelta(minutes=i), 0.0, i / 12,
                                int(100 + 32900 * min(1.0, i / 9, (60 - i) / 9)),
                                details=det, src="tf"))
    f = write_readsb_trace(tmp_path / "abc201_2026-10-01_tf_trace.json", "abc201", "XA-JSN",
                           first, pts, builder={"source": "s3", "feed": "turbofire_firehose"})
    (track, *_), = ahr.parse_trace_json(str(f), "ABC201")
    assert len(track) == 61
    assert track[0].timestamp == "2026-10-01T10:00:00.000Z"
    assert track[30].alt == pytest.approx(33000 * 0.3048)
    assert track[0].trace_source == "S3_TF" and track[0].pos_source == "tf"
    assert (track[0].fa_flight_id, track[0].flight_number, track[0].fa_orig,
            track[0].fa_dest) == ("AMX123-1-airline-1p", "AMX123", "AAA", "BBB")
    assert track[1].flight_number is None


def test_callsign_from_details(ahr, tmp_path):
    pts, _ = flight_trace(DAY, DAY + timedelta(hours=10), 60, (0, 0), (0, 5))
    pts[30][8] = {"flight": "AMX402  ", "alt_geom": 20000, "squawk": "1200"}
    f = write_readsb_trace(tmp_path / "abc201_2026-10-01_trace.json", "abc201", "XA-JSN",
                           DAY, pts)
    track = ahr.parse_trace_json(str(f), "ABC201")[0][0]
    p = next(x for x in track if x.flight_number)
    assert p.flight_number == "AMX402"
    assert p.alt == pytest.approx(20000 * 0.3048)            # details alt_geom wins


# ---- folders -------------------------------------------------------------------------

def test_discovery_skips_other_names_and_counts_nodata(ahr, trace_fixture):
    found = ahr.discover_trace_folder(str(trace_fixture["json_dir"]))
    assert [Path(f).name for f in found.files] == ["abc201_2026-10-01_trace.json"]
    assert found.nodata == [("abc202", "2026-10-01")]
    assert found.skipped == ["XA-JSN-track-EGM96_avg.kml"]
    assert found.source == "ADSBX"


def test_json_preferred_over_kml_for_one_tail_day(ahr, trace_fixture, tmp_path):
    d = tmp_path / "both"
    shutil.copytree(trace_fixture["json_dir"], d)
    shutil.copy(trace_fixture["kml_dir"] / "abc201_2026-10-01_baro_avg.kml", d)
    (d / "abc203_2026-10-01_baro_avg.kml").write_text("<kml/>")   # KML only: read it
    found = ahr.discover_trace_folder(str(d))
    assert [Path(f).name for f in found.files] == ["abc201_2026-10-01_trace.json",
                                                   "abc203_2026-10-01_baro_avg.kml"]
    assert found.json_over_kml == 1


@pytest.mark.parametrize("marker,expect", [
    ("LICENSE-ODbL.txt", "ADSBLOL"), ("manifest.csv", "ADSBLOL"), (None, "ADSBX")])
def test_source_from_the_folder(ahr, tmp_path, marker, expect):
    d = tmp_path / "amx_2026-03-01_2026-05-30_traces_lol" if expect == "ADSBLOL" and \
        marker == "manifest.csv" else tmp_path / "src"
    d.mkdir()
    pts, _ = flight_trace(DAY, DAY + timedelta(hours=10), 60, (0, 0), (0, 5))
    write_readsb_trace(d / "abc201_2026-10-01_trace.json", "abc201", "XA-JSN", DAY, pts)
    if marker:
        (d / marker).write_text("x")
    assert ahr.discover_trace_folder(str(d)).source == expect


def test_builder_folder_source(ahr, tmp_path):
    d = tmp_path / "ta"
    d.mkdir()
    pts = [readsb_point(DAY, DAY + timedelta(minutes=i), 0.0, i / 12, 30000, src="ta")
           for i in range(30)]
    write_readsb_trace(d / "abc201_2026-10-01_ta_trace.json", "abc201", "XA-JSN", DAY, pts,
                       builder={"source": "s3", "feed": "turbofire_adsb"})
    (d / "abc201_2026-10-02_ta_baro.kml").write_text("<kml/>")
    found = ahr.discover_trace_folder(str(d))
    assert found.source == "S3_TA" and len(found.files) == 2


def test_source_override(ahr, trace_fixture):
    assert ahr.discover_trace_folder(str(trace_fixture["json_dir"]),
                                     source="adsblol").source == "ADSBLOL"


# ---- end to end ----------------------------------------------------------------------

def _build(fx, out, *inputs):
    cmd = [sys.executable, str(SCRIPT), *inputs, "--airports", str(fx["airports"]),
           "--routes", str(fx["routes"]), "--confidence", "permissive", "--airline", "TST",
           "--format", "gpkg", "--output", str(out)]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         universal_newlines=True, timeout=120)
    assert res.returncode == 0, res.stdout[-3000:] + res.stderr[-2000:]
    return res.stdout, sqlite3.connect(str(out))


def test_json_and_kml_give_the_same_flights(trace_fixture, tmp_path):
    out_j, cj = _build(trace_fixture, tmp_path / "j.gpkg", "--trace-folder",
                       str(trace_fixture["json_dir"]))
    _, ck = _build(trace_fixture, tmp_path / "k.gpkg", "--kml-folder",
                   str(trace_fixture["kml_dir"]))
    q = ("SELECT flight_id, origin, dest, takeoff_time, landing_time, n_points_full, "
         "orig_source, dest_source FROM flights ORDER BY flight_id")
    assert cj.execute(q).fetchall() == ck.execute(q).fetchall()
    assert len(cj.execute(q).fetchall()) == 2
    meta = dict(cj.execute("SELECT key, value FROM meta"))
    assert (meta["track_input"], meta["track_source"]) == ("trace-folder", "ADSBX")
    assert json.loads(meta["trace_folder"])["nodata"] == 1
    assert "1 tail-day(s) checked with no data" in out_j
    assert "skipping 1 file(s) whose names are not trace files" in out_j


def test_trace_folder_needs_trace_files(tmp_path, trace_fixture):
    empty = tmp_path / "empty"
    empty.mkdir()
    cmd = [sys.executable, str(SCRIPT), "--trace-folder", str(empty), "--airports",
           str(trace_fixture["airports"]), "--routes", str(trace_fixture["routes"]),
           "--output", str(tmp_path / "x.gpkg")]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         universal_newlines=True, timeout=60)
    assert res.returncode == 1 and "No trace files found" in res.stdout


# ---- the real ADS-B Exchange pair (optional; data is not committed) -------------------

@pytest.mark.skipif(not (REAL / "a01820_2026-03-01_trace.json").exists(),
                    reason="real fixture pair not present in tests/fixtures/real/")
def test_real_pair_matches_the_website_kml(ahr):
    kml = ahr.parse_kml_tracks(str(REAL / "a01820_2026-03-01_website_baro_avg.kml"), "A01820")
    js = ahr.parse_trace_json(str(REAL / "a01820_2026-03-01_trace.json"), "A01820")
    assert len(js) == len(kml) == 5
    for (jp, jreg, *_), (kp, kreg, *_) in zip(js, kml):
        assert jreg == kreg == "N105JS"
        # the site writes each section's last point twice; the JSON's own repeated times
        # (same time, another position) are in both
        kk = [kp[0]] + [b for a, b in zip(kp, kp[1:])
                        if (a.timestamp, a.lat, a.lon) != (b.timestamp, b.lat, b.lon)]
        assert len(kk) == len(kp) - 1
        assert [(p.timestamp, p.lat, p.lon) for p in jp] == \
            [(p.timestamp, p.lat, p.lon) for p in kk]
        assert max(abs(a.alt - b.alt) for a, b in zip(jp, kk)) <= 40.0


def test_flightaware_identity_from_builder_points(trace_fixture, tmp_path):
    # an S3 firehose (tf) trace: the track ends 25 min out of AAA (ADS-B lost), and the
    # FlightAware fields on its own points name the destination
    d = tmp_path / "tf"
    d.mkdir()
    first = DAY + timedelta(hours=10)
    pts = []
    for i in range(26):
        alt = int(100 + 32900 * min(1.0, i / 9))
        pts.append(readsb_point(first, first + timedelta(minutes=i), 0.0, i / 12, alt,
                                details={"flight": "AMX123", "orig": "AAA", "dest": "BBB",
                                         "flight_id": "AMX123-1-airline-1p"}, src="tf"))
    write_readsb_trace(d / "abc201_2026-10-01_tf_trace.json", "abc201", "XA-JSN", first, pts,
                       builder={"source": "s3", "feed": "turbofire_firehose"})
    _, conn = _build(trace_fixture, tmp_path / "tf.gpkg", "--trace-folder", str(d),
                     "--flightaware-identity", "true")
    row = conn.execute("SELECT origin, dest, dest_source, flight_number, flight_number_source "
                       "FROM flights").fetchone()
    assert row == ("AAA", "BBB", "flightaware", "AMX123", "flightaware")
    assert dict(conn.execute("SELECT key, value FROM meta"))["track_source"] == "S3_TF"
