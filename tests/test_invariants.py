"""Tests for cross-file joining invariants."""

from datetime import timedelta
import pytest


class TestJoinInvariants:
    def test_cross_aircraft_join_blocked(self, ahr, make_segment, utc):
        a = make_segment("E47FA6", utc, duration_min=60,
                         start_lat=-23.4, start_lon=-46.5,
                         end_lat=-22.9, end_lon=-43.2,
                         origin_code="SBGR")
        b = make_segment("E47FA7", utc + timedelta(hours=2), duration_min=60,
                         start_lat=-22.9, start_lon=-43.2,
                         end_lat=-23.4, end_lon=-46.5,
                         dest_code="SBGR")
        with pytest.raises(AssertionError, match="Cross-aircraft"):
            ahr.assert_join_invariants(a, b)

    def test_non_chronological_join_blocked(self, ahr, make_segment, utc):
        a = make_segment("E47FA6", utc + timedelta(hours=4), duration_min=60,
                         start_lat=-23.4, start_lon=-46.5,
                         end_lat=-22.9, end_lon=-43.2)
        b = make_segment("E47FA6", utc, duration_min=60,
                         start_lat=-22.9, start_lon=-43.2,
                         end_lat=-23.4, end_lon=-46.5)
        with pytest.raises(AssertionError, match="Non-chronological"):
            ahr.assert_join_invariants(a, b)

    def test_zero_gap_join_blocked(self, ahr, make_segment, utc):
        a = make_segment("E47FA6", utc, duration_min=60,
                         start_lat=-23.4, start_lon=-46.5,
                         end_lat=-22.9, end_lon=-43.2)
        # b starts at exactly the same time a ends
        b_start = a.landing_time
        b = make_segment("E47FA6", b_start, duration_min=60,
                         start_lat=-22.9, start_lon=-43.2,
                         end_lat=-23.4, end_lon=-46.5)
        with pytest.raises(AssertionError, match="Non-chronological|Zero|gap"):
            ahr.assert_join_invariants(a, b)

    def test_valid_chronological_passes(self, ahr, make_segment, utc):
        a = make_segment("E47FA6", utc, duration_min=60,
                         start_lat=-23.4, start_lon=-46.5,
                         end_lat=-22.9, end_lon=-43.2)
        b = make_segment("E47FA6", utc + timedelta(hours=3), duration_min=60,
                         start_lat=-22.9, start_lon=-43.2,
                         end_lat=-23.4, end_lon=-46.5)
        # Should not raise
        ahr.assert_join_invariants(a, b)
