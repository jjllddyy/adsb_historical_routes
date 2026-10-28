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
