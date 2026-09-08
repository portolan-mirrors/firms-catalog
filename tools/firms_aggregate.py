#!/usr/bin/env python3
"""Build A5 hexagon aggregates of the detections, at three zoom levels each.

Why aggregates exist: the full record is hundreds of millions of points. No
tileset renders that at full resolution, and no browser wants it. An aggregate
answers "where does fire happen, how intensely, and how does that change" in a
file small enough to open instantly.

`gpio process aggregate a5` pivots exactly one categorical column per run
(a second --breakdown replaces the first, it does not add to it), so each
dimension is its own product. Every product carries the same FRP rollups, so
they stay comparable.

`gpio process overview` then rolls each base level up to coarser A5
resolutions. Counts and sums roll up exactly; averages are count-weighted.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE_RES = 8
OVERVIEWS = "5,2"
METRICS = "sum:frp,avg:frp,max:frp"

# product name -> column pivoted into count_<value> columns
PRODUCTS = {
    "by-year":     "year",      # long-term trend, fills in as the backfill lands
    "by-month":    "month",     # seasonality
    "by-sensor":   "sensor",    # platform contribution and cross-checks
    "by-daynight": "daynight",  # night detections indicate active flaming
}


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-800:], r.stderr[-800:], file=sys.stderr)
        raise SystemExit(f"failed: {' '.join(cmd[:4])}")
    print("   ", r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "ok")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="detections/ dir holding year=*/")
    ap.add_argument("--out", required=True, help="aggregates output dir")
    ap.add_argument("--resolution", type=int, default=BASE_RES)
    ap.add_argument("--levels", default=OVERVIEWS)
    a = ap.parse_args()

    data, out = Path(a.data), Path(a.out)
    parts = sorted(data.glob("year=*/detections.parquet"))
    if not parts:
        raise SystemExit(f"no year partitions under {data}")
    out.mkdir(parents=True, exist_ok=True)

    # The published table carries no month column, because the detections file
    # ships no sort-key columns. Stage a temp input that adds year and month for
    # the pivot. This never reaches the published detections.
    import duckdb, tempfile
    tmpdir = tempfile.mkdtemp(prefix="firms-agg-")
    src = str(Path(tmpdir) / "agg_input.parquet")
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; SET memory_limit='6GB';")
    con.execute(f"""
        COPY (SELECT * EXCLUDE (geometry),
                     CAST(year(acq_date) AS INTEGER) AS year,
                     CAST(month(acq_date) AS UTINYINT) AS month,
                     geometry
              FROM read_parquet('{data}/year=*/detections.parquet'))
        TO '{src}' (FORMAT PARQUET, COMPRESSION zstd)
    """)
    print(f"staged aggregation input -> {src}")

    for name, column in PRODUCTS.items():
        target = out / f"{name}.parquet"
        print(f"[{name}] pivot on {column}")
        run(["gpio", "process", "aggregate", "a5", src, str(target),
             "--resolution", str(a.resolution), "--metric", METRICS,
             "--breakdown", column, "--out-geometry", "polygon",
             "--geoparquet-version", "2.0"])
        run(["gpio", "process", "overview", str(target), "--levels", a.levels])
    print(f"\n{len(PRODUCTS)} product(s) x 3 level(s) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
