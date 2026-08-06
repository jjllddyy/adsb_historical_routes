"""Tests for --stitch-gaps: inferred straight-line connectors across coverage gaps.

Default output is unchanged (one track per flight); enabling stitching splits a flight at
its coverage gaps into real-data placemarks (normal colour) plus one gray connector per gap.
"""

from datetime import timedelta


def _gapped_segment(ahr, make_track_point, utc):
    # 5 points, a 31-minute coverage gap, then 5 more points (one flight, A -> B)
    pts = [make_track_point(utc + timedelta(minutes=i), 0.0, i * 0.1, 10000.0) for i in range(5)]
    pts += [make_track_point(utc + timedelta(minutes=36 + i), 0.0, 1.0 + i * 0.1, 10000.0) for i in range(5)]
    return ahr.FlightSegment("AC1", "AC1", pts,
                             ahr.Airport("A", 0.0, 0.0, 0), ahr.Airport("B", 0.0, 2.0, 0),
                             "ff0000ff", "2", "ff", "t.kml")


def test_default_output_is_single_track_no_connector(ahr, make_track_point, utc, tmp_path):
    seg = _gapped_segment(ahr, make_track_point, utc)
    out = str(tmp_path / "d.kml")
    ahr.create_output_kml([seg], out, sample_minutes=0)  # sample_minutes<=0 keeps all points
    txt = open(out).read()
    assert txt.count("<Placemark") == 1          # one track for the whole flight (gap drawn implicitly)
    assert "ff888888" not in txt                 # no inferred connectors by default
    assert "Inferred gap connector" not in txt


def test_stitch_splits_and_adds_gray_connector(ahr, make_track_point, utc, tmp_path):
    seg = _gapped_segment(ahr, make_track_point, utc)
    out = str(tmp_path / "s.kml")
    ahr.create_output_kml([seg], out, sample_minutes=0,
                          stitch_gaps=True, inferred_color="ff888888", stitch_gap_min=5.0)
    txt = open(out).read()
    # two real runs (either side of the gap) + one connector placemark
    assert txt.count("<Placemark") == 3
    assert txt.count("ff888888") == 1            # exactly one inferred connector, in gray
    assert "Inferred gap connector" in txt
    # the real runs keep the segment colour
    assert "ff0000ff" in txt


def test_stitch_custom_color_and_threshold(ahr, make_track_point, utc, tmp_path):
    seg = _gapped_segment(ahr, make_track_point, utc)
    # threshold above the gap -> no split, no connector
    out1 = str(tmp_path / "hi.kml")
    ahr.create_output_kml([seg], out1, sample_minutes=0, stitch_gaps=True, stitch_gap_min=60.0)
    assert open(out1).read().count("<Placemark") == 1
    # custom connector colour flows through
    out2 = str(tmp_path / "c.kml")
    ahr.create_output_kml([seg], out2, sample_minutes=0, stitch_gaps=True,
                          inferred_color="ffaaaaaa", stitch_gap_min=5.0)
    assert "ffaaaaaa" in open(out2).read()
