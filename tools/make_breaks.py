#!/usr/bin/env python3
"""Compute quantile class breaks per aggregate level and store them in the
tileset's own PMTiles metadata.

Fixed breaks cannot serve both a 7-day window and a full year: a ramp tuned for
a week saturates when a year's counts are 50 times larger, and whole continents
render as the top class. Quantiles of the actual distribution fix that, and
computing them at build time keeps the classes stable while panning, which a
viewport-derived ramp would not.

Breaks are stored under `firms:breaks`, keyed by the zoom range of the band they
were computed from, so a viewer picks the set matching what it is drawing:

    "firms:breaks": {"0-7": {"count": [...], "avg_frp": [...]}, "8-14": {...}}

Zero-valued cells are excluded. A cell with no detections is not part of the
distribution being classified, and including the zeros drags every break down.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

# Seven classes, so six interior breaks. Weighted towards the upper tail
# because fire counts per cell are heavily skewed: an even split would put
# five of seven classes inside the noise.
QUANTILES = [0.50, 0.75, 0.90, 0.96, 0.99, 0.997]
METRICS = ["count", "avg_frp", "max_frp", "sum_frp"]


def breaks_for(con, parquet: str) -> dict:
    cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM '{parquet}'").fetchall()}
    out = {}
    for m in METRICS:
        if m not in cols:
            continue
        qs = ", ".join(f"quantile_cont({m}, {q})" for q in QUANTILES)
        row = con.execute(
            f"SELECT {qs} FROM '{parquet}' WHERE {m} IS NOT NULL AND {m} > 0"
        ).fetchone()
        if row is None or row[0] is None:
            continue
        # Round to something a legend can print, and keep them strictly rising
        # so two classes never collapse into one unreadable band.
        vals, prev = [], 0.0
        for v in row:
            v = float(v)
            v = round(v) if v >= 10 else round(v, 1)
            if v <= prev:
                v = prev + (1 if prev >= 10 else 0.1)
            vals.append(v)
            prev = v
        out[m] = vals
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pmtiles", help="archive to annotate in place")
    ap.add_argument("--band", action="append", required=True, metavar="MIN-MAX:PARQUET",
                    help="zoom range and the aggregate it was tiled from; repeatable")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    table = {}
    for spec in a.band:
        zooms, _, path = spec.partition(":")
        if not path:
            sys.exit(f"--band wants MIN-MAX:PARQUET, got {spec!r}")
        table[zooms] = breaks_for(con, path)
        got = ", ".join(f"{k}={v}" for k, v in table[zooms].items())
        print(f"  z{zooms}: {got}")

    if a.dry_run:
        return 0

    md = json.loads(subprocess.run(
        ["pmtiles", "show", a.pmtiles, "--metadata"],
        capture_output=True, text=True, check=True).stdout)
    md["firms:breaks"] = table
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(md, f)
        tmp = f.name
    try:
        subprocess.run(["pmtiles", "edit", a.pmtiles, f"--metadata={tmp}"], check=True)
    finally:
        Path(tmp).unlink(missing_ok=True)
    print(f"  wrote firms:breaks into {a.pmtiles}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
