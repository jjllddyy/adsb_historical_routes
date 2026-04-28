"""Tests for bearing utility functions."""

import pytest


class TestBearing:
    def test_due_north_is_zero(self, ahr):
        b = ahr.bearing(0.0, 0.0, 1.0, 0.0)
        assert abs(b - 0.0) < 0.5 or abs(b - 360.0) < 0.5

    def test_due_east_is_ninety(self, ahr):
        b = ahr.bearing(0.0, 0.0, 0.0, 1.0)
        assert abs(b - 90.0) < 0.5

    def test_due_south_is_one_eighty(self, ahr):
        b = ahr.bearing(1.0, 0.0, 0.0, 0.0)
        assert abs(b - 180.0) < 0.5

    def test_due_west_is_two_seventy(self, ahr):
        b = ahr.bearing(0.0, 1.0, 0.0, 0.0)
        assert abs(b - 270.0) < 0.5

    def test_result_is_zero_to_360(self, ahr):
        # SW should be ~225
        b = ahr.bearing(0.0, 0.0, -1.0, -1.0)
        assert 0.0 <= b < 360.0
        assert abs(b - 225.0) < 0.5


class TestAngularDiff:
    def test_zero_diff(self, ahr):
        assert ahr.angular_diff(45.0, 45.0) == 0.0

    def test_thirty_diff(self, ahr):
        assert abs(ahr.angular_diff(45.0, 75.0) - 30.0) < 1e-6

    def test_wraparound_small(self, ahr):
        # 350 to 10 is 20 deg, not 340
        assert abs(ahr.angular_diff(350.0, 10.0) - 20.0) < 1e-6

    def test_max_is_one_eighty(self, ahr):
        assert ahr.angular_diff(0.0, 180.0) == 180.0


class TestMeanBearing:
    def test_consistent_north_track(self, ahr, make_track_point, utc):
        from datetime import timedelta
        # 5 points heading due north
        pts = [
            make_track_point(utc + timedelta(seconds=i * 60), i * 0.1, 0.0)
            for i in range(5)
        ]
        b = ahr.mean_bearing(pts, window_minutes=10.0)
        assert abs(b - 0.0) < 1.0 or abs(b - 360.0) < 1.0

    def test_consistent_east_track(self, ahr, make_track_point, utc):
        from datetime import timedelta
        pts = [
            make_track_point(utc + timedelta(seconds=i * 60), 0.0, i * 0.1)
            for i in range(5)
        ]
        b = ahr.mean_bearing(pts, window_minutes=10.0)
        assert abs(b - 90.0) < 1.0

    def test_short_track_falls_back_to_full(self, ahr, make_track_point, utc):
        from datetime import timedelta
        pts = [
            make_track_point(utc + timedelta(seconds=i * 30), 0.0, i * 0.1)
            for i in range(3)
        ]
        # 1.5 minutes total < 10 minute window — should still return a meaningful bearing
        b = ahr.mean_bearing(pts, window_minutes=10.0)
        assert abs(b - 90.0) < 1.0
