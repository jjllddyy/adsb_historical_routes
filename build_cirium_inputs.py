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
