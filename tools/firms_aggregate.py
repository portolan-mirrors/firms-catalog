#!/usr/bin/env python3
"""Build A5 aggregates of the detections, then one combined multi-level tileset.

Why aggregates exist: the full record is hundreds of millions of points. No
tileset renders that at full resolution. An aggregate answers "where does fire
happen, how intensely, and how does that change" in a file that opens instantly.

`gpio process aggregate a5` pivots exactly one categorical column per run. A
second --breakdown replaces the first rather than adding to it. So each
dimension is aggregated separately and the results are joined on a5_cell, which
is safe because every run uses the same resolution and therefore the same cells.

The combined file carries:
  count, sum_frp, avg_frp, max_frp   rollups shared by every product
  count_<day>                        one per acquisition day
  count_<sensor>                     MODIS and the three VIIRS platforms
  count_d / count_n                  day and night

`gpio process overview` then rolls it up to coarser A5 levels, and
`gpio pmtiles pyramid --include-features` puts the aggregate bands and the raw
points in ONE archive, so a viewer can switch at a zoom threshold with no
second request.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

SCHEME = "a5"
# H3 r6 holds 170k cells for this data against A5 r10's 189k, and H3 r3 holds
# 6.7k against A5 r6's 9.2k, so these pairings compare like with like.
DEFAULTS = {"a5": {"base": 10, "levels": "6"},
            "h3": {"base": 6, "levels": "3"}}
BASE_RES = 8
OVERVIEWS = "6,4"   # r2 (143 cells) is unreadable at full zoom-out; r4 (1,224) reads well
METRICS = "sum:frp,avg:frp,max:frp"
FEATURES_MIN_ZOOM = 10

# breakdown column -> how many pivoted values to allow
DIMENSIONS = {"day": 40, "sensor": 8, "daynight": 4}


def run(cmd: list[str], quiet: bool = True) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-1200:], r.stderr[-1200:], file=sys.stderr)
        raise SystemExit(f"failed: {' '.join(cmd[:5])}")
    if not quiet and r.stdout.strip():
        print("   ", r.stdout.strip().splitlines()[-1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="detections/ dir holding year=*/")
    ap.add_argument("--out", required=True, help="aggregate output dir")
    ap.add_argument("--tiles", help="also write a combined pyramid here")
    ap.add_argument("--scheme", choices=["a5", "h3"], default=SCHEME)
    ap.add_argument("--resolution", type=int)
    ap.add_argument("--levels")
    ap.add_argument("--features-min-zoom", type=int, default=FEATURES_MIN_ZOOM)
    a = ap.parse_args()

    d = DEFAULTS[a.scheme]
    if a.resolution is None:
        a.resolution = d["base"]
    if a.levels is None:
        a.levels = d["levels"]

    data, out = Path(a.data), Path(a.out)
    if not sorted(data.glob("year=*/*.parquet")):
        raise SystemExit(f"no year partitions under {data}")
    out.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; SET memory_limit='6GB';")
    tmp = Path(tempfile.mkdtemp(prefix="firms-agg-"))

    # The published table ships no derived time columns, so add the pivot keys
    # here. This staging file never reaches the published detections.
    src = tmp / "input.parquet"
    con.execute(f"""
        COPY (SELECT * EXCLUDE (geometry, year),
                     CAST(year(acq_date) AS INTEGER) AS year,
                     CAST(month(acq_date) AS UTINYINT) AS month,
                     strftime(acq_date, '%Y%m%d') AS day,
                     geometry
              FROM read_parquet('{data}/year=*/*.parquet',
                                hive_partitioning=true))
        TO '{src}' (FORMAT PARQUET, COMPRESSION zstd)
    """)
    print(f"staged pivot input -> {src}")

    # One aggregate per dimension, same resolution so the cells line up.
    parts = {}
    for dim, limit in DIMENSIONS.items():
        p = tmp / f"{dim}.parquet"
        print(f"[{dim}] {a.scheme} aggregate at r{a.resolution}")
        run(["gpio", "process", "aggregate", a.scheme, str(src), str(p),
             "--resolution", str(a.resolution), "--metric", METRICS,
             "--breakdown", dim, "--breakdown-limit", str(limit),
             "--out-geometry", "polygon", "--geoparquet-version", "2.0"])
        parts[dim] = p

    # Join the pivots onto the first product, which supplies geometry and the
    # shared rollups.
    cell = f"{a.scheme}_cell"
    base = parts["day"]
    others = [d for d in DIMENSIONS if d != "day"]
    sel = ["b.* EXCLUDE (geometry)"]
    joins = []
    for i, d in enumerate(others):
        al = f"j{i}"
        cols = [c for c in con.execute(
            f"SELECT column_name FROM (DESCRIBE SELECT * FROM '{parts[d]}')").fetchall()]
        keep = [c[0] for c in cols if c[0].startswith("count_")]
        sel += [f"{al}.\"{c}\"" for c in keep]
        joins.append(f"JOIN '{parts[d]}' {al} USING ({cell})")
    combined = out / "cells.parquet"
    con.execute(f"""
        COPY (SELECT {', '.join(sel)}, b.geometry
              FROM '{base}' b {' '.join(joins)})
        TO '{combined}' (FORMAT PARQUET, COMPRESSION zstd, COMPRESSION_LEVEL 15)
    """)
    n = con.execute(f"SELECT count(*) FROM '{combined}'").fetchone()[0]
    print(f"combined -> {combined} ({n:,} cells)")

    # gpio needs GeoParquet metadata on the joined output.
    fixed = out / "cells_gp.parquet"
    run(["gpio", "convert", "geoparquet", str(combined), str(fixed),
         "--geoparquet-version", "2.0", "--compression", "zstd",
         "--compression-level", "15"])
    fixed.replace(combined)

    print("[overview] rolling up")
    run(["gpio", "process", "overview", str(combined), "--levels", a.levels, "--force"],
        quiet=False)

    if a.tiles:
        tiles = Path(a.tiles); tiles.mkdir(parents=True, exist_ok=True)
        archive = tiles / "fire.pmtiles"
        print(f"[pyramid] aggregate bands + raw points from z{a.features_min_zoom}")
        run(["gpio", "pmtiles", "pyramid", str(combined), str(archive),
             "--levels", a.levels,
             "--include-features",
             "--features-source", str(src),
             "--features-min-zoom", str(a.features_min_zoom), "-f"])
        mb = archive.stat().st_size / 1e6
        print(f"  {archive} ({mb:,.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
