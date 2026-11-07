"""flown_routes v2: flight lines split at the date line (±180°).

QGIS reprojects to EPSG:3857 by wrapping longitudes into -180..180, so a leg from 179°
to -179° would be drawn the long way round the world. v2 stores ``flights`` and
``tracks_full`` as MULTILINESTRINGs cut at ±180; the KML is unchanged.
"""

import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import kml_compare
from tests.conftest import _write_track_kml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "adsb_historical_routes.py"
EXPORTER = PROJECT_ROOT / "gpkg_to_kml.py"


# ---- the rule -------------------------------------------------------------

def test_eastbound_crossing(ahr):
    parts = ahr.split_at_antimeridian([(170.0, -10.0), (178.0, -12.0), (-176.0, -15.0),
                                       (-170.0, -14.0)])
    # 178 -> -176 is 6° the short way; it crosses at t = 2/6, lat -12 - 3 * 2/6 = -13
    assert parts == [[(170.0, -10.0), (178.0, -12.0), (180.0, -13.0)],
                     [(-180.0, -13.0), (-176.0, -15.0), (-170.0, -14.0)]]


def test_westbound_crossing(ahr):
    parts = ahr.split_at_antimeridian([(-175.0, 20.0), (175.0, 30.0)])
    assert parts == [[(-175.0, 20.0), (-180.0, 25.0)], [(180.0, 25.0), (175.0, 30.0)]]


def test_no_crossing_keeps_exact_coordinates(ahr):
    coords = [(-99.123456789, 19.4, 2240.5, 1.79e9), (-100.3, 20.6, 1500.25, 1.79e9 + 60.5),
              (179.9, 0.0, 1.0, 1.79e9 + 99.0)]
    assert ahr.split_at_antimeridian(coords[:2]) == [coords[:2]]
    # A 280° jump (-100.3 -> 179.9) is taken the short way, across the date line.
    assert len(ahr.split_at_antimeridian(coords)) == 2


def test_z_and_m_interpolated(ahr):
    parts = ahr.split_at_antimeridian([(179.0, 0.0, 1000.0, 100.0),
                                       (-179.0, 2.0, 3000.0, 200.0)])
    assert parts == [[(179.0, 0.0, 1000.0, 100.0), (180.0, 1.0, 2000.0, 150.0)],
                     [(-180.0, 1.0, 2000.0, 150.0), (-179.0, 2.0, 3000.0, 200.0)]]


def test_every_longitude_stays_in_range(ahr):
    coords = [(170.0, 0.0), (-170.0, 1.0), (170.0, 2.0), (-170.0, 3.0)]
    parts = ahr.split_at_antimeridian(coords)
    assert len(parts) == 4
    assert all(-180.0 <= x <= 180.0 for part in parts for x, _ in part)


def test_join_is_the_inverse(ahr):
    for coords in ([(1.0, 2.0)],
                   [(170.0, 0.0, 5.0), (-170.0, 1.0, 6.0)],
                   [(170.0, 0.0), (-170.0, 1.0), (-160.0, 2.0), (170.0, 3.0), (160.0, 4.0)]):
        assert ahr.join_antimeridian_parts(ahr.split_at_antimeridian(coords)) == coords


# ---- end to end -------------------------------------------------------------

NFFN = (-17.755, 177.443)     # Nadi
NSFA = (-13.830, -172.008)    # Apia


def _wrap(lon):
    return (lon + 180.0) % 360.0 - 180.0


def _pacific_points(start, minutes, a, b, step_s=20):
    """(t, lat, lon, alt_m) from a to b the short way across 180°."""
    b_lon = b[1] + 360.0 if b[1] - a[1] < -180.0 else b[1] - 360.0 if b[1] - a[1] > 180 else b[1]
    n = int(minutes * 60 / step_s)
    out = []
    for i in range(n + 1):
        f = i / n
        alt = 30.0 + 9970.0 * min(1.0, f / 0.15, (1 - f) / 0.15)
        out.append((start + timedelta(seconds=i * step_s), a[0] + (b[0] - a[0]) * f,
                    _wrap(a[1] + (b_lon - a[1]) * f), alt))
    return out


@pytest.fixture(scope="module")
def pacific(tmp_path_factory):
    root = tmp_path_factory.mktemp("pacific")
    kml_dir = root / "kml"
    kml_dir.mkdir()
    (root / "airports.csv").write_text(
        "airport,latitude,longitude,elevation_ft\n"
        f"NFFN,{NFFN[0]},{NFFN[1]},59\nNSFA,{NSFA[0]},{NSFA[1]},58\n")
    (root / "routes.csv").write_text(
        "origin,destination,avg_enroute_min\nNFFN,NSFA,110\nNSFA,NFFN,110\n")
    d = datetime(2026, 10, 1, 1, 0, 0, 500000, tzinfo=timezone.utc)
    _write_track_kml(kml_dir / "c8a001_2026-10-01_baro_avg.kml", "DQ-FAA", [
        _pacific_points(d, 110, NFFN, NSFA),
        _pacific_points(d + timedelta(hours=4), 110, NSFA, NFFN),
    ])
    return root


def _run(root, output, *extra):
    cmd = [sys.executable, str(SCRIPT), "--kml-folder", str(root / "kml"),
           "--airports", str(root / "airports.csv"), "--routes", str(root / "routes.csv"),
           "--confidence", "permissive", "--airline", "FJI", "--output", str(output), *extra]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         universal_newlines=True, timeout=300)
    assert res.returncode == 0, res.stdout[-3000:] + res.stderr[-3000:]


def test_crossing_flights_are_split_and_kml_is_unchanged(ahr, pacific, tmp_path):
    (tmp_path / "direct").mkdir()
    (tmp_path / "via").mkdir()
    _run(pacific, tmp_path / "direct" / "p.kml", "--format", "kml", "--legacy-kml")
    _run(pacific, tmp_path / "via" / "p.kml", "--format", "both")
    diffs, stats = kml_compare.compare_kml(str(tmp_path / "direct" / "p.kml"),
                                           str(tmp_path / "via" / "p.kml"))
    assert diffs == [] and stats["placemarks"] == 2
    conn = sqlite3.connect(str(tmp_path / "via" / "p.gpkg"))
    assert conn.execute("SELECT geometry_type_name, z, m FROM gpkg_geometry_columns "
                        "WHERE table_name IN ('flights', 'tracks_full') ORDER BY table_name"
                        ).fetchall() == [("MULTILINESTRING", 1, 0), ("MULTILINESTRING", 1, 1)]
    rows = conn.execute("SELECT f.city_pair, f.geom, t.geom FROM flights f "
                        "JOIN tracks_full t USING (flight_id) ORDER BY f.fid").fetchall()
    assert sorted(r[0] for r in rows) == ["NFFN-NSFA", "NSFA-NFFN"]
    for pair, fgeom, tgeom in rows:
        for blob in (fgeom, tgeom):
            kind, _, _, parts = ahr.gpkg_writer.decode_geometry(blob)
            assert kind == "MULTILINESTRING" and len(parts) == 2
            assert all(-180.0 <= v[0] <= 180.0 for part in parts for v in part)
            east = pair == "NFFN-NSFA"
            assert parts[0][-1][0] == (180.0 if east else -180.0)
            assert parts[1][0][0] == -parts[0][-1][0]
            assert parts[0][-1][1:] == parts[1][0][1:]     # same lat, Z (and M)
        # tracks_full keeps every builder point, plus the two ±180 vertices
        n_full = conn.execute("SELECT n_points_full FROM flights WHERE city_pair = ?",
                              (pair,)).fetchone()[0]
        assert sum(len(p) for p in ahr.gpkg_writer.decode_geometry(tgeom)[3]) == n_full + 2
    assert conn.execute("SELECT max(od_distance_ratio) FROM flights").fetchone()[0] < 1.2


def test_v1_file_still_exports(ahr, pacific, tmp_path):
    """A v1 file (LINESTRINGs) exports the same KML as its v2 rebuild."""
    _run(pacific, tmp_path / "p.kml", "--format", "both")
    v1 = tmp_path / "v1.gpkg"
    v1.write_bytes((tmp_path / "p.gpkg").read_bytes())
    conn = sqlite3.connect(str(v1))
    # The R-tree update triggers call GDAL's ST_* functions, which plain sqlite3 lacks.
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' "
                                "AND name LIKE 'rtree_%'").fetchall():
        conn.execute(f'DROP TRIGGER "{name}"')
    for table, has_m in (("flights", False), ("tracks_full", True)):
        for fid, blob in conn.execute(f"SELECT fid, geom FROM {table}").fetchall():
            parts = ahr.gpkg_writer.decode_geometry(blob)[3]
            line, _ = ahr.gpkg_writer.encode_linestring(
                ahr.join_antimeridian_parts(parts), has_z=True, has_m=has_m)
            conn.execute(f"UPDATE {table} SET geom = ? WHERE fid = ?", (line, fid))
    conn.execute("UPDATE gpkg_geometry_columns SET geometry_type_name = 'LINESTRING'")
    conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    # Same file name in two folders: the KML document name comes from it.
    for src, d in ((v1, "from_v1"), (tmp_path / "p.gpkg", "from_v2")):
        (tmp_path / d).mkdir()
        out = tmp_path / d / "p.kml"
        res = subprocess.run([sys.executable, str(EXPORTER), str(src), "-o", str(out)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True, timeout=120)
        assert res.returncode == 0, res.stderr
    assert ((tmp_path / "from_v1" / "p.kml").read_bytes()
            == (tmp_path / "from_v2" / "p.kml").read_bytes())


def test_unknown_schema_version_rejected(ahr, pacific, tmp_path):
    _run(pacific, tmp_path / "p.kml", "--format", "gpkg")
    conn = sqlite3.connect(str(tmp_path / "p.gpkg"))
    conn.execute("UPDATE meta SET value = '4' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="unsupported flown_routes schema version 4"):
        ahr.load_flown_gpkg(str(tmp_path / "p.gpkg"))
