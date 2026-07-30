#!/usr/bin/env python3
"""Build adsb_historical_routes input files (routes_time + airports) from a raw Cirium CSV."""

import argparse
import csv
import os
import sys
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Tuple


def parse_number(raw: str) -> Optional[float]:
    """Parse a Cirium numeric cell, stripping thousands-commas/quotes/whitespace.

    Returns None for blank or non-numeric values.
    """
    if raw is None:
        return None
    s = raw.strip().strip('"').replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def read_cirium(path: str) -> Tuple[List[dict], bool]:
    """Read a Cirium CSV into normalized row dicts.

    Returns (rows, has_report_source). Each row: report_source (str, '' if column
    absent), orig/dest (upper IATA), flights/block_mins (float or None).
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        has_rs = "report_source" in fields
        rows = []
        for row in reader:
            rows.append({
                "report_source": (row.get("report_source") or "").strip() if has_rs else "",
                "orig": (row.get("Orig") or "").strip().upper(),
                "dest": (row.get("Dest") or "").strip().upper(),
                "flights": parse_number(row.get("Flights", "")),
                "block_mins": parse_number(row.get("Block Mins", "")),
            })
    return rows, has_rs


def aggregate_route_times(rows: List[dict]) -> Tuple[Dict[Tuple[str, str], float], List[Tuple[str, str]]]:
    """Per (orig,dest): pool aircraft types per report month (Σblock/Σflights), take max month.

    Rows with flights<=0/None, blank block_mins, or orig==dest are skipped. Routes seen but
    with no valid flight rows in any month are returned in dropped_no_flights.
    """
    # route -> month -> [sum_block, sum_flights]
    acc: Dict[Tuple[str, str], Dict[str, List[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    seen = set()
    for r in rows:
        o, d = r["orig"], r["dest"]
        if not o or not d or o == d:
            continue
        seen.add((o, d))
        f, b = r["flights"], r["block_mins"]
        if f is None or b is None or f <= 0:
            continue
        month = r["report_source"] or "_single"
        bucket = acc[(o, d)][month]
        bucket[0] += b
        bucket[1] += f

    route_times: Dict[Tuple[str, str], float] = {}
    for route, months in acc.items():
        monthly = [blk / fl for (blk, fl) in months.values() if fl > 0]
        if monthly:
            route_times[route] = max(monthly)
    dropped_no_flights = sorted(seen - set(route_times))
    return route_times, dropped_no_flights
