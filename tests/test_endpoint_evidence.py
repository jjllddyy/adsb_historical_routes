"""Endpoint evidence (flown_routes v3): per-end provenance, candidates and confidence (F6),
ACARS flight identity (F1), POS end extension (F2), POS-only flights (F3), aircraft
rotation (F4), planned legs (F5), the evidence order (F8) and dropped-segment recovery (F9).
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

T0 = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def net(ahr):
    """AAA (0,0); BBB (0,5) and DDD (0,6) on one line east of it; CCC (4,2)."""
    aps = [ahr.Airport("AAA", 0.0, 0.0, 100), ahr.Airport("BBB", 0.0, 5.0, 100),
           ahr.Airport("CCC", 4.0, 2.0, 100), ahr.Airport("DDD", 0.0, 6.0, 100)]
    routes = {("AAA", "BBB"): 60.0, ("BBB", "AAA"): 60.0, ("AAA", "DDD"): 72.0,
              ("DDD", "AAA"): 72.0, ("AAA", "CCC"): 60.0, ("CCC", "AAA"): 60.0}
    return {"airports": aps, "by_code": {a.code: a for a in aps}, "routes": routes}


def _seg(ahr, make_segment, net, start, minutes, a, b, orig=None, dest=None, n=30,
         aircraft="ABC101", alt_m=10000.0):
    s = make_segment(aircraft, start, minutes, a[0], a[1], b[0], b[1], n_points=n, alt_m=alt_m)
    s.origin = net["by_code"][orig] if orig else None
    s.destination = net["by_code"][dest] if dest else None
    s.is_complete = s.origin is not None and s.destination is not None
    s.orig_source = "observed" if orig else None
    s.dest_source = "observed" if dest else None
    return s


# ---- F6: provenance of the 1.4.0 endpoints ---------------------------------------

def test_new_segment_marks_detected_ends_observed(ahr, make_segment):
    s = make_segment("X", T0, 60, 0, 0, 0, 5, origin_code="AAA")
    assert (s.orig_source, s.dest_source) == ("observed", None)
    assert (s.orig_candidates, s.dest_candidates) == ([], [])


def test_route_time_rescue_records_source_and_ranked_candidates(ahr, make_segment, net):
    # 30 min out of AAA toward BBB/DDD: route time picks BBB (60 min) over DDD (72 min)
    s = _seg(ahr, make_segment, net, T0, 45, (0, 0), (0, 3.0), orig="AAA")
    rm = ahr.try_recover_endpoint(s, net["routes"], net["by_code"], rt_rescue=True,
                                  geom_rescue=False, route_tol=0.4, corridor_xt=50,
                                  corridor_slack=1.15, dir_aware=True, corridor_gate=True)
    assert rm == "route_time_forward"
    assert s.destination.code == "BBB" and s.dest_source == "route_time"
    assert s.dest_candidates == ["BBB", "DDD"]


def test_geometry_rescue_records_source(ahr, make_segment, net):
    s = _seg(ahr, make_segment, net, T0, 10, (0, 0), (0, 1.0), orig="AAA")
    rm = ahr.try_recover_endpoint(s, net["routes"], net["by_code"], rt_rescue=True,
                                  geom_rescue=True, route_tol=0.4, corridor_xt=50,
                                  corridor_slack=1.15, dir_aware=True, corridor_gate=True)
    assert rm == "geometry_forward" and s.dest_source == "geometry"
    assert s.dest_candidates[0] == s.destination.code and len(s.dest_candidates) == 2


def test_reverse_route_time_records_origin_source(ahr, make_segment, net):
    s = _seg(ahr, make_segment, net, T0, 50, (0, 1.0), (0, 5.0), dest="BBB")
    rm = ahr.try_recover_endpoint(s, net["routes"], net["by_code"], rt_rescue=True,
                                  geom_rescue=False, route_tol=0.4, corridor_xt=50,
                                  corridor_slack=1.15, dir_aware=True, corridor_gate=True)
    assert rm == "route_time_reverse"
    assert s.origin.code == "AAA" and s.orig_source == "route_time"
    assert s.orig_candidates == ["AAA"]


def test_join_carries_end_sources(ahr, make_segment, net):
    preset = replace(ahr.PRESETS["permissive"], max_join_distance_km=50,
                     route_time_tolerance=0.3)
    a = _seg(ahr, make_segment, net, T0, 25, (0, 0), (0, 2.0), orig="AAA")
    b = _seg(ahr, make_segment, net, T0 + timedelta(minutes=35), 25, (0, 3.0), (0, 5.0),
             dest="BBB")
    out = ahr.combine_segments_intelligently([a, b], net["airports"], net["routes"],
                                             preset=preset)
    assert len(out) == 1
    f = out[0]
    assert (f.origin.code, f.destination.code) == ("AAA", "BBB")
    # the 10-minute hole is crossed by a bearing (Case 5) join: the far end is only as
    # good as the join that brought it
    assert f.rescue_method == "bearing_join"
    assert (f.orig_source, f.dest_source) == ("observed", "bearing_join")


@pytest.mark.parametrize("osrc,dsrc,ocand,dcand,expect", [
    ("observed", "observed", [], [], "high"),
    ("observed", "planned:ofp:fn", [], [], "high"),
    ("observed", "pos_observed", [], [], "medium"),
    ("rotation", "observed", [], [], "medium"),
    ("observed", "planned:ofp:tail", [], [], "medium"),
    ("observed", "route_time", [], ["BBB"], "low"),
    ("observed", "geometry", [], [], "low"),
    ("bearing_join", "observed", [], [], "low"),
    ("observed", "rotation_chained", [], [], "low"),
    ("observed", "rotation", [], ["BBB", "DDD"], "low"),   # more than one candidate
])
def test_endpoint_confidence(ahr, osrc, dsrc, ocand, dcand, expect):
    assert ahr.endpoint_confidence(osrc, dsrc, ocand, dcand) == expect
