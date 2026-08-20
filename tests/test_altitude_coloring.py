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
