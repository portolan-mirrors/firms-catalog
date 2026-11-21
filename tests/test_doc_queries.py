#!/usr/bin/env python3
"""Run every SQL block in the published docs against the published data.

The catalog's documents promise that each query was run before it was written
down. This gate keeps that promise true after publication: it extracts every
fenced ```sql block from the documents under catalog/, runs the blocks of
each document in order in one DuckDB session, and fails on the first error.

One session per document matters: a document states its INSTALL/LOAD and SET
statements once, in its first block, and the later blocks assume them, the
same way a reader's session would.

Results are not asserted, only success. The data refreshes hourly, so any
pinned number would fail the moment the mirror does its job.

The queries read hundreds of megabytes from the public bucket, so this gate
runs on a schedule (.github/workflows/check-docs.yml), not on pull requests.

Run: python3 tests/test_doc_queries.py
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted(ROOT.glob("catalog/**/*.md"))
BLOCK = re.compile(r"^```sql\s*$(.*?)^```\s*$", re.M | re.S)


def main() -> int:
    failures = 0
    total = 0
    for doc in DOCS:
        blocks = BLOCK.findall(doc.read_text())
        if not blocks:
            continue
        rel = doc.relative_to(ROOT)
        con = duckdb.connect()
        con.execute("SET enable_progress_bar = false")
        for i, sql in enumerate(blocks, 1):
            total += 1
            started = time.time()
            try:
                con.execute(sql)
                print(f"ok    {rel} block {i} ({time.time() - started:.0f}s)")
            except Exception as exc:  # noqa: BLE001 - report and continue
                failures += 1
                first = str(exc).splitlines()[0]
                print(f"FAIL  {rel} block {i}: {first}")
        con.close()
    if total == 0:
        print("error  no ```sql blocks found under catalog/")
        return 1
    print(f"\n{total - failures} of {total} documented quer"
          f"{'y' if total == 1 else 'ies'} ran clean")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
