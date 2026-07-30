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


def test_parse_number_strips_commas(bci):
    assert bci.parse_number('"5,100"') == 5100.0
    assert bci.parse_number("28,440") == 28440.0
    assert bci.parse_number("30") == 30.0
    assert bci.parse_number("") is None
    assert bci.parse_number("   ") is None
    assert bci.parse_number("n/a") is None


def test_read_cirium_with_report_source(bci, tmp_path):
    p = tmp_path / "src.csv"
    p.write_text(
        "report_source,Mkt Al,Op Al,Orig,Dest,Manu,Type,Aircraft Family,"
        "Aircraft Type,Equip,Flights,Seats,ASMs,Block Mins\n"
        '2026-06,LA,JJ,aep,gig,Airbus,Narrow,A320,A320,320,30,"5,220","6,389,280","5,100"\n'
    )
    rows, has_rs = bci.read_cirium(str(p))
    assert has_rs is True
    assert len(rows) == 1
    r = rows[0]
    assert (r["orig"], r["dest"]) == ("AEP", "GIG")
    assert r["report_source"] == "2026-06"
    assert r["flights"] == 30.0 and r["block_mins"] == 5100.0


def test_read_cirium_without_report_source(bci, tmp_path):
    p = tmp_path / "src.csv"
    p.write_text(
        "Mkt Al,Op Al,Orig,Dest,Manu,Type,Aircraft Family,Aircraft Type,"
        "Equip,Flights,Seats,ASMs,Block Mins\n"
        "LA,JJ,GRU,SCL,Airbus,Narrow,A320,A320,320,100,17000,1000000,18000\n"
    )
    rows, has_rs = bci.read_cirium(str(p))
    assert has_rs is False
    assert rows[0]["report_source"] == ""
    assert rows[0]["orig"] == "GRU" and rows[0]["flights"] == 100.0


def _row(orig, dest, flights, block, rs=""):
    return {"report_source": rs, "orig": orig, "dest": dest,
            "flights": flights, "block_mins": block}


def test_aggregate_single_group_pools_aircraft(bci):
    # Two aircraft types, same route/no report month: Σblock/Σflights.
    rows = [_row("GRU", "SCL", 10, 1000), _row("GRU", "SCL", 10, 1400)]
    times, dropped = bci.aggregate_route_times(rows)
    assert times[("GRU", "SCL")] == (1000 + 1400) / (10 + 10)  # 120.0
    assert dropped == []


def test_aggregate_max_across_months(bci):
    # June avg = 100, July avg = 130 -> take the larger (130).
    rows = [
        _row("AEP", "GIG", 10, 1000, rs="2026-06"),
        _row("AEP", "GIG", 10, 1300, rs="2026-07"),
    ]
    times, _ = bci.aggregate_route_times(rows)
    assert times[("AEP", "GIG")] == 130.0


def test_aggregate_pools_within_month_then_maxes(bci):
    # 2026-06 pooled avg = (1000+500)/(10+10)=75 ; 2026-07 = 2000/20=100 -> 100.
    rows = [
        _row("A", "B", 10, 1000, rs="2026-06"),
        _row("A", "B", 10, 500, rs="2026-06"),
        _row("A", "B", 20, 2000, rs="2026-07"),
    ]
    times, _ = bci.aggregate_route_times(rows)
    assert times[("A", "B")] == 100.0


def test_aggregate_skips_zero_flights_and_self_loops(bci):
    rows = [
        _row("A", "B", 0, 500),      # zero flights -> skipped
        _row("A", "B", 5, 400),      # valid -> 80
        _row("C", "C", 5, 400),      # self loop -> skipped, not seen as route
    ]
    times, dropped = bci.aggregate_route_times(rows)
    assert times == {("A", "B"): 80.0}
    assert ("C", "C") not in times


def test_aggregate_route_with_no_valid_flights_dropped(bci):
    rows = [_row("A", "B", 0, 500), _row("A", "B", None, 500)]
    times, dropped = bci.aggregate_route_times(rows)
    assert times == {}
    assert dropped == [("A", "B")]
