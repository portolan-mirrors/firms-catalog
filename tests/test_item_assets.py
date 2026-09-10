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
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog" / "detections"
# Source Cooperative's CDN answers 403 to the default Python agent, so every
# request names itself; a rejected client looks exactly like a missing file.
UA = {"User-Agent": "firms-catalog-tests/1.0"}

errors: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def head(url: str) -> int | None:
    try:
        req = urllib.request.Request(url, method="HEAD", headers=UA)
        return urllib.request.urlopen(req, timeout=30).status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError):
        return None


items = sorted(CATALOG.glob("year=*/[0-9]*.json"))
if not items:
    print("no items to check; skipping")
    raise SystemExit(0)

probe = head("https://data.source.coop/portolan-mirrors/firms-catalog/"
             "detections/collection.json")
if probe is None:
    print("network unavailable; skipping")
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
        code = head(url)
        checked += 1
        check(code == 200, f"{path.parent.name}: asset '{name}' -> {href} is HTTP {code}")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print(f"OK: {checked} item asset(s) across {len(items)} item(s) resolve")
