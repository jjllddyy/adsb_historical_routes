"""flown_routes v3 columns (request §5) on the flags-off build of the synthetic fixtures."""

import sqlite3

import pytest

from tests.test_gpkg_output import run

V3_COLUMNS = [
    "flight_number", "flight_number_source", "flight_number_conflict",
    "orig_source", "dest_source", "orig_free", "dest_free", "orig_free_source",
    "dest_free_source", "orig_candidates", "dest_candidates", "endpoint_confidence",
    "endpoint_conflict", "planned_leg_key", "n_pos_fixes",
]


@pytest.fixture(scope="module")
def built(flown_fixture, tmp_path_factory):
    d = tmp_path_factory.mktemp("v3")
    run(flown_fixture, d / "r.kml", "--gpkg-points")
    return sqlite3.connect(str(d / "r.gpkg"))


def test_v3_columns_follow_the_v2_ones(built):
    cols = [r[1] for r in built.execute("PRAGMA table_info(flights)")]
    assert cols[-len(V3_COLUMNS):] == V3_COLUMNS
    assert dict(built.execute("SELECT key, value FROM meta"))["schema_version"] == "3"


def test_flags_off_provenance(built):
    rows = {r[0]: r[1:] for r in built.execute(
        "SELECT flight_id, orig_source, dest_source, orig_free, dest_free, origin, dest, "
        "endpoint_confidence, endpoint_conflict, flight_number, planned_leg_key, n_pos_fixes "
        "FROM flights")}
    # complete flights are observed at both ends
    assert rows["ABC001_20261001T100000Z"][:2] == ("observed", "observed")
    assert rows["ABC001_20261001T100000Z"][6] == "high"
    # the joined flight's destination was reached across a bearing join
    assert rows["ABC002_20261001T100700Z"][:2] == ("observed", "bearing_join")
    assert rows["ABC002_20261001T100700Z"][6] == "low"
    for r in rows.values():
        assert r[2:4] == r[4:6]               # no planned legs: free = final
        assert r[7] == 0 and r[8] is None and r[9] is None and r[10] == 0
