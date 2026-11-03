"""Shared pytest fixtures for adsb_historical_routes tests."""

import importlib.util
import json
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


def _write_track_kml(path: Path, registration: str, flights) -> None:
    """One ADS-B-Exchange-style day file: one gx:Track holding every point of the day."""
    whens, coords = [], []
    for pts in flights:
        for t, lat, lon, alt in pts:
            whens.append(f"<when>{t:%Y-%m-%dT%H:%M:%S}.{t.microsecond // 1000:03d}Z</when>")
            coords.append(f"<gx:coord>{lon:.6f} {lat:.6f} {alt:.0f}</gx:coord>")
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">'
        f"<Folder><Folder><name>{registration} track</name><Placemark><name>{registration}</name>"
        "<Style><LineStyle><color>ff1c1ae3</color><width>4</width></LineStyle></Style>"
        "<gx:Track><altitudeMode>absolute</altitudeMode><extrude>1</extrude>"
        + "".join(whens) + "".join(coords) + "</gx:Track></Placemark></Folder></Folder></kml>")


def _flight_points(start: datetime, minutes: float, a, b, step_s: int = 20, skip=None):
    """(t, lat, lon, alt_m) along a->b: climb/descent over the first/last 15%, cruise 10 km.
    ``skip`` = (from_min, to_min) leaves a coverage hole."""
    n = int(minutes * 60 / step_s)
    out = []
    for i in range(n + 1):
        f = i / n
        m = f * minutes
        if skip and skip[0] < m < skip[1]:
            continue
        alt = 30.0 + 9970.0 * min(1.0, f / 0.15, (1 - f) / 0.15)
        out.append((start + timedelta(seconds=i * step_s),
                    a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, alt))
    return out


@pytest.fixture(scope="session")
def flown_fixture(tmp_path_factory):
    """Synthetic VIV-like inputs: 4 flights over 2 days for 2 aircraft.

    * ABC001 10-01: AAA->BBB, then BBB->AAA with an 8-min interior gap (stitch connector);
      10-02: AAA->BBB.
    * ABC002 10-01: AAA->CCC with a 30-min hole, so it arrives as two segments that are
      joined, plus firehose fixes inside the hole (--integrate-pos fills it).
    """
    root = tmp_path_factory.mktemp("flown_fixture")
    kml_dir = root / "kml"
    fh_dir = root / "firehose"
    kml_dir.mkdir()
    fh_dir.mkdir()
    AAA, BBB, CCC = (0.0, 0.0), (0.0, 5.0), (4.0, 2.0)
    (root / "airports.csv").write_text(
        "airport,latitude,longitude,elevation_ft\nAAA,0.0,0.0,100\nBBB,0.0,5.0,100\nCCC,4.0,2.0,100\n")
    (root / "routes.csv").write_text(
        "origin,destination,avg_enroute_min\nAAA,BBB,60\nBBB,AAA,60\nAAA,CCC,80\nCCC,AAA,80\n")
    d1 = datetime(2026, 10, 1, 10, 0, 0, 250000, tzinfo=timezone.utc)
    d2 = datetime(2026, 10, 2, 9, 30, 0, 125000, tzinfo=timezone.utc)
    _write_track_kml(kml_dir / "abc001_2026-10-01_baro_avg.kml", "XA-AAA", [
        _flight_points(d1, 60, AAA, BBB),
        _flight_points(d1 + timedelta(hours=2), 60, BBB, AAA, skip=(30, 38)),
    ])
    _write_track_kml(kml_dir / "abc001_2026-10-02_baro_avg.kml", "XA-AAA", [
        _flight_points(d2, 60, AAA, BBB),
    ])
    hole = (25, 55)
    _write_track_kml(kml_dir / "abc002_2026-10-01_baro_avg.kml", "XA-BBB", [
        _flight_points(d1 + timedelta(minutes=7), 80, AAA, CCC, skip=hole),
    ])
    start = d1 + timedelta(minutes=7)
    for k, m in enumerate(range(hole[0] + 1, hole[1], 2)):
        t = start + timedelta(minutes=m)
        f = m / 80
        (fh_dir / f"XABBB_{k:04d}.json").write_text(json.dumps({
            "icao": "abc002", "timestamp": int(t.timestamp()),
            "latitude": AAA[0] + (CCC[0] - AAA[0]) * f,
            "longitude": AAA[1] + (CCC[1] - AAA[1]) * f, "altitude": "33000"}))
    return {"root": root, "kml_dir": kml_dir, "firehose": fh_dir,
            "airports": root / "airports.csv", "routes": root / "routes.csv"}


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


def _acars_pos_body(reg: str, flight: str, t: datetime, lat: float, lon: float, fl: int) -> str:
    """A raw ACARS (ARINC 620) POS report as the LATAM feed delivers it."""
    def ddmm(v, width):
        deg = int(abs(v))
        tenths = int(round((abs(v) - deg) * 600))
        return f"{deg:0{width}d}{tenths:03d}"
    ns = "S" if lat < 0 else "N"
    ew = "W" if lon < 0 else "E"
    return (f"QU CPYXXXX\r\n.SCLASLA {t:%d%H%M}\r\nFML\r\nFI {flight}/AN {reg}\r\n"
            f"DT QXB GRU9 {t:%d%H%M} F90A\r\n"
            f"-  POS{ns}{ddmm(lat, 2)}{ew}{ddmm(lon, 3)},WPT01,{t:%H%M%S},{fl},WPT02,"
            f"{t:%H%M%S},WPT03,M53,19932,498/TS{t:%H%M%S},{t:%d%m%y}630D\r\n")


def write_acars_pos(folder: Path, reg: str, flight: str, t: datetime, lat: float, lon: float,
                    fl: int, n: int) -> Path:
    """One POS report file, named ``<TAIL>_raw_<n>.json`` with the receive stamp in its uri."""
    tail = reg.replace("-", "")
    received = t + timedelta(seconds=20)
    path = folder / f"{tail}_raw_{n:05d}.json"
    path.write_text(json.dumps({
        "body": _acars_pos_body(reg, flight, t, lat, lon, fl),
        "uri": f"s3://bucket/acars/{tail}-{received:%Y%m%d%H%M%S}000.txt"}))
    return path


@pytest.fixture(scope="session")
def evidence_fixture(tmp_path_factory):
    """Synthetic LATAM-like inputs for the endpoint-evidence features (raw ACARS POS).

    Airports AAA (0,0), BBB (0,5), CCC (4,2), DDD (0,6): BBB and DDD lie on one line from
    AAA, the case where route time and geometry cannot tell the destination apart.

    * ABC101 (XA-AAA, mapped), 10-01:
      - 10:00 AAA->DDD, 72 min, JJ1234. ADS-B sees only the first 30 min; POS reports
        every 6 min continue to DDD, the last one low over DDD.
      - 13:00 DDD->AAA, 72 min, JJ1235, all ADS-B (POS inside the window too).
      - 17:00 AAA->CCC, 60 min, JJ1236: POS only (no ADS-B).
    * CC-BMD (not in the tail map), 10-01 11:00 AAA->BBB, 60 min, LA800: POS only.
    * Planned legs for all four.
    """
    root = tmp_path_factory.mktemp("evidence_fixture")
    kml_dir = root / "kml"
    pos_dir = root / "pos"
    kml_dir.mkdir()
    pos_dir.mkdir()
    AAA, BBB, CCC, DDD = (0.0, 0.0), (0.0, 5.0), (4.0, 2.0), (0.0, 6.0)
    (root / "airports.csv").write_text(
        "airport,latitude,longitude,elevation_ft\nAAA,0.0,0.0,100\nBBB,0.0,5.0,100\n"
        "CCC,4.0,2.0,100\nDDD,0.0,6.0,100\n")
    (root / "routes.csv").write_text(
        "origin,destination,avg_enroute_min\nAAA,BBB,60\nBBB,AAA,60\nAAA,DDD,72\n"
        "DDD,AAA,72\nAAA,CCC,60\nCCC,AAA,60\n")
    (root / "tails.csv").write_text("tail_number,icao_hex\nXAAAA,ABC101\n")
    d1 = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    _write_track_kml(kml_dir / "abc101_2026-10-01_baro_avg.kml", "XA-AAA", [
        _flight_points(d1, 72, AAA, DDD, skip=(30, 73)),
        _flight_points(d1 + timedelta(hours=3), 72, DDD, AAA),
    ])
    n = 0

    def pos_along(reg, flight, start, minutes, a, b, from_min, to_min, step_min):
        nonlocal n
        for m in range(from_min, to_min + 1, step_min):
            f = m / minutes
            alt_ft = 33000 * min(1.0, f / 0.15, (1 - f) / 0.15)
            fl = max(int(round(alt_ft / 100.0)), 10)
            n += 1
            write_acars_pos(pos_dir, reg, flight, start + timedelta(minutes=m),
                            a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, fl, n)

    pos_along("XA-AAA", "JJ1234", d1, 72, AAA, DDD, 36, 72, 6)
    n += 1   # the feed delivers some reports twice
    write_acars_pos(pos_dir, "XA-AAA", "JJ1234", d1 + timedelta(minutes=48),
                    0.0, 4.0, 330, n)
    pos_along("XA-AAA", "JJ1235", d1 + timedelta(hours=3), 72, DDD, AAA, 20, 50, 10)
    pos_along("XA-AAA", "JJ1236", d1 + timedelta(hours=7), 60, AAA, CCC, 0, 60, 6)
    pos_along("CC-BMD", "LA800", d1 + timedelta(hours=1), 60, AAA, BBB, 0, 60, 6)
    (root / "legs.csv").write_text(
        "leg_key,source,carrier_icao,flight_number,flight_digits,tail,orig,dest,"
        "sched_dep_utc,ofp_date,ambiguous\n"
        "2026-10-01_TAM1234_AAA-DDD_b0,ofp,TAM,TAM1234,1234,XAAAA,AAA,DDD,"
        "2026-10-01T10:00:00Z,2026-10-01,0\n"
        "2026-10-01_TAM1235_DDD-AAA_b0,ofp,TAM,TAM1235,1235,XAAAA,DDD,AAA,"
        "2026-10-01T13:00:00Z,2026-10-01,0\n"
        "2026-10-01_TAM1236_AAA-CCC_b0,ofp,TAM,TAM1236,1236,XAAAA,AAA,CCC,"
        "2026-10-01T17:00:00Z,2026-10-01,0\n"
        "2026-10-01_LAN800_AAA-BBB_b0,ofp,LAN,LAN800,800,CCBMD,AAA,BBB,"
        "2026-10-01T11:00:00Z,2026-10-01,0\n")
    return {"root": root, "kml_dir": kml_dir, "pos": pos_dir, "tails": root / "tails.csv",
            "airports": root / "airports.csv", "routes": root / "routes.csv",
            "legs": root / "legs.csv"}
