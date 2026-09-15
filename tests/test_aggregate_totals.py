#!/usr/bin/env python3
"""An aggregate's totals must equal the detections it was built from.

`firms_aggregate` used to read year=<Y>/*.parquet, which matches more than the
archive: live.parquet holds the rolling window, whose rows are already in the
archive, so every overlapping day was counted twice. The aggregates and the
tileset built from them were both wrong, and nothing showed it -- the map looks
the same whether a cell says 40,000 or 41,400, and the only way to notice is to
add the cells up and compare.

Since aggregates now live beside the archive, the same glob would also have
swept them into their own input.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "firms_aggregate.py"


def main() -> int:
    failures = []

    def check(name, cond, detail=""):
        print(f"{'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' — ' + detail}")
        if not cond:
            failures.append(name)

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")

    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "detections" / "year=2031"
        d.mkdir(parents=True)
        # An archive, and a window whose rows the archive already holds.
        con.execute(f"""
            COPY (SELECT TIMESTAMP '2031-01-01' + INTERVAL (i) HOUR AS acq_datetime,
                         CAST(TIMESTAMP '2031-01-01' + INTERVAL (i) HOUR AS DATE) AS acq_date,
                         'MODIS' AS sensor, 'D' AS daynight, 1.0 AS frp,
                         ST_Point((i % 60) - 30, (i % 30) - 15) AS geometry
                  FROM range(240) t(i))
            TO '{d}/detections.parquet' (FORMAT parquet)""")
        con.execute(f"""
            COPY (SELECT * FROM read_parquet('{d}/detections.parquet') LIMIT 50)
            TO '{d}/live.parquet' (FORMAT parquet)""")

        out = Path(td) / "agg"
        r = subprocess.run(
            [sys.executable, str(TOOL), "--data", str(d.parent), "--out", str(out),
             "--year", "2031", "--resolution", "8", "--levels", "5"],
            capture_output=True, text=True)
        if r.returncode != 0:
            check("aggregate runs", False, r.stderr[-300:])
            print(f"\nFAILED: {', '.join(failures)}")
            return 1

        total = con.execute(
            f"SELECT sum(count) FROM read_parquet('{out}/cells.parquet')").fetchone()[0]
        check("aggregate total equals the archive, not archive plus window",
              total == 240, f"got {total}, archive is 240, archive+window is 290")

        # The overview must agree with its base, or the map changes totals as
        # the user crosses a band edge.
        ov = con.execute(
            f"SELECT sum(count) FROM read_parquet('{out}/cells_r5.parquet')").fetchone()[0]
        check("the r5 overview totals the same as the r8 base", ov == total,
              f"r5 {ov} vs r8 {total}")

        # Named input: an aggregate sitting beside the archive must not become
        # input to the next run.
        (d / "aggregate-r8.parquet").write_bytes((out / "cells.parquet").read_bytes())
        r2 = subprocess.run(
            [sys.executable, str(TOOL), "--data", str(d.parent), "--out", str(Path(td) / "agg2"),
             "--year", "2031", "--resolution", "8", "--levels", "5"],
            capture_output=True, text=True)
        again = con.execute(
            f"SELECT sum(count) FROM read_parquet('{Path(td) / 'agg2'}/cells.parquet')"
        ).fetchone()[0] if r2.returncode == 0 else None
        check("an aggregate beside the archive is not fed back in", again == 240,
              f"got {again} (rc={r2.returncode})")

    print()
    if failures:
        print(f"FAILED: {', '.join(failures)}")
        return 1
    print("OK: aggregate totals match the detections they were built from")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
