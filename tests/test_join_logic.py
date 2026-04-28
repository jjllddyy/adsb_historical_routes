"""Tests for cross-file joining logic with confidence presets."""

from datetime import timedelta


class TestStrictRejects:
    def test_long_overnight_gap_rejected(self, ahr, make_segment, utc):
        a = make_segment("E47FA6", utc, duration_min=60,
                         start_lat=-23.43, start_lon=-46.47,
                         end_lat=-22.81, end_lon=-43.24,
                         origin_code="SBGR")  # ends mid-cruise (no dest)
        b = make_segment("E47FA6", utc + timedelta(hours=10), duration_min=60,
                         start_lat=-23.43, start_lon=-46.47,
                         end_lat=-22.81, end_lon=-43.24,
                         dest_code="SBGL")  # next day, no origin
        preset = ahr.PRESETS["strict"]
        out = ahr.combine_segments_intelligently([a, b], airports=[], routes_dict={}, preset=preset)
        # 10-hour gap > strict's 2-hour ceiling: not joined
        assert len(out) <= 2
        # Neither should be marked complete-via-join
        for s in out:
            assert not (s.origin and s.origin.code == "SBGR" and s.destination and s.destination.code == "SBGL")

    def test_dest_eq_next_origin_not_joined(self, ahr, make_segment, utc):
        # Real sequential flights through SBGR — must NOT join
        a = make_segment("E47FA6", utc, duration_min=60,
                         start_lat=-22.81, start_lon=-43.24,
                         end_lat=-23.43, end_lon=-46.47,
                         origin_code="SBGL", dest_code="SBGR")
        b = make_segment("E47FA6", utc + timedelta(hours=2), duration_min=60,
                         start_lat=-23.43, start_lon=-46.47,
                         end_lat=-15.87, end_lon=-47.92,
                         origin_code="SBGR", dest_code="SBBR")
        preset = ahr.PRESETS["balanced"]
        out = ahr.combine_segments_intelligently([a, b], airports=[], routes_dict={}, preset=preset)
        # Both segments preserved as separate flights
        assert len(out) == 2


class TestBearingAwareJoin:
    def test_aligned_bearing_joins_under_permissive(self, ahr, make_segment, utc):
        # Aircraft heading SE (~135°). Segment A ends mid-cruise.
        # Segment B continues SE from a point ~400km further along the bearing
        # with a 30-min cruise gap on top of A's 60-min duration.
        # Implied speed: ~400 km / 0.5 h = 800 km/h (within 200-1100 km/h band)
        a = make_segment("E47FA6", utc, duration_min=60,
                         start_lat=-15.0, start_lon=-50.0,
                         end_lat=-16.5, end_lon=-48.5,
                         origin_code="SBBR")  # dest=None, ends heading SE
        b_start = utc + timedelta(hours=1, minutes=30)  # 30-min gap after A's 60-min duration
        b = make_segment("E47FA6", b_start, duration_min=45,
                         start_lat=-19.5, start_lon=-45.5,  # ~400km SE of A's endpoint
                         end_lat=-21.0, end_lon=-43.0,
                         dest_code="SBSV")  # origin=None
        preset = ahr.PRESETS["permissive"]
        out = ahr.combine_segments_intelligently([a, b], airports=[], routes_dict={}, preset=preset)
        # Should be joined into one complete segment
        complete = [s for s in out if s.origin and s.destination]
        assert len(complete) == 1
        assert complete[0].origin.code == "SBBR"
        assert complete[0].destination.code == "SBSV"

    def test_misaligned_bearing_not_joined(self, ahr, make_segment, utc):
        # A ends mid-cruise heading SE
        a = make_segment("E47FA6", utc, duration_min=60,
                         start_lat=-15.0, start_lon=-50.0,
                         end_lat=-16.5, end_lon=-48.5,
                         origin_code="SBBR")
        # B starts 200km away, but heading NORTHWEST (back toward A's start) — wrong direction
        b_start = utc + timedelta(hours=1, minutes=15)
        b = make_segment("E47FA6", b_start, duration_min=45,
                         start_lat=-18.3, start_lon=-46.7,
                         end_lat=-15.5, end_lon=-49.5,
                         dest_code="SBBR")
        preset = ahr.PRESETS["permissive"]
        out = ahr.combine_segments_intelligently([a, b], airports=[], routes_dict={}, preset=preset)
        # Bearing mismatch — should NOT bearing-join (bearing diff > 30°)
        complete = [s for s in out if s.origin and s.destination
                    and s.origin.code == "SBBR" and s.destination.code == "SBBR"]
        assert len(complete) == 0
