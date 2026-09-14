#!/usr/bin/env python3
"""The rolling window must fold into its year without gaps or duplicates.

The failure this guards is silent by construction: the window slides forward
and the archive does not, so once they stop overlapping the detections in
between are in no published file, every job still succeeds, and the row count
still climbs. A test is the only thing that notices.
"""
import subprocess
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "merge_live.py"


def write(con, path, start, hours, sensor="MODIS"):
    path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"""
        COPY (SELECT
                TIMESTAMP '{start}' + INTERVAL (i) HOUR AS acq_datetime,
                CAST(TIMESTAMP '{start}' + INTERVAL (i) HOUR AS DATE) AS acq_date,
                '{sensor}' AS sensor, 1.0 AS frp,
                ST_Point(i % 90, i % 45) AS geometry,
                {int(start[:4])} AS year
              FROM range({hours}) t(i))
        TO '{path}' (FORMAT parquet, COMPRESSION zstd)""")


def run(data, year, *extra):
    return subprocess.run(
        [sys.executable, str(TOOL), "--data", str(data), "--year", str(year), *extra],
        capture_output=True, text=True)


def rows(con, p):
    return con.execute(f"SELECT count(*) FROM read_parquet('{p}')").fetchone()[0]


def main() -> int:
    import tempfile
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    failures = []

    def check(name, cond, detail=""):
        print(f"{'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' — ' + detail}")
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as td:
        # Overlapping: archive to 12:00, window from 06:00 past it.
        d = Path(td) / "a"
        write(con, d / "year=2026/detections.parquet", "2026-09-01 00:00:00", 13)
        write(con, d / "year=2026/live.parquet", "2026-09-01 06:00:00", 18)
        r = run(d, 2026)
        n = rows(con, d / "year=2026/detections.parquet")
        # 6 archive rows before the window, plus the window's 18.
        check("overlapping window merges without duplicates", r.returncode == 0 and n == 24,
              f"rc={r.returncode} rows={n} {r.stderr[-200:]}")
        hi = con.execute(
            f"SELECT max(acq_datetime) FROM read_parquet('{d}/year=2026/detections.parquet')"
        ).fetchone()[0]
        check("merged archive reaches the window's end", str(hi) == "2026-09-01 23:00:00", str(hi))

    with tempfile.TemporaryDirectory() as td:
        # Disjoint: the hole this tool exists to catch.
        d = Path(td) / "b"
        write(con, d / "year=2026/detections.parquet", "2026-09-01 00:00:00", 5)
        write(con, d / "year=2026/live.parquet", "2026-09-08 00:00:00", 5)
        r = run(d, 2026)
        check("a window past the archive is refused", r.returncode == 1 and "gap" in r.stderr,
              f"rc={r.returncode} {r.stderr[-200:]}")
        check("refusing leaves the archive untouched",
              rows(con, d / "year=2026/detections.parquet") == 5)
        r = run(d, 2026, "--allow-gap")
        check("--allow-gap merges anyway", r.returncode == 0,
              f"rc={r.returncode} {r.stderr[-200:]}")

    with tempfile.TemporaryDirectory() as td:
        # Nothing new: the window is entirely behind the archive's end.
        d = Path(td) / "c"
        write(con, d / "year=2026/detections.parquet", "2026-09-01 00:00:00", 48)
        write(con, d / "year=2026/live.parquet", "2026-09-01 06:00:00", 5)
        r = run(d, 2026)
        check("a window the archive already covers is a no-op",
              r.returncode == 0 and rows(con, d / "year=2026/detections.parquet") == 48,
              r.stdout[-150:])

    with tempfile.TemporaryDirectory() as td:
        # New year: no archive yet, and the window carries its first days.
        d = Path(td) / "d"
        write(con, d / "year=2027/live.parquet", "2027-01-01 00:00:00", 30)
        r = run(d, 2027)
        seeded = d / "year=2027/detections.parquet"
        check("a new year is seeded from the window",
              r.returncode == 0 and seeded.exists() and rows(con, seeded) == 30,
              f"rc={r.returncode} {r.stderr[-200:]}")

    with tempfile.TemporaryDirectory() as td:
        # The same shape mid-year is not a new year, it is a missing archive.
        d = Path(td) / "e"
        write(con, d / "year=2027/live.parquet", "2027-06-01 00:00:00", 30)
        r = run(d, 2027)
        check("an archive missing mid-year is refused, not seeded",
              r.returncode == 1 and not (d / "year=2027/detections.parquet").exists(),
              f"rc={r.returncode} {r.stderr[-200:]}")

    with tempfile.TemporaryDirectory() as td:
        # Across the turn the window spans two years; each is handled on its
        # own, and the old year must not be seeded from a partial window.
        d = Path(td) / "f"
        write(con, d / "year=2026/detections.parquet", "2026-12-01 00:00:00", 24 * 30)
        write(con, d / "year=2026/live.parquet", "2026-12-28 00:00:00", 96)
        write(con, d / "year=2027/live.parquet", "2027-01-01 00:00:00", 48)
        r26, r27 = run(d, 2026), run(d, 2027)
        hi26 = con.execute(
            f"SELECT max(acq_datetime) FROM read_parquet('{d}/year=2026/detections.parquet')"
        ).fetchone()[0]
        check("across the turn the old year extends to its end",
              r26.returncode == 0 and str(hi26) == "2026-12-31 23:00:00",
              f"rc={r26.returncode} hi={hi26}")
        check("across the turn the new year is seeded",
              r27.returncode == 0 and rows(con, d / "year=2027/detections.parquet") == 48,
              f"rc={r27.returncode} {r27.stderr[-200:]}")

    print()
    if failures:
        print(f"FAILED: {', '.join(failures)}")
        return 1
    print("OK: the rolling window folds into its year without gaps or duplicates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
