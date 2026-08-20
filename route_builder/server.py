#!/usr/bin/env python3
"""ADSB Historical Route Builder — stdlib-only local server. See EXTENSION_SPEC.md."""
import json
import os
import shutil

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve(path, base=REPO_ROOT):
    """Expand ~ and make absolute (relative paths resolve against the repo root)."""
    p = os.path.expanduser(path)
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(base, p))


def load_manifest(app_dir):
    """Copy manifest.default.json -> manifest.json on first run; return resolved manifest."""
    runtime = os.path.join(app_dir, "manifest.json")
    seed = os.path.join(app_dir, "manifest.default.json")
    if not os.path.exists(runtime):
        shutil.copy(seed, runtime)
    man = json.load(open(runtime))
    roots = man.setdefault("roots", {})
    for k in ("input_default", "airports_dir", "routes_dir", "output_default"):
        if roots.get(k):
            roots[k] = _resolve(roots[k])
    roots["allowed_roots"] = [_resolve(r) for r in roots.get("allowed_roots", [])]
    return man


def save_manifest(app_dir, manifest):
    """Persist the runtime manifest.json (used by preset add/delete)."""
    with open(os.path.join(app_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)


def confine_path(path, allowed_roots):
    """Return realpath(path) if it lives inside one of allowed_roots, else PermissionError."""
    rp = os.path.realpath(os.path.expanduser(path))
    for root in allowed_roots:
        rr = os.path.realpath(root)
        if rp == rr or rp.startswith(rr + os.sep):
            return rp
    raise PermissionError(f"path outside allowed roots: {path}")


SCRIPT_PATH = os.path.join(REPO_ROOT, "adsb_historical_routes.py")
SCRIPT_PATH_BASENAME = "adsb_historical_routes.py"

# key -> (flag, script_default). None default means "always emit if truthy/non-empty".
_ARGV_MAP = [
    ("sample", "--sample", 2), ("maxgap", "--maxgap", 20),
    ("group", "--group", "destination"), ("confidence", "--confidence", "balanced"),
    ("airport_radius_km", "--airport-radius-km", None),
    ("max_join_gap_hours", "--max-join-gap-hours", None),
    ("max_join_distance_km", "--max-join-distance-km", None),
    ("route_time_tolerance", "--route-time-tolerance", None),
    ("corridor_cross_track_km", "--corridor-cross-track-km", None),
    ("route_time_rescue", "--route-time-rescue", ""),
    ("color", "--color", ""), ("width", "--width", ""), ("opacity", "--opacity", 100),
    ("color_mode", "--color-mode", "source"), ("altitude_bands", "--altitude-bands", 24),
    ("inferred_color", "--inferred-color", "ff888888"),
    ("stitch_gap_min", "--stitch-gap-min", 5),
    ("pos_color", "--pos-color", "ff00a5ff"), ("firehose_color", "--firehose-color", "ff00ff00"),
    ("pos_folder", "--pos-folder", ""), ("firehose_folder", "--firehose-folder", ""),
    ("tail_icao", "--tail-icao", ""),
]
_BOOL_FLAGS = [("labels", "--labels"), ("icons", "--icons"), ("ground", "--ground"),
               ("stitch_gaps", "--stitch-gaps"), ("integrate_pos", "--integrate-pos")]


def build_argv(params, script=None):
    """Map a params dict -> a `python3 -u adsb_historical_routes.py …` argv list."""
    argv = ["python3", "-u", script or SCRIPT_PATH]
    if params.get("kml_files"):
        argv += ["--kml-files", *params["kml_files"]]
    elif params.get("kml_folder"):
        argv += ["--kml-folder", params["kml_folder"]]
    argv += ["--airports", params.get("airports", "")]
    argv += ["--routes", params.get("routes", "")]
    out = os.path.join(params.get("output_dir", ""), params.get("output_name", ""))
    argv += ["--output", out]
    for key, flag, default in _ARGV_MAP:
        if key not in params:
            continue
        val = params[key]
        if val in (None, "") or val == default:
            continue
        argv += [flag, str(val)]
    for key, flag in _BOOL_FLAGS:
        if params.get(key):                       # only emit when user turned it on
            argv += [flag, "true"]
    return argv


def suggest_name(batch_stem, params):
    """Suggest an output filename from an input batch folder name + enabled features."""
    m = __import__("re").match(r"([a-z]+)_(.+)", batch_stem)
    airline, period = (m.group(1), m.group(2)) if m else (batch_stem, "")
    stem = f"{airline}_routes_{period}" if period else f"{batch_stem}_routes"
    if params.get("confidence") == "strict":
        stem += "_strict"
    if params.get("stitch_gaps"):
        stem += "_stitched"
    if params.get("integrate_pos"):
        stem += "_pos"
    if params.get("color_mode") == "altitude":
        stem += "_altcolor"
    return stem + ".kml"
