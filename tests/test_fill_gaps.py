#!/usr/bin/env python3
"""A year's published detections must cover every hour, and holes must be refilled.

The shape this guards happened for real: refresh-nrt stalled for six days, the
rolling window aged past the archive's end, and 42 hours of detections were in
no published file while every job but one reported green. fill_gaps finds the
hole and replaces the days it touches with a refetched copy; this checks it
does so without duplicates, refuses when the refetch does not close the hole,
and leaves the archive alone when it cannot help.

The API is not called: --chunks hands the tool what a fetch would have written.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
FILL = ROOT / "tools" / "fill_gaps.py"
MERGE = ROOT / "tools" / "merge_live.py"
NOW = "2026-09-24 10:00:00"


def write(con, path, start, end, every_min=30, sensor="MODIS", year_col=False):
    """One detection every `every_min` minutes in [start, end)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    extra = ", 2026 AS year" if year_col else ""
    con.execute(f"""
        COPY (SELECT t AS acq_datetime, CAST(t AS DATE) AS acq_date,
                     '{sensor}' AS sensor, 1.0 AS frp,
                     ST_Point(epoch(t) % 90, epoch(t) % 45) AS geometry{extra}
              FROM generate_series(TIMESTAMP '{start}', TIMESTAMP '{end}' - INTERVAL 1 MINUTE,
                                   INTERVAL ({every_min}) MINUTE) g(t))
        TO '{path}' (FORMAT parquet, COMPRESSION zstd)""")


def run(tool, data, *extra):
    return subprocess.run(
        [sys.executable, str(tool), "--data", str(data), "--year", "2026", *extra],
        capture_output=True, text=True)


def fill(data, *extra):
    return run(FILL, data, "--now", NOW, *extra)


def q(con, sql):
    return con.execute(sql).fetchone()[0]


def src(p):
    return f"read_parquet('{p}', hive_partitioning=false)"


def main() -> int:
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    failures = []

    def check(name, cond, detail=""):
        print(f"{'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' — ' + detail}")
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as td:
        # Covered: archive to the window, window to now.
        d = Path(td) / "a"
        arc = d / "year=2026/detections.parquet"
        write(con, arc, "2026-01-01", "2026-09-20")
        write(con, d / "year=2026/live.parquet", "2026-09-17", "2026-09-24 06:00")
        before = arc.stat().st_mtime_ns
        r = fill(d)
        check("a covered year passes", r.returncode == 0 and "no gap" in r.stdout,
              f"rc={r.returncode} {r.stdout[-200:]} {r.stderr[-200:]}")
        check("a covered year is left untouched", arc.stat().st_mtime_ns == before)

    with tempfile.TemporaryDirectory() as td:
        # The 2026-09 incident: the archive stops 09-15 05:46, the recovered
        # window starts 09-17 00:01.
        d = Path(td) / "b"
        arc = d / "year=2026/detections.parquet"
        live = d / "year=2026/live.parquet"
        write(con, arc, "2026-01-01", "2026-09-15 05:46", year_col=True)
        write(con, live, "2026-09-17 00:01", "2026-09-24 06:00")
        r = fill(d, "--check-only")
        check("the hole between archive and window is reported",
              r.returncode == 1 and "hole: 2026-09-15 05:30" in r.stdout
              and "2026-09-15, 2026-09-16, 2026-09-17" in r.stdout,
              f"rc={r.returncode} {r.stdout[-300:]}")

        # A refetch of 09-15..09-17 on a different cadence, so its rows are
        # told apart from the archive's partial copy of 09-15.
        chunks = Path(td) / "chunks"
        write(con, chunks / "VIIRS_SNPP_NRT/2026-09-15_3.parquet",
              "2026-09-15", "2026-09-18", every_min=20, sensor="VIIRS_SNPP")
        before_jan = q(con, f"SELECT count(*) FROM {src(arc)} WHERE acq_date < '2026-09-15'")
        r = fill(d, "--chunks", str(chunks))
        check("the hole is filled from the refetch", r.returncode == 0,
              f"rc={r.returncode} {r.stdout[-300:]} {r.stderr[-300:]}")
        check("days outside the hole keep the archive's rows",
              q(con, f"SELECT count(*) FROM {src(arc)} WHERE acq_date < '2026-09-15'")
              == before_jan)
        check("a partial edge day is replaced, not duplicated",
              q(con, f"SELECT count(*) FROM {src(arc)} WHERE acq_date = '2026-09-15'") == 72
              and q(con, f"SELECT count(DISTINCT sensor) FROM {src(arc)} "
                         f"WHERE acq_date = '2026-09-15'") == 1)
        check("the stray year column is gone",
              q(con, f"SELECT count(*) FROM parquet_schema('{arc}') WHERE name = 'year'") == 0)

        r = run(MERGE, d)
        n = q(con, f"SELECT count(*) FROM {src(arc)}")
        dups = q(con, f"SELECT count(*) - count(DISTINCT acq_datetime) FROM {src(arc)}")
        check("the window then merges", r.returncode == 0, f"{r.stderr[-300:]}")
        check("and the year has no duplicate rows", dups == 0, f"{dups} duplicates in {n}")
        r = fill(d, "--check-only")
        check("and the year now checks clean", r.returncode == 0, r.stdout[-300:])

    with tempfile.TemporaryDirectory() as td:
        # A stalled refresh: the window is frozen behind now. Nothing is lost
        # yet, but it will be, so it has to read as a hole, not "up to date".
        d = Path(td) / "c"
        write(con, d / "year=2026/detections.parquet", "2026-01-01", "2026-09-15 05:46")
        write(con, d / "year=2026/live.parquet", "2026-09-08", "2026-09-15 05:46")
        r = fill(d, "--check-only")
        check("a stale window shows as a hole at the tail",
              r.returncode == 1 and "2026-09-24 04:00:00" in r.stdout,
              f"rc={r.returncode} {r.stdout[-300:]}")

    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "d"
        arc = d / "year=2026/detections.parquet"
        write(con, arc, "2026-01-01", "2026-09-15 05:46")
        write(con, d / "year=2026/live.parquet", "2026-09-17 00:01", "2026-09-24 06:00")
        before = arc.stat().st_mtime_ns

        empty = Path(td) / "empty/VIIRS_SNPP_NRT"
        empty.mkdir(parents=True)
        (empty / "2026-09-15_3.parquet").write_bytes(b"")  # fetched, no rows
        r = fill(d, "--chunks", str(empty.parent))
        check("a refetch with nothing in it is refused",
              r.returncode == 1 and arc.stat().st_mtime_ns == before,
              f"rc={r.returncode} {r.stderr[-200:]}")

        r = subprocess.run([sys.executable, str(FILL), "--data", str(d), "--year", "2026",
                            "--now", NOW, "--key", ""], capture_output=True, text=True)
        check("holes with no key to refetch them fail",
              r.returncode == 2 and arc.stat().st_mtime_ns == before,
              f"rc={r.returncode} {r.stderr[-200:]}")

        short = Path(td) / "short"
        write(con, short / "VIIRS_SNPP_NRT/2026-09-15_1.parquet",
              "2026-09-15", "2026-09-16", every_min=20, sensor="VIIRS_SNPP")
        r = fill(d, "--chunks", str(short))
        check("a refetch that leaves a hole fails, and changes nothing",
              r.returncode == 1 and "still uncovered: 2026-09-15 23:40" in r.stderr
              and arc.stat().st_mtime_ns == before,
              f"rc={r.returncode} {r.stderr[-300:]}")

    print()
    if failures:
        print(f"FAILED: {', '.join(failures)}")
        return 1
    print("OK: every hour of the year is covered, and holes are refilled without duplicates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
