"""--qgz / gpkg_to_qgz.py: the QGIS project is built by ~/Git/qgis `prepare`.

A fake qgis checkout stands in for the real one: its scripts/env.sh points QGIS_PY at
this Python, and its `prepare` records the arguments and folder it ran with.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "adsb_historical_routes.py"
GPKG_TO_QGZ = PROJECT_ROOT / "gpkg_to_qgz.py"

FAKE_PREPARE = '''
import json, os, sys
args = sys.argv[1:]
if os.environ.get("FAKE_PREPARE_FAIL"):
    print("usage: prepare ...", file=sys.stderr)
    print("prepare: error: unrecognized arguments: --project-name x", file=sys.stderr)
    sys.exit(2)
opt = dict(zip(args[::2], args[1::2]))
json.dump({"argv": args, "cwd": os.getcwd()}, open(os.path.join(opt["--out"], "call.json"), "w"))
open(os.path.join(opt["--out"], opt["--project-name"] + ".qgz"), "wb").write(b"PK fake")
print("project: done")
'''


@pytest.fixture
def qgis_repo(tmp_path):
    repo = tmp_path / "qgis"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "env.sh").write_text(f'export QGIS_PY="{sys.executable}"\n')
    (repo / "prepare").mkdir()
    (repo / "prepare" / "__init__.py").write_text("")
    (repo / "prepare" / "__main__.py").write_text(FAKE_PREPARE)
    return repo


@pytest.fixture
def navdb(tmp_path):
    path = tmp_path / "navdb_2610.gpkg"
    path.write_bytes(b"")
    return path


@pytest.fixture
def company(tmp_path):
    path = tmp_path / "company_route_lan.gpkg"
    path.write_bytes(b"")
    return path


def _run(cmd, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    # Python 3.6-compatible spelling (no capture_output / text).
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=300, env=env)


def _build(fx, out, *extra, env_extra=None):
    return _run([sys.executable, str(SCRIPT), "--kml-folder", str(fx["kml_dir"]),
                 "--airports", str(fx["airports"]), "--routes", str(fx["routes"]),
                 "--confidence", "permissive", "--airline", "tst", "--format", "gpkg",
                 "--output", str(out), *extra], env_extra)


def _call(folder):
    call = json.loads((folder / "call.json").read_text())
    return call, dict(zip(call["argv"][::2], call["argv"][1::2]))


def test_qgz_runs_prepare_with_the_flown_gpkg(flown_fixture, tmp_path, qgis_repo, navdb):
    out = tmp_path / "out"
    res = _build(flown_fixture, out / "lan.gpkg", "--qgz", "--navdb", str(navdb),
                 "--qgis-repo", str(qgis_repo))
    assert res.returncode == 0, res.stdout[-2000:] + res.stderr
    assert (out / "lan.gpkg").is_file() and (out / "lan.qgz").is_file()
    call, opt = _call(out)
    assert opt == {"--airline": "TST", "--navdb": str(navdb.resolve()),
                   "--flown": str((out / "lan.gpkg").resolve()),
                   "--out": str(out.resolve()), "--project-name": "lan"}
    assert "--company" not in call["argv"]
    # Run from the repo, where prepare finds keys/ and its packages.
    assert Path(call["cwd"]).resolve() == qgis_repo.resolve()
    assert "(QGIS project; links to lan.gpkg and navdb_2610.gpkg)" in res.stdout


def test_qgz_with_company(flown_fixture, tmp_path, qgis_repo, navdb, company):
    out = tmp_path / "out"
    res = _build(flown_fixture, out / "lan.gpkg", "--qgz", "--navdb", str(navdb),
                 "--company", str(company), "--qgis-repo", str(qgis_repo))
    assert res.returncode == 0, res.stderr
    call, opt = _call(out)
    assert opt["--company"] == str(company.resolve())
    assert opt["--flown"] == str((out / "lan.gpkg").resolve())
    assert call["argv"][-4:] == ["--out", str(out.resolve()), "--project-name", "lan"]
    assert "links to lan.gpkg, company_route_lan.gpkg and navdb_2610.gpkg" in res.stdout


def test_qgz_failure_keeps_the_gpkg_and_points_at_the_handoff(flown_fixture, tmp_path,
                                                               qgis_repo, navdb):
    out = tmp_path / "out"
    res = _build(flown_fixture, out / "lan.gpkg", "--qgz", "--navdb", str(navdb),
                 "--qgis-repo", str(qgis_repo), env_extra={"FAKE_PREPARE_FAIL": "1"})
    assert res.returncode == 1
    assert (out / "lan.gpkg").is_file() and not (out / "lan.qgz").exists()
    assert "error: .qgz not built: qgis prepare exited 2" in res.stderr
    assert "unrecognized arguments: --project-name" in res.stderr
    assert "qgis_prepare_partial_inputs.md" in res.stderr


def test_qgz_without_a_qgis_repo_fails_cleanly(flown_fixture, tmp_path, navdb):
    res = _build(flown_fixture, tmp_path / "lan.gpkg", "--qgz", "--navdb", str(navdb),
                 "--qgis-repo", str(tmp_path / "nowhere"))
    assert res.returncode == 1
    assert "no QGIS viewer repo" in res.stderr
    assert (tmp_path / "lan.gpkg").is_file()


@pytest.mark.parametrize("extra, message", [
    (["--qgz"], "--qgz needs --navdb"),
    (["--qgz", "--navdb", "/no/such/navdb.gpkg"], "navdb GeoPackage does not exist"),
    (["--qgz", "--navdb", "NAVDB", "--company", "/no/such/company.gpkg"],
     "company GeoPackage does not exist"),
    (["--qgz", "--navdb", "NAVDB", "--format", "kml"], "--qgz needs the GeoPackage"),
])
def test_qgz_options_are_checked_before_the_build(flown_fixture, tmp_path, navdb, extra,
                                                  message):
    extra = [str(navdb) if a == "NAVDB" else a for a in extra]
    res = _run([sys.executable, str(SCRIPT), "--kml-folder", str(flown_fixture["kml_dir"]),
                "--airports", str(flown_fixture["airports"]),
                "--routes", str(flown_fixture["routes"]),
                "--output", str(tmp_path / "lan.gpkg"), *extra])
    assert res.returncode == 1
    assert message in res.stdout
    assert "Processing:" not in res.stdout
    assert not (tmp_path / "lan.gpkg").exists()


def test_gpkg_to_qgz_builds_from_an_existing_gpkg(flown_fixture, tmp_path, qgis_repo, navdb,
                                                  company):
    gpkg = tmp_path / "out" / "lan.gpkg"
    assert _build(flown_fixture, gpkg).returncode == 0
    res = _run([sys.executable, str(GPKG_TO_QGZ), str(gpkg), "--navdb", str(navdb),
                "--company", str(company), "--qgis-repo", str(qgis_repo),
                "-o", str(tmp_path / "proj" / "lan_explorer.qgz")])
    assert res.returncode == 0, res.stderr
    assert (tmp_path / "proj" / "lan_explorer.qgz").is_file()
    _, opt = _call(tmp_path / "proj")
    assert opt["--project-name"] == "lan_explorer" and opt["--airline"] == "TST"
    assert opt["--flown"] == str(gpkg.resolve())
    # default output: the GeoPackage path with .qgz
    assert _run([sys.executable, str(GPKG_TO_QGZ), str(gpkg), "--navdb", str(navdb),
                 "--qgis-repo", str(qgis_repo)]).returncode == 0
    assert (tmp_path / "out" / "lan.qgz").is_file()


def test_gpkg_to_qgz_errors(tmp_path, navdb):
    res = _run([sys.executable, str(GPKG_TO_QGZ), str(tmp_path / "missing.gpkg"),
                "--navdb", str(navdb)])
    assert res.returncode == 2 and "does not exist" in res.stderr
    bogus = tmp_path / "bogus.gpkg"
    bogus.write_text("not sqlite")
    res = _run([sys.executable, str(GPKG_TO_QGZ), str(bogus), "--navdb", str(navdb)])
    assert res.returncode == 1 and "not a flown_routes GeoPackage" in res.stderr
    res = _run([sys.executable, str(GPKG_TO_QGZ), str(bogus)])
    assert res.returncode == 2 and "--navdb is required" in res.stderr
