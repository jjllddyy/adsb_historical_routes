"""Tests for turn-robust corridor geometry endpoint recovery.

Geometry recovery infers a single MISSING origin/destination for a truncated track
(one end at a known airport, the other airborne at the ADS-B coverage boundary) from
the airline's route-network corridors. Key guarantees exercised here:
  - it is turn-robust (only the airborne endpoint's corridor position matters, never
    the shape of the path between the endpoints);
  - it only ever *fills* a missing endpoint and never alters a segment that already
    has both airports (recovery-only, never a filter that can drop a matched flight).
"""

from datetime import timedelta


class TestCrossTrack:
    def test_point_on_the_line_is_zero(self, ahr):
        # A point sitting on the A->B great circle has ~0 cross-track distance.
        assert abs(ahr.cross_track_km(0.0, 5.0, 0.0, 0.0, 0.0, 10.0)) < 1.0

    def test_point_one_degree_off_is_about_111km(self, ahr):
        # One degree of latitude off the line ~= 111 km cross-track.
        assert 100.0 < abs(ahr.cross_track_km(1.0, 5.0, 0.0, 0.0, 0.0, 10.0)) < 125.0


class TestCorridorInference:
    def _airports(self, ahr):
        return {
            "A": ahr.Airport("A", 0.0, 0.0, 0),
            "B": ahr.Airport("B", 0.0, 10.0, 0),   # due east of A
            "C": ahr.Airport("C", 10.0, 0.0, 0),   # due north of A
        }

    def test_picks_route_neighbour_aligned_with_track(self, ahr, make_segment, utc):
        # Departs A heading east and truncates mid-cruise at (0, 4). Two route options
        # from A: B (east) and C (north). Only B's corridor contains the airborne end.
        seg = make_segment("AC1", utc, duration_min=30,
                            start_lat=0.0, start_lon=0.0,
                            end_lat=0.0, end_lon=4.0,
                            origin_code="A")  # destination missing
        routes = {("A", "B"): 90.0, ("A", "C"): 90.0}
        code, which = ahr.infer_endpoint_by_corridor(seg, routes, self._airports(ahr), 70.0)
        assert (code, which) == ("B", "dest")

    def test_turn_within_route_does_not_prevent_match(self, ahr, make_track_point, utc):
        # A dogleg: the aircraft bulges ~130 km north mid-flight (a SID/weather turn),
        # but departs A and truncates on the A->B corridor. Because only the airborne
        # ENDPOINT's corridor position is tested, the turn is irrelevant and B matches.
        pts = []
        # lat,lon waypoints: start on corridor, bulge north, return to corridor at the end
        path = [(0.0, 0.0), (1.2, 1.0), (1.2, 2.0), (0.4, 3.2), (0.0, 4.0)]
        for i, (lat, lon) in enumerate(path):
            pts.append(make_track_point(utc + timedelta(minutes=8 * i), lat, lon, 10000.0))
        seg = ahr.FlightSegment("AC2", "AC2", pts,
                                ahr.Airport("A", 0.0, 0.0, 0), None,
                                "ff0000ff", "2", "ff", "test.kml")
        routes = {("A", "B"): 90.0, ("A", "C"): 90.0}
        code, which = ahr.infer_endpoint_by_corridor(seg, routes, self._airports(ahr), 70.0)
        assert (code, which) == ("B", "dest")

    def test_reverse_infers_missing_origin(self, ahr, make_segment, utc):
        # Arrival at B with an airborne origin end on the A->B corridor: infer origin A.
        seg = make_segment("AC3", utc, duration_min=30,
                            start_lat=0.0, start_lon=7.0,   # airborne, on the corridor
                            end_lat=0.0, end_lon=10.0,
                            dest_code="B")  # origin missing
        routes = {("A", "B"): 90.0}
        code, which = ahr.infer_endpoint_by_corridor(seg, routes, self._airports(ahr), 70.0)
        assert (code, which) == ("A", "origin")

    def test_off_corridor_returns_nothing(self, ahr, make_segment, utc):
        # Airborne end far off any A-route corridor -> no confident inference.
        seg = make_segment("AC4", utc, duration_min=30,
                            start_lat=0.0, start_lon=0.0,
                            end_lat=8.0, end_lon=4.0,   # well north of the A->B line
                            origin_code="A")
        routes = {("A", "B"): 90.0}
        code, which = ahr.infer_endpoint_by_corridor(seg, routes, self._airports(ahr), 70.0)
        assert code is None

    def test_never_alters_a_complete_segment(self, ahr, make_segment, utc):
        # Both endpoints already known: inference must be a no-op (recovery only).
        seg = make_segment("AC5", utc, duration_min=90,
                            start_lat=0.0, start_lon=0.0,
                            end_lat=0.0, end_lon=10.0,
                            origin_code="A", dest_code="B")
        routes = {("A", "Z"): 90.0}
        airports = self._airports(ahr)
        airports["Z"] = ahr.Airport("Z", 0.0, 20.0, 0)
        code, which = ahr.infer_endpoint_by_corridor(seg, routes, airports, 70.0)
        assert (code, which) == (None, None)


class TestRecoveryOnlyInvariant:
    def test_try_recover_leaves_complete_segment_untouched(self, ahr, make_segment, utc):
        # try_recover_endpoint must not touch a segment that already has both airports,
        # regardless of what the route table says.
        seg = make_segment("AC6", utc, duration_min=90,
                            start_lat=0.0, start_lon=0.0,
                            end_lat=0.0, end_lon=10.0,
                            origin_code="A", dest_code="B")
        airports_dict = {"A": ahr.Airport("A", 0, 0, 0), "B": ahr.Airport("B", 0, 10, 0),
                         "Z": ahr.Airport("Z", 0, 20, 0)}
        method = ahr.try_recover_endpoint(
            seg, {("A", "Z"): 90.0}, airports_dict,
            rt_rescue=True, geom_rescue=True,
            route_tol=0.6, corridor_xt=120.0, corridor_slack=1.15, dir_aware=True,
        )
        assert method == ""
        assert seg.origin.code == "A" and seg.destination.code == "B"
