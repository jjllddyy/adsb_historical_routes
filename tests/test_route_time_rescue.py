"""Tests for bearing-checked route-time rescue."""

from datetime import timedelta


class TestRouteTimeRescue:
    def test_aligned_bearing_rescues(self, ahr, make_segment, utc):
        # Origin SBGR (-23.43, -46.47), heading south to SAEZ (-34.82, -58.54)
        # Mean bearing should be ~225° (southwest)
        seg = make_segment("E47FA6", utc, duration_min=140,
                           start_lat=-23.43, start_lon=-46.47,
                           end_lat=-30.0, end_lon=-52.0,  # mid-cruise endpoint heading SW
                           origin_code="SBGR")
        airports = {
            "SBGR": ahr.Airport("SBGR", -23.43, -46.47, 2459),
            "SAEZ": ahr.Airport("SAEZ", -34.82, -58.54, 67),
            "SBSV": ahr.Airport("SBSV", -12.91, -38.32, 64),  # NORTHEAST of SBGR
        }
        routes = {
            ("SBGR", "SAEZ"): 140,
            ("SBGR", "SBSV"): 140,  # same duration as a confounder, opposite bearing
        }
        result = ahr.match_route_by_time_with_bearing(seg, routes, airports, tolerance=0.20)
        assert result == "SAEZ"

    def test_misaligned_bearing_rejects(self, ahr, make_segment, utc):
        # Origin SBGR but heading NORTH (toward SBSV not SAEZ)
        seg = make_segment("E47FA6", utc, duration_min=140,
                           start_lat=-23.43, start_lon=-46.47,
                           end_lat=-15.0, end_lon=-43.0,  # northeast
                           origin_code="SBGR")
        airports = {
            "SBGR": ahr.Airport("SBGR", -23.43, -46.47, 2459),
            "SAEZ": ahr.Airport("SAEZ", -34.82, -58.54, 67),  # SOUTHWEST
        }
        # Only SAEZ in routes; bearing won't match → no rescue
        routes = {("SBGR", "SAEZ"): 140}
        result = ahr.match_route_by_time_with_bearing(seg, routes, airports, tolerance=0.20)
        assert result is None

    def test_no_origin_returns_none(self, ahr, make_segment, utc):
        seg = make_segment("E47FA6", utc, duration_min=140,
                           start_lat=-23.43, start_lon=-46.47,
                           end_lat=-30.0, end_lon=-52.0)  # no origin_code
        airports = {"SAEZ": ahr.Airport("SAEZ", -34.82, -58.54, 67)}
        routes = {("SBGR", "SAEZ"): 140}
        result = ahr.match_route_by_time_with_bearing(seg, routes, airports)
        assert result is None
