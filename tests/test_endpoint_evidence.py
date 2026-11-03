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


# ---- F1: ACARS flight identity ---------------------------------------------------

@pytest.mark.parametrize("raw,expect", [
    ("JJ1234", ("JJ", "1234")), ("LAP0123", ("LAP", "123")), ("4C0123", ("4C", "123")),
    ("L71234", ("L7", "1234")), ("JJ1234A", ("JJ", "1234")), ("", None), ("JJ", None),
])
def test_split_flight_number(ahr, raw, expect):
    assert ahr.split_flight_number(raw) == expect


def test_pos_loader_keeps_flight_number_and_registration(ahr, tmp_path):
    from tests.conftest import write_acars_pos
    write_acars_pos(tmp_path, "XA-AAA", "JJ1234", T0, -23.5, -46.5, 350, 1)
    write_acars_pos(tmp_path, "CC-BMD", "LA800", T0, -33.0, -70.5, 360, 2)
    unmapped = {}
    idx = ahr.load_pos_points(str(tmp_path), {"XAAAA": "ABC101"}, unmapped=unmapped)
    (tp,) = idx[("ABC101", "2026-10-01")]
    assert (tp.flight_number, tp.registration) == ("JJ1234", "XA-AAA")
    (cc,) = unmapped[("CCBMD", "2026-10-01")]
    assert (cc.flight_number, cc.registration, cc.source) == ("LA800", "CC-BMD", "POS")


def test_pos_loader_skips_unmapped_files_unless_asked(ahr, tmp_path):
    from tests.conftest import write_acars_pos
    write_acars_pos(tmp_path, "CC-BMD", "LA800", T0, -33.0, -70.5, 360, 2)
    assert dict(ahr.load_pos_points(str(tmp_path), {"XAAAA": "ABC101"})) == {}


def _fix(ahr, t, lat, lon, alt_ft=35000, fi=None, reg="XAAAA"):
    p = ahr.TrackPoint(t.strftime("%Y-%m-%dT%H:%M:%SZ"), lon, lat, alt_ft * 0.3048, source="POS")
    p.flight_number, p.registration = fi, reg
    return p


def test_flight_number_from_pos_in_window(ahr, make_segment, net):
    s = _seg(ahr, make_segment, net, T0, 60, (0, 0), (0, 5), orig="AAA", dest="BBB")
    fixes = [_fix(ahr, T0 + timedelta(minutes=m), 0, m / 12, fi="JJ1234") for m in (10, 20, 30)]
    fixes.append(_fix(ahr, T0 + timedelta(minutes=90), 0, 5, fi="JJ7777"))   # after landing
    ahr.assign_flight_numbers([s], {"ABC101": fixes})
    assert (s.flight_number, s.flight_number_source, s.flight_number_conflict) == \
        ("JJ1234", "acars_fi", False)


def test_flight_number_conflict_keeps_majority(ahr, make_segment, net):
    s = _seg(ahr, make_segment, net, T0, 60, (0, 0), (0, 5), orig="AAA", dest="BBB")
    fixes = [_fix(ahr, T0 + timedelta(minutes=m), 0, m / 12, fi="JJ1234") for m in (10, 20, 30)]
    fixes.append(_fix(ahr, T0 + timedelta(minutes=40), 0, 3.3, fi="JJ9999"))
    fixes.append(_fix(ahr, T0 + timedelta(minutes=45), 0, 3.7, fi=None))   # no FI: ignored
    ahr.assign_flight_numbers([s], {"ABC101": fixes})
    assert (s.flight_number, s.flight_number_conflict) == ("JJ1234", True)


def test_no_pos_no_flight_number(ahr, make_segment, net):
    s = _seg(ahr, make_segment, net, T0, 60, (0, 0), (0, 5), orig="AAA", dest="BBB")
    ahr.assign_flight_numbers([s], {})
    assert (s.flight_number, s.flight_number_source, s.flight_number_conflict) == \
        (None, None, False)


# ---- F2: POS before / after the ADS-B ends ----------------------------------------

@pytest.fixture
def ctx(ahr, net):
    return ahr.EvidenceContext(net["airports"], net["routes"], ahr.PRESETS["permissive"],
                               ahr.EvidenceOptions(pos_extend_ends=True))


def _toward_ddd(ahr, minutes, fi="JJ1234", alt_ft=33000, last_low=True):
    """POS fixes AAA->DDD (72 min) at the given minutes; the 72-min one low over DDD."""
    out = []
    for m in minutes:
        low = last_low and m == 72
        out.append(_fix(ahr, T0 + timedelta(minutes=m), 0.0, 6.0 * m / 72,
                        alt_ft=1000 if low else alt_ft, fi=fi))
    return out


def _lost_at_30(ahr, make_segment, net):
    """AAA->DDD whose ADS-B ends 30 min out; 1.4.0 guessed BBB by route time."""
    s = _seg(ahr, make_segment, net, T0, 30, (0, 0), (0, 2.5), orig="AAA", dest="BBB")
    s.dest_source, s.dest_candidates = "route_time", ["BBB", "DDD"]
    return s


def test_extend_to_an_observed_end(ahr, make_segment, net, ctx):
    s = _lost_at_30(ahr, make_segment, net)
    fixes = ahr.FixSeries(_toward_ddd(ahr, range(36, 73, 6)))
    stats = ahr.extend_ends_with_pos(s, fixes, None, None, ctx)
    assert s.destination.code == "DDD" and s.dest_source == "pos_observed"
    assert s.dest_candidates == []
    assert s.points[-1].source == "POS" and s.landing_time == T0 + timedelta(minutes=72)
    assert s.flight_duration == pytest.approx(72.0)
    assert stats["after_km"] == pytest.approx(3.5 * 111.2, rel=0.01)
    assert stats["before_km"] == 0.0
    assert s.evidence_changes == [{"feature": "F2", "endpoint": "dest", "before": "BBB",
                                   "after": "DDD", "source": "pos_observed"}]


def test_extension_stops_at_a_speed_jump(ahr, make_segment, net, ctx):
    s = _lost_at_30(ahr, make_segment, net)
    fixes = _toward_ddd(ahr, range(36, 73, 6))
    fixes[2] = _fix(ahr, fixes[2].datetime, 9.0, 3.5, fi="JJ1234")   # 1,000 km off the line
    ahr.extend_ends_with_pos(s, ahr.FixSeries(fixes), None, None, ctx)
    assert s.landing_time == T0 + timedelta(minutes=42)
    assert s.dest_source != "pos_observed"


def test_extension_stops_at_a_gap(ahr, make_segment, net, ctx):
    s = _lost_at_30(ahr, make_segment, net)
    fixes = _toward_ddd(ahr, [36, 42, 48] + [72])    # 24-min gap is fine...
    ahr.extend_ends_with_pos(s, ahr.FixSeries(fixes), None, None, ctx)
    assert s.dest_source == "pos_observed"
    s2 = _lost_at_30(ahr, make_segment, net)
    ctx.options.pos_extend_max_gap_min = 20.0        # ...but not over a 20-min limit
    ahr.extend_ends_with_pos(s2, ahr.FixSeries(fixes), None, None, ctx)
    assert s2.landing_time == T0 + timedelta(minutes=48)


def test_extension_stops_at_the_neighbour_window(ahr, make_segment, net, ctx):
    s = _lost_at_30(ahr, make_segment, net)
    fixes = ahr.FixSeries(_toward_ddd(ahr, range(36, 73, 6)))
    ahr.extend_ends_with_pos(s, fixes, None, T0 + timedelta(minutes=50), ctx)
    assert s.landing_time == T0 + timedelta(minutes=48)


def test_extension_stops_at_a_flight_number_change(ahr, make_segment, net, ctx):
    s = _lost_at_30(ahr, make_segment, net)
    inside = [_fix(ahr, T0 + timedelta(minutes=20), 0.0, 1.67, fi="JJ1234")]
    after = _toward_ddd(ahr, [36, 42]) + _toward_ddd(ahr, [48, 54], fi="JJ5555")
    ahr.extend_ends_with_pos(s, ahr.FixSeries(inside + after), None, None, ctx)
    assert s.landing_time == T0 + timedelta(minutes=42)


def test_observed_end_is_not_extended(ahr, make_segment, net, ctx):
    s = _seg(ahr, make_segment, net, T0, 72, (0, 0), (0, 6.0), orig="AAA", dest="DDD")
    later = [_fix(ahr, T0 + timedelta(minutes=m), 0.0, 6.0 - (m - 100) / 20, fi="JJ9")
             for m in (100, 106)]
    n = len(s.points)
    ahr.extend_ends_with_pos(s, ahr.FixSeries(later), None, None, ctx)
    assert len(s.points) == n and s.dest_source == "observed"


def test_extension_reruns_the_rescue_on_the_longer_track(ahr, make_segment, net, ctx):
    # POS carries the track to 70 min, still at cruise: no airport seen, but route time
    # on the longer track now fits DDD (72 min) and not BBB (60 min)
    s = _lost_at_30(ahr, make_segment, net)
    fixes = _toward_ddd(ahr, range(36, 71, 6), last_low=False) + \
        [_fix(ahr, T0 + timedelta(minutes=70), 0.0, 5.83, alt_ft=20000, fi="JJ1234")]
    ahr.extend_ends_with_pos(s, ahr.FixSeries(fixes), None, None, ctx)
    assert (s.destination.code, s.dest_source) == ("DDD", "route_time")
    assert s.evidence_changes[0]["after"] == "DDD"


def test_backward_extension_observes_the_origin(ahr, make_segment, net, ctx):
    # ADS-B picks the flight up 40 min out of AAA; POS covers the climb-out
    s = _seg(ahr, make_segment, net, T0 + timedelta(minutes=40), 32, (0, 3.33), (0, 6.0),
             dest="DDD")
    before = [_fix(ahr, T0, 0.0, 0.0, alt_ft=900, fi="JJ1234")] + \
        [_fix(ahr, T0 + timedelta(minutes=m), 0.0, 6.0 * m / 72, fi="JJ1234")
         for m in (6, 12, 18, 24, 30)]
    stats = ahr.extend_ends_with_pos(s, ahr.FixSeries(before), None, None, ctx)
    assert (s.origin.code, s.orig_source) == ("AAA", "pos_observed")
    assert s.takeoff_time == T0 and stats["before_km"] > 300


# ---- F4 / F5 / F8: rotation, planned legs and the evidence order ---------------------

def _leg(ahr, digits, orig, dest, sched, tail="XAAAA", ambiguous=False, key=None,
         carrier="TAM"):
    return ahr.PlannedLeg(key or f"{sched:%Y-%m-%d}_{carrier}{digits}_{orig}-{dest}", "ofp",
                          carrier, f"{carrier}{digits}", digits, tail, orig, dest,
                          sched, ambiguous)


def _legs(*legs):
    by_tail = {}
    for lg in legs:
        by_tail.setdefault(lg.tail, []).append(lg)
    return by_tail


def _guessed_bbb(ahr, make_segment, net, start=T0):
    """AAA->? lost 30 min out; 1.4.0 route time said BBB."""
    s = _seg(ahr, make_segment, net, start, 30, (0, 0), (0, 2.5), orig="AAA", dest="BBB")
    s.registration = "XA-AAA"
    s.dest_source, s.dest_candidates = "route_time", ["BBB", "DDD"]
    return s


def _observed(ahr, make_segment, net, start, a, b, orig, dest, minutes=72):
    s = _seg(ahr, make_segment, net, start, minutes, a, b, orig=orig, dest=dest)
    s.registration = "XA-AAA"
    return s


@pytest.fixture
def rctx(ahr, net):
    return ahr.EvidenceContext(net["airports"], net["routes"], ahr.PRESETS["permissive"],
                               ahr.EvidenceOptions(rotation_endpoints=True))


def test_rotation_from_an_observed_neighbour(ahr, make_segment, net, rctx):
    f1 = _guessed_bbb(ahr, make_segment, net)
    f2 = _observed(ahr, make_segment, net, T0 + timedelta(hours=2), (0, 6), (0, 0), "DDD", "AAA")
    ahr.resolve_endpoints([f1, f2], rctx)
    assert (f1.destination.code, f1.dest_source) == ("DDD", "rotation")
    assert f1.dest_free == ("DDD", "rotation")
    assert f1.evidence_changes == [{"feature": "F4", "endpoint": "dest", "before": "BBB",
                                    "after": "DDD", "source": "rotation"}]
    assert (f2.orig_source, f2.dest_source) == ("observed", "observed")


def test_rotation_chained_from_an_inferred_neighbour(ahr, make_segment, net, rctx):
    f1 = _guessed_bbb(ahr, make_segment, net)
    # picked up 890 km south of AAA, where no route or corridor leads: no rescue
    f2 = _seg(ahr, make_segment, net, T0 + timedelta(hours=2), 60, (-8.0, 0), (0, 0), dest="AAA")
    f2.registration = "XA-AAA"
    ahr.resolve_endpoints([f1, f2], rctx)
    assert (f1.destination.code, f1.dest_source) == ("BBB", "route_time")
    assert (f2.origin.code, f2.orig_source) == ("BBB", "rotation_chained")


def test_rotation_conflict_keeps_observed_ends(ahr, make_segment, net, rctx):
    f1 = _observed(ahr, make_segment, net, T0, (0, 0), (0, 5), "AAA", "BBB", minutes=60)
    f2 = _observed(ahr, make_segment, net, T0 + timedelta(hours=2), (0, 6), (0, 0), "DDD", "AAA")
    ahr.resolve_endpoints([f1, f2], rctx)
    assert f1.destination.code == "BBB" and f2.origin.code == "DDD"
    assert f1.endpoint_conflict and f2.endpoint_conflict


def test_rotation_gap_too_long(ahr, make_segment, net, rctx):
    f1 = _guessed_bbb(ahr, make_segment, net)
    f2 = _observed(ahr, make_segment, net, T0 + timedelta(hours=20), (0, 6), (0, 0), "DDD", "AAA")
    ahr.resolve_endpoints([f1, f2], rctx)
    assert (f1.destination.code, f1.dest_source) == ("BBB", "route_time")
    assert not f1.endpoint_conflict


def test_rotation_off_changes_nothing(ahr, make_segment, net):
    ctx = ahr.EvidenceContext(net["airports"], net["routes"], ahr.PRESETS["permissive"],
                              ahr.EvidenceOptions())
    f1 = _guessed_bbb(ahr, make_segment, net)
    f2 = _observed(ahr, make_segment, net, T0 + timedelta(hours=2), (0, 6), (0, 0), "DDD", "AAA")
    ahr.resolve_endpoints([f1, f2], ctx)
    assert (f1.destination.code, f1.dest_source) == ("BBB", "route_time")


@pytest.fixture
def pctx(ahr, net):
    return ahr.EvidenceContext(net["airports"], net["routes"], ahr.PRESETS["permissive"],
                               ahr.EvidenceOptions(planned_legs=["legs.csv"]))


def test_planned_rule1_flight_number(ahr, make_segment, net, pctx):
    f1 = _guessed_bbb(ahr, make_segment, net)
    f1.flight_number, f1.flight_number_source = "JJ1234", "acars_fi"
    leg = _leg(ahr, "1234", "AAA", "DDD", T0)
    ahr.resolve_endpoints([f1], pctx, _legs(leg))
    assert (f1.destination.code, f1.dest_source) == ("DDD", "planned:ofp:fn")
    assert f1.planned_leg_key == leg.leg_key and f1.planned_rule == 1
    assert f1.dest_free == ("BBB", "route_time")          # what the plan-free logic said
    assert f1.flight_number == "JJ1234"                   # the ACARS number stays


def test_planned_rule2_tail_time_and_one_observed_end(ahr, make_segment, net, pctx):
    f1 = _guessed_bbb(ahr, make_segment, net)
    ahr.resolve_endpoints([f1], pctx, _legs(_leg(ahr, "1234", "AAA", "DDD",
                                                 T0 - timedelta(minutes=30))))
    assert (f1.destination.code, f1.dest_source) == ("DDD", "planned:ofp:tail")
    assert (f1.flight_number, f1.flight_number_source) == ("TAM1234", "planned:ofp")


def test_planned_rule2_needs_a_matching_observed_end(ahr, make_segment, net, pctx):
    f1 = _guessed_bbb(ahr, make_segment, net)
    ahr.resolve_endpoints([f1], pctx, _legs(_leg(ahr, "1234", "CCC", "DDD", T0)))
    assert f1.dest_source == "route_time" and f1.planned_leg_key is None


def test_planned_ambiguous_row_never_matches(ahr, make_segment, net, pctx):
    f1 = _guessed_bbb(ahr, make_segment, net)
    f1.flight_number, f1.flight_number_source = "JJ1234", "acars_fi"
    ahr.resolve_endpoints([f1], pctx, _legs(_leg(ahr, "1234", "AAA", "DDD", T0,
                                                 ambiguous=True)))
    assert f1.dest_source == "route_time" and f1.planned_leg_key is None


def test_planned_leg_claimed_by_two_flights_is_not_used(ahr, make_segment, net, pctx):
    f1 = _guessed_bbb(ahr, make_segment, net)
    f2 = _guessed_bbb(ahr, make_segment, net, start=T0 + timedelta(hours=3))
    ahr.resolve_endpoints([f1, f2], pctx, _legs(_leg(ahr, "1234", "AAA", "DDD", T0)))
    assert f1.planned_leg_key is None and f2.planned_leg_key is None


def test_planned_leg_outside_the_window(ahr, make_segment, net, pctx):
    f1 = _guessed_bbb(ahr, make_segment, net)
    f1.flight_number, f1.flight_number_source = "JJ1234", "acars_fi"
    ahr.resolve_endpoints([f1], pctx, _legs(_leg(ahr, "1234", "AAA", "DDD",
                                                 T0 + timedelta(minutes=61))))
    assert f1.planned_leg_key is None


def test_planned_never_overrides_an_observed_end(ahr, make_segment, net, pctx):
    f1 = _observed(ahr, make_segment, net, T0, (0, 0), (0, 5), "AAA", "BBB", minutes=60)
    f1.flight_number, f1.flight_number_source = "JJ1234", "acars_fi"
    ahr.resolve_endpoints([f1], pctx, _legs(_leg(ahr, "1234", "AAA", "DDD", T0)))
    assert (f1.destination.code, f1.dest_source) == ("BBB", "observed")
    assert f1.endpoint_conflict and f1.planned_leg_key is not None


def test_order_rule1_beats_rotation(ahr, make_segment, net):
    ctx = ahr.EvidenceContext(net["airports"], net["routes"], ahr.PRESETS["permissive"],
                              ahr.EvidenceOptions(rotation_endpoints=True, planned_legs=["x"]))
    f1 = _guessed_bbb(ahr, make_segment, net)
    f1.flight_number, f1.flight_number_source = "JJ1234", "acars_fi"
    f2 = _observed(ahr, make_segment, net, T0 + timedelta(hours=2), (4, 2), (0, 0), "CCC", "AAA")
    ahr.resolve_endpoints([f1, f2], ctx, _legs(_leg(ahr, "1234", "AAA", "DDD", T0)))
    assert (f1.destination.code, f1.dest_source) == ("DDD", "planned:ofp:fn")
    assert f1.dest_free == ("CCC", "rotation")


def test_order_rotation_beats_rule2(ahr, make_segment, net):
    ctx = ahr.EvidenceContext(net["airports"], net["routes"], ahr.PRESETS["permissive"],
                              ahr.EvidenceOptions(rotation_endpoints=True, planned_legs=["x"]))
    f1 = _guessed_bbb(ahr, make_segment, net)
    f2 = _observed(ahr, make_segment, net, T0 + timedelta(hours=2), (4, 2), (0, 0), "CCC", "AAA")
    ahr.resolve_endpoints([f1, f2], ctx, _legs(_leg(ahr, "1234", "AAA", "DDD", T0)))
    assert (f1.destination.code, f1.dest_source) == ("CCC", "rotation")


def test_no_number_no_leg_flight_is_unchanged(ahr, make_segment, net):
    ctx = ahr.EvidenceContext(net["airports"], net["routes"], ahr.PRESETS["permissive"],
                              ahr.EvidenceOptions(acars_identity=True, planned_legs=["x"]))
    f1 = _guessed_bbb(ahr, make_segment, net)
    before = (f1.origin.code, f1.destination.code, f1.orig_source, f1.dest_source,
              list(f1.dest_candidates), len(f1.points), f1.takeoff_time, f1.landing_time)
    ahr.assign_flight_numbers([f1], {})
    ahr.resolve_endpoints([f1], ctx, _legs(_leg(ahr, "1234", "CCC", "DDD", T0)))
    assert (f1.origin.code, f1.destination.code, f1.orig_source, f1.dest_source,
            list(f1.dest_candidates), len(f1.points), f1.takeoff_time, f1.landing_time) == before
    assert f1.flight_number is None and f1.planned_leg_key is None
    assert not f1.endpoint_conflict


def test_load_planned_legs(ahr, tmp_path):
    a = tmp_path / "a.csv"
    a.write_text("leg_key,source,carrier_icao,flight_number,flight_digits,tail,orig,dest,"
                 "sched_dep_utc,ofp_date,ambiguous\n"
                 "k1,ofp,TAM,TAM3036,3036,PRMAG,SBSP,SBCF,2026-07-14T11:00:00Z,2026-07-14,0\n"
                 "k2,ofp,TAM,TAM0036,0036,PR-MAG,SBCF,SBSP,2026-07-15T11:00:00Z,2026-07-15,1\n")
    b = tmp_path / "b.csv"
    b.write_text(a.read_text().splitlines()[0] + "\n"
                 "k3,cirium_flight,LAN,LAN800,800,CCBMD,SCEL,SAEZ,2026-07-16T01:00:00Z,,0\n")
    by_tail, info = ahr.load_planned_legs([str(a), str(b)])
    assert [lg.leg_key for lg in by_tail["PRMAG"]] == ["k1", "k2"]
    assert by_tail["PRMAG"][1].digits == "36" and by_tail["PRMAG"][1].ambiguous
    assert by_tail["CCBMD"][0].source == "cirium_flight"
    assert info == [{"file": "a.csv", "rows": 2, "from": "2026-07-14", "to": "2026-07-15"},
                    {"file": "b.csv", "rows": 1, "from": "2026-07-16", "to": "2026-07-16"}]


# ---- F3: POS-only flights; F9: dropped segments; the whole stage --------------------

def _session(ahr, start, a, b, minutes=60, step=6, fi="JJ1236", reg="XA-AAA"):
    """POS fixes a->b, low over both airports."""
    out = []
    for m in range(0, minutes + 1, step):
        f = m / minutes
        alt = 1000 if m in (0, minutes) else 33000
        out.append(_fix(ahr, start + timedelta(minutes=m), a[0] + (b[0] - a[0]) * f,
                        a[1] + (b[1] - a[1]) * f, alt_ft=alt, fi=fi, reg=reg))
    return out


def test_pos_sessions_split_at_ground_stop_and_gap(ahr, ctx):
    fixes = (_session(ahr, T0, (0, 0), (4, 2)) +
             _session(ahr, T0 + timedelta(minutes=100), (4, 2), (0, 0)) +   # 40 min on ground
             _session(ahr, T0 + timedelta(hours=6), (0, 0), (0, 5)))
    sessions = ahr.pos_sessions(fixes, ctx)
    assert [len(x) for x in sessions] == [11, 11, 11]


def test_pos_sessions_split_at_a_flight_number_change(ahr, ctx):
    a = _session(ahr, T0, (0, 0), (4, 2))
    b = _session(ahr, T0 + timedelta(minutes=66), (4, 2), (0, 0), fi="JJ1237")
    b[0] = _fix(ahr, b[0].datetime, 3.9, 2.0, alt_ft=20000, fi="JJ1237")   # no ground stop seen
    assert [len(x) for x in ahr.pos_sessions(a + b, ctx)] == [11, 11]


def _stage(ahr, net, **opts):
    return ahr.EvidenceContext(net["airports"], net["routes"], ahr.PRESETS["permissive"],
                               ahr.EvidenceOptions(**opts))


def test_session_becomes_a_pos_only_flight(ahr, net):
    ctx = _stage(ahr, net, pos_only_flights=True)
    pos = {"ABC101": ahr.FixSeries(_session(ahr, T0, (0, 0), (4, 2)))}
    flights, report = ahr.run_endpoint_evidence({}, {}, pos, ctx, mapped={"ABC101"},
                                                registrations={"ABC101": "XA-AAA"})
    (f,) = flights
    assert (f.origin.code, f.destination.code) == ("AAA", "CCC")
    assert (f.orig_source, f.dest_source, f.disposition) == ("pos_observed", "pos_observed",
                                                              "pos_only")
    assert f.aircraft_id == "ABC101" and f.registration == "XA-AAA" and f.hex_known
    assert {p.source for p in f.points} == {"POS"} and len(f.points) == 11
    assert report["pos_only"]["kept"] == 1


def test_unmapped_fleet_only_with_all(ahr, net):
    pos = {"CCBMD": ahr.FixSeries(_session(ahr, T0, (0, 0), (0, 5), fi="LA800",
                                           reg="CC-BMD"))}
    flights, _ = ahr.run_endpoint_evidence({}, {}, pos, _stage(ahr, net, pos_only_flights=True),
                                           mapped=set(), registrations={})
    assert flights == []
    flights, report = ahr.run_endpoint_evidence(
        {}, {}, pos, _stage(ahr, net, pos_only_flights=True, pos_fleets="all"),
        mapped=set(), registrations={})
    (f,) = flights
    assert (f.aircraft_id, f.registration, f.hex_known) == ("CCBMD", "CC-BMD", False)
    assert report["pos_only"]["by_prefix"] == {"CC": 1}


def test_pos_inside_an_adsb_flight_is_not_a_pos_only_flight(ahr, make_segment, net):
    s = _seg(ahr, make_segment, net, T0, 60, (0, 0), (4, 2), orig="AAA", dest="CCC")
    s.disposition = "kept_complete"
    pos = {"ABC101": ahr.FixSeries(_session(ahr, T0, (0, 0), (4, 2)))}
    flights, _ = ahr.run_endpoint_evidence({"ABC101": [s]}, {}, pos,
                                           _stage(ahr, net, pos_only_flights=True),
                                           mapped={"ABC101"}, registrations={})
    assert flights == [s]


def _dropped_no_airport(ahr, make_segment, net):
    """ADS-B saw only 20 min of cruise between AAA and DDD: 1.4.0 dropped it."""
    s = _seg(ahr, make_segment, net, T0 + timedelta(minutes=25), 20, (0, 2.08), (0, 3.75))
    s.disposition = "dropped_unjoined"
    row = {"disposition": "dropped_unjoined", "drop_reason": "no_airport_match"}
    return s, row


def test_dropped_segment_recovered_by_pos(ahr, make_segment, net):
    seg, row = _dropped_no_airport(ahr, make_segment, net)
    pos = {"ABC101": ahr.FixSeries(_toward_ddd(ahr, [0, 6, 12, 18, 50, 56, 62, 68, 72]))}
    pos["ABC101"][0] = _fix(ahr, T0, 0.0, 0.0, alt_ft=900, fi="JJ1234")
    ctx = _stage(ahr, net, pos_extend_ends=True, recover_dropped=True)
    flights, report = ahr.run_endpoint_evidence({}, {"ABC101": [(seg, row)]}, pos, ctx,
                                                mapped={"ABC101"}, registrations={})
    assert flights == [seg]
    assert (seg.origin.code, seg.destination.code) == ("AAA", "DDD")
    assert seg.disposition == "kept_recovered"
    assert row["disposition"] == "kept_recovered" and row["drop_reason"] == ""
    assert report["recovered"] == {"no_airport_match": 1}
    assert report["still_dropped"] == {}


def test_dropped_segment_without_evidence_stays_dropped(ahr, make_segment, net):
    seg, row = _dropped_no_airport(ahr, make_segment, net)
    ctx = _stage(ahr, net, pos_extend_ends=True, recover_dropped=True)
    flights, report = ahr.run_endpoint_evidence({}, {"ABC101": [(seg, row)]}, {}, ctx,
                                                mapped={"ABC101"}, registrations={})
    assert flights == [] and row["disposition"] == "dropped_unjoined"
    assert report["still_dropped"] == {"no_airport_match": 1}


def test_dropped_segment_not_recovered_when_flag_off(ahr, make_segment, net):
    seg, row = _dropped_no_airport(ahr, make_segment, net)
    pos = {"ABC101": ahr.FixSeries(_toward_ddd(ahr, [0, 6, 12, 18, 50, 56, 62, 68, 72]))}
    pos["ABC101"][0] = _fix(ahr, T0, 0.0, 0.0, alt_ft=900, fi="JJ1234")
    flights, _ = ahr.run_endpoint_evidence({}, {"ABC101": [(seg, row)]}, pos,
                                           _stage(ahr, net, pos_extend_ends=True),
                                           mapped={"ABC101"}, registrations={})
    assert flights == [] and seg.origin is None


def test_kept_flight_made_invalid_by_rotation_keeps_its_1_4_0_ends(ahr, make_segment, net):
    # origin inferred AAA; the next flight leaves from AAA too, so rotation would make it
    # AAA->AAA: the flight keeps the 1.4.0 answer instead of being lost
    s = _seg(ahr, make_segment, net, T0, 50, (0, 1.0), (0, 5.0), dest="BBB")
    s.origin, s.orig_source, s.is_complete = net["by_code"]["AAA"], "route_time", True
    s.destination, s.dest_source = net["by_code"]["BBB"], "route_time"
    s.disposition = "kept_rescued"
    nxt = _seg(ahr, make_segment, net, T0 + timedelta(hours=2), 60, (0, 0), (0, 5),
               orig="AAA", dest="BBB")
    nxt.disposition = "kept_complete"
    prev = _seg(ahr, make_segment, net, T0 - timedelta(hours=2), 60, (0, 5), (0, 0),
                orig="BBB", dest="AAA")
    prev.disposition = "kept_complete"
    flights, _ = ahr.run_endpoint_evidence({"ABC101": [prev, s, nxt]}, {}, {},
                                           _stage(ahr, net, rotation_endpoints=True),
                                           mapped={"ABC101"}, registrations={})
    assert s in flights
    assert (s.origin.code, s.destination.code) == ("AAA", "BBB")
    assert (s.orig_source, s.dest_source) == ("route_time", "route_time")
    assert s.endpoint_conflict


def test_planned_leg_airport_not_in_the_list_falls_through(ahr, make_segment, net, pctx):
    f1 = _guessed_bbb(ahr, make_segment, net)
    f1.flight_number, f1.flight_number_source = "JJ1234", "acars_fi"
    ahr.resolve_endpoints([f1], pctx, _legs(_leg(ahr, "1234", "AAA", "ZZZ", T0)))
    assert (f1.destination.code, f1.dest_source) == ("BBB", "route_time")
    assert f1.planned_leg_key is not None and f1.is_complete


def test_report_follows_the_kept_flights(ahr, net):
    ctx = _stage(ahr, net, pos_only_flights=True)
    pos = {"ABC101": ahr.FixSeries(_session(ahr, T0, (0, 0), (4, 2)) +
                                   _session(ahr, T0 + timedelta(days=2), (0, 0), (4, 2)))}
    flights, report = ahr.run_endpoint_evidence({}, {}, pos, ctx, mapped={"ABC101"},
                                                registrations={})
    assert report["pos_only"]["kept"] == 2
    kept = [f for f in flights if f.takeoff_date_str == "2026-10-01"]
    again = ahr.build_evidence_report(kept, report["dropped_items"], "2026-10-01", "2026-10-01")
    assert again["pos_only"]["kept"] == 1 and again["pos_only"]["by_month"] == {"2026-10": 1}
