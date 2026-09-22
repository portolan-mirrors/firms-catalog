#!/usr/bin/env python3
"""Every asset an item advertises must actually be published.

An item that names a file which is not there is worse than an item with no
asset at all: a viewer registers the layer, the fetch 404s, and MapLibre never
reaches "loaded", so `idle` never fires and every idle-driven readout freezes
at its initial value. That failure has happened in this repo more than once.

It happened again when the per-year tile asset keyed off a local
styles/default.json -- styles are generated and committed, the archive is
uploaded separately, so the item promised a fire-<year>.pmtiles that 404ed.

Network gate. Skipped when offline, because a missing network is not a broken
catalog.

Run: python3 tests/test_item_assets.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog" / "detections"
sys.path.insert(0, str(ROOT / "tools"))

from net import force_ipv4, head  # noqa: E402

force_ipv4()

errors: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


items = sorted(CATALOG.glob("year=*/[0-9]*.json"))
if not items:
    print("no items to check; skipping")
    raise SystemExit(0)

# The layout contract the explorer relies on.
#
# apps/firms-explorer/deck.html used to learn the per-year archives by reading
# collection.json, then all 27 items, then range-probing all 27 pmtiles: 55
# requests before it could draw, to rediscover a naming scheme that has never
# varied. It now derives them -- year=<Y>/fire-<Y>.pmtiles for 2000..thisYear.
#
# That trades a runtime check for a build-time one, which only works if the
# build-time one exists. This is it. If make_items.py ever renames the asset
# or moves the directory, the explorer would silently 404 on every year; this
# fails first, offline, with the reason.
for path in items:
    doc = json.loads(path.read_text())
    year = doc["id"]
    check(path.parent.name == f"year={year}",
          f"{path}: item {year} is not in year={year}/; deck.html derives "
          f"that directory and would 404")
    asset = (doc.get("assets") or {}).get("pmtiles")
    if asset is not None:
        check(asset.get("href") == f"./fire-{year}.pmtiles",
              f"{path}: pmtiles href is {asset.get('href')!r}, not "
              f"'./fire-{year}.pmtiles'; deck.html derives that name")

probe, _ = head("https://data.source.coop/portolan-mirrors/firms-catalog/"
                "detections/collection.json")
# A string means the request never reached the server, even after retries.
# A missing network is not a broken catalog; an HTTP answer of any kind is.
if not isinstance(probe, int):
    print(f"network unavailable ({probe}); skipping")
    raise SystemExit(0)

checked = 0
for path in items:
    doc = json.loads(path.read_text())
    base = ("https://data.source.coop/portolan-mirrors/firms-catalog/"
            f"detections/year={doc['id']}/")
    for name, asset in (doc.get("assets") or {}).items():
        href = asset.get("href", "")
        if href.startswith(("http://", "https://")):
            url = href
        elif href.startswith("./"):
            url = base + href[2:]
        else:
            continue
        code, _ = head(url)
        checked += 1
        check(code == 200, f"{path.parent.name}: asset '{name}' -> {href} is "
              + (f"HTTP {code}" if isinstance(code, int) else f"unreachable ({code})"))

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print(f"OK: {checked} item asset(s) across {len(items)} item(s) resolve, "
      f"and all {len(items)} follow the year=<Y>/fire-<Y>.pmtiles layout")
