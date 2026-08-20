#!/usr/bin/env python3
"""ADSB Historical Route Builder — stdlib-only local server. See EXTENSION_SPEC.md."""
import json
import os
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve(path: str, base: str = REPO_ROOT) -> str:
    """Expand ~ and make absolute (relative paths resolve against the repo root)."""
    p = os.path.expanduser(path)
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(base, p))


def load_manifest(app_dir: str) -> dict:
    """Copy manifest.default.json -> manifest.json on first run; return resolved manifest."""
    runtime = os.path.join(app_dir, "manifest.json")
    seed = os.path.join(app_dir, "manifest.default.json")
    if not os.path.exists(runtime):
        shutil.copy(seed, runtime)
    with open(runtime) as f:
        man = json.load(f)
    roots = man.setdefault("roots", {})
    for k in ("input_default", "airports_dir", "routes_dir", "output_default"):
        if roots.get(k):
            roots[k] = _resolve(roots[k])
    roots["allowed_roots"] = [_resolve(r) for r in roots.get("allowed_roots", [])]
    return man


def save_manifest(app_dir: str, manifest: dict) -> None:
    """Persist the runtime manifest.json (used by preset add/delete)."""
    with open(os.path.join(app_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)


def confine_path(path: str, allowed_roots: List[str]) -> str:
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
    ("color", "--color", ""), ("width", "--width", ""), ("opacity", "--opacity", None),
    ("color_mode", "--color-mode", "source"), ("altitude_bands", "--altitude-bands", 24),
    ("inferred_color", "--inferred-color", "ff888888"),
    ("stitch_gap_min", "--stitch-gap-min", 5),
    ("pos_color", "--pos-color", "ff00a5ff"), ("firehose_color", "--firehose-color", "ff00ff00"),
    ("pos_folder", "--pos-folder", ""), ("firehose_folder", "--firehose-folder", ""),
    ("tail_icao", "--tail-icao", ""),
]
_BOOL_FLAGS = [("labels", "--labels"), ("icons", "--icons"), ("ground", "--ground"),
               ("stitch_gaps", "--stitch-gaps"), ("integrate_pos", "--integrate-pos")]


def build_argv(params: Dict[str, Any], script: Optional[str] = None) -> List[str]:
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


def suggest_name(batch_stem: str, params: Dict[str, Any]) -> str:
    """Suggest an output filename from an input batch folder name + enabled features."""
    m = re.match(r"([a-z]+)_(.+)", batch_stem)
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


_PHASE_PREFIXES = ("Loading extra position sources", "Combining segments by aircraft",
                   "Creating output KML", "Position-source integration:")


def classify_line(line: str) -> Tuple[str, str]:
    s = line.strip()
    if s.startswith("Processing:"):
        return ("progress", s)
    for p in _PHASE_PREFIXES:
        if s.startswith(p):
            return ("phase", p.rstrip(":. "))
    return ("raw", s)


def parse_summary(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    def grab(pat, cast=str):
        mm = re.search(pat, text)
        return cast(mm.group(1).replace(",", "")) if mm else None
    for key, pat, cast in [
        ("total_flights", r"TOTAL VALID COMPLETE FLIGHTS:\s*(\d+)", int),
        ("output", r"Output written to:\s*(.+)", str),
        ("points_in", r"Data reduction:\s*(\d+) points", int),
        ("points_out", r"Data reduction:\s*\d+ points -> (\d+) points", int),
        ("compression", r"Compression ratio:\s*([\d.]+)%", float),
        ("connectors", r"Gap stitching:\s*(\d+) inferred connector", int),
    ]:
        v = grab(pat, cast)
        if v is not None:
            out[key] = v.strip() if isinstance(v, str) else v
    mm = re.search(r"filled ([\d,]+) real fix\(es\) into (\d+) of (\d+) flights", text)
    if mm:
        out["filled_fixes"] = int(mm.group(1).replace(",", ""))
        out["filled_flights"] = int(mm.group(2))
        out["total_flights"] = int(mm.group(3))
    return out


def _count_kml(folder: str) -> int:
    try:
        return sum(1 for e in os.scandir(folder) if e.is_file() and e.name.endswith(".kml"))
    except OSError:
        return 0


def _count_csv_rows(path: str) -> int:
    try:
        with open(path) as f:
            return max(0, sum(1 for _ in f) - 1)   # minus header
    except OSError:
        return 0


def validate(params: Dict[str, Any], allowed_roots: List[str]) -> Dict[str, Any]:
    errors, warnings = [], []

    def ok(path, label):
        if not path:
            errors.append(f"{label} not selected")
            return None
        try:
            rp = confine_path(path, allowed_roots)
        except PermissionError:
            errors.append(f"{label} is outside the allowed folders")
            return None
        if not os.path.exists(rp):
            errors.append(f"{label} not found: {path}")
            return None
        return rp

    kml_count = 0
    if params.get("kml_files"):
        kml_count = len(params["kml_files"])
    else:
        kf = ok(params.get("kml_folder"), "Input KML folder")
        if kf:
            kml_count = _count_kml(kf)
            if kml_count == 0:
                errors.append("Input folder contains no .kml files")
    ap = ok(params.get("airports"), "Airports CSV")
    rt = ok(params.get("routes"), "Routes CSV")

    tail_count = 0
    if params.get("integrate_pos"):
        if not params.get("tail_icao"):
            errors.append("Integrate positions is on but no tail<->icao CSV is set")
        if not (params.get("pos_folder") or params.get("firehose_folder")):
            errors.append("Integrate positions is on but no POS or firehose folder is set")
        ti = ok(params.get("tail_icao"), "Tail<->ICAO CSV") if params.get("tail_icao") else None
        tail_count = _count_csv_rows(ti) if ti else 0

    out_path = os.path.join(params.get("output_dir", ""), params.get("output_name", ""))
    if not params.get("output_name"):
        errors.append("Output filename not set")
    output_exists = bool(out_path) and os.path.exists(out_path)
    if output_exists:
        warnings.append("Output file already exists and will be overwritten")
    if params.get("color_mode") == "altitude" and params.get("color"):
        warnings.append("Line-color override is ignored in altitude color mode")

    return {
        "kml_count": kml_count,
        "airports_count": _count_csv_rows(ap) if ap else 0,
        "routes_count": _count_csv_rows(rt) if rt else 0,
        "tail_icao_count": tail_count,
        "output_path": out_path,
        "output_exists": output_exists,
        "argv": build_argv(params),
        "warnings": warnings,
        "errors": errors,
    }
