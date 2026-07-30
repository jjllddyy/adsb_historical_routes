"""Tests for multi-hop joining (reassembling flights split into 3+ fragments).

Single-hop joining can only stitch two adjacent fragments per pass, so a flight broken
into three pieces by two mid-cruise ADS-B dropouts is left incomplete and dropped.
Multi-hop joining lets a merged segment keep absorbing further adjacent fragments in one
pass; every hop is still gated by the same join Cases.
"""

from datetime import timedelta

from dataclasses import replace


def _three_fragments(make_segment, utc):
    """A -> B flight along the equator, split into three consecutive in-corridor pieces."""
    f1 = make_segment("AC9", utc, duration_min=40,
                      start_lat=0.0, start_lon=0.0,
                      end_lat=0.0, end_lon=3.0,
                      origin_code="A")                      # origin known, dest missing
    f2 = make_segment("AC9", utc + timedelta(minutes=50), duration_min=40,
                      start_lat=0.0, start_lon=3.1,
                      end_lat=0.0, end_lon=6.0)             # both ends airborne (NONE-NONE)
    f3 = make_segment("AC9", utc + timedelta(minutes=100), duration_min=40,
                      start_lat=0.0, start_lon=6.1,
                      end_lat=0.0, end_lon=10.0,
                      dest_code="B")                        # dest known, origin missing
    return [f1, f2, f3]


class TestMultiHopJoin:
    def test_three_fragments_reassemble_under_balanced(self, ahr, make_segment, utc):
        segs = _three_fragments(make_segment, utc)
        # Empty routes_dict: force the reassembly to happen via joining, not route-time.
        out = ahr.combine_segments_intelligently(
            segs, airports=[], routes_dict={}, preset=ahr.PRESETS["balanced"]
        )
        complete = [s for s in out if s.origin and s.destination]
        assert len(complete) == 1
        assert complete[0].origin.code == "A"
        assert complete[0].destination.code == "B"
        # All three fragments' points ended up in the single reassembled flight.
        assert len(complete[0].points) == sum(len(s.points) for s in segs)

    def test_single_hop_leaves_third_fragment_unjoined(self, ahr, make_segment, utc):
        segs = _three_fragments(make_segment, utc)
        # Same knobs as balanced but multi-hop disabled: the chain cannot fully close.
        single_hop = replace(ahr.PRESETS["balanced"], name="single_hop", multi_hop_join=False,
                             geometry_rescue=False)
        out = ahr.combine_segments_intelligently(
            segs, airports=[], routes_dict={}, preset=single_hop
        )
        complete = [s for s in out if s.origin and s.destination
                    and s.origin.code == "A" and s.destination.code == "B"]
        assert len(complete) == 0

    def test_multi_hop_still_stops_at_a_real_flight_boundary(self, ahr, make_segment, utc):
        # Two genuine sequential flights A->B and B->D sharing airport B must NOT be
        # merged, even with multi-hop enabled (Case 3: dest == next origin).
        a = make_segment("AC10", utc, duration_min=60,
                        start_lat=0.0, start_lon=0.0,
                        end_lat=0.0, end_lon=5.0,
                        origin_code="A", dest_code="B")
        b = make_segment("AC10", utc + timedelta(hours=2), duration_min=60,
                        start_lat=0.0, start_lon=5.0,
                        end_lat=0.0, end_lon=10.0,
                        origin_code="B", dest_code="D")
        out = ahr.combine_segments_intelligently(
            [a, b], airports=[], routes_dict={}, preset=ahr.PRESETS["balanced"]
        )
        assert len(out) == 2
