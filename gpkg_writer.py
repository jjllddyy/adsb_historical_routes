"""
Minimal GeoPackage writer/reader built on the standard-library ``sqlite3`` module.

Shared between the route builders (``adsb_historical_routes`` and
``company_route_extractor``); copy (vendor) this file rather than forking it. No GDAL,
no third-party packages.

What it writes (OGC GeoPackage 1.4, as GDAL >= 3.8 and QGIS 3.44 expect):
  * the core tables (``gpkg_spatial_ref_sys``, ``gpkg_contents``,
    ``gpkg_geometry_columns``, ``gpkg_extensions``) plus GDAL's ``gpkg_ogr_contents``
    feature-count cache, kept correct by the same triggers GDAL installs;
  * feature tables with GeoPackage binary geometry (header + ISO WKB), Z and/or M;
  * attribute (non-spatial) tables;
  * an R-tree spatial index per feature table, filled during the bulk load, with the
    standard maintenance triggers created *after* the load (they call ``ST_*``
    functions that only GDAL/SpatiaLite provide, so they must not fire here).

Usage::

    w = GeoPackageWriter("out.gpkg")
    w.add_feature_table("lines", "LINESTRING", [("name", "TEXT")], z=True)
    blob, bbox = encode_linestring([(lon, lat, z), ...], has_z=True)
    w.insert_features("lines", [(blob, bbox, ("a",))])
    w.add_attribute_table("meta", [("key", "TEXT"), ("value", "TEXT")])
    w.insert_rows("meta", [("schema_version", "1")])
    w.add_index("lines", ["name"])
    w.close()   # builds counts, extents, triggers; atomically renames into place
"""

import os
import sqlite3
import struct
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

GPKG_APPLICATION_ID = 0x47504B47  # "GPKG"
GPKG_USER_VERSION = 10400         # GeoPackage 1.4.0

WGS84_SRS_ID = 4326
WGS84_WKT = (
    'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,'
    'AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,'
    'AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,'
    'AUTHORITY["EPSG","9122"]],AXIS["Latitude",NORTH],AXIS["Longitude",EAST],'
    'AUTHORITY["EPSG","4326"]]'
)

_GEOM_TYPE_CODES = {"POINT": 1, "LINESTRING": 2}
_GEOM_CODE_NAMES = {v: k for k, v in _GEOM_TYPE_CODES.items()}

Bbox = Tuple[float, float, float, float]  # (minx, maxx, miny, maxy), the R-tree order


# ---------------------------------------------------------------------------
# Value formatting
# ---------------------------------------------------------------------------

def format_datetime(dt: datetime) -> str:
    """UTC ``DATETIME`` in the GeoPackage form ``YYYY-MM-DDTHH:MM:SS.sssZ``."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def utc_now_str() -> str:
    return format_datetime(datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Geometry encoding (GeoPackage binary = "GP" header + ISO WKB, little-endian)
# ---------------------------------------------------------------------------

def _wkb_type(base: int, has_z: bool, has_m: bool) -> int:
    return base + (1000 if has_z else 0) + (2000 if has_m else 0)


def _header(srs_id: int, bbox: Optional[Bbox]) -> bytes:
    # flags: bit0 = little-endian; bits1-3 = envelope type (0 none, 1 = xy)
    if bbox is None:
        return b"GP" + bytes((0, 0x01)) + struct.pack("<i", srs_id)
    minx, maxx, miny, maxy = bbox
    return (b"GP" + bytes((0, 0x03)) + struct.pack("<i", srs_id)
            + struct.pack("<4d", minx, maxx, miny, maxy))


def encode_point(x: float, y: float, z: Optional[float] = None,
                 srs_id: int = WGS84_SRS_ID) -> Tuple[bytes, Bbox]:
    """Encode a Point / PointZ. Points carry no envelope in the header (as GDAL writes them)."""
    if z is None:
        body = struct.pack("<BI2d", 1, _wkb_type(1, False, False), x, y)
    else:
        body = struct.pack("<BI3d", 1, _wkb_type(1, True, False), x, y, z)
    return _header(srs_id, None) + body, (x, x, y, y)


def encode_linestring(coords: Sequence[Sequence[float]], has_z: bool = False,
                      has_m: bool = False, srs_id: int = WGS84_SRS_ID) -> Tuple[bytes, Bbox]:
    """Encode a LineString[Z][M] from ``(x, y[, z][, m])`` tuples; returns (blob, bbox)."""
    dims = 2 + (1 if has_z else 0) + (1 if has_m else 0)
    flat: List[float] = []
    for c in coords:
        if len(c) != dims:
            raise ValueError(f"expected {dims} ordinates per vertex, got {len(c)}")
        flat.extend(c)
    xs = flat[0::dims]
    ys = flat[1::dims]
    bbox = (min(xs), max(xs), min(ys), max(ys))
    n = len(coords)
    body = (struct.pack("<BII", 1, _wkb_type(2, has_z, has_m), n)
            + struct.pack(f"<{n * dims}d", *flat))
    return _header(srs_id, bbox) + body, bbox


def decode_geometry(blob: bytes) -> Tuple[str, bool, bool, List[Tuple[float, ...]]]:
    """Decode a GeoPackage Point/LineString blob -> (type_name, has_z, has_m, vertices)."""
    if blob[:2] != b"GP":
        raise ValueError("not a GeoPackage geometry blob")
    flags = blob[3]
    env = (flags >> 1) & 0x07
    env_len = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[env]
    off = 8 + env_len
    endian = "<" if blob[off] == 1 else ">"
    (code,) = struct.unpack_from(endian + "I", blob, off + 1)
    off += 5
    has_z = (code // 1000) in (1, 3)
    has_m = (code // 1000) in (2, 3)
    base = code % 1000
    dims = 2 + has_z + has_m
    if base == 1:
        verts = [struct.unpack_from(f"{endian}{dims}d", blob, off)]
    elif base == 2:
        (n,) = struct.unpack_from(endian + "I", blob, off)
        flat = struct.unpack_from(f"{endian}{n * dims}d", blob, off + 4)
        verts = [flat[i:i + dims] for i in range(0, n * dims, dims)]
    else:
        raise ValueError(f"unsupported WKB geometry type {code}")
    return _GEOM_CODE_NAMES[base], has_z, has_m, verts


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class _Table:
    def __init__(self, name: str, columns: List[Tuple[str, str]], geometry_type: Optional[str],
                 z: bool, m: bool):
        self.name = name
        self.columns = columns
        self.geometry_type = geometry_type
        self.z = z
        self.m = m
        self.count = 0
        self.extent: Optional[List[float]] = None  # [minx, maxx, miny, maxy]
        self.indexes: List[Tuple[List[str], bool]] = []


class GeoPackageWriter:
    """Write a new GeoPackage. The file is built under ``<path>.part`` and renamed on close()."""

    def __init__(self, path: str, srs_id: int = WGS84_SRS_ID):
        if srs_id != WGS84_SRS_ID:
            raise ValueError("only EPSG:4326 is supported")
        self.path = path
        self.srs_id = srs_id
        self._tmp = path + ".part"
        for p in (self._tmp, self._tmp + "-journal"):
            if os.path.exists(p):
                os.remove(p)
        self.conn = sqlite3.connect(self._tmp)
        self._tables: Dict[str, _Table] = {}
        c = self.conn
        c.execute(f"PRAGMA application_id = {GPKG_APPLICATION_ID}")
        c.execute(f"PRAGMA user_version = {GPKG_USER_VERSION}")
        # The file is only renamed into place after a successful close(), so a crash
        # leaves a ``.part`` file, never a half-written GeoPackage under the real name.
        c.execute("PRAGMA journal_mode = OFF")
        c.execute("PRAGMA synchronous = OFF")
        c.execute("BEGIN")
        self._create_core()

    # -- core tables ---------------------------------------------------------

    def _create_core(self) -> None:
        c = self.conn
        c.execute(
            "CREATE TABLE gpkg_spatial_ref_sys (srs_name TEXT NOT NULL,"
            "srs_id INTEGER NOT NULL PRIMARY KEY,organization TEXT NOT NULL,"
            "organization_coordsys_id INTEGER NOT NULL,definition TEXT NOT NULL,"
            "description TEXT)")
        c.executemany(
            "INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
            [("Undefined Cartesian SRS", -1, "NONE", -1, "undefined",
              "undefined Cartesian coordinate reference system"),
             ("Undefined geographic SRS", 0, "NONE", 0, "undefined",
              "undefined geographic coordinate reference system"),
             ("WGS 84 geodetic", 4326, "EPSG", 4326, WGS84_WKT,
              "longitude/latitude coordinates in decimal degrees on the WGS 84 spheroid")])
        c.execute(
            "CREATE TABLE gpkg_contents (table_name TEXT NOT NULL PRIMARY KEY,"
            "data_type TEXT NOT NULL,identifier TEXT UNIQUE,description TEXT DEFAULT '',"
            "last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),"
            "min_x DOUBLE, min_y DOUBLE,max_x DOUBLE, max_y DOUBLE,srs_id INTEGER,"
            "CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id))")
        c.execute(
            "CREATE TABLE gpkg_geometry_columns (table_name TEXT NOT NULL,"
            "column_name TEXT NOT NULL,geometry_type_name TEXT NOT NULL,"
            "srs_id INTEGER NOT NULL,z TINYINT NOT NULL,m TINYINT NOT NULL,"
            "CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),"
            "CONSTRAINT uk_gc_table_name UNIQUE (table_name),"
            "CONSTRAINT fk_gc_tn FOREIGN KEY (table_name) REFERENCES gpkg_contents(table_name),"
            "CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys (srs_id))")
        c.execute(
            "CREATE TABLE gpkg_extensions (table_name TEXT,column_name TEXT,"
            "extension_name TEXT NOT NULL,definition TEXT NOT NULL,scope TEXT NOT NULL,"
            "CONSTRAINT ge_tce UNIQUE (table_name, column_name, extension_name))")
        c.execute(
            "CREATE TABLE gpkg_ogr_contents(table_name TEXT NOT NULL PRIMARY KEY,"
            "feature_count INTEGER DEFAULT NULL)")

    # -- table creation ------------------------------------------------------

    def _register(self, t: _Table, data_type: str, identifier: Optional[str],
                  description: str) -> None:
        if t.name in self._tables:
            raise ValueError(f"table {t.name} already exists")
        self._tables[t.name] = t
        cols = ['"fid" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL']
        if t.geometry_type:
            cols.append(f'"geom" {t.geometry_type}')
        cols += [f"{_q(n)} {typ}" for n, typ in t.columns]
        self.conn.execute(f"CREATE TABLE {_q(t.name)} ({', '.join(cols)})")
        self.conn.execute(
            "INSERT INTO gpkg_contents (table_name, data_type, identifier, description, srs_id) "
            "VALUES (?,?,?,?,?)",
            (t.name, data_type, identifier or t.name, description,
             self.srs_id if t.geometry_type else None))

    def add_feature_table(self, name: str, geometry_type: str, columns: List[Tuple[str, str]],
                          z: bool = False, m: bool = False, identifier: Optional[str] = None,
                          description: str = "") -> None:
        """Create a feature table with an ``fid`` key and a ``geom`` column of ``geometry_type``."""
        if geometry_type not in _GEOM_TYPE_CODES:
            raise ValueError(f"unsupported geometry type {geometry_type}")
        t = _Table(name, columns, geometry_type, z, m)
        self._register(t, "features", identifier, description)
        self.conn.execute(
            "INSERT INTO gpkg_geometry_columns VALUES (?,?,?,?,?,?)",
            (name, "geom", geometry_type, self.srs_id, int(z), int(m)))
        self.conn.execute(f'CREATE VIRTUAL TABLE {_q("rtree_" + name + "_geom")} '
                          f'USING rtree(id, minx, maxx, miny, maxy)')
        self.conn.execute(
            "INSERT INTO gpkg_extensions VALUES (?,?,?,?,?)",
            (name, "geom", "gpkg_rtree_index",
             "http://www.geopackage.org/spec120/#extension_rtree", "write-only"))

    def add_attribute_table(self, name: str, columns: List[Tuple[str, str]],
                            identifier: Optional[str] = None, description: str = "") -> None:
        """Create a non-spatial table (``data_type = 'attributes'``) with an ``fid`` key."""
        self._register(_Table(name, columns, None, False, False), "attributes",
                       identifier, description)

    # -- inserts -------------------------------------------------------------

    def insert_features(self, name: str,
                        rows: Iterable[Tuple[Optional[bytes], Optional[Bbox], Sequence]]) -> int:
        """Insert ``(geom_blob, bbox, values)`` rows; values follow the table's column order."""
        t = self._tables[name]
        ncol = len(t.columns)
        sql = (f"INSERT INTO {_q(name)} (fid, geom, {', '.join(_q(n) for n, _ in t.columns)}) "
               f"VALUES (?, ?, {', '.join('?' * ncol)})")
        rtree_sql = f'INSERT INTO {_q("rtree_" + name + "_geom")} VALUES (?,?,?,?,?)'
        batch, rbatch = [], []
        fid = t.count
        ext = t.extent
        for blob, bbox, values in rows:
            if len(values) != ncol:
                raise ValueError(f"{name}: expected {ncol} values, got {len(values)}")
            fid += 1
            batch.append((fid, blob, *values))
            if blob is not None and bbox is not None:
                rbatch.append((fid, *bbox))
                if ext is None:
                    ext = list(bbox)
                else:
                    if bbox[0] < ext[0]: ext[0] = bbox[0]
                    if bbox[1] > ext[1]: ext[1] = bbox[1]
                    if bbox[2] < ext[2]: ext[2] = bbox[2]
                    if bbox[3] > ext[3]: ext[3] = bbox[3]
            if len(batch) >= 20000:
                self.conn.executemany(sql, batch)
                self.conn.executemany(rtree_sql, rbatch)
                batch, rbatch = [], []
        if batch:
            self.conn.executemany(sql, batch)
            self.conn.executemany(rtree_sql, rbatch)
        added = fid - t.count
        t.count = fid
        t.extent = ext
        return added

    def insert_rows(self, name: str, rows: Iterable[Sequence]) -> int:
        """Insert rows into an attribute table; values follow the table's column order."""
        t = self._tables[name]
        ncol = len(t.columns)
        sql = (f"INSERT INTO {_q(name)} ({', '.join(_q(n) for n, _ in t.columns)}) "
               f"VALUES ({', '.join('?' * ncol)})")
        before = t.count
        batch = []
        for values in rows:
            if len(values) != ncol:
                raise ValueError(f"{name}: expected {ncol} values, got {len(values)}")
            batch.append(tuple(values))
            if len(batch) >= 20000:
                self.conn.executemany(sql, batch)
                t.count += len(batch)
                batch = []
        if batch:
            self.conn.executemany(sql, batch)
            t.count += len(batch)
        return t.count - before

    def add_index(self, name: str, columns: List[str], unique: bool = False) -> None:
        """Queue a B-tree index; indexes are created on close(), after the bulk load."""
        self._tables[name].indexes.append((columns, unique))

    # -- finalize ------------------------------------------------------------

    def _create_rtree_triggers(self, t: _Table) -> None:
        # The GeoPackage 1.4 R-tree maintenance triggers, as GDAL writes them.
        tn, rt = _q(t.name), _q("rtree_" + t.name + "_geom")
        p = "rtree_" + t.name + "_geom"
        g = '"geom"'
        ins = (f"INSERT OR REPLACE INTO {rt} VALUES (NEW.\"fid\",ST_MinX(NEW.{g}), "
               f"ST_MaxX(NEW.{g}),ST_MinY(NEW.{g}), ST_MaxY(NEW.{g}))")
        stmts = [
            f'CREATE TRIGGER "{p}_insert" AFTER INSERT ON {tn} '
            f"WHEN (new.{g} NOT NULL AND NOT ST_IsEmpty(NEW.{g})) BEGIN {ins}; END",
            f'CREATE TRIGGER "{p}_update6" AFTER UPDATE OF {g} ON {tn} '
            f'WHEN OLD."fid" = NEW."fid" AND (NEW.{g} NOTNULL AND NOT ST_IsEmpty(NEW.{g})) '
            f"AND (OLD.{g} NOTNULL AND NOT ST_IsEmpty(OLD.{g})) BEGIN UPDATE {rt} SET "
            f"minx = ST_MinX(NEW.{g}), maxx = ST_MaxX(NEW.{g}),miny = ST_MinY(NEW.{g}), "
            f'maxy = ST_MaxY(NEW.{g}) WHERE id = NEW."fid";END',
            f'CREATE TRIGGER "{p}_update7" AFTER UPDATE OF {g} ON {tn} '
            f'WHEN OLD."fid" = NEW."fid" AND (NEW.{g} NOTNULL AND NOT ST_IsEmpty(NEW.{g})) '
            f"AND (OLD.{g} ISNULL OR ST_IsEmpty(OLD.{g})) BEGIN INSERT INTO {rt} VALUES "
            f'(NEW."fid",ST_MinX(NEW.{g}), ST_MaxX(NEW.{g}),ST_MinY(NEW.{g}), '
            f"ST_MaxY(NEW.{g})); END",
            f'CREATE TRIGGER "{p}_update2" AFTER UPDATE OF {g} ON {tn} '
            f'WHEN OLD."fid" = NEW."fid" AND (NEW.{g} ISNULL OR ST_IsEmpty(NEW.{g})) '
            f'BEGIN DELETE FROM {rt} WHERE id = OLD."fid"; END',
            f'CREATE TRIGGER "{p}_update5" AFTER UPDATE ON {tn} '
            f'WHEN OLD."fid" != NEW."fid" AND (NEW.{g} NOTNULL AND NOT ST_IsEmpty(NEW.{g})) '
            f'BEGIN DELETE FROM {rt} WHERE id = OLD."fid"; {ins}; END',
            f'CREATE TRIGGER "{p}_update4" AFTER UPDATE ON {tn} '
            f'WHEN OLD."fid" != NEW."fid" AND (NEW.{g} ISNULL OR ST_IsEmpty(NEW.{g})) '
            f'BEGIN DELETE FROM {rt} WHERE id IN (OLD."fid", NEW."fid"); END',
            f'CREATE TRIGGER "{p}_delete" AFTER DELETE ON {tn} '
            f'WHEN old.{g} NOT NULL BEGIN DELETE FROM {rt} WHERE id = OLD."fid"; END',
        ]
        for s in stmts:
            self.conn.execute(s)

    def _create_count_triggers(self, t: _Table) -> None:
        tn = _q(t.name)
        lit = "'" + t.name.replace("'", "''") + "'"
        self.conn.execute(
            f'CREATE TRIGGER "trigger_insert_feature_count_{t.name}" AFTER INSERT ON {tn} '
            f"BEGIN UPDATE gpkg_ogr_contents SET feature_count = feature_count + 1 "
            f"WHERE lower(table_name) = lower({lit}); END")
        self.conn.execute(
            f'CREATE TRIGGER "trigger_delete_feature_count_{t.name}" AFTER DELETE ON {tn} '
            f"BEGIN UPDATE gpkg_ogr_contents SET feature_count = feature_count - 1 "
            f"WHERE lower(table_name) = lower({lit}); END")

    def close(self) -> None:
        """Create indexes, triggers, extents and counts, then rename the file into place."""
        c = self.conn
        now = utc_now_str()
        for t in self._tables.values():
            for cols, unique in t.indexes:
                iname = f"idx_{t.name}_{'_'.join(cols)}"
                c.execute(f"CREATE {'UNIQUE ' if unique else ''}INDEX {_q(iname)} "
                          f"ON {_q(t.name)} ({', '.join(_q(x) for x in cols)})")
            if t.geometry_type:
                self._create_rtree_triggers(t)
                ext = t.extent
                c.execute("UPDATE gpkg_contents SET min_x=?, min_y=?, max_x=?, max_y=? "
                          "WHERE table_name=?",
                          (ext[0], ext[2], ext[1], ext[3], t.name) if ext
                          else (None, None, None, None, t.name))
            c.execute("UPDATE gpkg_contents SET last_change=? WHERE table_name=?", (now, t.name))
            c.execute("INSERT INTO gpkg_ogr_contents VALUES (?, ?)", (t.name, t.count))
            self._create_count_triggers(t)
        c.execute("COMMIT")
        c.execute("PRAGMA journal_mode = DELETE")
        c.close()
        os.replace(self._tmp, self.path)

    def abort(self) -> None:
        """Discard a partially written file."""
        try:
            self.conn.close()
        finally:
            if os.path.exists(self._tmp):
                os.remove(self._tmp)
