"""Tests verifying ConfidencePreset values flow into airport matching and segment detection."""

from datetime import timedelta


class TestAirportRadiusWiring:
    def test_strict_radius_misses_far_airport(self, ahr):
        airport = ahr.Airport("FAR", 0.0, 0.0, 0)
        # 30 km away — outside strict's 20 km radius
        match = ahr.find_nearest_airport(0.0, 0.27, alt_meters=10.0,
                                         airports=[airport],
                                         max_distance_km=20, max_alt_diff_ft=3000.0,
                                         lenient=True)
        assert match is None

    def test_balanced_radius_catches_30km_airport(self, ahr):
        airport = ahr.Airport("MED", 0.0, 0.0, 0)
        match = ahr.find_nearest_airport(0.0, 0.27, alt_meters=10.0,
                                         airports=[airport],
                                         max_distance_km=50, max_alt_diff_ft=3000.0,
                                         lenient=True)
        assert match is not None
        assert match.code == "MED"


class TestPresetUsedInDetection:
    def test_preset_radius_passed_through(self, ahr, make_track_point, utc):
        """detect_flight_segments_with_preset matches origin/dest using preset.airport_radius_km."""
        # Build a track that ends 30km from the only airport
        from datetime import timedelta
        airport = ahr.Airport("SBGR", -23.43, -46.47, 2459)
        # Endpoint at -23.43, -46.18 (~30km east of SBGR)
        # Use low altitude (100m) so airport altitude check passes; endpoints near airport elevation.
        pts = []
        for i in range(40):
            t = utc + timedelta(seconds=i * 30)
            lon = -46.47 + 0.0073 * i
            pts.append(make_track_point(t, -23.43, lon, 100.0))

        # Strict (20km): no match → both endpoints have origin/dest as None
        strict = ahr.PRESETS["strict"]
        strict_segs = ahr.detect_flight_segments_with_preset(pts, [airport], strict, max_gap_minutes=20.0)
        assert len(strict_segs) == 1
        _, _, origin_s, dest_s = strict_segs[0]
        # start is at SBGR (0km away) so origin matches under strict
        # but dest (30km away) should NOT match under strict
        assert dest_s is None

        balanced = ahr.PRESETS["balanced"]
        balanced_segs = ahr.detect_flight_segments_with_preset(pts, [airport], balanced, max_gap_minutes=20.0)
        _, _, _, dest_b = balanced_segs[0]
        assert dest_b is not None
        assert dest_b.code == "SBGR"
