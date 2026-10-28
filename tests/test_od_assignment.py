"""Regression tests for the origin/destination mislabels found in the VIV October data.

About 2.7% of VIV flights flew more than 2.5x the great-circle distance between their
labelled airports (e.g. 131 "MMTO->MMSM" flights of 2.5-10 h). Four root causes, each
pinned here with the geometry that exposed it.
"""

from datetime import timedelta

MMSM = (19.7438, -99.0151, 7369)   # Felipe Angeles (AIFA)
MMTO = (19.3369, -99.5658, 8466)   # Toluca
MMVA = (17.9943, -92.8182, 46)     # Villahermosa
MMLO = (20.9935, -101.4810, 5956)  # Leon/Bajio
MMMZ = (23.1614, -106.2660, 38)    # Mazatlan


def _ap(ahr, code, spec):
    return ahr.Airport(code, spec[0], spec[1], spec[2])


class TestFartherAirportCannotStealEndpoint:
    def test_climbing_departure_keeps_closest_airport(self, ahr):
        # VIV 0D08E6 2026-10-19: first fix 16 km from MMSM at 12,106 ft. MMSM's altitude
        # allowance (4,105 ft) rejects it, MMTO's larger one (4,270 ft) accepts it 89 km away.
        airports = [_ap(ahr, "MMSM", MMSM), _ap(ahr, "MMTO", MMTO)]
        match = ahr.find_nearest_airport(19.828, -98.892, 3690.0, airports,
                                         max_distance_km=100, lenient=True)
        assert match.code == "MMSM"

    def test_never_creates_a_new_match(self, ahr):
        # Same fix with only MMSM known: it was unmatched before and stays unmatched.
        match = ahr.find_nearest_airport(19.828, -98.892, 3690.0, [_ap(ahr, "MMSM", MMSM)],
                                         max_distance_km=100, lenient=True)
        assert match is None

    def test_cruise_altitude_over_closest_airport_not_reassigned(self, ahr):
        # Far above any climb/descent path from the closest airport: the farther airport
        # that passed the altitude check keeps the match, as before.
        near = ahr.Airport("NEAR", 0.0, 0.0, 0)
        far = ahr.Airport("FAR", 0.0, 0.5, 9000)   # ~56 km east, high elevation
        match = ahr.find_nearest_airport(0.0, 0.1, 3300.0, [near, far],
                                         max_distance_km=100, lenient=True)
        assert match.code == "FAR"


class TestRouteTimeRejectsOverflownDestination:
    def _truncated_departure(self, ahr, make_segment, utc):
        # MMSM departure heading NW, last seen ~100 km short of Mazatlan after 60 min:
        # already ~450 km past Leon, whose short block time fits the clipped duration best.
        return make_segment("0D08F3", utc, duration_min=60,
                            start_lat=MMSM[0], start_lon=MMSM[1],
                            end_lat=22.75, end_lon=-105.40, origin_code="MMSM")

    def test_corridor_gate_rejects_destination_behind_endpoint(self, ahr, make_segment, utc):
        seg = self._truncated_departure(ahr, make_segment, utc)
        airports = {c: _ap(ahr, c, s) for c, s in
                    [("MMSM", MMSM), ("MMLO", MMLO), ("MMMZ", MMMZ)]}
        routes = {("MMSM", "MMLO"): 60.0, ("MMSM", "MMMZ"): 95.0}
        got = ahr.match_route_by_time_with_bearing(seg, routes, airports, tolerance=0.6,
                                                   max_cross_track_km=50.0)
        assert got == "MMMZ"

    def test_ungated_legacy_behaviour_unchanged(self, ahr, make_segment, utc):
        seg = self._truncated_departure(ahr, make_segment, utc)
        airports = {c: _ap(ahr, c, s) for c, s in
                    [("MMSM", MMSM), ("MMLO", MMLO), ("MMMZ", MMMZ)]}
        routes = {("MMSM", "MMLO"): 60.0, ("MMSM", "MMMZ"): 95.0}
        got = ahr.match_route_by_time_with_bearing(seg, routes, airports, tolerance=0.6)
        assert got == "MMLO"


class TestTurnaroundNotJoined:
    def test_out_and_back_fragments_stay_separate(self, ahr, make_segment, utc):
        # MMSM -> (coverage lost ~150 km before MMVA) ... MMVA turnaround, no coverage ...
        # (coverage regained ~150 km out of MMVA) -> MMSM, 1.5 h later.
        out_leg = make_segment("0D08E6", utc, duration_min=40,
                               start_lat=MMSM[0], start_lon=MMSM[1],
                               end_lat=18.4, end_lon=-94.2, origin_code="MMSM")
        back_leg = make_segment("0D08E6", utc + timedelta(minutes=130), duration_min=55,
                                start_lat=18.5, start_lon=-94.0,
                                end_lat=MMSM[0], end_lon=MMSM[1], dest_code="MMSM")
        assert ahr.is_turnaround(out_leg, back_leg, time_gap_h=1.5)
        airports = [_ap(ahr, "MMSM", MMSM), _ap(ahr, "MMVA", MMVA)]
        out = ahr.combine_segments_intelligently(
            [out_leg, back_leg], airports, routes_dict={}, preset=ahr.PRESETS["permissive"])
        for s in out:
            assert not (s.origin and s.destination and s.origin.code == s.destination.code)
            assert s.flight_duration < 60   # never the 3 h out-and-back as one "flight"

    def test_continuation_is_not_a_turnaround(self, ahr, make_segment, utc):
        a = make_segment("X", utc, duration_min=60, start_lat=0.0, start_lon=0.0,
                         end_lat=0.0, end_lon=6.0)
        b = make_segment("X", utc + timedelta(minutes=90), duration_min=60,
                         start_lat=0.0, start_lon=9.0, end_lat=0.0, end_lon=15.0)
        assert not ahr.is_turnaround(a, b, time_gap_h=0.5)


class TestInferredDestinationDroppedOnJoin:
    def test_stale_route_time_destination_not_kept(self, ahr, make_segment, utc):
        # Fragment 1 (A, heading east) fits A->X by block time and is rescued to X; fragment
        # 2 continues east well past X 10 min later and is joined (Case 2). The merged track
        # must not keep X as its destination.
        a_ap = ahr.Airport("A", 0.0, 0.0, 0)
        x_ap = ahr.Airport("X", 0.0, 3.2, 0)
        frag1 = make_segment("AC", utc, duration_min=30, start_lat=0.0, start_lon=0.0,
                             end_lat=0.0, end_lon=3.0, origin_code="A")
        frag2 = make_segment("AC", utc + timedelta(minutes=40), duration_min=30,
                             start_lat=0.0, start_lon=4.0, end_lat=0.0, end_lon=6.0)
        out = ahr.combine_segments_intelligently(
            [frag1, frag2], [a_ap, x_ap], routes_dict={("A", "X"): 32.0},
            preset=ahr.PRESETS["permissive"])
        assert not any(s.destination and s.destination.code == "X" and s.points[-1].lon > 5
                       for s in out)
