"""Shared pytest fixtures for adsb_historical_routes tests."""

import importlib.util
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
