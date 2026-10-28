"""Tests for the vendored standard-library GeoPackage writer (gpkg_writer.py)."""

import os
import shutil
import sqlite3
import struct
import subprocess
from datetime import datetime, timezone

import pytest

import gpkg_writer as gw

QGIS_OGRINFO = "/Applications/QGIS.app/Contents/MacOS/ogrinfo"


def _ogrinfo():
    path = shutil.which("ogrinfo") or (QGIS_OGRINFO if os.path.exists(QGIS_OGRINFO) else None)
    return path


def _sample(path):
    w = gw.GeoPackageWriter(str(path))
    w.add_feature_table("lines", "LINESTRING", [("name", "TEXT"), ("t", "DATETIME")],
                        z=True, m=True)
    rows = []
    for i in range(3):
        blob, bbox = gw.encode_linestring(
            [(-99.0 + i, 19.0, 100.0, 1.7e9), (-98.0 + i, 20.5, 200.0, 1.7e9 + 60.25)],
            has_z=True, has_m=True)
        rows.append((blob, bbox, (f"l{i}", "2026-10-01T10:00:00.000Z")))
    assert w.insert_features("lines", rows) == 3
    w.add_feature_table("pts", "POINT", [("k", "TEXT")], z=True)
    w.insert_features("pts", [(*gw.encode_point(-99.0, 19.0, 10.0), ("a",)),
                              (*gw.encode_point(-97.0, 18.0, 20.0), ("b",))])
    w.add_attribute_table("meta", [("key", "TEXT"), ("value", "TEXT")])
    w.insert_rows("meta", [("schema_version", "1"), ("airline", "VIV")])
    w.add_index("lines", ["name"], unique=True)
    w.close()
    return path


class TestGeometryEncoding:
    def test_linestring_zm_round_trip(self):
        coords = [(-99.1, 19.2, 1234.5, 1791084560.437), (-98.0, 20.0, 0.0, 1791084620.0)]
        blob, bbox = gw.encode_linestring(coords, has_z=True, has_m=True)
        assert blob[:2] == b"GP" and blob[2] == 0
        assert blob[3] == 0x03                                  # little-endian, xy envelope
        assert struct.unpack_from("<i", blob, 4)[0] == 4326
        assert struct.unpack_from("<4d", blob, 8) == (-99.1, -98.0, 19.2, 20.0)
        assert bbox == (-99.1, -98.0, 19.2, 20.0)
        assert struct.unpack_from("<I", blob, 41)[0] == 3002     # ISO LineString ZM
        kind, has_z, has_m, verts = gw.decode_geometry(blob)
        assert (kind, has_z, has_m) == ("LINESTRING", True, True)
        assert [tuple(v) for v in verts] == coords               # doubles survive exactly

    def test_point_z_round_trip_without_envelope(self):
        blob, bbox = gw.encode_point(-99.5, 19.25, 2840.0)
        assert blob[3] == 0x01                                  # no envelope for points
        assert gw.decode_geometry(blob) == ("POINT", True, False, [(-99.5, 19.25, 2840.0)])
        assert bbox == (-99.5, -99.5, 19.25, 19.25)

    def test_dimension_mismatch_rejected(self):
        with pytest.raises(ValueError):
            gw.encode_linestring([(0.0, 0.0)], has_z=True)

    def test_datetime_format(self):
        dt = datetime(2026, 7, 2, 17, 56, 26, 820000, tzinfo=timezone.utc)
        assert gw.format_datetime(dt) == "2026-07-02T17:56:26.820Z"


class TestGeoPackageFile:
    def test_core_structure(self, tmp_path):
        path = _sample(tmp_path / "t.gpkg")
        assert not (tmp_path / "t.gpkg.part").exists()
        conn = sqlite3.connect(str(path))
        assert conn.execute("PRAGMA application_id").fetchone()[0] == 0x47504B47
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10400
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        contents = {r[0]: r[1:] for r in conn.execute(
            "SELECT table_name, data_type, srs_id, min_x, max_x, min_y, max_y FROM gpkg_contents")}
        assert contents["lines"] == ("features", 4326, -99.0, -96.0, 19.0, 20.5)
        assert contents["meta"][:2] == ("attributes", None)
        assert conn.execute("SELECT geometry_type_name, z, m FROM gpkg_geometry_columns "
                            "WHERE table_name='lines'").fetchone() == ("LINESTRING", 1, 1)
        assert conn.execute("SELECT srs_id FROM gpkg_spatial_ref_sys ORDER BY srs_id"
                            ).fetchall() == [(-1,), (0,), (4326,)]
        counts = dict(conn.execute("SELECT table_name, feature_count FROM gpkg_ogr_contents"))
        assert counts == {"lines": 3, "pts": 2, "meta": 2}

    def test_rtree_filled_and_triggers_present(self, tmp_path):
        conn = sqlite3.connect(str(_sample(tmp_path / "t.gpkg")))
        rt = conn.execute("SELECT id, minx, maxx, miny, maxy FROM rtree_lines_geom ORDER BY id"
                          ).fetchall()
        assert [r[0] for r in rt] == [1, 2, 3]
        assert rt[1][1:] == pytest.approx((-98.0, -97.0, 19.0, 20.5))
        assert conn.execute("SELECT count(*) FROM rtree_pts_geom").fetchone()[0] == 2
        assert conn.execute("SELECT extension_name FROM gpkg_extensions WHERE table_name='lines'"
                            ).fetchone()[0] == "gpkg_rtree_index"
        triggers = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='lines'")}
        assert {"rtree_lines_geom_insert", "rtree_lines_geom_delete",
                "trigger_insert_feature_count_lines"} <= triggers
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE type='index' AND "
                            "name='idx_lines_name'").fetchone()[0] == 1

    def test_meta_rows(self, tmp_path):
        conn = sqlite3.connect(str(_sample(tmp_path / "t.gpkg")))
        assert dict(conn.execute("SELECT key, value FROM meta")) == {
            "schema_version": "1", "airline": "VIV"}

    def test_abort_leaves_nothing(self, tmp_path):
        w = gw.GeoPackageWriter(str(tmp_path / "x.gpkg"))
        w.add_attribute_table("meta", [("key", "TEXT")])
        w.abort()
        assert os.listdir(tmp_path) == []

    @pytest.mark.skipif(_ogrinfo() is None, reason="GDAL ogrinfo not installed")
    def test_ogrinfo_opens_without_warnings(self, tmp_path):
        path = _sample(tmp_path / "t.gpkg")
        env = dict(os.environ)
        res = subprocess.run([_ogrinfo(), "-so", str(path), "lines"],
                             capture_output=True, text=True, env=env, timeout=60)
        assert res.returncode == 0, res.stderr
        assert "Warning" not in res.stdout + res.stderr
        assert "Feature Count: 3" in res.stdout
        assert "3D Measured Line String" in res.stdout
