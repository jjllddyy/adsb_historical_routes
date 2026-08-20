# tests/test_altitude_coloring.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import adsb_historical_routes as m


def test_hsl_to_rgb_primaries():
    assert m.hsl_to_rgb(0, 100, 50) == (255, 0, 0)      # red
    assert m.hsl_to_rgb(120, 100, 50) == (0, 255, 0)    # green
    assert m.hsl_to_rgb(240, 100, 50) == (0, 0, 255)    # blue
    assert m.hsl_to_rgb(60, 100, 50) == (255, 255, 0)   # yellow


def test_alt_hue_monotonic_and_clamped():
    assert m.alt_hue(-500) == 20            # clamp low
    assert m.alt_hue(50000) == 300          # clamp high
    hues = [m.alt_hue(a) for a in (0, 4000, 10000, 20000, 30000, 40000)]
    assert hues == sorted(hues)             # non-decreasing (red -> magenta)


def test_rgb_to_kml_is_aabbggrr():
    assert m.rgb_to_kml(255, 0, 0) == "ff0000ff"        # red -> A=ff B=00 G=00 R=ff
    assert m.rgb_to_kml(0, 0, 255) == "ffff0000"        # blue


def test_alt_band_color_low_is_warm_high_is_cool():
    low = m.alt_band_color(m.alt_band_index(300 * 0.3048, 24), 24)    # ~300 ft
    high = m.alt_band_color(m.alt_band_index(38000 * 0.3048, 24), 24) # ~38000 ft
    lr, lg, lb = int(low[6:8], 16), int(low[4:6], 16), int(low[2:4], 16)
    hr, hg, hb = int(high[6:8], 16), int(high[4:6], 16), int(high[2:4], 16)
    assert lr > lb          # low altitude reddish/warm
    assert hb > hr or hr > hg  # high altitude cool/magenta (blue or magenta dominant)


from datetime import datetime, timedelta, timezone


def _climb_segment(m):
    base = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    pts = []
    for i in range(12):
        ts = (base + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pts.append(m.TrackPoint(ts, -80.0 + i * 0.1, 0.0 + i * 0.1, i * 1000.0))  # climb 0->11000m
    return m.FlightSegment("AC1", "AC1", pts,
                           m.Airport("A", 0.0, -80.0, 0), m.Airport("B", 1.1, -78.9, 0),
                           "ff0000ff", "2", "ff", "t.kml")


def test_altitude_mode_emits_multiple_band_colors(tmp_path):
    seg = _climb_segment(m)
    out = str(tmp_path / "alt.kml")
    m.create_output_kml([seg], out, sample_minutes=0, color_mode="altitude", altitude_bands=24)
    txt = open(out).read()
    import re
    colors = set(re.findall(r"<color>([0-9a-f]{8})</color>", txt))
    assert len(colors) >= 3           # a climbing track spans several bands


def test_source_mode_default_single_track(tmp_path):
    seg = _climb_segment(m)
    out = str(tmp_path / "src.kml")
    m.create_output_kml([seg], out, sample_minutes=0)   # color_mode defaults to source
    txt = open(out).read()
    assert txt.count("<Placemark") == 1   # unchanged single-track behavior
