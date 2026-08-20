import json, os, sys, shutil, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "route_builder"))
import server  # noqa: E402

CAT = os.path.join(os.path.dirname(__file__), "..", "route_builder", "parameters.json")
APPDIR = os.path.join(os.path.dirname(__file__), "..", "route_builder")


def test_parameters_catalog_complete():
    cat = json.load(open(CAT))
    keys = {e["key"] for e in cat}
    required = {
        "kml_folder", "airports", "routes", "output_dir", "output_name",
        "sample", "maxgap", "group", "confidence", "airport_radius_km",
        "max_join_gap_hours", "max_join_distance_km", "route_time_tolerance",
        "corridor_cross_track_km", "route_time_rescue",
        "color", "width", "opacity", "labels", "icons", "ground",
        "color_mode", "altitude_bands",
        "stitch_gaps", "inferred_color", "stitch_gap_min",
        "integrate_pos", "pos_folder", "firehose_folder", "tail_icao",
        "pos_color", "firehose_color",
    }
    assert required <= keys, f"missing: {required - keys}"
    for e in cat:
        assert e.get("help"), f"{e['key']} missing help"
        assert e.get("example"), f"{e['key']} missing example"
        assert e.get("type") in {"folder", "file", "files", "text", "number",
                                 "slider", "toggle", "segmented", "color"}


def test_load_manifest_copies_seed_and_resolves_roots(tmp_path):
    d = str(tmp_path)
    shutil.copy(os.path.join(APPDIR, "manifest.default.json"),
                os.path.join(d, "manifest.default.json"))
    assert not os.path.exists(os.path.join(d, "manifest.json"))
    man = server.load_manifest(d)
    assert os.path.exists(os.path.join(d, "manifest.json"))          # seed copied to runtime
    assert os.path.isabs(man["roots"]["input_default"])              # ~ expanded, absolute
    assert any(p["builtin"] for p in man["presets"])
