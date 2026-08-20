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


def test_confine_path_accepts_inside_rejects_outside(tmp_path):
    root = str(tmp_path)
    inside = os.path.join(root, "sub", "f.kml")
    os.makedirs(os.path.dirname(inside)); open(inside, "w").close()
    assert server.confine_path(inside, [root]) == os.path.realpath(inside)
    import pytest
    with pytest.raises(PermissionError):
        server.confine_path("/etc/passwd", [root])
    with pytest.raises(PermissionError):
        server.confine_path(os.path.join(root, "..", "escape"), [root])


def test_build_argv_folder_and_defaults_omitted():
    argv = server.build_argv({
        "kml_folder": "/data/jat", "airports": "/a.csv", "routes": "/r.csv",
        "output_dir": "/out", "output_name": "j.kml",
        "confidence": "permissive", "corridor_cross_track_km": 50,
        "sample": 2, "opacity": 100, "stitch_gaps": False,
    })
    assert argv[:3] == ["python3", "-u", server.SCRIPT_PATH_BASENAME] or argv[1] == "-u"
    assert "--kml-folder" in argv and "/data/jat" in argv
    assert "--output" in argv and "/out/j.kml" in argv
    assert "--confidence" in argv and "permissive" in argv
    assert "--stitch-gaps" not in argv          # false boolean omitted
    assert "--sample" not in argv               # equals script default -> omitted


def test_build_argv_files_and_booleans():
    argv = server.build_argv({
        "kml_files": ["/a.kml", "/b.kml"], "airports": "/a.csv", "routes": "/r.csv",
        "output_dir": "/out", "output_name": "j.kml",
        "stitch_gaps": True, "integrate_pos": True,
    })
    i = argv.index("--kml-files")
    assert argv[i + 1] == "/a.kml" and argv[i + 2] == "/b.kml"
    assert "--stitch-gaps" in argv and argv[argv.index("--stitch-gaps") + 1] == "true"
    assert "--integrate-pos" in argv


def test_suggest_name_suffixes():
    assert server.suggest_name("jat_2026_jun-jul", {}) == "jat_routes_2026_jun-jul.kml"
    assert server.suggest_name("jat_2026_jun-jul", {"stitch_gaps": True}) \
        == "jat_routes_2026_jun-jul_stitched.kml"
    n = server.suggest_name("jat_2026_jun-jul", {"integrate_pos": True, "color_mode": "altitude"})
    assert n.endswith("_pos_altcolor.kml")


SAMPLE_STDOUT = """Processing: e80600_2026-07-01_baro_avg.kml (Aircraft: E80600)
Loading extra position sources (this reads many small files)...
Combining segments by aircraft...
Creating output KML...
TOTAL VALID COMPLETE FLIGHTS: 5607
Position-source integration: filled 340,298 real fix(es) into 3356 of 5607 flights
Output written to: output/jat.kml
Data reduction: 2059367 points -> 183302 points
Compression ratio: 8.9%
"""


def test_parse_summary_extracts_fields():
    s = server.parse_summary(SAMPLE_STDOUT)
    assert s["total_flights"] == 5607
    assert s["filled_fixes"] == 340298 and s["filled_flights"] == 3356
    assert s["output"].endswith("jat.kml")
    assert s["points_in"] == 2059367 and s["points_out"] == 183302
    assert s["compression"] == 8.9


def test_classify_line():
    assert server.classify_line("Processing: x.kml (Aircraft: E1)")[0] == "progress"
    assert server.classify_line("Creating output KML...")[0] == "phase"
    assert server.classify_line("random noise")[0] == "raw"
