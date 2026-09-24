#!/usr/bin/env python3
"""Fold the rolling window into its year's archive part.

Why this exists
---------------
`refresh-nrt` rewrites `live.parquet` -- a fixed span back from now -- and
nothing else. The year's `detections.parquet` only moves when someone runs a
year build by hand. That is fine while the window still reaches back past the
archive's last row, and silently lossy the moment it does not: the window
slides forward a day per day, the archive does not, and once they stop
overlapping every detection in between belongs to no published file. Nothing
errors. The collection's row count keeps climbing, because the window keeps
carrying rows; they are just newer ones, with a hole behind them.

So the archive is extended on a cadence shorter than the window is long, and
the overlap is asserted rather than assumed -- if it has already closed, that
is the one thing this tool must not paper over.

The window replaces the archive's tail rather than being appended to it. Both
carry the days they share, and the archive's copy of them is the poorer one:
it was written while those days were still arriving. `firms_build` unions with
no dedup, so an append would double every shared row.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent


def span(con, path: Path) -> tuple[datetime, datetime, int]:
    return con.execute(
        f"SELECT min(acq_datetime), max(acq_datetime), count(*) "
        f"FROM read_parquet('{path}')"
    ).fetchone()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True,
                    help="detections/ directory holding year=<YYYY>/")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--memory", default="6GB")
    ap.add_argument("--seed-days", type=int, default=10,
                    help="how far into a year an absent archive is still "
                         "normal (default 10). A year has no archive until "
                         "the first consolidation of it, so early January the "
                         "window IS the year and seeding is correct. Later in "
                         "the year an absent archive means something is wrong "
                         "and the window would silently become the whole year.")
    ap.add_argument("--allow-gap", action="store_true",
                    help="merge even where the window no longer reaches the "
                         "archive. The hole is already there and merging does "
                         "not create it, but it does bury the evidence, so it "
                         "takes an explicit flag.")
    a = ap.parse_args()

    part = Path(a.data) / f"year={a.year}"
    archive, live = part / "detections.parquet", part / "live.parquet"
    if not live.exists():
        print(f"no {live}; nothing to merge", flush=True)
        return 0
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{a.memory}'")
    con.execute("INSTALL spatial; LOAD spatial;")

    live_lo, live_hi, live_n = span(con, live)
    if not archive.exists():
        # A year begins with no archive part, and the rolling window is the
        # only thing that has its first days. Seeding from it is right then
        # and only then: an archive missing in, say, June means something
        # deleted it, and treating the window as the whole year would publish
        # seven days of detections as a year's worth.
        doy = live_lo.timetuple().tm_yday
        if live_lo.year != a.year or doy > a.seed_days:
            print(f"no {archive}, and the window starts {live_lo} -- day {doy} "
                  f"of {live_lo.year}, past the {a.seed_days}-day seeding "
                  f"window. Refusing to publish a rolling window as a whole "
                  f"year; restore or backfill the archive first.", file=sys.stderr)
            return 1
        print(f"no archive for {a.year} yet; seeding it from the window "
              f"({live_lo} .. {live_hi}, {live_n:,} rows)", flush=True)
        part.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=part) as tmp:
            parts = Path(tmp) / "parts"
            parts.mkdir()
            shutil.copy2(live, parts / "live_tail.parquet")
            r = subprocess.run(
                ["python3", str(HERE / "firms_build.py"), "--chunks", tmp,
                 "--out", str(a.data), "--years", str(a.year), "--memory", a.memory],
                text=True)
            if r.returncode != 0:
                return r.returncode
        lo, hi, n = span(con, archive)
        print(f"seeded  {lo} .. {hi}  {n:,} rows", flush=True)
        return 0
    arc_lo, arc_hi, arc_n = span(con, archive)
    print(f"archive {arc_lo} .. {arc_hi}  {arc_n:,} rows", flush=True)
    print(f"window  {live_lo} .. {live_hi}  {live_n:,} rows", flush=True)

    if live_lo > arc_hi:
        msg = (f"the window starts {live_lo} but the archive ends {arc_hi}: "
               f"detections in between are in no published file. Backfill that "
               f"range first (tools/fill_gaps.py), or pass --allow-gap to merge "
               f"anyway.")
        if not a.allow_gap:
            print(f"gap: {msg}", file=sys.stderr)
            return 1
        print(f"warning: {msg}", file=sys.stderr)
    if live_hi <= arc_hi:
        print("the archive already covers the window; nothing to do", flush=True)
        return 0

    with tempfile.TemporaryDirectory(dir=part) as tmp:
        parts = Path(tmp) / "parts"
        parts.mkdir()
        # Everything the window does not cover, then the window whole.
        con.execute(f"""
            COPY (SELECT * FROM read_parquet('{archive}')
                  WHERE acq_datetime < TIMESTAMP '{live_lo}')
            TO '{parts / "archive_head.parquet"}' (FORMAT parquet, COMPRESSION zstd)""")
        shutil.copy2(live, parts / "live_tail.parquet")
        r = subprocess.run(
            ["python3", str(HERE / "firms_build.py"), "--chunks", tmp,
             "--out", str(a.data), "--years", str(a.year), "--memory", a.memory],
            text=True)
        if r.returncode != 0:
            return r.returncode

    lo, hi, n = span(con, archive)
    print(f"merged  {lo} .. {hi}  {n:,} rows", flush=True)
    if hi < live_hi:
        print(f"merge did not reach the window's end {live_hi}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
