"""Tests for DiagnosticsRecorder."""

import csv
from datetime import timedelta
import pytest


@pytest.fixture
def recorder(ahr, tmp_path):
    out_kml = tmp_path / "out.kml"
    return ahr.DiagnosticsRecorder(str(out_kml), preset_name="balanced")


@pytest.fixture
def airports(ahr):
    return [
        ahr.Airport("SBGR", -23.43, -46.47, 2459),  # Sao Paulo
        ahr.Airport("SBGL", -22.81, -43.24, 28),    # Rio de Janeiro
    ]


class TestDiagnosticsRecorder:
    def test_writes_csv_with_header(self, ahr, recorder, tmp_path, airports, make_segment, utc):
        seg = make_segment("E47FA6", utc, duration_min=60,
                           start_lat=-23.43, start_lon=-46.47,
                           end_lat=-22.81, end_lon=-43.24,
                           origin_code="SBGR", dest_code="SBGL")
        recorder.record_raw(seg, segment_idx=0, airports=airports)
        recorder.record_outcome(seg, segment_idx=0, disposition="kept_complete", airports=airports)
        recorder.write_csv()

        csv_path = tmp_path / "out.kml.diagnostics.csv"
        assert csv_path.exists()
        with csv_path.open() as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert rows[0]["phase"] == "raw"
        assert rows[1]["phase"] == "final"

    def test_columns_match_spec(self, ahr, recorder, tmp_path, airports, make_segment, utc):
        seg = make_segment("E47FA6", utc, duration_min=60,
                           start_lat=-23.43, start_lon=-46.47,
                           end_lat=-22.81, end_lon=-43.24)
        recorder.record_raw(seg, segment_idx=0, airports=airports)
        recorder.write_csv()

        csv_path = tmp_path / "out.kml.diagnostics.csv"
        with csv_path.open() as f:
            reader = csv.DictReader(f)
            expected = {
                "phase", "preset", "aircraft_id", "registration", "source_file",
                "segment_idx", "takeoff_time", "landing_time", "duration_min",
                "num_points", "max_alt_m", "total_distance_km", "mean_bearing_deg",
                "origin_code", "dest_code", "origin_dist_km", "dest_dist_km",
                "nearest_airport_start_code", "nearest_airport_start_km",
                "nearest_airport_end_code", "nearest_airport_end_km",
                "disposition", "drop_reason", "joined_with_segment_idx", "rescue_method",
            }
            assert set(reader.fieldnames) == expected

    def test_preset_name_recorded(self, ahr, recorder, tmp_path, airports, make_segment, utc):
        seg = make_segment("E47FA6", utc, duration_min=60,
                           start_lat=-23.43, start_lon=-46.47,
                           end_lat=-22.81, end_lon=-43.24)
        recorder.record_raw(seg, segment_idx=0, airports=airports)
        recorder.write_csv()

        csv_path = tmp_path / "out.kml.diagnostics.csv"
        with csv_path.open() as f:
            row = next(csv.DictReader(f))
        assert row["preset"] == "balanced"

    def test_nearest_airport_filled_even_when_no_match(self, ahr, recorder, tmp_path, airports, make_segment, utc):
        # Segment endpoints far from any airport in our small DB
        seg = make_segment("E47FA6", utc, duration_min=60,
                           start_lat=10.0, start_lon=10.0,
                           end_lat=11.0, end_lon=11.0)
        recorder.record_raw(seg, segment_idx=0, airports=airports)
        recorder.write_csv()

        csv_path = tmp_path / "out.kml.diagnostics.csv"
        with csv_path.open() as f:
            row = next(csv.DictReader(f))
        # nearest_airport_*_code is the airport code regardless of preset radius (within 1000km)
        assert row["nearest_airport_start_code"] in {"SBGR", "SBGL", ""}
        assert row["nearest_airport_start_km"] != ""
        # origin_code is empty (it wasn't matched within the preset radius)
        assert row["origin_code"] == ""

    def test_outcome_fields_populated(self, ahr, recorder, tmp_path, airports, make_segment, utc):
        seg = make_segment("E47FA6", utc, duration_min=60,
                           start_lat=-23.43, start_lon=-46.47,
                           end_lat=-22.81, end_lon=-43.24,
                           origin_code="SBGR", dest_code="SBGL")
        recorder.record_outcome(seg, segment_idx=5, disposition="kept_joined",
                                joined_with=[3, 4], rescue_method="bearing_join",
                                airports=airports)
        recorder.write_csv()

        csv_path = tmp_path / "out.kml.diagnostics.csv"
        with csv_path.open() as f:
            row = next(csv.DictReader(f))
        assert row["disposition"] == "kept_joined"
        assert row["joined_with_segment_idx"] == "3,4"
        assert row["rescue_method"] == "bearing_join"
