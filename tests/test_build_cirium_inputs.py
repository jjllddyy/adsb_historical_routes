import os, sys, importlib.util
import pytest

AL_DIR = os.path.expanduser("~/Git/conversion_tools/airport_lookup")
_HAS_AL = os.path.exists(os.path.join(AL_DIR, "airport_converter.py"))
al_required = pytest.mark.skipif(not _HAS_AL, reason="airport_lookup not available")


@al_required
def test_airport_lookup_exposes_coordinates():
    if AL_DIR not in sys.path:
        sys.path.insert(0, AL_DIR)
    from airport_converter import AirportDatabase
    db = AirportDatabase(cache_dir=os.path.join(AL_DIR, ".airport_cache"), auto_update=False)
    db.load_database(min_type="small_airport")
    rec = db.convert_iata_to_icao("GRU")
    assert rec is not None and rec["icao"] == "SBGR"
    assert rec.get("latitude") not in (None, "")
    assert rec.get("longitude") not in (None, "")
    assert rec.get("elevation_ft") not in (None, "")
