#!/usr/bin/env python3
"""Find the hours a year's published detections do not cover, and refetch them.

Why this exists
---------------
Between them, the year's `detections.parquet` and its rolling `live.parquet`
are meant to cover every hour of the year up to now. Nothing used to check
that. From 2026-09-16 to 09-21 refresh-nrt failed every run, so `live.parquet`
stayed frozen on 09-08..09-15; consolidate-year compared that stale window to
the archive, found "nothing to do", and went green six days running. When the
refresh recovered, its seven-day window started on 09-16 00:01 while the
archive ended on 09-15 05:46. The 42 hours between had aged out of the only
feed that carried them, and merge_live's gap guard could refuse the merge but
had no way to repair it.

The repair source is the FIRMS area API, which serves any day, not just the
last seven. It needs a MAP_KEY and costs transactions, so it only fetches what
the check below finds missing.

What counts as a hole
---------------------
A span of more than --max-gap hours with no detection from ANY sensor. Four
satellites and some fire somewhere on Earth mean the published data never goes
quiet: the longest such span in 2026 up to 09-15 was 36 minutes. Three hours
is five times that, so a hole is always lost data, never a quiet night.

A single sensor going quiet is not treated as a hole. Satellites do go dark
(VIIRS_SNPP has several multi-day gaps in 2026), and refetching a real outage
every day would find nothing, forever. Those are reported, not repaired.

The span checked is the year's start to `now - --latency`, so a stalled
refresh shows up as a hole at the tail, and gets filled from the API, instead
of reading as an archive that is simply up to date.

Repair
------
Every day a hole touches is refetched whole, from each source FIRMS lists as
covering that day (science-quality and NRT ranges never overlap for a sensor,
the same rule the backfill relies on). Those days replace the archive's copy
outright: the archive's edge days were written while they were still arriving
and are partial by construction, so appending would duplicate and keeping them
would leave them short. The coverage check then runs again, and anything still
uncovered fails the run.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from firms_fetch import MAX_WINDOW, SOURCES, fetch, normalize  # noqa: E402

AVAIL = "https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{key}/ALL"


def parts(part: Path) -> list[Path]:
    return [p for p in (part / "detections.parquet", part / "live.parquet") if p.exists()]


def read(files) -> str:
    # hive_partitioning off: these files sit under year=<Y>/, and DuckDB would
    # otherwise invent a `year` column from the path.
    lst = ",".join(f"'{f}'" for f in files)
    return f"read_parquet([{lst}], union_by_name=true, hive_partitioning=false)"


def holes(con, files, lo: datetime, hi: datetime, max_gap_h: float):
    """Spans in [lo, hi] longer than max_gap_h with no detection at all."""
    if not files:
        return [(lo, hi)] if hi > lo else []
    return con.execute(f"""
        WITH t AS (
          SELECT DISTINCT acq_datetime AS t FROM {read(files)}
          WHERE acq_datetime > TIMESTAMP '{lo}' AND acq_datetime < TIMESTAMP '{hi}'
          UNION ALL SELECT TIMESTAMP '{lo}'
          UNION ALL SELECT TIMESTAMP '{hi}'),
        g AS (SELECT lag(t) OVER (ORDER BY t) AS a, t AS b FROM t)
        SELECT a, b FROM g
        WHERE b - a > INTERVAL ({max_gap_h * 60}) MINUTE ORDER BY a""").fetchall()


def quiet_sensors(con, files, max_gap_h: float):
    """Per-sensor silences: reported, never repaired (see the module doc)."""
    if not files:
        return []
    return con.execute(f"""
        WITH t AS (SELECT DISTINCT sensor, acq_datetime AS t FROM {read(files)}),
        g AS (SELECT sensor, lag(t) OVER (PARTITION BY sensor ORDER BY t) AS a, t AS b FROM t)
        SELECT sensor, a, b FROM g
        WHERE b - a > INTERVAL ({max_gap_h * 60}) MINUTE ORDER BY sensor, a""").fetchall()


def days_touched(spans, year: int, today: date) -> list[date]:
    out = set()
    for a, b in spans:
        d = max(a.date(), date(year, 1, 1))
        while d <= min(b.date(), date(year, 12, 31), today):
            out.add(d)
            d += timedelta(days=1)
    return sorted(out)


def availability(key: str, tries: int = 6) -> dict[str, tuple[date, date]]:
    # Slow and intermittently unreachable (see backfill.yml), so retried.
    delay = 10.0
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(AVAIL.format(key=key), timeout=120) as r:
                body = r.read().decode("utf-8", "replace")
            return {row["data_id"]: (date.fromisoformat(row["min_date"]),
                                     date.fromisoformat(row["max_date"]))
                    for row in csv.DictReader(io.StringIO(body))}
        except Exception as exc:  # noqa: BLE001
            if attempt == tries - 1:
                raise
            print(f"  retry data_availability ({exc})", file=sys.stderr, flush=True)
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


def runs(days: list[date]):
    """Contiguous runs of days, cut to the API's window cap."""
    i = 0
    while i < len(days):
        j = i
        while j + 1 < len(days) and days[j + 1] - days[j] == timedelta(days=1) \
                and j + 1 - i < MAX_WINDOW:
            j += 1
        yield days[i], j - i + 1
        i = j + 1


def fetch_days(con, key: str, days: list[date], out: Path) -> None:
    avail = availability(key)
    for source in SOURCES:
        if source not in avail:
            continue
        lo, hi = avail[source]
        mine = [d for d in days if lo <= d <= hi]
        for start, span in runs(mine):
            dest = out / source / f"{start.isoformat()}_{span}.parquet"
            dest.parent.mkdir(parents=True, exist_ok=True)
            n = normalize(con, fetch(key, source, start, span), source, dest)
            print(f"  {source} {start} +{span}d: {n:,} rows", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True,
                    help="detections/ directory holding year=<YYYY>/")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--now", help="ISO timestamp to treat as now (UTC; default: the clock)")
    ap.add_argument("--max-gap", type=float, default=3.0,
                    help="hours with no detection that count as a hole (default 3)")
    ap.add_argument("--latency", type=float, default=6.0,
                    help="hours back from now the data must reach (default 6; "
                         "FIRMS NRT lags observation by about 3)")
    ap.add_argument("--check-only", action="store_true",
                    help="report holes and exit 1 if any, without fetching")
    ap.add_argument("--chunks",
                    help="use already-fetched chunks from this directory "
                         "instead of calling the API")
    ap.add_argument("--key", default=os.environ.get("FIRMS_MAP_KEY", ""))
    ap.add_argument("--memory", default="6GB")
    a = ap.parse_args()

    part = Path(a.data) / f"year={a.year}"
    archive = part / "detections.parquet"
    now = (datetime.fromisoformat(a.now) if a.now
           else datetime.now(timezone.utc).replace(tzinfo=None))
    lo = datetime(a.year, 1, 1)
    hi = min(now - timedelta(hours=a.latency), datetime(a.year + 1, 1, 1))
    if hi <= lo:
        print(f"{a.year} has not started far enough to check", flush=True)
        return 0
    if not archive.exists():
        # merge_live owns this case: seeding early in a year, refusing later.
        print(f"no {archive}; nothing to check", flush=True)
        return 0

    con = duckdb.connect()
    con.execute(f"SET memory_limit='{a.memory}'")
    con.execute("INSTALL spatial; LOAD spatial;")

    for sensor, s, e in quiet_sensors(con, [archive], 24):
        print(f"note: {sensor} silent {s} .. {e} (not repaired: may be a real outage)",
              flush=True)

    found = holes(con, parts(part), lo, hi, a.max_gap)
    if not found:
        print(f"{a.year}: covered {lo} .. {hi}, no gap over {a.max_gap:g}h", flush=True)
        return 0
    for s, e in found:
        print(f"hole: {s} .. {e} ({e - s})", flush=True)
    days = days_touched(found, a.year, now.date())
    print(f"days to refetch: {', '.join(d.isoformat() for d in days)}", flush=True)
    if a.check_only:
        return 1
    if not a.chunks and not a.key:
        print("error: holes found but no FIRMS_MAP_KEY to refetch them", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(dir=part) as tmp:
        tmp = Path(tmp)
        got = tmp / "fetched"
        if a.chunks:
            got = Path(a.chunks)
        else:
            fetch_days(con, a.key, days, got)
        chunk_files = [f for f in got.glob("*/*.parquet") if f.stat().st_size > 0]
        if not chunk_files:
            print("error: FIRMS returned nothing for the missing days", file=sys.stderr)
            return 1
        dl = ",".join(f"DATE '{d}'" for d in days)
        # Only build/ is handed to firms_build, which reads every subdirectory
        # of --chunks: the raw fetched chunks beside it would count twice.
        build = tmp / "build"
        keep = build / "parts" / "archive_keep.parquet"
        fill = build / "parts" / "refetched.parquet"
        keep.parent.mkdir(parents=True)
        # Whole days out, whole days in: the refetched copy of a day replaces
        # the archive's, so a partial edge day is completed, not duplicated.
        con.execute(f"""
            COPY (SELECT * FROM {read([archive])}
                  WHERE acq_date NOT IN ({dl}))
            TO '{keep}' (FORMAT parquet, COMPRESSION zstd)""")
        con.execute(f"""
            COPY (SELECT * FROM {read(chunk_files)}
                  WHERE acq_date IN ({dl}))
            TO '{fill}' (FORMAT parquet, COMPRESSION zstd)""")
        n_keep = con.execute(f"SELECT count(*) FROM {read([keep])}").fetchone()[0]
        n_fill = con.execute(f"SELECT count(*) FROM {read([fill])}").fetchone()[0]
        print(f"keeping {n_keep:,} archive rows, adding {n_fill:,} refetched", flush=True)
        if n_fill == 0:
            print("error: no refetched rows fall on the missing days", file=sys.stderr)
            return 1
        # Built beside the archive, not over it, and swapped in only once it
        # checks clean: a repair that does not close the hole must leave the
        # archive exactly as it found it, evidence and all.
        out = tmp / "out"
        r = subprocess.run(
            [sys.executable, str(HERE / "firms_build.py"), "--chunks", str(build),
             "--out", str(out), "--years", str(a.year), "--memory", a.memory],
            text=True)
        if r.returncode != 0:
            return r.returncode
        new = out / f"year={a.year}" / "detections.parquet"
        n = con.execute(f"SELECT count(*) FROM {read([new])}").fetchone()[0]
        if n != n_keep + n_fill:
            print(f"error: rebuilt archive has {n:,} rows, expected {n_keep + n_fill:,}",
                  file=sys.stderr)
            return 1
        left = holes(con, [new] + [p for p in parts(part) if p != archive],
                     lo, hi, a.max_gap)
        for s, e in left:
            print(f"still uncovered: {s} .. {e} ({e - s})", file=sys.stderr)
        if left:
            return 1
        os.replace(new, archive)
    print(f"{a.year}: filled; covered {lo} .. {hi}, {n:,} archive rows", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
