#!/usr/bin/env python3
"""
KML equivalence check for the GeoPackage migration (spec §6).

Two route KMLs are equivalent when they have the same folder tree, the same placemarks
per folder in the same order, and per placemark the same name, description text, line
style (colour; width compared as a number), icon style, altitude mode/extrude,
coordinates to 1e-6 and ``when`` instants to 1 ms. Whitespace, attribute order and the
spelling of a timestamp (``...:56Z`` vs ``...:56.000Z``) don't matter.

Streams both files (iterparse), so it handles multi-hundred-MB outputs.

    python3 kml_compare.py direct.kml via_gpkg.kml        # exit 0 = equivalent
"""

import argparse
import calendar
import re
import sys
import xml.etree.ElementTree as ET
from itertools import zip_longest
from typing import Iterator, List, Tuple

COORD_TOL = 1e-6
_WHEN = re.compile(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d+))?Z?$")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def when_ms(text: str) -> int:
    """``YYYY-MM-DDTHH:MM:SS[.f...]Z`` -> Unix epoch milliseconds."""
    m = _WHEN.match(text.strip())
    if not m:
        raise ValueError(f"unparseable <when>: {text!r}")
    y, mo, d, h, mi, s, frac = m.groups()
    secs = calendar.timegm((int(y), int(mo), int(d), int(h), int(mi), int(s)))
    ms = round(float("0." + frac) * 1000) if frac else 0
    return secs * 1000 + ms


def _child(el, name):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _text(el, *path):
    for name in path:
        if el is None:
            return None
        el = _child(el, name)
    return el.text if el is not None else None


def _placemark_record(pm) -> dict:
    style = _child(pm, "Style")
    icon = _child(style, "IconStyle") if style is not None else None
    track = None
    for el in pm.iter():
        if _local(el.tag) == "Track":
            track = el
            break
    whens, coords = [], []
    if track is not None:
        for c in track:
            t = _local(c.tag)
            if t == "when":
                whens.append(when_ms(c.text))
            elif t == "coord":
                coords.append(tuple(float(v) for v in c.text.split()))
    return {
        "name": _text(pm, "name"),
        "description": _text(pm, "description"),
        "color": (_text(style, "LineStyle", "color") or "").lower(),
        "width": _text(style, "LineStyle", "width"),
        "icon_href": _text(icon, "Icon", "href"),
        "icon_scale": _text(icon, "scale"),
        "altitude_mode": _text(track, "altitudeMode") if track is not None else None,
        "extrude": _text(track, "extrude") if track is not None else None,
        "whens": whens,
        "coords": coords,
    }


def iter_kml(path: str) -> Iterator[Tuple]:
    """Yield ("document", name, description), ("folder", path) and
    ("placemark", path, record) in document order."""
    tags: List[str] = []       # open element tags
    folders: List[str] = []    # open folder names
    for event, el in ET.iterparse(path, events=("start", "end")):
        tag = _local(el.tag)
        if event == "start":
            tags.append(tag)
            if tag == "Folder":
                folders.append("")
            continue
        tags.pop()
        parent = tags[-1] if tags else None
        if tag == "name" and parent == "Folder":
            folders[-1] = el.text or ""
            yield ("folder", tuple(folders))
        elif tag == "Document":
            yield ("document", _text(el, "name"), _text(el, "description"))
        elif tag == "Placemark":
            yield ("placemark", tuple(folders), _placemark_record(el))
            el.clear()
        elif tag == "Folder":
            folders.pop()


def _width_equal(a, b) -> bool:
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return False


def _diff_placemarks(where: str, a: dict, b: dict) -> List[str]:
    out = []
    for key in ("name", "description", "color", "icon_href", "icon_scale",
                "altitude_mode", "extrude"):
        if a[key] != b[key]:
            out.append(f"{where}: {key} {a[key]!r} != {b[key]!r}")
    if not _width_equal(a["width"], b["width"]):
        out.append(f"{where}: width {a['width']!r} != {b['width']!r}")
    if len(a["whens"]) != len(b["whens"]) or len(a["coords"]) != len(b["coords"]):
        out.append(f"{where}: point counts {len(a['whens'])}/{len(a['coords'])} != "
                   f"{len(b['whens'])}/{len(b['coords'])}")
        return out
    for i, (ta, tb) in enumerate(zip(a["whens"], b["whens"])):
        if ta != tb:
            out.append(f"{where}: when[{i}] {ta} != {tb} (ms)")
            break
    for i, (ca, cb) in enumerate(zip(a["coords"], b["coords"])):
        if len(ca) != len(cb) or any(abs(x - y) > COORD_TOL for x, y in zip(ca, cb)):
            out.append(f"{where}: coord[{i}] {ca} != {cb}")
            break
    return out


def compare_kml(path_a: str, path_b: str, max_diffs: int = 20) -> Tuple[List[str], dict]:
    """Return (differences, stats). No differences means the two KMLs are equivalent."""
    diffs: List[str] = []
    stats = {"folders": 0, "placemarks": 0, "points": 0}
    for ea, eb in zip_longest(iter_kml(path_a), iter_kml(path_b)):
        if ea is None or eb is None:
            extra = ea or eb
            side = "first" if ea is not None else "second"
            diffs.append(f"only in {side} file: {extra[0]} {extra[1]}")
            break
        if ea[0] != eb[0]:
            diffs.append(f"structure differs: {ea[0]} {ea[1]} vs {eb[0]} {eb[1]}")
            break
        kind = ea[0]
        if kind == "document":
            if ea[1:] != eb[1:]:
                diffs.append(f"document name/description {ea[1:]} != {eb[1:]}")
        elif kind == "folder":
            stats["folders"] += 1
            if ea[1] != eb[1]:
                diffs.append(f"folder {'/'.join(ea[1])} != {'/'.join(eb[1])}")
                break
        else:
            stats["placemarks"] += 1
            stats["points"] += len(ea[2]["whens"])
            where = f"{'/'.join(ea[1])} placemark #{stats['placemarks']}"
            if ea[1] != eb[1]:
                diffs.append(f"{where}: folder {'/'.join(ea[1])} != {'/'.join(eb[1])}")
                break
            diffs.extend(_diff_placemarks(where, ea[2], eb[2]))
        if len(diffs) >= max_diffs:
            break
    return diffs, stats


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Check two route KMLs for spec §6 equivalence.")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--max-diffs", type=int, default=20)
    args = p.parse_args(argv)
    diffs, stats = compare_kml(args.a, args.b, args.max_diffs)
    print(f"Compared {stats['folders']} folders, {stats['placemarks']} placemarks, "
          f"{stats['points']} points")
    if diffs:
        print("NOT EQUIVALENT:")
        for d in diffs:
            print("  " + d)
        return 1
    print("EQUIVALENT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
