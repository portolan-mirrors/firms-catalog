#!/usr/bin/env python3
"""Compute quantile class breaks per aggregate level and store them in the
tileset's own PMTiles metadata.

Fixed breaks cannot serve both a 7-day window and a full year: a ramp tuned for
a week saturates when a year's counts are 50 times larger, and whole continents
render as the top class. Quantiles of the actual distribution fix that, and
computing them at build time keeps the classes stable while panning, which a
viewport-derived ramp would not.

Breaks are keyed by the aggregate level they were computed from, never by a
zoom range. A level usually spans several zooms, and keying by zoom means the
colours can jump while the cells on screen stay identical -- the same r8 cell
reads as one class at z5 and another at z6. Keyed by level, the classes change
exactly when the cells change and never in between.

The zoom range each level occupies is copied in alongside, straight from the
pyramid, so a viewer can match without knowing how the pyramid was built:

    "firms:breaks": {"r6": {"minzoom": 0, "maxzoom": 4,
                            "metrics": {"count": [...], "avg_frp": [...]}}}

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
    ap.add_argument("--band", action="append", required=True, metavar="LEVEL:PARQUET",
                    help="a5 level and the aggregate it was tiled from; repeatable")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    md = json.loads(subprocess.run(
        ["pmtiles", "show", a.pmtiles, "--metadata"],
        capture_output=True, text=True, check=True).stdout)

    # The pyramid is the authority on which zooms a level covers. Deriving the
    # range here, rather than taking it on the command line, is what keeps the
    # breaks and the geometry from drifting apart.
    py = md.get("gpio:pyramid")
    if isinstance(py, str):
        py = json.loads(py)
    zooms = {b.get("level"): (b.get("minzoom"), b.get("maxzoom"))
             for b in (py or {}).get("bands", [])}
    if not zooms:
        sys.exit("no gpio:pyramid bands in the archive; cannot key breaks by level")

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    table = {}
    for spec in a.band:
        level, _, path = spec.partition(":")
        if not path:
            sys.exit(f"--band wants LEVEL:PARQUET, got {spec!r}")
        try:
            lo, hi = zooms[int(level)]
        except (KeyError, ValueError):
            sys.exit(f"level {level} is not a band in {a.pmtiles}: "
                     f"have {sorted(k for k in zooms if isinstance(k, int))}")
        table[f"r{int(level)}"] = {
            "minzoom": lo, "maxzoom": hi, "metrics": breaks_for(con, path)}
        got = ", ".join(f"{k}={v}" for k, v in table[f"r{int(level)}"]["metrics"].items())
        print(f"  r{level} (z{lo}-{hi if hi is not None else 'max'}): {got}")

    if a.dry_run:
        return 0

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
