#!/usr/bin/env python3
"""Build the all-time monthly archive: every month since 2000 on coarse cells.

Measured rather than assumed. Three numbers decided this shape:

  * coarse cells with fine time columns are cheap -- r6 x 366 daily = 2.9 MB
  * the r6 cell union converges, new cells decaying 13,558 -> 375 across seven
    spread years, because fire recurs in the same places
  * so 27 years of monthly columns costs about 7-8 MB, at a flat marginal
    0.261 MB per added year

An earlier estimate of ~79 MB scaled naively from per-year file sizes and was
wrong; if a build lands near that figure, stop and re-measure rather than
assume the architecture still holds.

Each year is aggregated independently and cached, so the archive builds from
the years published today and extends as the rest land: adding a year is a
re-join, not a re-read.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import duckdb

PUBLIC = "https://data.source.coop/portolan-mirrors/firms-catalog/detections"
BASE_RES = 6
OVERVIEW = 4
ZSTD_LEVEL = 22


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-1500:], r.stderr[-1500:], file=sys.stderr)
        raise SystemExit(f"failed: {' '.join(cmd[:6])}")


def year_cells(con, year: int, cache: Path) -> Path:
    """One year aggregated to r6 with a month breakdown, cached on disk."""
    out = cache / f"cells_{year}_m.parquet"
    if out.exists():
        return out
    # Only geometry and the month are needed, so column pruning keeps this to
    # a fraction of each 460 MB file. The month is formatted YYYYMM because
    # gpio names each pivot column after the value it saw, and the archive's
    # declared axis requires six digits.
    src = cache / f"{year}_min.parquet"
    con.execute(f"""
        COPY (SELECT geometry, strftime(acq_datetime, '%Y%m') AS month, frp
              FROM read_parquet('{PUBLIC}/year={year}/detections.parquet'))
        TO '{src}' (FORMAT parquet, COMPRESSION zstd)""")
    run(["gpio", "process", "aggregate", "a5", str(src), str(out),
         "--resolution", str(BASE_RES), "--metric", "sum:frp,avg:frp,max:frp",
         "--breakdown", "month", "--breakdown-limit", "12",
         "--out-geometry", "polygon", "--geoparquet-version", "2.0"])
    src.unlink(missing_ok=True)
    return out


def join_years(con, parts: dict[int, Path], out: Path) -> None:
    """One row per cell, one column per month, across every year."""
    sel = ["u.a5_cell", "any_value(u.geometry) AS geometry"]
    frm = []
    # The shared rollups have to survive the join: gpio process overview needs
    # a count column to roll levels up, and refuses the file without one.
    # They are combined across years rather than taken from any single year --
    # count and sum add, max takes the extreme, and the average is weighted by
    # count, because a plain mean of yearly means would weight a quiet year the
    # same as a busy one.
    tot, sums, maxes = [], [], []
    for i, (_year, path) in enumerate(sorted(parts.items())):
        cols = sorted(
            c[0] for c in con.execute(
                f"DESCRIBE SELECT * FROM '{path}'").fetchall()
            if c[0].startswith("count_"))
        sel += [f't{i}."{c}" AS "{c}"' for c in cols]
        frm.append(f"LEFT JOIN '{path}' t{i} ON t{i}.a5_cell = u.a5_cell")
        tot.append(f"coalesce(t{i}.count, 0)")
        sums.append(f"coalesce(t{i}.sum_frp, 0)")
        maxes.append(f"t{i}.max_frp")
    total = " + ".join(tot)
    total_frp = " + ".join(sums)
    sel += [f"({total}) AS count",
            f"({total_frp}) AS sum_frp",
            f"greatest({', '.join(maxes)}) AS max_frp",
            f"CASE WHEN ({total}) > 0 THEN ({total_frp}) / ({total}) END "
            f"AS avg_frp"]
    union = " UNION ".join(
        f"SELECT a5_cell, geometry FROM '{p}'" for p in parts.values())
    con.execute(f"""
        COPY (SELECT {', '.join(sel)}
              FROM (SELECT a5_cell, any_value(geometry) AS geometry
                    FROM ({union}) GROUP BY a5_cell) u
              {' '.join(frm)} GROUP BY ALL)
        TO '{out}' (FORMAT parquet, COMPRESSION zstd,
                    COMPRESSION_LEVEL {ZSTD_LEVEL})""")


def parse_years(text: str) -> list[int]:
    if "-" in text and "," not in text:
        lo, hi = (int(x) for x in text.split("-"))
        return list(range(lo, hi + 1))
    return [int(y) for y in text.split(",")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", required=True, help="2000-2026 or 2000,2001")
    ap.add_argument("--cache", default="../catalog-staging/alltime")
    ap.add_argument("--tiles", default="../catalog-staging/preview")
    ap.add_argument("--catalog", default="catalog/detections")
    ap.add_argument("--skip-tiles", action="store_true",
                    help="build the joined parquet and stop")
    a = ap.parse_args()

    cache = Path(a.cache)
    cache.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")

    parts: dict[int, Path] = {}
    for y in parse_years(a.years):
        try:
            parts[y] = year_cells(con, y, cache)
            print(f"  {y}: {parts[y].stat().st_size / 1e6:5.2f} MB", flush=True)
        except Exception as exc:  # noqa: BLE001 - an unpublished year is normal
            print(f"  {y}: skipped ({str(exc)[:70]})", flush=True)
    if not parts:
        raise SystemExit("no years aggregated")

    combined = cache / "alltime.parquet"
    join_years(con, parts, combined)
    size = combined.stat().st_size / 1e6
    ncol = len(con.execute(f"DESCRIBE SELECT * FROM '{combined}'").fetchall())
    nrow = con.execute(f"SELECT count(*) FROM '{combined}'").fetchone()[0]
    print(f"\njoined {len(parts)} year(s): {nrow:,} cells x {ncol} cols "
          f"= {size:.1f} MB")
    if size > 25:
        print("  WARNING: far above the measured 7-8 MB. Stop and re-measure "
              "before building on this.", file=sys.stderr)

    if a.skip_tiles:
        return 0

    run(["gpio", "process", "overview", str(combined),
         "--levels", str(OVERVIEW), "--force"])

    tiles = Path(a.tiles)
    tiles.mkdir(parents=True, exist_ok=True)
    archive = tiles / "alltime.pmtiles"
    run(["gpio", "pmtiles", "pyramid", str(combined), str(archive),
         "--levels", str(OVERVIEW), "-f"])
    print(f"  {archive} ({archive.stat().st_size / 1e6:.1f} MB)")

    here = Path(__file__).resolve().parent
    run(["python3", str(here / "make_timeline.py"), str(archive)])
    run(["python3", str(here / "make_breaks.py"), str(archive),
         "--band", f"{BASE_RES}:{combined}",
         "--band", f"{OVERVIEW}:{cache / f'alltime_r{OVERVIEW}.parquet'}"])
    run(["python3", str(here / "make_styles.py"), str(archive),
         "--out", str(Path(a.catalog) / "styles-alltime"),
         "--tiles", "../alltime.pmtiles", "--suffix", ", all years"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
