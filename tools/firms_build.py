#!/usr/bin/env python3
"""Compact FIRMS chunks into published per-year GeoParquet 2.0 files.

Layout, one file per year:

    detections/year=<YYYY>/detections.parquet

Rows are ordered by (month, Hilbert). The `year=` path key already prunes to a
year, so ordering inside the file buys two more things: row-group statistics on
acq_datetime prune to a month, and the Hilbert run inside each month keeps the
bbox statistics tight for a spatial filter. A pure Hilbert sort would scatter
every month across the whole file and remove month pruning.

A chunk can straddle a year boundary, so candidate chunks for year Y include
the tail of Y-1. The year filter is applied on acq_date, never on the filename.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

ROW_GROUP = 100_000
ZSTD_LEVEL = 15


def connect(mem: str, tmp: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute(f"SET memory_limit='{mem}'; SET temp_directory='{tmp}';")
    return con


def candidates(chunks: Path, year: int) -> list[str]:
    """Chunk files that may hold rows for this year."""
    out = []
    for src in sorted(chunks.iterdir()):
        if not src.is_dir():
            continue
        for f in src.glob("*.parquet"):
            if f.stat().st_size == 0:
                continue  # sentinel: window fetched, no detections
            # Date-named chunks (from the area API) can be narrowed by name.
            # Bulk NRT chunks carry no date in the name, so they are always
            # candidates and the SQL year filter decides.
            dated = len(f.name) > 4 and f.name[:4].isdigit()
            if not dated or f.name.startswith((f"{year}-", f"{year - 1}-12-")):
                out.append(str(f))
    return out


def build_year(con, chunks: Path, year: int, outdir: Path, verbose: bool) -> int:
    files = candidates(chunks, year)
    if not files:
        return 0
    lst = ",".join(f"'{f}'" for f in files)
    dest = outdir / f"year={year}"
    dest.mkdir(parents=True, exist_ok=True)
    final = dest / "detections.parquet"

    with tempfile.TemporaryDirectory() as td:
        staged = Path(td) / "ordered.parquet"
        con.execute(f"""
            COPY (
              SELECT * EXCLUDE (geometry), geometry
              FROM read_parquet([{lst}], union_by_name=true)
              WHERE year(acq_date) = {year}
              ORDER BY month(acq_date), ST_Hilbert(geometry)
            ) TO '{staged}'
              (FORMAT PARQUET, COMPRESSION zstd, COMPRESSION_LEVEL {ZSTD_LEVEL},
               ROW_GROUP_SIZE {ROW_GROUP})
        """)
        n = con.execute(f"SELECT count(*) FROM read_parquet('{staged}')").fetchone()[0]
        if n == 0:
            shutil.rmtree(dest, ignore_errors=True)
            return 0
        # Upgrade to GeoParquet 2.0 (native Parquet GEOMETRY logical type).
        cmd = ["gpio", "convert", "geoparquet", str(staged), str(final),
               "--geoparquet-version", "2.0",
               "--compression", "zstd", "--compression-level", str(ZSTD_LEVEL),
               "--row-group-size", str(ROW_GROUP), "--overwrite"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            # Older gpio builds lack --overwrite on convert.
            final.unlink(missing_ok=True)
            r = subprocess.run([c for c in cmd if c != "--overwrite"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                print(r.stdout[-1500:], r.stderr[-1500:], file=sys.stderr)
                raise SystemExit(f"gpio convert failed for {year}")
    mb = final.stat().st_size / 1e6
    print(f"  year={year}: {n:,} rows, {mb:,.0f} MB", flush=True)
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--years", help="comma list; default = every year found")
    ap.add_argument("--memory", default="8GB")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    chunks, outdir = Path(a.chunks), Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    tmp = outdir.parent / ".duckdb-tmp"
    tmp.mkdir(exist_ok=True)
    con = connect(a.memory, tmp)

    if a.years:
        years = [int(y) for y in a.years.split(",")]
    else:
        allf = [str(f) for src in chunks.iterdir() if src.is_dir()
                for f in src.glob("*.parquet") if f.stat().st_size > 0]
        if not allf:
            print("no chunks found", file=sys.stderr)
            return 1
        lst = ",".join(f"'{f}'" for f in allf)
        years = [r[0] for r in con.execute(
            f"SELECT DISTINCT year(acq_date) y FROM read_parquet([{lst}], union_by_name=true) ORDER BY y"
        ).fetchall()]

    print(f"building {len(years)} year(s): {years[0]}..{years[-1]}", flush=True)
    total = 0
    for y in years:
        total += build_year(con, chunks, y, outdir, a.verbose)
    print(f"TOTAL {total:,} rows across {len(years)} year files", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
