#!/usr/bin/env python3
"""Add running-total columns to an aggregate, so a range sum is two reads.

Colouring the map by a selection means summing the selected buckets per cell.
As a MapLibre expression that is one property read per selected bucket per cell
per frame, and with daily data a selection can span hundreds of buckets: a year
archive dragged at 33 ms a frame against 8 ms for the same map with a nine-term
selection. The cost is the term count, not the cells.

A running total removes it. cum[hi] - cum[lo-1] is the same answer in two reads
whatever the selection width, so the expression stops caring how much time is
selected.

It costs about 2.3x the file size, because cumulative values are larger and
denser than the sparse counts they replace. That is a poor trade if bytes are
the constraint and an obvious one if interaction is.

Done column-wise with numpy rather than as SQL: expressing 366 running totals
as nested sums is quadratic in the column count and takes minutes.

The columns are named `count_c<key>`, not `cum_<key>`, and that is load-bearing.
gpio builds a pyramid's overviews itself and drops columns it does not
recognise, so a differently-named column survives on the base band and vanishes
from every coarser one -- which is precisely where wide selections hurt most.
Named as a breakdown count it is rolled up by summing, and summing running
totals across child cells is exactly right: a parent's total at time T is the
sum of its children's totals at T. Verified on 158,798 cells, where every
cell's running total at the last bucket equals its overall count, max
difference zero.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

BUCKET = re.compile(r"^count_(\d{6}|\d{8})$")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--out", required=True)
    ap.add_argument("--compression-level", type=int, default=15)
    a = ap.parse_args()

    table = pq.read_table(a.source)
    buckets = sorted(c for c in table.column_names if BUCKET.match(c))
    if not buckets:
        raise SystemExit(f"{a.source} carries no count_<digits> columns")

    # Accumulate in int64: a cell's running total over a whole archive is far
    # larger than any single bucket, and silently overflowing an int32 here
    # would corrupt exactly the cells that matter most.
    running = np.zeros(table.num_rows, dtype=np.int64)
    out = table
    for name in buckets:
        col = table.column(name).to_numpy(zero_copy_only=False)
        running = running + np.nan_to_num(col, nan=0).astype(np.int64)
        out = out.append_column(f"count_c{name[6:]}", pa.array(running))

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(out, a.out, compression="zstd",
                   compression_level=a.compression_level,
                   # Keep the GeoParquet metadata: dropping it makes gpio treat
                   # the file as a plain table and refuse to tile it.
                   store_schema=True)
    src_mb = Path(a.source).stat().st_size / 1e6
    out_mb = Path(a.out).stat().st_size / 1e6
    print(f"  {len(buckets)} bucket(s) -> {len(buckets)} cumulative column(s)")
    print(f"  {src_mb:.2f} MB -> {out_mb:.2f} MB ({out_mb / src_mb:.1f}x)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
