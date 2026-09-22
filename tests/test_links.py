#!/usr/bin/env python3
"""Every relative link and asset href resolves to a file that exists.

This catches the most common hand-edit mistake: adding a child link before the
directory it points at exists. Dependency-free and offline, so it runs in
milliseconds on a clean checkout.

Data bytes are never in git -- a GeoParquet partition or a PMTiles archive is
too large for it, and the repository rule is that they stage outside the
catalog and upload separately. So a data href pointing at a file that is not
on disk is the normal case everywhere, in CI and locally alike, and this gate
defers it rather than failing. It is not skipped silently: the count is
printed, and ``--remote`` resolves every deferred href against the bucket that
actually serves it.

    python3 tests/test_links.py            # offline, the default
    python3 tests/test_links.py --remote   # also HEAD the data at public_base

The remote pass answers the question the offline one cannot: the href is
spelled correctly *and the object is there*. It is opt-in because it is the
only gate in the suite that needs the network, and because a bucket outage
would otherwise turn a red build into a coin toss. Set CHECK_REMOTE=1 for the
same thing without the argument.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from net import force_ipv4  # noqa: E402
from publish import load_config  # noqa: E402

config = load_config()
BASE = ROOT / config["publish_dir"]
PUBLIC_BASE = config["public_base"].rstrip("/")

errors: list[str] = []

REMOTE = "--remote" in sys.argv or os.environ.get("CHECK_REMOTE") == "1"
# The exemption reads the suffix and nothing else. A directory rule or a path
# prefix rule widens on its own as the catalog grows. This tuple does not.
DATA_SUFFIXES = (
    ".parquet", ".pmtiles", ".tif", ".tiff", ".copc.laz", ".laz", ".gpkg",
    ".zarr", ".geojsonl", ".shp", ".zip",
)
# Anything but urllib's own: data.source.coop answers Python-urllib/3.x with a
# 403 on every key, present or missing, which would read as the whole catalog
# having gone missing at once.
USER_AGENT = "firms-catalog-link-check"
TIMEOUT = 30


def is_remote(href: str) -> bool:
    return "://" in href or href.startswith(("#", "mailto:"))


def is_data(href: str) -> bool:
    """True when href points at a file kept out of git on purpose."""
    return href.lower().endswith(DATA_SUFFIXES)


def stac_documents() -> list[Path]:
    """Every STAC object under the published directory."""
    out = []
    for path in sorted(BASE.rglob("*.json")):
        if any(part.startswith(".") for part in path.relative_to(BASE).parts):
            continue
        if path.name.endswith(".style.json") or "styles" in path.parts:
            continue
        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            errors.append(f"{path.relative_to(ROOT)}: invalid JSON ({exc})")
            continue
        if isinstance(doc, dict) and doc.get("type") in {
            "Catalog", "Collection", "Feature"
        }:
            out.append(path)
    return out


def published_url(target: Path) -> str | None:
    """Where target is served, or None if it falls outside publish_dir."""
    try:
        rel = target.relative_to(BASE)
    except ValueError:
        return None
    return f"{PUBLIC_BASE}/{rel.as_posix()}"


documents = stac_documents()
checked = 0
# url -> the first "<doc>: <what>" that asked for it, for the error message.
deferred: dict[str, str] = {}
unmapped = 0


def check_href(path: Path, what: str, href: str) -> None:
    """One relative href: on disk, or deferred to the bucket that serves it."""
    global checked, unmapped
    rel_path = path.relative_to(ROOT)
    target = (path.parent / href).resolve()
    if target.exists():
        checked += 1
        return
    if not is_data(href):
        errors.append(f"{rel_path}: {what} -> {href} does not exist")
        return
    # The bytes are legitimately absent. Remember where they would be served.
    url = published_url(target)
    if url is None:
        # Outside publish_dir, so nothing publishes it and no URL can prove it
        # right. Structural correctness is all this can be held to.
        unmapped += 1
        return
    deferred.setdefault(url, f"{rel_path}: {what}")


for path in documents:
    doc = json.loads(path.read_text())

    for link in doc.get("links", []):
        href = link.get("href", "")
        if not href or is_remote(href):
            continue
        # The web-map-links extension references PMTiles as a link, not an
        # asset, so the data rule has to cover links too. A PMTiles archive is
        # data and lives in object storage like any other.
        check_href(path, f"rel:{link.get('rel')}", href)

    for key, asset in (doc.get("assets") or {}).items():
        href = asset.get("href", "")
        if not href or is_remote(href):
            continue
        check_href(path, f"asset {key}", href)


def head(url: str) -> tuple[int | str, int | None]:
    """(status, content length). status is a string when the request failed."""
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            length = response.headers.get("content-length")
            return response.status, int(length) if length else None
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
        return f"{type(exc).__name__}: {exc}", None


if REMOTE and deferred:
    force_ipv4()
    urls = sorted(deferred)
    # A HEAD apiece is a round trip apiece, and there are hundreds. Eight at a
    # time keeps the gate under a minute without leaning on the gateway.
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(head, urls))
    for url, (status, length) in zip(urls, results):
        where = deferred[url]
        if status == 200 and length != 0:
            checked += 1
        elif status == 200:
            # publish.py compares large objects on size alone, so a truncated
            # upload stays accepted on every later run. Zero bytes is the one
            # truncation that is unambiguous.
            errors.append(f"{where} -> {url} is empty")
        else:
            errors.append(f"{where} -> {url} returned {status}")

if deferred and not REMOTE:
    print(
        f"note   {len(deferred)} data href(s) not checked: the bytes live in "
        "object storage,\n       not git. Run with --remote to resolve them "
        "against the bucket."
    )
if unmapped:
    print(f"note   {unmapped} data href(s) point outside {config['publish_dir']}/")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)

scope = "on disk and at the bucket" if REMOTE else "on disk"
print(f"OK: {checked} href(s) {scope} across {len(documents)} object(s)")
