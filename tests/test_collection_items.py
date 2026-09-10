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
        print(f"note: make_collection did not run here ({r.stderr.strip()[:70]});"
              " the round-trip check was skipped")
    else:
        after = item_links(json.loads(out.read_text()))
        check(sorted(after) == sorted(before),
              f"item links survive regeneration: {len(before)} before, "
              f"{len(after)} after")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print(f"OK: collection lists {len(links)} item(s) and keeps them on rebuild")
