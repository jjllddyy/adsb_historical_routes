"""Integration test: run the full pipeline against 1 aircraft × 3 days from real LAN data."""

import csv
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "adsb_historical_routes.py"
KML_DIR = PROJECT_ROOT / "kml_input" / "kml_downloads"
AIRPORTS = PROJECT_ROOT / "input" / "latam_la_airports.csv"
ROUTES = PROJECT_ROOT / "input" / "latam_la_routes_time.csv"


def _pick_sample_files() -> list:
    if not KML_DIR.exists():
        return []
    files_by_hex = {}
    for f in sorted(KML_DIR.glob("*.kml")):
        m = re.match(r"^([a-f0-9]+)_(\d{4}-\d{2}-\d{2})_baro_avg\.kml$", f.name)
        if m:
            files_by_hex.setdefault(m.group(1), []).append(f)
    if not files_by_hex:
        return []
    # Pick first hex with >= 3 substantial (>50KB) days
    for hex_id, files in files_by_hex.items():
        substantial = [f for f in files if f.stat().st_size > 50_000]
        if len(substantial) >= 3:
            return [str(f) for f in sorted(substantial)[:3]]
    return []


@pytest.mark.skipif(not KML_DIR.exists(), reason="kml_input/kml_downloads not present")
@pytest.mark.skipif(not AIRPORTS.exists(), reason="latam_la_airports.csv not present")
@pytest.mark.skipif(not ROUTES.exists(), reason="latam_la_routes_time.csv not present")
def test_three_presets_monotonic(tmp_path):
    sample = _pick_sample_files()
    if not sample:
        pytest.skip("No suitable sample files found")

    counts = {}
    for preset in ["strict", "balanced", "permissive"]:
        out_kml = tmp_path / f"out_{preset}.kml"
        cmd = [
            sys.executable, str(SCRIPT),
            "--kml-files", *sample,
            "--airports", str(AIRPORTS),
            "--routes", str(ROUTES),
            "--output", str(out_kml),
            "--confidence", preset,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, f"Failed under {preset}: {result.stderr}"
        assert out_kml.exists(), f"Missing KML for {preset}"

        diag_path = tmp_path / f"out_{preset}.kml.diagnostics.csv"
        assert diag_path.exists(), f"Missing diagnostics for {preset}"
        with diag_path.open() as f:
            rows = list(csv.DictReader(f))
        kept = sum(1 for r in rows if r["phase"] == "final" and r["disposition"].startswith("kept_"))
        counts[preset] = kept
        print(f"{preset}: {kept} kept")

    assert counts["strict"] <= counts["balanced"] <= counts["permissive"], \
        f"Monotonicity violated: {counts}"
