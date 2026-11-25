#!/usr/bin/env python3
"""Take a year's item from the bucket when it describes more than the one in git.

A year's item is generated, not written: consolidate-year rebuilds the current
year every day and make_items.py reads its row count, bounds and time range
back from the file that was just uploaded. Nothing commits the result -- the
repository keeps the stable definition and the bucket keeps the measurements
-- so the item in git is a snapshot from the last time a human committed one.

publish.py syncs catalog/ 1:1, snapshot included. The hourly refresh checks
the repository out and publishes, and so put the snapshot back over the
generated item within the hour, every day. Measured on 2026-11-25: the 2026
archive ran to 25 November and held 53,011,191 rows; the published item beside
it was byte for byte the one in git, ending 15 September with 41,805,142. The
year's styles went back with it, and the collection's row count was summed
from the same snapshot. No job failed, because nothing was missing: each run
published a valid catalog, just an old one.

So before a workflow builds on the items, this brings the checkout level with
the bucket. For each year worth asking about it reads the published item, and
where that one ends later than the checkout's it replaces the checkout's copy
with the published bytes, along with the styles the item declares. The run
then builds its collection from the measurements that are actually current,
and the publisher finds the item unchanged and leaves it alone.

Only forward. A published item that is level with the checkout's, or behind
it, is left where it is, which is how a finished year's committed item -- and
any edit made to it in git -- still reaches the bucket.

    python3 tools/pull_items.py --catalog catalog --data ./staging/publish/detections
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import net  # noqa: E402

PUBLIC = "https://data.source.coop/portolan-mirrors/firms-catalog"


def end_of(item: dict | None) -> str:
    """The item's end_datetime, or "" when there is no item or no end."""
    return ((item or {}).get("properties") or {}).get("end_datetime") or ""


def read_item(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def years_to_check(catalog: Path, data: Path | None) -> list[int]:
    """The years whose published item could be ahead of the checkout's.

    Two kinds. A year this run has staged is one it is rebuilding, and in
    January that includes a year git has no item for at all. And a year whose
    item in the checkout stops short of 31 December was committed part-way
    through: its archive has grown since, whether or not this run touches it,
    and it stays that way after the rolling window has moved on and nothing
    regenerates the item again.

    A finished year is neither. Its archive does not change, so its committed
    item cannot be behind, and asking about all of them would add twenty-six
    requests to an hourly job in order to learn nothing.
    """
    years = set()
    if data is not None:
        years |= {int(p.name.split("=")[1]) for p in Path(data).glob("year=*")
                  if p.is_dir()}
    for path in (catalog / "detections").glob("year=*/[0-9]*.json"):
        year = int(path.parent.name.split("=")[1])
        if end_of(read_item(path))[:10] < f"{year}-12-31":
            years.add(year)
    return sorted(years)


def fetch(get, url: str, year: int, what: str) -> bytes | None:
    """The object's bytes, None when the bucket does not have it, or a refusal.

    A 404 is an answer about the object. Anything else that is not a 200 --
    a 5xx once the retries are spent, a 403, no answer at all -- leaves it
    unknown whether the checkout is behind, and carrying on would publish the
    checkout's copy over an item that may well be newer. That is the failure
    this tool exists to stop, so it stops the run instead: nothing has been
    uploaded yet, and the next scheduled run asks again.
    """
    status, body = get(url)
    if status == 200 and body is not None:
        return body
    if status == 404:
        return None
    raise SystemExit(
        f"{year}: the published {what} did not answer ({status}): {url}\n"
        f"  refusing to go on, because publishing the checkout's copy could "
        f"move the item backwards")


def link_item(catalog: Path, year: int, item: dict) -> None:
    """List the item in the checkout's collection, if it is not there yet.

    Only a year git has never seen needs this. make_collection.py carries item
    links across from the collection on disk and make_items.py appends them,
    so an item that arrives from the bucket has to bring its own -- the same
    link make_items.py writes, in the same order.
    """
    path = catalog / "detections" / "collection.json"
    coll = json.loads(path.read_text())
    href = f"./year={year}/{year}.json"
    links = coll.get("links", [])
    if any(l.get("rel") == "item" and l.get("href") == href for l in links):
        return
    title = (item.get("properties") or {}).get("title") or str(year)
    items = [l for l in links if l.get("rel") == "item"]
    items.append({"rel": "item", "href": href,
                  "type": "application/geo+json", "title": title})
    coll["links"] = ([l for l in links if l.get("rel") != "item"]
                     + sorted(items, key=lambda l: l.get("href", "")))
    path.write_text(json.dumps(coll, indent=2) + "\n")


def pull(catalog: Path, years: list[int], *, get=net.get,
         base: str = PUBLIC) -> list[int]:
    """Bring each year's item level with the bucket. Returns the years taken."""
    taken = []
    for year in years:
        ydir = catalog / "detections" / f"year={year}"
        remote = f"{base}/detections/year={year}"
        body = fetch(get, f"{remote}/{year}.json", year, "item")
        if body is None:
            print(f"  {year}: no published item; keeping the checkout as it is")
            continue
        try:
            theirs = json.loads(body)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{year}: the published item is not JSON ({exc})")
        if not isinstance(theirs, dict) or theirs.get("id") != str(year):
            raise SystemExit(f"{year}: the published item is not the {year} item")
        mine = read_item(ydir / f"{year}.json")
        if mine is not None and end_of(theirs) <= end_of(mine):
            print(f"  {year}: checkout ends {end_of(mine) or 'nowhere'}, published "
                  f"ends {end_of(theirs) or 'nowhere'}; keeping the checkout's")
            continue

        # The styles the item declares come with it. Each carries a file:size
        # and a file:checksum of the bytes consolidate-year built, and the
        # checkout's styles are the committed ones: taking the item alone
        # would publish a declaration beside bytes it does not describe.
        # Fetched before anything is written, so a year arrives whole or not
        # at all.
        styles = {}
        for name, asset in (theirs.get("assets") or {}).items():
            href = asset.get("href", "")
            if "style" not in (asset.get("roles") or []) or not href.startswith("./"):
                continue
            # The href came over the network and is about to become a path.
            rel = PurePosixPath(href[2:])
            if ".." in rel.parts or rel.is_absolute():
                raise SystemExit(f"{year}: asset '{name}' points outside the "
                                 f"year directory: {href}")
            style = fetch(get, f"{remote}/{rel}", year, f"style '{name}'")
            if style is None:
                raise SystemExit(f"{year}: the published item declares '{name}' "
                                 f"at {href}, and the bucket does not have it")
            styles[rel] = style

        for rel, style in styles.items():
            (ydir / rel).parent.mkdir(parents=True, exist_ok=True)
            (ydir / rel).write_bytes(style)
        ydir.mkdir(parents=True, exist_ok=True)
        # The served bytes, not a re-serialisation: publish.py compares MD5s,
        # and an item that differs only in whitespace is uploaded every hour.
        (ydir / f"{year}.json").write_bytes(body)
        link_item(catalog, year, theirs)
        taken.append(year)
        had = f"ended {end_of(mine)}" if mine is not None else "had no item"
        print(f"  {year}: checkout {had}; took the published item, which ends "
              f"{end_of(theirs)}, and {len(styles)} style(s)")
    return taken


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True, help="tracked catalog/ directory")
    ap.add_argument("--data", help="staged detections/ directory; every year=* "
                                   "under it is checked, item in git or not")
    a = ap.parse_args()

    net.force_ipv4()
    catalog = Path(a.catalog)
    years = years_to_check(catalog, Path(a.data) if a.data else None)
    taken = pull(catalog, years)
    print(f"\n  {len(years)} year(s) checked, {len(taken)} item(s) taken from "
          f"the bucket")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
