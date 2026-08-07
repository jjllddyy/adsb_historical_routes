"""Tests for --integrate-pos: extra position sources (ACARS POS, ADS-B firehose).

Covers the loaders (tail<->hex mapping, POS/firehose parsing), the source-aware merge
(ADS-B authoritative, extras fill gaps), and source-marked rendering (POS/firehose track
portions drawn in their own colour; real gap-fill data is NOT an inferred connector).
"""

import json
from datetime import timedelta


# ---- loaders --------------------------------------------------------------

def test_load_tail_to_hex_normalizes(ahr, tmp_path):
    p = tmp_path / "map.csv"
    p.write_text("tail_number,icao_hex\nCC-DIK,e80624\nCC AWA,E80600\n")
    m = ahr.load_tail_to_hex(str(p))
    assert m["CCDIK"] == "E80624"      # punctuation stripped, hex upper-cased
    assert m["CCAWA"] == "E80600"


def test_load_pos_points_parses_urlencoded_body(ahr, tmp_path):
    tail_map = {"CCDIK": "E80624"}
    body = ("JetSMART_Position=" +
            '[{"tail_number":"CC-DIK","created_at":"2026-07-08 06:40:54",'
            '"freetext":"064054,35000,120,S 33.5690 W 060.0890"}]')
    # SQS wrapper with a URL-encoded body
    import urllib.parse
    wrapped = {"body": urllib.parse.quote_plus(body)}
    f = tmp_path / "msg.json"
    f.write_text(json.dumps(wrapped))
    pts = ahr.load_pos_points(str(tmp_path), tail_map)
    key = ("E80624", "2026-07-08")
    assert key in pts
    tp = pts[key][0]
    assert tp.source == "POS"
    assert tp.lat < 0 and tp.lon < 0            # S / W -> negative
    assert abs(tp.lat + 33.569) < 1e-3
    assert abs(tp.alt - 35000 * 0.3048) < 1.0   # ft -> m


def test_load_firehose_points_uses_icao_and_unix_ts(ahr, tmp_path):
    f = tmp_path / "CCDIK_153029.json"
    f.write_text(json.dumps({"icao": "e80624", "timestamp": 1782024629,
                             "latitude": 7.358, "longitude": -73.578, "altitude": 38000}))
    pts = ahr.load_firehose_points(str(tmp_path))
    assert len(pts) == 1
    (hexc, _date), lst = next(iter(pts.items()))
    assert hexc == "E80624"
    tp = lst[0]
    assert tp.source == "FIREHOSE"
    assert abs(tp.alt - 38000 * 0.3048) < 1.0


# ---- merge ----------------------------------------------------------------

def test_merge_keeps_adsb_and_fills_gaps(ahr, make_track_point, utc):
    adsb = [make_track_point(utc + timedelta(minutes=i), 0.0, i * 0.1, 10000.0) for i in range(3)]
    # one extra fix inside the (later) gap, one nearly coincident with an ADS-B fix
    gap_fill = make_track_point(utc + timedelta(minutes=20), 0.0, 5.0, 11000.0)
    gap_fill.source = "POS"
    dup = make_track_point(utc + timedelta(seconds=5), 0.0, 0.0, 10000.0)
    dup.source = "POS"
    merged = ahr.merge_track_with_sources(adsb, [gap_fill, dup], dedup_seconds=30.0)
    sources = [p.source for p in merged]
    assert sources.count("POS") == 1               # near-duplicate dropped, gap-fill kept
    assert "POS" in sources and "ADSB" in sources
    assert merged == sorted(merged, key=lambda p: p.datetime)  # chronological


# ---- enrichment (post-detection, per-flight, interior only) ---------------

def test_enrich_fills_interior_gap_only(ahr, make_track_point, utc):
    # a detected flight A->B with a 30-min interior ADS-B gap
    pts = [make_track_point(utc + timedelta(minutes=i), 0.0, i * 0.1, 10000.0) for i in range(3)]
    pts += [make_track_point(utc + timedelta(minutes=33 + i), 0.0, 0.6 + i * 0.1, 10000.0) for i in range(3)]
    seg = ahr.FlightSegment("E80600", "CCAWA", pts,
                            ahr.Airport("A", 0.0, 0.0, 0), ahr.Airport("B", 0.0, 2.0, 0),
                            "ff0000ff", "2", "ff", "t.kml")
    d = seg.takeoff_time.strftime("%Y-%m-%d")
    inside = make_track_point(seg.takeoff_time + timedelta(minutes=15), 0.0, 0.4, 11000.0)
    inside.source = "POS"
    outside = make_track_point(seg.landing_time + timedelta(minutes=90), 0.0, 5.0, 11000.0)  # different flight
    outside.source = "POS"
    pos_index = {("E80600", d): [inside, outside]}
    n_flights, n_fixes = ahr.enrich_segments_with_sources([seg], pos_index, min_fill_gap_seconds=300.0)
    assert (n_flights, n_fixes) == (1, 1)              # only the in-window fix used
    assert any(p.source == "POS" for p in seg.points)
    assert seg.takeoff_time == pts[0].datetime          # endpoints (origin/dest) unchanged
    assert seg.points[0].source == "ADSB" and seg.points[-1].source == "ADSB"


def test_enrich_noop_without_index(ahr, make_track_point, utc):
    pts = [make_track_point(utc + timedelta(minutes=i), 0.0, i * 0.1, 10000.0) for i in range(4)]
    seg = ahr.FlightSegment("E80600", "CCAWA", pts, ahr.Airport("A", 0.0, 0.0, 0),
                            ahr.Airport("B", 0.0, 2.0, 0), "ff0000ff", "2", "ff", "t.kml")
    assert ahr.enrich_segments_with_sources([seg], {}, 300.0) == (0, 0)
    assert len(seg.points) == 4


# ---- rendering ------------------------------------------------------------

def _mixed_segment(ahr, make_track_point, utc):
    pts = [make_track_point(utc + timedelta(minutes=i), 0.0, i * 0.1, 10000.0) for i in range(3)]
    fill = make_track_point(utc + timedelta(minutes=4), 0.0, 0.5, 11000.0)
    fill.source = "POS"
    pts.append(fill)
    pts += [make_track_point(utc + timedelta(minutes=5 + i), 0.0, 0.6 + i * 0.1, 10000.0) for i in range(3)]
    return ahr.FlightSegment("AC1", "AC1", pts,
                             ahr.Airport("A", 0.0, 0.0, 0), ahr.Airport("B", 0.0, 2.0, 0),
                             "ff0000ff", "2", "ff", "t.kml")


def test_mark_sources_colors_pos_portion(ahr, make_track_point, utc, tmp_path):
    seg = _mixed_segment(ahr, make_track_point, utc)
    out = str(tmp_path / "m.kml")
    ahr.create_output_kml([seg], out, sample_minutes=0, mark_sources=True,
                          pos_color="ff00a5ff")
    txt = open(out).read()
    assert "ff00a5ff" in txt          # POS portion drawn in its own colour
    assert "ff0000ff" in txt          # ADS-B portion keeps segment colour
    assert txt.count("<Placemark") >= 2


def test_no_mark_sources_keeps_single_track(ahr, make_track_point, utc, tmp_path):
    seg = _mixed_segment(ahr, make_track_point, utc)
    out = str(tmp_path / "s.kml")
    ahr.create_output_kml([seg], out, sample_minutes=0)   # feature off -> unchanged
    txt = open(out).read()
    assert txt.count("<Placemark") == 1
    assert "ff00a5ff" not in txt
