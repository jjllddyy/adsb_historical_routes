#!/usr/bin/env python3
"""ADSB Historical Route Builder — stdlib-only local server. See EXTENSION_SPEC.md."""
import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs, unquote

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
    roots.setdefault("airport_lookup_dir", "~/Git/conversion_tools/airport_lookup")
    for k in ("input_default", "airports_dir", "routes_dir", "output_default", "airport_lookup_dir"):
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


# ── Cirium input builder (second tool: build_cirium_inputs.py) ──────────────────
# Turns a raw Cirium schedule export into the *_airports.csv / *_routes_time.csv the
# splitter needs. Reuses RunManager with a build-specific summary parser (below).
BUILD_SCRIPT = os.path.join(REPO_ROOT, "build_cirium_inputs.py")
DEFAULT_AIRPORT_OVERRIDE = os.path.join(REPO_ROOT, "airport_overrides.csv")


def build_cirium_argv(params: Dict[str, Any], airport_lookup_dir: str = "",
                      airport_override: str = "", script: Optional[str] = None) -> List[str]:
    """Map a params dict -> a `python3 -u build_cirium_inputs.py …` argv list.

    ``airport_lookup_dir``/``airport_override`` come from server config (the manifest),
    NOT the request body, because the lookup dir is imported as code by the subprocess.
    """
    out_prefix = os.path.join(params.get("output_dir", ""), params.get("output_prefix", ""))
    argv = ["python3", "-u", script or BUILD_SCRIPT,
            "--input", params.get("cirium_input", ""),
            "--output", out_prefix]
    if airport_lookup_dir:
        argv += ["--airport-lookup-dir", airport_lookup_dir]
    if airport_override:
        argv += ["--airport-override", airport_override]
    return argv


def parse_build_summary(text: str) -> Dict[str, Any]:
    """Parse build_cirium_inputs.py's report into a structured summary."""
    out: Dict[str, Any] = {}
    for key, pat, cast in [
        ("rows_read", r"Source rows read:\s*(\d+)", int),
        ("routes_written", r"Routes written:\s*(\d+)", int),
        ("airports_written", r"Airports written:\s*(\d+)", int),
    ]:
        mm = re.search(pat, text)
        if mm:
            out[key] = cast(mm.group(1))
    mm = re.search(r"VERDICT:\s*(READY|NEEDS INPUT)(?:\s*[—-]\s*(.+?))?\s*$", text, re.M)
    if mm:
        out["ready"] = mm.group(1) == "READY"
        out["verdict"] = mm.group(1)
        if mm.group(2):
            out["reasons"] = mm.group(2).strip().rstrip(".")
    return out


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
        for f in params["kml_files"]:
            try:
                rp = confine_path(f, allowed_roots)
            except PermissionError:
                errors.append("A selected KML file is outside the allowed folders"); break
            if not os.path.exists(rp):
                errors.append(f"KML file not found: {f}"); break
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
        if params.get("pos_folder"):
            try:
                confine_path(params["pos_folder"], allowed_roots)
            except PermissionError:
                errors.append("ACARS POS folder is outside the allowed folders")
        if params.get("firehose_folder"):
            try:
                confine_path(params["firehose_folder"], allowed_roots)
            except PermissionError:
                errors.append("Firehose folder is outside the allowed folders")
        ti = ok(params.get("tail_icao"), "Tail<->ICAO CSV") if params.get("tail_icao") else None
        tail_count = _count_csv_rows(ti) if ti else 0

    od = params.get("output_dir", "")
    if od:
        try:
            confine_path(od, allowed_roots)
        except PermissionError:
            errors.append("Output folder is outside the allowed folders")

    out_path = os.path.join(params.get("output_dir", ""), params.get("output_name", ""))
    if not params.get("output_name"):
        errors.append("Output filename not set")
    elif os.path.basename(params["output_name"]) != params["output_name"]:
        errors.append("Output filename must be a plain filename, not a path")
    output_exists = bool(out_path) and os.path.exists(out_path)
    if output_exists:
        warnings.append("Output file already exists and will be overwritten")
    if params.get("color_mode") == "altitude" and params.get("color"):
        warnings.append("Line-color override is ignored in altitude color mode")
    if params.get("altitude_bands") is not None and params["altitude_bands"] < 1:
        errors.append("Altitude bands must be >= 1")

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


def validate_cirium(params: Dict[str, Any], allowed_roots: List[str],
                    airport_lookup_dir: str = "",
                    airport_override: str = "") -> Dict[str, Any]:
    """Pre-flight for the Cirium build: confine paths, report the output files + argv."""
    errors: List[str] = []
    warnings: List[str] = []

    ci = params.get("cirium_input")
    if not ci:
        errors.append("Cirium CSV not selected")
    else:
        try:
            rp = confine_path(ci, allowed_roots)
            if not os.path.exists(rp):
                errors.append(f"Cirium CSV not found: {ci}")
        except PermissionError:
            errors.append("Cirium CSV is outside the allowed folders")

    out_dir = params.get("output_dir", "")
    prefix = params.get("output_prefix", "")
    if not out_dir:
        errors.append("Output folder not selected")
    else:
        try:
            confine_path(out_dir, allowed_roots)
        except PermissionError:
            errors.append("Output folder is outside the allowed folders")
    if not prefix:
        errors.append("Output name (prefix) not set")
    elif os.path.basename(prefix) != prefix:
        errors.append("Output name must be a plain name, not a path")

    out_prefix = os.path.join(out_dir, prefix)
    outputs = {
        "airports": out_prefix + "_airports.csv",
        "routes": out_prefix + "_routes_time.csv",
        "report": out_prefix + "_error.txt",
    }
    outputs_exist = bool(prefix) and os.path.exists(outputs["airports"]) and os.path.exists(outputs["routes"])
    if outputs_exist:
        warnings.append("Generated files already exist and will be overwritten")
    if airport_lookup_dir and not os.path.isdir(airport_lookup_dir):
        errors.append(f"airport_lookup tool not found at {airport_lookup_dir} "
                      "(set roots.airport_lookup_dir in manifest.json)")

    return {
        "outputs": outputs,
        "outputs_exist": outputs_exist,
        "argv": build_cirium_argv(params, airport_lookup_dir, airport_override),
        "warnings": warnings,
        "errors": errors,
    }


class RunManager:
    """Runs the flight-route script as a subprocess, streaming stdout as progress events."""

    def __init__(self) -> None:
        self._jobs: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, argv: List[str], total_files: int = 0, cwd: str = REPO_ROOT,
              summary_parser=None, success_fn=None) -> str:
        """Spawn argv as a subprocess and start streaming its stdout; return the new job id.

        ``summary_parser(text) -> dict`` and ``success_fn(code, summary) -> bool`` are
        per-tool: they default to the flight-route splitter's, but a second tool (e.g. the
        Cirium input builder) supplies its own so the same run/stream machinery is reused.
        """
        jid = uuid.uuid4().hex[:12]
        job = {"q": queue.Queue(), "proc": None, "status": "running",
               "total": total_files, "processed": 0, "start": time.time(),
               "buf": [], "summary": {}, "output": None,
               "summary_parser": summary_parser or parse_summary,
               "success_fn": success_fn or (lambda code, summary: code == 0 and "output" in summary)}
        with self._lock:
            self._jobs[jid] = job
        try:
            proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
        except OSError as e:
            job["status"] = "error"
            job["q"].put(("error", {"message": str(e)}))
            job["q"].put(("done", {"exit_code": -1, "success": False, "summary": {},
                                   "output_size": None}))
            return jid
        job["proc"] = proc
        threading.Thread(target=self._pump, args=(jid, job), daemon=True).start()
        return jid

    def _pump(self, jid: str, job: dict) -> None:
        """Read the subprocess's stdout line-by-line, pushing events until it exits."""
        try:
            proc = job["proc"]
            for line in proc.stdout:
                line = line.rstrip("\n")
                job["buf"].append(line)
                job["q"].put(("line", {"text": line}))
                kind, payload = classify_line(line)
                if kind == "progress":
                    job["processed"] += 1
                    elapsed = time.time() - job["start"]
                    done_n = job["processed"]
                    eta = (elapsed / done_n) * (job["total"] - done_n) if done_n and job["total"] else None
                    pct = (done_n / job["total"] * 100) if job["total"] else None
                    job["q"].put(("progress", {"processed": done_n, "total": job["total"],
                                               "percent": pct, "eta_sec": eta, "phase": None}))
                elif kind == "phase":
                    job["q"].put(("phase", {"phase": payload}))
            code = proc.wait()
            text = "\n".join(job["buf"])
            summary = job["summary_parser"](text)
            job["summary"] = summary
            out = summary.get("output")
            size = os.path.getsize(out) if out and os.path.exists(out) else None
            success = job["success_fn"](code, summary)
            job["status"] = "done" if success else "error"
            job["q"].put(("done", {"exit_code": code, "success": success,
                                   "summary": summary, "output_size": size}))
        except Exception as e:
            job["status"] = "error"
            job["q"].put(("error", {"message": str(e)}))
            job["q"].put(("done", {"exit_code": -1, "success": False, "summary": {},
                                   "output_size": None}))

    def events(self, jid: str):
        """Yield (event_name, payload) tuples for a job until its 'done' event, then stop."""
        job = self._jobs.get(jid)
        if not job:
            return
        while True:
            name, payload = job["q"].get()
            yield name, payload
            if name == "done":
                break

    def cancel(self, jid: str) -> None:
        """Terminate a still-running job's subprocess."""
        job = self._jobs.get(jid)
        if job and job.get("proc") and job["status"] == "running":
            job["proc"].terminate()

    def get(self, jid: str) -> Optional[dict]:
        """Return the raw job dict for jid, or None if it doesn't exist."""
        return self._jobs.get(jid)


APP_DIR = os.path.dirname(os.path.abspath(__file__))


def browse(path: str, allowed_roots: List[str]) -> Dict[str, Any]:
    """List one directory's contents (confined to allowed_roots) for the folder picker."""
    rp = confine_path(path, allowed_roots)
    entries = []
    for e in sorted(os.scandir(rp), key=lambda x: (x.is_file(), x.name.lower())):
        if e.name.startswith("."):
            continue
        if e.is_dir():
            entries.append({"name": e.name, "type": "dir", "kml_count": _count_kml(e.path)})
        else:
            ext = e.name.rsplit(".", 1)[-1].lower() if "." in e.name else ""
            kind = {"kml": "kml", "csv": "csv", "json": "json"}.get(ext, "other")
            entries.append({"name": e.name, "type": "file", "kind": kind,
                            "size": e.stat().st_size})
    parent = os.path.dirname(rp)
    return {"path": rp, "parent": parent if parent != rp else None, "entries": entries}


def list_outputs(dir_path: str, allowed_roots: List[str]) -> Dict[str, Any]:
    """List .kml files in a completed-run's output directory."""
    rp = confine_path(dir_path, allowed_roots)
    files = [{"name": e.name, "size": e.stat().st_size}
             for e in os.scandir(rp) if e.is_file() and e.name.endswith(".kml")]
    return {"files": sorted(files, key=lambda f: f["name"])}


def make_server(port: int, app_dir: str = APP_DIR,
                 allowed_roots: Optional[List[str]] = None) -> ThreadingHTTPServer:
    """Build a configured (not-yet-serving) ThreadingHTTPServer bound to 127.0.0.1:port."""
    manifest = load_manifest(app_dir)
    roots = allowed_roots or manifest["roots"]["allowed_roots"]
    with open(os.path.join(app_dir, "parameters.json")) as f:
        params_catalog = json.load(f)
    runs = RunManager()
    # Cirium-build config comes from the manifest / repo, never the request body: the
    # airport_lookup dir is imported as code by the build subprocess, so it must be trusted.
    airport_lookup_dir = manifest["roots"].get("airport_lookup_dir", "")
    airport_override = DEFAULT_AIRPORT_OVERRIDE if os.path.exists(DEFAULT_AIRPORT_OVERRIDE) else ""

    def man_input(_dir: str) -> str:
        return load_manifest(_dir)["roots"]["input_default"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:
            pass

        def _send(self, code: int, obj: Any = None, ctype: str = "application/json") -> None:
            """Write a full JSON (or raw bytes) HTTP response with the right headers."""
            body = b"" if obj is None else (obj if isinstance(obj, bytes)
                                            else json.dumps(obj).encode())
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _body(self) -> Any:
            """Read and JSON-decode the request body (empty body decodes to {})."""
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")

        def _check_host(self) -> bool:
            """Reject (with a 403) any request whose Host header isn't localhost/127.0.0.1.

            Defense-in-depth against DNS-rebinding-style requests reaching this
            loopback-only server with a spoofed Host header. Returns False (after
            already sending the 403) when the request should not be dispatched.
            """
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            if host not in ("127.0.0.1", "localhost"):
                self._send(403, {"error": "invalid host header"})
                return False
            return True

        def do_GET(self) -> None:
            """Route GET requests: static assets, config/browse/outputs reads, and SSE stream."""
            if not self._check_host():
                return
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path == "/":
                    with open(os.path.join(app_dir, "index.html"), "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
                if u.path == "/parameters.json":
                    return self._send(200, params_catalog)
                if u.path == "/api/config":
                    man = load_manifest(app_dir)
                    return self._send(200, {"roots": man["roots"], "defaults": man["defaults"],
                                            "presets": man["presets"], "parameters": params_catalog})
                if u.path == "/api/browse":
                    path = unquote(q.get("path", [man_input(app_dir)])[0])
                    return self._send(200, browse(path, roots))
                if u.path == "/api/outputs":
                    return self._send(200, list_outputs(unquote(q["dir"][0]), roots))
                if u.path.startswith("/api/run/") and u.path.endswith("/stream"):
                    return self._stream(u.path.split("/")[3])
                return self._send(404, {"error": "not found"})
            except PermissionError as e:
                return self._send(403, {"error": str(e)})
            except Exception as e:
                return self._send(400, {"error": str(e)})

        def do_POST(self) -> None:
            """Route POST requests: validate, preset add, run start/cancel/reveal."""
            if not self._check_host():
                return
            u = urlparse(self.path)
            try:
                if u.path == "/api/validate":
                    return self._send(200, validate(self._body(), roots))
                if u.path == "/api/presets":
                    return self._send(200, self._add_preset(self._body()))
                if u.path == "/api/run":
                    params = self._body()
                    v = validate(params, roots)
                    if v["errors"]:
                        return self._send(400, {"error": "; ".join(v["errors"])})
                    jid = runs.start(build_argv(params), total_files=v["kml_count"])
                    return self._send(200, {"job_id": jid})
                if u.path == "/api/build-cirium/validate":
                    return self._send(200, validate_cirium(self._body(), roots,
                                                           airport_lookup_dir, airport_override))
                if u.path == "/api/build-cirium/run":
                    params = self._body()
                    v = validate_cirium(params, roots, airport_lookup_dir, airport_override)
                    if v["errors"]:
                        return self._send(400, {"error": "; ".join(v["errors"])})
                    jid = runs.start(
                        build_cirium_argv(params, airport_lookup_dir, airport_override),
                        summary_parser=parse_build_summary,
                        success_fn=lambda code, summary: code == 0)
                    return self._send(200, {"job_id": jid})
                if u.path.startswith("/api/run/") and u.path.endswith("/cancel"):
                    runs.cancel(u.path.split("/")[3]); return self._send(200, {"status": "cancelled"})
                if u.path.startswith("/api/run/") and u.path.endswith("/reveal"):
                    return self._reveal(u.path.split("/")[3])
                return self._send(404, {"error": "not found"})
            except PermissionError as e:
                return self._send(403, {"error": str(e)})
            except Exception as e:
                return self._send(400, {"error": str(e)})

        def do_DELETE(self) -> None:
            """Route DELETE requests: remove a user preset (409 if it's a built-in)."""
            if not self._check_host():
                return
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/api/presets":
                name = unquote(q.get("name", [""])[0])
                man = load_manifest(app_dir)
                tgt = [p for p in man["presets"] if p["name"] == name]
                if tgt and tgt[0].get("builtin"):
                    return self._send(409, {"error": "cannot delete a built-in preset"})
                return self._send(200, self._del_preset(name))
            return self._send(404, {"error": "not found"})

        def _add_preset(self, body: Dict[str, Any]) -> Dict[str, Any]:
            """Add (or reject an overwrite of) a named preset; persist and return the list."""
            man = load_manifest(app_dir)
            name = body.get("name", "").strip()
            if not name:
                raise ValueError("preset name required")
            man["presets"] = [p for p in man["presets"] if p["name"] != name or p.get("builtin")]
            if any(p["name"] == name and p.get("builtin") for p in man["presets"]):
                raise ValueError("cannot overwrite a built-in preset")
            man["presets"].append({"name": name, "params": body.get("params", {})})
            save_manifest(app_dir, man)
            return {"presets": man["presets"]}

        def _del_preset(self, name: str) -> Dict[str, Any]:
            # Callers (do_DELETE) must already have rejected built-in presets with a 409;
            # this only mutates + saves + returns, so exactly one HTTP response is ever sent.
            man = load_manifest(app_dir)
            man["presets"] = [p for p in man["presets"] if p["name"] != name]
            save_manifest(app_dir, man)
            return {"presets": man["presets"]}

        def _stream(self, jid: str) -> None:
            """Serve Server-Sent Events for a run until its 'done' event, once headers are sent."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            # Headers are already on the wire once we get here, so no matter what goes wrong
            # below (client disconnect or any other error), we must never attempt a second
            # response -- swallow everything instead of letting it propagate to do_GET's
            # outer except, which would try to _send() again onto the same connection.
            try:
                for name, payload in runs.events(jid):
                    chunk = f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                pass

        def _reveal(self, jid: str) -> None:
            """Reveal a finished job's output file in Finder (macOS 'open -R'), if available."""
            job = runs.get(jid)
            out = (job or {}).get("summary", {}).get("output")
            if out and os.path.exists(out) and shutil.which("open"):
                subprocess.Popen(["open", "-R", out], shell=False)
                return self._send(200, {"status": "revealed"})
            return self._send(200, {"status": "unavailable", "path": out})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    ap = argparse.ArgumentParser(description="ADSB Historical Route Builder server")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()
    manifest = load_manifest(APP_DIR)
    port = args.port if args.port is not None else manifest.get("server", {}).get("port", 8765)
    try:
        srv = make_server(port, APP_DIR)
    except OSError as e:
        raise SystemExit(f"Could not bind 127.0.0.1:{port} ({e}). Try --port <other>.")
    url = f"http://127.0.0.1:{port}"
    print(f"ADSB Historical Route Builder → {url}  (Ctrl-C to stop)")
    if args.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")


if __name__ == "__main__":
    main()
