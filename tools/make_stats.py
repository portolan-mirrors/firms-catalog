#!/usr/bin/env python3
"""Build the timeline's stats sidecar: cell bounds plus monthly counts.

Why this exists. The timeline and the map want opposite things from the same
archive. The timeline needs every time bucket over a coarse grid; the map needs
a fine grid and cares about one selection at a time. Serving both from one
tileset means a zoomed-out tile carrying 311 monthly columns on every cell,
which is why the all-time archive had to fall back to r4 there -- at r6 the z0
tile is several megabytes. The 7-day archive escapes this only by having eight
buckets instead of 311.

Splitting them fixes both ends. The map keeps its tiles. The timeline reads a
flat table with no geometry at all: bounds are enough to answer "is this cell
in view", and the polygons are already in the tiles.

Written as one row group per file so a reader decompresses once, and split by
era so a viewer can paint the recent years immediately and fetch the rest while
the user is reading. The bounds live in their own file rather than in each
chunk, or geometry would be duplicated four times and dominate them.

    python3 tools/make_stats.py --source catalog-staging/alltime/alltime.parquet \
        --out catalog-staging/stats
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import duckdb

MONTH_COL = re.compile(r"^count_(\d{6})$")
# Newest first: the first chunk is the one the app blocks on.
CHUNKS = [("recent", 2022, 2026), ("era3", 2015, 2021),
          ("era2", 2008, 2014), ("era1", 2000, 2007)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="all-time aggregate parquet")
    ap.add_argument("--out", required=True)
    ap.add_argument("--compression-level", type=int, default=22)
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")

    cols = [c[0] for c in con.execute(
        f"DESCRIBE SELECT * FROM '{a.source}'").fetchall()]
    months = sorted(c for c in cols if MONTH_COL.match(c))
    if not months:
        raise SystemExit(f"{a.source} carries no count_YYYYMM columns")

    def write(name: str, select: str) -> tuple[str, int]:
        p = out / f"{name}.parquet"
        # One row group: a reader that has to stitch several pays for it on
        # every query, and these files are small enough not to need paging.
        con.execute(f"""COPY ({select}) TO '{p}'
            (FORMAT parquet, COMPRESSION zstd,
             COMPRESSION_LEVEL {a.compression_level}, ROW_GROUP_SIZE 1000000)""")
        return str(p), p.stat().st_size

    manifest = {"cells": "cells.parquet", "chunks": [], "unit": "month"}

    _, size = write("cells", f"""
        SELECT a5_cell,
               ST_XMin(geometry) AS w, ST_YMin(geometry) AS s,
               ST_XMax(geometry) AS e, ST_YMax(geometry) AS n
        FROM '{a.source}'""")
    print(f"  cells   bounds only        {size / 1e6:6.2f} MB")

    for name, lo, hi in CHUNKS:
        keep = [c for c in months if lo <= int(MONTH_COL.match(c).group(1)[:4]) <= hi]
        if not keep:
            continue
        sel = ", ".join(["a5_cell"] + [f'"{c}"' for c in keep])
        _, size = write(name, f"SELECT {sel} FROM '{a.source}'")
        manifest["chunks"].append({
            "file": f"{name}.parquet", "from": keep[0][6:], "to": keep[-1][6:],
            "buckets": len(keep), "bytes": size,
        })
        print(f"  {name:7s} {lo}-{hi} {len(keep):>4} cols  {size / 1e6:6.2f} MB")

    (out / "stats.json").write_text(json.dumps(manifest, indent=2) + "\n")
    total = sum(c["bytes"] for c in manifest["chunks"]) + \
        (out / "cells.parquet").stat().st_size
    print(f"  total                      {total / 1e6:6.2f} MB")
    print(f"  manifest -> {out / 'stats.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
