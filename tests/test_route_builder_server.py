import json, os, sys, shutil, tempfile
import threading as _th
import urllib.error as _err
import urllib.request as _rq
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


def test_build_argv_opacity_has_no_default_and_is_always_emitted_when_set():
    # adsb_historical_routes.py's --opacity has no argparse default (real default is None,
    # "use original"), so *any* explicit opacity value -- including 100 -- must be emitted.
    argv = server.build_argv({
        "kml_folder": "/data/jat", "airports": "/a.csv", "routes": "/r.csv",
        "output_dir": "/out", "output_name": "j.kml", "opacity": 100,
    })
    assert "--opacity" in argv and argv[argv.index("--opacity") + 1] == "100"

    argv_omitted = server.build_argv({
        "kml_folder": "/data/jat", "airports": "/a.csv", "routes": "/r.csv",
        "output_dir": "/out", "output_name": "j.kml",
    })
    assert "--opacity" not in argv_omitted             # opacity not in params -> flag omitted


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


def _csv(path, header, rows):
    with open(path, "w") as f:
        f.write(header + "\n" + "\n".join(rows) + "\n")


def test_validate_counts_and_errors(tmp_path):
    root = str(tmp_path)
    kdir = os.path.join(root, "kml"); os.makedirs(kdir)
    for n in ("a.kml", "b.kml", "c.kml"):
        open(os.path.join(kdir, n), "w").close()
    ap = os.path.join(root, "ap.csv"); _csv(ap, "airport,latitude,longitude,elevation_ft",
                                             ["AAA,0,0,0", "BBB,1,1,1"])
    rt = os.path.join(root, "rt.csv"); _csv(rt, "origin,destination,avg_enroute_min",
                                             ["AAA,BBB,60"])
    params = {"kml_folder": kdir, "airports": ap, "routes": rt,
              "output_dir": root, "output_name": "o.kml"}
    v = server.validate(params, [root])
    assert v["kml_count"] == 3 and v["airports_count"] == 2 and v["routes_count"] == 1
    assert v["output_exists"] is False and not v["errors"]
    # missing airports -> error
    v2 = server.validate({**params, "airports": os.path.join(root, "nope.csv")}, [root])
    assert any("airport" in e.lower() for e in v2["errors"])


def test_validate_rejects_kml_files_outside_allowed_roots(tmp_path):
    root = str(tmp_path)
    ap = os.path.join(root, "ap.csv"); _csv(ap, "airport,latitude,longitude,elevation_ft",
                                             ["AAA,0,0,0", "BBB,1,1,1"])
    rt = os.path.join(root, "rt.csv"); _csv(rt, "origin,destination,avg_enroute_min",
                                             ["AAA,BBB,60"])
    outside_dir = tempfile.mkdtemp()
    try:
        outside_kml = os.path.join(outside_dir, "sneaky.kml")
        open(outside_kml, "w").close()
        params = {"kml_files": [outside_kml], "airports": ap, "routes": rt,
                  "output_dir": root, "output_name": "o.kml"}
        v = server.validate(params, [root])
        assert v["errors"]
        assert any("outside the allowed folders" in e for e in v["errors"])
    finally:
        shutil.rmtree(outside_dir, ignore_errors=True)


def test_validate_rejects_output_dir_outside_allowed_roots(tmp_path):
    root = str(tmp_path)
    kdir = os.path.join(root, "kml"); os.makedirs(kdir)
    open(os.path.join(kdir, "a.kml"), "w").close()
    ap = os.path.join(root, "ap.csv"); _csv(ap, "airport,latitude,longitude,elevation_ft",
                                             ["AAA,0,0,0", "BBB,1,1,1"])
    rt = os.path.join(root, "rt.csv"); _csv(rt, "origin,destination,avg_enroute_min",
                                             ["AAA,BBB,60"])
    outside_dir = tempfile.mkdtemp()
    try:
        # a not-yet-created subdirectory: confine_path must reject on containment alone,
        # not because the path doesn't exist yet.
        nonexistent_output_dir = os.path.join(outside_dir, "not_created_yet")
        params = {"kml_folder": kdir, "airports": ap, "routes": rt,
                  "output_dir": nonexistent_output_dir, "output_name": "o.kml"}
        v = server.validate(params, [root])
        assert v["errors"]
        assert any("output folder is outside" in e.lower() for e in v["errors"])
    finally:
        shutil.rmtree(outside_dir, ignore_errors=True)


def test_validate_rejects_pos_and_firehose_folders_outside_allowed_roots(tmp_path):
    root = str(tmp_path)
    kdir = os.path.join(root, "kml"); os.makedirs(kdir)
    open(os.path.join(kdir, "a.kml"), "w").close()
    ap = os.path.join(root, "ap.csv"); _csv(ap, "airport,latitude,longitude,elevation_ft",
                                             ["AAA,0,0,0", "BBB,1,1,1"])
    rt = os.path.join(root, "rt.csv"); _csv(rt, "origin,destination,avg_enroute_min",
                                             ["AAA,BBB,60"])
    ti = os.path.join(root, "tail.csv"); _csv(ti, "tail,icao", ["N1,ABC123"])
    outside_dir = tempfile.mkdtemp()
    try:
        base_params = {"kml_folder": kdir, "airports": ap, "routes": rt,
                       "output_dir": root, "output_name": "o.kml",
                       "integrate_pos": True, "tail_icao": ti}
        # pos_folder outside allowed_roots -> error
        v = server.validate({**base_params, "pos_folder": outside_dir}, [root])
        assert v["errors"]
        assert any("POS folder is outside the allowed folders" in e for e in v["errors"])
        # firehose_folder outside allowed_roots -> error
        v2 = server.validate({**base_params, "firehose_folder": outside_dir}, [root])
        assert v2["errors"]
        assert any("Firehose folder is outside the allowed folders" in e for e in v2["errors"])
    finally:
        shutil.rmtree(outside_dir, ignore_errors=True)


def test_validate_rejects_unsafe_output_name(tmp_path):
    root = str(tmp_path)
    kdir = os.path.join(root, "kml"); os.makedirs(kdir)
    open(os.path.join(kdir, "a.kml"), "w").close()
    ap = os.path.join(root, "ap.csv"); _csv(ap, "airport,latitude,longitude,elevation_ft",
                                             ["AAA,0,0,0", "BBB,1,1,1"])
    rt = os.path.join(root, "rt.csv"); _csv(rt, "origin,destination,avg_enroute_min",
                                             ["AAA,BBB,60"])
    base_params = {"kml_folder": kdir, "airports": ap, "routes": rt, "output_dir": root}

    v = server.validate({**base_params, "output_name": "../x.kml"}, [root])
    assert any("plain filename" in e.lower() for e in v["errors"])

    v2 = server.validate({**base_params, "output_name": "/tmp/x.kml"}, [root])
    assert any("plain filename" in e.lower() for e in v2["errors"])

    v3 = server.validate({**base_params, "output_name": "x.kml"}, [root])
    assert not any("plain filename" in e.lower() for e in v3["errors"])


def test_validate_rejects_zero_altitude_bands(tmp_path):
    root = str(tmp_path)
    kdir = os.path.join(root, "kml"); os.makedirs(kdir)
    open(os.path.join(kdir, "a.kml"), "w").close()
    ap = os.path.join(root, "ap.csv"); _csv(ap, "airport,latitude,longitude,elevation_ft",
                                             ["AAA,0,0,0", "BBB,1,1,1"])
    rt = os.path.join(root, "rt.csv"); _csv(rt, "origin,destination,avg_enroute_min",
                                             ["AAA,BBB,60"])
    params = {"kml_folder": kdir, "airports": ap, "routes": rt,
              "output_dir": root, "output_name": "o.kml", "altitude_bands": 0}
    v = server.validate(params, [root])
    assert any("altitude bands" in e.lower() for e in v["errors"])


def test_runmanager_streams_and_parses(tmp_path):
    fake = os.path.join(str(tmp_path), "fake.py")
    with open(fake, "w") as f:
        f.write(
            "print('Processing: a.kml (Aircraft: E1)')\n"
            "print('Processing: b.kml (Aircraft: E2)')\n"
            "print('Creating output KML...')\n"
            "print('TOTAL VALID COMPLETE FLIGHTS: 2')\n"
            "print('Output written to: %s')\n" % os.path.join(str(tmp_path), "o.kml")
        )
    open(os.path.join(str(tmp_path), "o.kml"), "w").close()
    rm = server.RunManager()
    jid = rm.start(["python3", "-u", fake], total_files=2)
    kinds, done = [], None
    for name, payload in rm.events(jid):
        kinds.append(name)
        if name == "done":
            done = payload
    assert "progress" in kinds and "done" in kinds
    assert done["success"] is True
    assert done["summary"]["total_flights"] == 2


def test_runmanager_spawn_failure_emits_error_and_done_without_hanging():
    rm = server.RunManager()
    jid = rm.start(["/nonexistent/definitely_not_a_binary_xyz123"], total_files=0)
    kinds, done = [], None
    for name, payload in rm.events(jid):          # would hang forever if events() never ends
        kinds.append(name)
        if name == "done":
            done = payload
    assert "error" in kinds and "done" in kinds
    assert "output_size" in done and done["output_size"] is None
    assert done["success"] is False


def _start(tmp_path):
    d = str(tmp_path)
    shutil.copy(os.path.join(APPDIR, "manifest.default.json"),
                os.path.join(d, "manifest.default.json"))
    shutil.copy(os.path.join(APPDIR, "parameters.json"), os.path.join(d, "parameters.json"))
    # index.html may not exist yet in this task; a stub keeps GET / happy
    if not os.path.exists(os.path.join(d, "index.html")):
        open(os.path.join(d, "index.html"), "w").write("<!doctype html><title>rb</title>")
    srv = server.make_server(0, d, allowed_roots=[d])
    port = srv.server_address[1]
    t = _th.Thread(target=srv.serve_forever, daemon=True); t.start()
    return srv, f"http://127.0.0.1:{port}"


def _get(base, path):
    return json.loads(_rq.urlopen(base + path).read())


def test_http_config_browse_and_presets(tmp_path):
    srv, base = _start(tmp_path)
    try:
        cfg = _get(base, "/api/config")
        assert "presets" in cfg and "parameters" in cfg
        # browse the temp dir
        sub = os.path.join(str(tmp_path), "kml"); os.makedirs(sub)
        open(os.path.join(sub, "x.kml"), "w").close()
        br = _get(base, "/api/browse?path=" + _rq.quote(str(tmp_path)))
        assert any(e["name"] == "kml" and e["type"] == "dir" for e in br["entries"])
        # add a preset
        req = _rq.Request(base + "/api/presets", method="POST",
                          data=json.dumps({"name": "Mine", "params": {"sample": 3}}).encode(),
                          headers={"Content-Type": "application/json"})
        res = json.loads(_rq.urlopen(req).read())
        assert any(p["name"] == "Mine" for p in res["presets"])
    finally:
        srv.shutdown()


def test_http_delete_preset_user_ok_builtin_returns_409(tmp_path):
    srv, base = _start(tmp_path)
    try:
        # add a user (non-builtin) preset, then delete it -> 200, removed
        add_req = _rq.Request(base + "/api/presets", method="POST",
                              data=json.dumps({"name": "Mine2", "params": {"sample": 4}}).encode(),
                              headers={"Content-Type": "application/json"})
        added = json.loads(_rq.urlopen(add_req).read())
        assert any(p["name"] == "Mine2" for p in added["presets"])

        del_req = _rq.Request(base + "/api/presets?name=" + _rq.quote("Mine2"), method="DELETE")
        with _rq.urlopen(del_req) as resp:
            assert resp.status == 200
            body = json.loads(resp.read())
        assert not any(p["name"] == "Mine2" for p in body["presets"])

        # attempting to delete a built-in preset -> HTTP 409, preset still present
        cfg = _get(base, "/api/config")
        builtin_name = next(p["name"] for p in cfg["presets"] if p.get("builtin"))
        bad_req = _rq.Request(base + "/api/presets?name=" + _rq.quote(builtin_name), method="DELETE")
        try:
            _rq.urlopen(bad_req)
            assert False, "expected HTTPError with code 409"
        except _err.HTTPError as e:
            assert e.code == 409

        cfg2 = _get(base, "/api/config")
        assert any(p["name"] == builtin_name for p in cfg2["presets"])
    finally:
        srv.shutdown()


# ── Cirium input builder (second tool) ─────────────────────────────────────────

BUILD_STDOUT_READY = """Airport overrides applied: 1 from airport_overrides.csv (LIM)
============================================================
Cirium input build report
============================================================
Source rows read:        232
Routes written:          116
Airports written:        30
Routes dropped (no map): 0

VERDICT: READY — both files complete; adsb_historical_routes.py can run.
============================================================
"""

BUILD_STDOUT_NEEDS = """Source rows read:        232
Routes written:          100
Airports written:        28

VERDICT: NEEDS INPUT — 2 unmapped IATA code(s).
============================================================
"""


def test_build_cirium_argv():
    argv = server.build_cirium_argv(
        {"cirium_input": "/in/poe.csv", "output_dir": "/repo/input", "output_prefix": "poe_2026"},
        airport_lookup_dir="/tools/airport_lookup", airport_override="/repo/airport_overrides.csv")
    assert argv[:3] == ["python3", "-u", server.BUILD_SCRIPT] or argv[1] == "-u"
    assert "--input" in argv and "/in/poe.csv" in argv
    i = argv.index("--output")
    assert argv[i + 1] == "/repo/input/poe_2026"
    assert argv[argv.index("--airport-lookup-dir") + 1] == "/tools/airport_lookup"
    assert argv[argv.index("--airport-override") + 1] == "/repo/airport_overrides.csv"


def test_parse_build_summary_ready_and_needs():
    r = server.parse_build_summary(BUILD_STDOUT_READY)
    assert r["ready"] is True and r["verdict"] == "READY"
    assert r["routes_written"] == 116 and r["airports_written"] == 30 and r["rows_read"] == 232
    n = server.parse_build_summary(BUILD_STDOUT_NEEDS)
    assert n["ready"] is False and n["verdict"] == "NEEDS INPUT"
    assert "unmapped" in n.get("reasons", "")


def test_validate_cirium(tmp_path):
    root = str(tmp_path)
    ci = os.path.join(root, "poe.csv"); open(ci, "w").write("Orig,Dest\n")
    v = server.validate_cirium(
        {"cirium_input": ci, "output_dir": root, "output_prefix": "poe_2026"},
        [root], airport_lookup_dir="", airport_override="")
    assert not v["errors"]
    assert v["outputs"]["airports"].endswith("poe_2026_airports.csv")
    assert v["outputs"]["routes"].endswith("poe_2026_routes_time.csv")
    # missing input -> error
    v2 = server.validate_cirium({"cirium_input": os.path.join(root, "nope.csv"),
                                 "output_dir": root, "output_prefix": "x"}, [root])
    assert any("not found" in e.lower() for e in v2["errors"])
    # cirium input outside allowed roots -> error
    other = tempfile.mkdtemp(); oc = os.path.join(other, "p.csv"); open(oc, "w").write("x\n")
    v3 = server.validate_cirium({"cirium_input": oc, "output_dir": root, "output_prefix": "x"}, [root])
    assert any("outside the allowed" in e.lower() for e in v3["errors"])
    # bad airport_lookup_dir -> error
    v4 = server.validate_cirium({"cirium_input": ci, "output_dir": root, "output_prefix": "x"},
                                [root], airport_lookup_dir="/no/such/lookup/dir")
    assert any("airport_lookup" in e.lower() for e in v4["errors"])
