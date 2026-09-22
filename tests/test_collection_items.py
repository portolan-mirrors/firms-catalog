#!/usr/bin/env python3
"""Regenerating the collection must not drop its item links.

make_collection.py builds `links` from a literal, and make_items.py appends one
rel:item per published year afterwards. Run in that order it works; run
make_collection.py again and every item link disappears silently -- no error,
no failed gate, just a catalog that has stopped listing its items. This is the
gate that makes that loud.

No network: it runs the real builder against a temp tree.

Run: python3 tests/test_collection_items.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

errors: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def item_links(doc: dict) -> list[str]:
    return [l["href"] for l in doc.get("links", []) if l.get("rel") == "item"]


live = ROOT / "catalog" / "detections" / "collection.json"
if not live.exists():
    print("no collection.json to check; skipping")
    raise SystemExit(0)

doc = json.loads(live.read_text())
years = sorted(p.name.split("=")[1]
               for p in (ROOT / "catalog" / "detections").glob("year=*")
               if (p / f"{p.name.split('=')[1]}.json").exists())
links = item_links(doc)

check(len(links) == len(years),
      f"one item link per published year: {len(links)} links, {len(years)} years")
for y in years:
    check(any(f"year={y}/{y}.json" in h for h in links),
          f"year {y} has an item link")

# The real defence: run the builder and confirm the links survive it.
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "collection.json"
    out.write_text(live.read_text())
    before = item_links(json.loads(out.read_text()))
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_collection.py"),
         "--data", str(ROOT / ".." / "catalog-staging" / "publish" / "detections"),
         "--out", str(out)],
        capture_output=True, text=True)
    if r.returncode != 0:
        # No staged data locally is normal; the static checks above still ran.
        # Anything else is a real break and must not read as a skip: when the
        # builder started refusing a window-only tree, this printed a note and
        # passed while the hourly refresh failed every hour for a week.
        if "no year tables under" in r.stderr:
            print("note: nothing staged here; the round-trip check was skipped")
        else:
            check(False, f"make_collection failed: {r.stderr.strip()[-300:]}")
    else:
        after = item_links(json.loads(out.read_text()))
        check(sorted(after) == sorted(before),
              f"item links survive regeneration: {len(before)} before, "
              f"{len(after)} after")

# The year span in the description comes from the items, not the staged files.
#
# --data sees only what is staged, and CI stages the rolling window plus the
# current year. Deriving the span from that published a collection describing
# twenty-seven years of fire as "covering 2026 to 2026" -- the same mistake the
# extent and row count had already been fixed for, left in the one place that
# feeds the prose a reader actually sees. This pins the CI shape directly: one
# staged year in, the full published span out.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "make_collection", ROOT / "tools" / "make_collection.py")
_mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mc)

_years = [2026]
_mc.widen_from_items(ROOT / "catalog" / "detections",
                     (None, None, None, None, None, None, 0), _years)
_years = sorted(set(_years))
_ids = sorted(int(p.parent.name.split("=")[1])
              for p in (ROOT / "catalog" / "detections").glob("year=*/[0-9]*.json"))
check(_years == _ids,
      f"year span widens from the items: got {_years[:1]}..{_years[-1:]} "
      f"({len(_years)}), items are {_ids[:1]}..{_ids[-1:]} ({len(_ids)})")

# The hourly refresh stages the rolling window and nothing else.
#
# It never downloads the archives it is extending, so year=<Y>/live.parquet is
# the only table under --data. A guard that required a detections.parquet there
# turned every hourly run into a failed job -- fifty-five of them -- while every
# other gate stayed green, because nothing else runs the builder in that shape.
# This does, against a temp copy of the real item tree so the window is counted
# past its year's published end the way it is in production.
import shutil  # noqa: E402

import duckdb  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    stage = Path(tmp) / "stage/detections/year=2026"
    stage.mkdir(parents=True)
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute(f"""
        COPY (SELECT
                TIMESTAMP '2026-09-20 00:00:00' + INTERVAL (i) HOUR AS acq_datetime,
                CAST(TIMESTAMP '2026-09-20 00:00:00' + INTERVAL (i) HOUR AS DATE) AS acq_date,
                'VIIRS' AS sensor, 1.0 AS frp,
                ST_Point((i % 180) - 90, (i % 90) - 45) AS geometry,
                2026 AS year
              FROM range(72) t(i))
        TO '{stage / "live.parquet"}' (FORMAT parquet, COMPRESSION zstd)""")

    cat = Path(tmp) / "cat"
    shutil.copytree(ROOT / "catalog" / "detections", cat)
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_collection.py"),
         "--data", str(stage.parent), "--out", str(cat / "collection.json")],
        capture_output=True, text=True)
    check(r.returncode == 0,
          f"a window-only staging tree builds: rc={r.returncode} "
          f"{r.stderr.strip()[-200:]}")
    if r.returncode == 0:
        rebuilt = json.loads((cat / "collection.json").read_text())
        # Counted past the archive, not from zero and not twice: the item says
        # where the published year ends and only the rows after that are new.
        check(rebuilt["table:row_count"] == doc["table:row_count"] + 72,
              f"the window is counted past the items: "
              f"{doc['table:row_count']} + 72 != {rebuilt['table:row_count']}")
        check(sorted(item_links(rebuilt)) == sorted(links),
              "a window-only rebuild keeps every item link")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print(f"OK: collection lists {len(links)} item(s) and keeps them on rebuild")
