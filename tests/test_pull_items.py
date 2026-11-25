#!/usr/bin/env python3
"""A publish must not move a year's item backwards.

consolidate-year rebuilds the current year every day and regenerates its item
from the file it just uploaded. Nothing commits that item, so the copy in git
is whatever a human last committed. The hourly refresh checks the repository
out and syncs catalog/ 1:1, and so put the git copy back over the generated
one within the hour, every day: on 2026-11-25 the published 2026 item said the
year ended on 15 September and held 41.8 million rows, beside an archive that
ran to 25 November and held 53 million. Every job was green throughout.

tools/pull_items.py is the step that stops it: before a workflow builds on the
items, it takes the bucket's copy of any item that describes more of its year
than the checkout's does. This is the gate for that tool, and for the wiring
that makes it matter -- a publisher that does not call it is the bug again.

No network: the bucket is a dict.

Run: python3 tests/test_pull_items.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

errors: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


try:
    import pull_items
except ImportError as exc:
    print(f"error  tools/pull_items.py is missing ({exc}); nothing stops a "
          f"publish from overwriting a regenerated item with the copy in git")
    raise SystemExit(1)

BASE = "https://bucket.invalid/firms-catalog"


def item(year: int, end: str, rows: int, styles=("default",)) -> dict:
    return {
        "type": "Feature", "id": str(year), "collection": "detections",
        "properties": {
            "title": f"Active fire detections, {year}",
            "start_datetime": f"{year}-01-01T00:07:00Z",
            "end_datetime": end,
            "table:row_count": rows,
        },
        "assets": {
            "data": {"href": "./detections.parquet", "roles": ["data"]},
            **{f"style-{s}": {"href": f"./styles/{s}.json", "roles": ["style"]}
               for s in styles},
        },
    }


def dump(doc: dict) -> bytes:
    return (json.dumps(doc, indent=2) + "\n").encode()


def checkout(tmp: Path, items: dict[int, dict], linked=None) -> Path:
    """A catalog/ tree holding these items, each with a committed style."""
    cat = tmp / "catalog"
    for year, doc in items.items():
        d = cat / "detections" / f"year={year}"
        (d / "styles").mkdir(parents=True)
        (d / f"{year}.json").write_bytes(dump(doc))
        (d / "styles" / "default.json").write_text('{"name": "committed"}\n')
    links = [{"rel": "root", "href": "../catalog.json"}]
    links += [{"rel": "item", "href": f"./year={y}/{y}.json",
               "type": "application/geo+json",
               "title": f"Active fire detections, {y}"}
              for y in (items if linked is None else linked)]
    (cat / "detections").mkdir(parents=True, exist_ok=True)
    (cat / "detections" / "collection.json").write_bytes(
        dump({"type": "Collection", "id": "detections", "links": links}))
    return cat


class Bucket:
    """Answers GETs from a dict, and remembers what it was asked for."""

    def __init__(self, objects: dict):
        self.objects = objects
        self.asked: list[str] = []

    def get(self, url: str):
        self.asked.append(url)
        answer = self.objects.get(url, 404)
        return (200, answer) if isinstance(answer, bytes) else (answer, None)


def published(year: int, doc: dict, style: bytes = b'{"name": "rebuilt"}\n') -> dict:
    base = f"{BASE}/detections/year={year}"
    return {f"{base}/{year}.json": dump(doc),
            **{f"{base}/{a['href'][2:]}": style
               for a in doc["assets"].values() if "style" in a["roles"]}}


def refuses(fn) -> str | None:
    """The refusal message, or None when the call went through."""
    try:
        fn()
    except SystemExit as exc:
        return str(exc.code)
    return None


stale = item(2026, "2026-09-15T05:46:00Z", 41_805_142)
fresh = item(2026, "2026-11-25T11:02:00Z", 53_011_191, styles=("default", "day-night"))

# The observed failure. The checkout's item stops in September; the bucket's
# was regenerated this morning. What the run goes on to publish must be the
# bucket's, byte for byte, so the publisher finds nothing to upload.
with tempfile.TemporaryDirectory() as tmp:
    cat = checkout(Path(tmp), {2025: item(2025, "2025-12-31T23:49:00Z", 9), 2026: stale})
    bucket = Bucket(published(2026, fresh))
    taken = pull_items.pull(cat, [2026], get=bucket.get, base=BASE)
    ydir = cat / "detections" / "year=2026"
    now = json.loads((ydir / "2026.json").read_text())
    check(taken == [2026], f"the year whose item moved is reported, got {taken}")
    check(now["properties"]["end_datetime"] == "2026-11-25T11:02:00Z",
          f"the item ends where the published year does, not at the commit: "
          f"{now['properties']['end_datetime']}")
    check(now["properties"]["table:row_count"] == 53_011_191,
          f"and counts the published rows: {now['properties']['table:row_count']:,}")
    check((ydir / "2026.json").read_bytes() == dump(fresh),
          "the item is the published bytes exactly, so publishing it is a no-op")
    # The item declares each style's size and checksum. Taking the item and
    # leaving the committed styles would publish a declaration about bytes the
    # same run then replaces.
    check((ydir / "styles" / "default.json").read_bytes() == b'{"name": "rebuilt"}\n',
          "the styles the item declares come with it")
    check((ydir / "styles" / "day-night.json").exists(),
          "including one the checkout never had")

# An item only moves forward. A bucket that is behind the checkout, or level
# with it, leaves the checkout alone: that is how a finished year's committed
# item, and any change made to it in git, still reaches the bucket.
for label, theirs in (("behind", item(2026, "2026-09-01T00:00:00Z", 5)),
                      ("level", item(2026, "2026-09-15T05:46:00Z", 5))):
    with tempfile.TemporaryDirectory() as tmp:
        cat = checkout(Path(tmp), {2026: stale})
        bucket = Bucket(published(2026, theirs))
        taken = pull_items.pull(cat, [2026], get=bucket.get, base=BASE)
        kept = (cat / "detections" / "year=2026" / "2026.json").read_bytes()
        check(taken == [] and kept == dump(stale),
              f"a published item that is {label} does not replace the checkout's")
        check(len(bucket.asked) == 1,
              f"and its styles are not fetched: asked for {len(bucket.asked)} object(s)")

# "Not there" and "did not answer" are different findings. The first is a year
# with no item yet and is nothing to act on. The second leaves it unknown
# whether the checkout is behind, and going on would publish the checkout's
# copy over an item that may be newer -- so the run stops before it uploads.
with tempfile.TemporaryDirectory() as tmp:
    cat = checkout(Path(tmp), {2026: stale})
    taken = pull_items.pull(cat, [2026], get=Bucket({}).get, base=BASE)
    check(taken == [], "an item the bucket does not have is not an error")

for answer in ("URLError: timed out", 503, 403):
    with tempfile.TemporaryDirectory() as tmp:
        cat = checkout(Path(tmp), {2026: stale})
        bucket = Bucket({f"{BASE}/detections/year=2026/2026.json": answer})
        said = refuses(lambda: pull_items.pull(cat, [2026], get=bucket.get, base=BASE))
        check(said is not None and "2026" in said,
              f"an item that answers {answer!r} stops the run, got {said!r}")

# All of a year or none of it: an item written before a style failed would
# declare bytes the checkout does not hold.
with tempfile.TemporaryDirectory() as tmp:
    cat = checkout(Path(tmp), {2026: stale})
    objects = published(2026, fresh)
    objects[f"{BASE}/detections/year=2026/styles/day-night.json"] = "URLError: reset"
    said = refuses(lambda: pull_items.pull(cat, [2026], get=Bucket(objects).get, base=BASE))
    ydir = cat / "detections" / "year=2026"
    check(said is not None, "a style that does not answer stops the run")
    check((ydir / "2026.json").read_bytes() == dump(stale)
          and (ydir / "styles" / "default.json").read_text() == '{"name": "committed"}\n',
          "and leaves the item and its styles as they were")

# The hrefs come from a document fetched over the network, and they become
# paths. One that climbs out of the year's directory is refused.
with tempfile.TemporaryDirectory() as tmp:
    cat = checkout(Path(tmp), {2026: stale})
    climbing = item(2026, "2026-11-25T11:02:00Z", 5)
    climbing["assets"]["style-default"]["href"] = "./../../../escaped.json"
    objects = {f"{BASE}/detections/year=2026/2026.json": dump(climbing),
               f"{BASE}/detections/year=2026/../../../escaped.json": b"{}"}
    said = refuses(lambda: pull_items.pull(cat, [2026], get=Bucket(objects).get, base=BASE))
    check(said is not None and not (Path(tmp) / "escaped.json").exists(),
          "a style href that leaves the year directory is refused")

# A year git has never seen. In January the new year's item exists only in
# the bucket, and the collection in git does not list it. Taking the item
# without its link fails the one-link-per-item gate; skipping the item drops
# the year from the collection's count for twenty-three hours a day.
with tempfile.TemporaryDirectory() as tmp:
    cat = checkout(Path(tmp), {2025: item(2025, "2025-12-31T23:49:00Z", 9),
                               2026: item(2026, "2026-12-31T23:58:00Z", 9)})
    first = item(2027, "2027-01-03T02:10:00Z", 400_000)
    taken = pull_items.pull(cat, [2027], get=Bucket(published(2027, first)).get, base=BASE)
    coll = json.loads((cat / "detections" / "collection.json").read_text())
    hrefs = [l["href"] for l in coll["links"] if l.get("rel") == "item"]
    check(taken == [2027] and (cat / "detections/year=2027/2027.json").exists(),
          "a year the checkout has no item for is taken from the bucket")
    check(hrefs == ["./year=2025/2025.json", "./year=2026/2026.json",
                    "./year=2027/2027.json"],
          f"and the collection lists it, in order, beside the others: {hrefs}")
    check(coll["links"][0] == {"rel": "root", "href": "../catalog.json"},
          "without disturbing the links that are not items")

# Which years to ask about. A finished year's archive does not grow, so its
# committed item cannot be behind and is not worth a request an hour. A year
# the run has staged is being rebuilt; a year whose committed item stops
# short of 31 December was committed part-way through and has moved on since,
# staged or not -- which is 2026 from the middle of January 2027, once the
# rolling window has left it and nothing regenerates its item again.
with tempfile.TemporaryDirectory() as tmp:
    cat = checkout(Path(tmp), {2025: item(2025, "2025-12-31T23:49:00Z", 9), 2026: stale})
    data = Path(tmp) / "staging" / "detections"
    (data / "year=2027").mkdir(parents=True)
    years = pull_items.years_to_check(cat, data)
    check(years == [2026, 2027],
          f"the unfinished year and the staged year, not the finished one: {years}")
    check(pull_items.years_to_check(cat, None) == [2026],
          "with nothing staged, the unfinished year still")

# The wiring. The tool only helps a workflow that runs it, and it has to run
# before the collection is built: the collection's row count and the window's
# cutoff are read from the items on disk, so building first counts September's
# item and then publishes November's beside a total that disagrees with it.
publishers = [p for p in sorted((ROOT / ".github" / "workflows").glob("*.yml"))
              if "tools/publish.py --confirm" in p.read_text()]
check(len(publishers) >= 2,
      f"the publishing workflows are found: {[p.name for p in publishers]}")
for path in publishers:
    text = path.read_text()
    pulled = text.find("tools/pull_items.py")
    check(pulled != -1,
          f"{path.name} publishes catalog/ without taking the published items "
          f"first, so it puts the items in git back over the regenerated ones")
    check(pulled != -1 and pulled < text.find("tools/make_collection.py"),
          f"{path.name} takes the published items before it builds the collection")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print(f"OK: a publish never moves an item backwards "
      f"({len(publishers)} publishing workflow(s) checked)")
