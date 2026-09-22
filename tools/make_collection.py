#!/usr/bin/env python3
"""Generate catalog/detections/collection.json from the built data.

Extents, row counts and the partition file list are measured from the Parquet,
never hand-written. Run this after tools/firms_build.py and before publishing.
This is also what lets the scheduled refresh restamp `updated` and the temporal
extent without a commit: the generator runs, the publisher uploads, and the
repository keeps only the stable definition.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import hashlib
import urllib.error
import urllib.request

import duckdb

# Source Cooperative's CDN 403s the default urllib agent, which returns None
# from a HEAD and reads exactly like "not published yet".
UA = {"User-Agent": "firms-catalog-tools/1.0 "
                    "(+https://github.com/portolan-mirrors/firms-catalog)"}


def remote_size(url: str) -> int | None:
    """Content-Length for a published asset, or None if it is not there."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers=UA)
        n = urllib.request.urlopen(req, timeout=30).headers.get("Content-Length")
        return int(n) if n else None
    except (urllib.error.URLError, OSError, ValueError):
        return None

ALLTIME_AGG_LEVELS = (5, 8, 10)
PUBLIC = "https://data.source.coop/portolan-mirrors/firms-catalog"
S3 = "s3://portolan-mirrors/firms-catalog"
REPO = "https://github.com/portolan-mirrors/firms-catalog"
APP = "https://portolan-mirrors.github.io/firms-catalog/"

# name -> (type, description). Descriptions are the same text AGENTS.md carries.
COLUMNS = [
    ("geometry", "geometry", "Detection centre point (CRS84). This is the centre of the flagged pixel, not the actual fire location."),
    ("acq_datetime", "timestamp", "Acquisition time in UTC, composed from the published acq_date and acq_time."),
    ("acq_date", "date", "Acquisition date as published."),
    ("sensor", "string", "MODIS, VIIRS_SNPP, VIIRS_NOAA20 or VIIRS_NOAA21."),
    ("satellite", "string", "Platform name: Terra, Aqua, Suomi-NPP, NOAA-20 or NOAA-21. Expanded from the published short codes."),
    ("instrument", "string", "MODIS or VIIRS."),
    ("quality", "string", "sp for science-quality, nrt for near-real-time. NRT locations are less accurate and carry no type."),
    ("version", "string", "Collection and processing designation as published, for example 2.0NRT or 6.1."),
    ("brightness", "double", "MODIS channel 21/22 brightness temperature in Kelvin. NULL for VIIRS rows."),
    ("bright_t31", "double", "MODIS channel 31 brightness temperature in Kelvin. NULL for VIIRS rows."),
    ("bright_ti4", "double", "VIIRS I-4 channel brightness temperature in Kelvin. NULL for MODIS rows."),
    ("bright_ti5", "double", "VIIRS I-5 channel brightness temperature in Kelvin. NULL for MODIS rows."),
    ("scan", "double", "Along-scan pixel size at the detection, in km for MODIS and m for VIIRS."),
    ("track", "double", "Along-track pixel size at the detection, in km for MODIS and m for VIIRS."),
    ("frp", "double", "Fire radiative power in megawatts."),
    ("daynight", "string", "D for a daytime detection, N for night."),
    ("confidence", "string", "As published, and NOT comparable across instruments: MODIS gives an integer 0-100, VIIRS gives l, n or h. No crosswalk is published here because none is documented upstream."),
    ("confidence_pct", "int32", "Numeric confidence 0-100. MODIS rows only; NULL for VIIRS."),
    ("type", "int32", "0 vegetation fire, 1 active volcano, 2 other static land source, 3 offshore. NULL for every NRT row, because FIRMS does not attribute type in near-real-time."),
]


def widen_from_items(catalog_dir: Path, live: tuple, years: list[int]) -> tuple:
    """Union the staged extent with every published item's.

    `years` is widened in place for the same reason as the extent: it names the
    span in the collection description, and deriving it from the staged files
    alone made a CI run that stages only the current year describe twenty-seven
    years of fire as "covering 2026 to 2026".
    """
    minx, miny, maxx, maxy, t0, t1, n = live
    for path in sorted(catalog_dir.glob("year=*/[0-9]*.json")):
        try:
            item = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        bbox = item.get("bbox")
        if bbox and len(bbox) == 4:
            minx = bbox[0] if minx is None else min(minx, bbox[0])
            miny = bbox[1] if miny is None else min(miny, bbox[1])
            maxx = bbox[2] if maxx is None else max(maxx, bbox[2])
            maxy = bbox[3] if maxy is None else max(maxy, bbox[3])
        props = item.get("properties") or {}
        for key, keep in (("start_datetime", min), ("end_datetime", max)):
            raw = props.get(key)
            if not raw:
                continue
            when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if key == "start_datetime":
                t0 = keep(t0, when.replace(tzinfo=None)) if t0 else when.replace(tzinfo=None)
            else:
                t1 = keep(t1, when.replace(tzinfo=None)) if t1 else when.replace(tzinfo=None)
        n += props.get("table:row_count") or 0
        if (yr := item.get("id", "")).isdigit():
            years.append(int(yr))
    return minx, miny, maxx, maxy, t0, t1, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="detections/ directory holding year=*/")
    ap.add_argument("--out", required=True, help="collection.json path")
    ap.add_argument("--pmtiles", default="fire-latest.pmtiles")
    ap.add_argument("--pmtiles-layers", default="aggregate,features")
    ap.add_argument("--alltime", default="alltime.pmtiles",
                    help="all-time archive filename; empty to omit")
    # Filenames only. Each asset title is read from the style's own "name", so
    # the name lives in one place instead of drifting between the two tools.
    ap.add_argument("--styles", default="default.json,avg-frp.json")
    ap.add_argument("--thumbnail", default="detections.thumb.jpg")
    ap.add_argument("--logo", default="detections.logo.png",
                    help="collection logo; empty to omit")
    a = ap.parse_args()

    data = Path(a.data)
    # Named explicitly rather than globbed as year=*/*.parquet. Each year now
    # holds its archive, its rolling window and its a5 aggregates side by side,
    # and they are three different tables: counting a cell-per-row aggregate as
    # detections would inflate table:row_count and quietly stop it meaning what
    # it says. The partition is the archives; the window is counted past them;
    # the aggregates are not counted at all.
    partitions = sorted(data.glob("year=*/detections.parquet"))
    live_files = sorted(data.glob("year=*/live.parquet"))
    files = partitions + live_files
    # Any year table will do, archive or window. The hourly refresh stages the
    # rolling window and nothing else -- it never downloads the archives it is
    # extending -- so demanding a detections.parquet here refused the one
    # caller that runs every hour. What the split above decides is what gets
    # counted, not what has to be present; the branches below already carry a
    # live.parquet whose year is described by an item rather than a local file.
    if not files:
        raise SystemExit(f"no year tables under {data}")

    # Count each row once. An item already reports its year's detections.parquet,
    # read from the footer, so scanning a local copy of that same file adds it
    # twice -- downloading one year to work on it silently inflated the
    # collection by that year's row count. live.parquet is never in an item, so
    # it is always counted here.
    itemised = {p.parent.name for p in
                Path(a.out).parent.glob("year=*/[0-9]*.json")}
    counted = [f for f in files
               if f.name != "detections.parquet" or f.parent.name not in itemised]

    # live.parquet overlaps its year's archive part rather than following it.
    # The rolling window is a fixed span back from now, so it re-reports the
    # days the archive already holds -- normally the last day or two, and all
    # of them whenever the archive has just been rebuilt from the same feed.
    # Counting it whole inflated the collection by that overlap every time.
    # Each live file is therefore counted only past its year's archive.
    cutoffs = {}
    for f in live_files:
        year = f.parent.name
        archive = f.parent / "detections.parquet"
        if archive.exists():
            cutoffs[str(f)] = ("file", str(archive))
        elif year in itemised:
            item = next(Path(a.out).parent.glob(f"{year}/[0-9]*.json"), None)
            if item:
                end = json.loads(item.read_text())["properties"].get("end_datetime")
                if end:
                    cutoffs[str(f)] = ("value", end)
    if not counted:
        # Every local file is already described by an item; the extent comes
        # entirely from them, so seed the scan with an empty result.
        minx = miny = maxx = maxy = None
        t0 = t1 = None
        n = 0
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    if counted:
        # One SELECT per file rather than one over the list, because the live
        # files carry a WHERE the archive files must not.
        selects = []
        for f in counted:
            where = ""
            cut = cutoffs.get(str(f))
            if cut and cut[0] == "file":
                where = (" WHERE acq_datetime > (SELECT max(acq_datetime) "
                         f"FROM read_parquet('{cut[1]}'))")
            elif cut and cut[0] == "value":
                where = f" WHERE acq_datetime > TIMESTAMP '{cut[1]}'"
            selects.append(f"SELECT * FROM read_parquet('{f}'){where}")
        union = " UNION ALL ".join(selects)
        minx, miny, maxx, maxy, t0, t1, n = con.execute(f"""
        SELECT min(ST_X(geometry)), min(ST_Y(geometry)),
               max(ST_X(geometry)), max(ST_Y(geometry)),
               min(acq_datetime), max(acq_datetime), count(*)
        FROM ({union})""").fetchone()
    years = [int(f.parent.name.split("=")[1]) for f in files]

    # Widen the extent to cover the published items.
    #
    # --data sees only what is staged locally, which in practice is the rolling
    # window: the archive years are built and uploaded from CI and never land
    # on this machine. Reporting that as the collection's extent told a client
    # the dataset spans eight days and holds two million rows, when it spans
    # twenty-seven years and holds six hundred million. The items are the
    # authority here -- each carries its year's real bounds and row count, read
    # from the Parquet footer.
    minx, miny, maxx, maxy, t0, t1, n = widen_from_items(
        Path(a.out).parent, (minx, miny, maxx, maxy, t0, t1, n), years)
    years = sorted(set(years))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731

    # Visualization: the PMTiles archive is a collection-level rel:pmtiles link
    # carrying a non-empty pmtiles:layers array (PORTO-FMT-011, PORTO-FMT-012).
    # Styles are collection-level assets with the style role, exactly one of
    # which also carries default (PORTO-CORE-069, PORTO-CORE-070).
    def file_meta(rel: str) -> dict:
        """file:size and file:checksum for an asset that ships in the catalog.

        checksum is a multihash: '1220' (sha2-256, 32 bytes) then the digest.
        """
        f = Path(a.out).parent / rel
        if not f.is_file():
            return {}
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        return {"file:size": f.stat().st_size, "file:checksum": "1220" + h}

    assets = {}
    styles = [x for x in a.styles.split(",") if x]
    for i, fn in enumerate(styles):
        sp = Path(a.out).parent / "styles" / fn
        title = fn
        if sp.is_file():
            title = json.loads(sp.read_text()).get("name", fn)
        roles = ["style", "default"] if i == 0 else ["style"]
        assets[f"style-{Path(fn).stem}"] = {
            "href": f"./styles/{fn}",
            "type": "application/vnd.mapbox.style+json",
            "title": title or fn,
            "roles": roles,
            **file_meta(f"styles/{fn}"),
        }
    # The tileset also ships as an asset, not only as the rel:pmtiles link the
    # format spec requires. Clients that look for a renderable asset find it
    # here; the link stays for those that follow the spec. Same bytes either way.
    assets["pmtiles"] = {
        "href": f"./{a.pmtiles}",
        "type": "application/vnd.pmtiles",
        "title": "Fire detections, aggregate bands plus raw points",
        "roles": ["visual", "data"],
        "pmtiles:layers": [x for x in a.pmtiles_layers.split(",") if x],
    }

    # The all-time archive is a second visual asset rather than a replacement.
    # fire-latest covers seven days at full detail; this one covers every month
    # since 2000 on coarse cells, and is the only asset from which a client can
    # see the whole record without knowing which years exist. Advertised only
    # when the bytes are actually staged, because an asset naming a file that
    # was never uploaded is worse than no asset.
    if a.alltime and (data / a.alltime).exists():
        assets["pmtiles-alltime"] = {
            "href": f"./{a.alltime}",
            "type": "application/vnd.pmtiles",
            "title": "All years, monthly aggregate, vector tiles",
            "roles": ["visual", "overview"],
            "pmtiles:layers": ["aggregate"],
        }

    # The a5 aggregates behind the all-time tileset, as GeoParquet. Same
    # argument as the per-year ones on each item: the tiles carry these numbers
    # but only as MVT, and a query engine wants a file. r10 is the all-time
    # fine band, which the year archives have no equivalent of -- they switch
    # to raw points where all-time switches to r10.
    # Published, not merely staged. Advertising a file that is only on this
    # machine gives a 404 to everyone else, and the asset gate rightly fails
    # the build for it -- the same rule the per-year aggregates follow in
    # make_items.py. The size comes from the bucket for the same reason.
    for lvl in ALLTIME_AGG_LEVELS:
        rel = f"alltime-r{lvl}.parquet"
        size = remote_size(f"{PUBLIC}/detections/{rel}")
        if not size:
            continue
        assets[f"aggregate-r{lvl}"] = {
            "href": f"./{rel}",
            "type": "application/vnd.apache.parquet",
            "title": f"All years aggregated to a5 r{lvl}, monthly columns",
            "roles": ["data", "aggregate"],
            "file:size": size,
        }

    assets["thumbnail"] = {
        "href": f"./{a.thumbnail}",
        "type": "image/jpeg",
        "title": "Global fire detection density, rendered from the default style",
        "roles": ["thumbnail"],
        **file_meta(a.thumbnail),
    }

    # Distinct from the thumbnail on purpose: the thumbnail is a render of the
    # data and changes with it, while the logo identifies the catalogue and
    # does not. Clients that show one rarely want the other in its place.
    if a.logo and (Path(a.out).parent / a.logo).exists():
        assets["logo"] = {
            "href": f"./{a.logo}",
            "type": "image/png",
            "title": "FIRMS mirror logo",
            "roles": ["logo"],
            **file_meta(a.logo),
        }

    col = {
        "type": "Collection",
        "stac_version": "1.1.0",
        "stac_extensions": [
            "https://schemas.portolan-sdi.org/portolan/v0.2.0/schema.json",
            "https://schemas.portolan-sdi.org/incubating/partition/v1.0.0/schema.json",
            "https://stac-extensions.github.io/table/v1.2.0/schema.json",
            "https://stac-extensions.github.io/web-map-links/v1.3.0/schema.json",
            "https://stac-extensions.github.io/file/v2.1.0/schema.json",
        ],
        "id": "detections",
        "title": "Active Fire Detections (MODIS and VIIRS)",
        "description": (
            f"Global satellite active fire and thermal anomaly detections from NASA FIRMS, "
            f"as one year-partitioned GeoParquet 2.0 table of {n:,} rows covering "
            f"{years[0]} to {years[-1]}. Carries MODIS (Terra and Aqua, 1 km) and VIIRS "
            f"(Suomi-NPP, NOAA-20, NOAA-21, 375 m). Near-real-time rows are appended hourly "
            f"and replaced by science-quality rows as NASA finalizes them; the `quality` "
            f"column says which is which. Rows are ordered by month and then by a Hilbert "
            f"index, so a reader prunes on both time and space. Read the "
            f"[agent guide](AGENTS.md) before querying: `confidence` is encoded differently "
            f"per instrument and `type` is NULL for all near-real-time rows."
        ),
        "license": "CC0-1.0",
        "keywords": ["fire", "wildfire", "active fire", "thermal anomaly", "MODIS",
                     "VIIRS", "NASA", "FIRMS", "near real-time"],
        "updated": now,
        "providers": [
            {"name": "NASA LANCE FIRMS",
             "description": "Fire Information for Resource Management System, part of NASA's Earth Observing System Data and Information System (EOSDIS).",
             "roles": ["producer", "licensor"],
             "url": "https://firms.modaps.eosdis.nasa.gov/"},
            {"name": "Portolan Mirrors",
             "roles": ["host"],
             "url": REPO},
        ],
        "extent": {
            "spatial": {"bbox": [[round(minx, 4), round(miny, 4), round(maxx, 4), round(maxy, 4)]]},
            "temporal": {"interval": [[iso(t0), iso(t1)]]},
        },
        "partition:scheme": "hive",
        "partition:strategy": "temporal",
        "partition:keys": [
            {"name": "year", "type": "int32", "description": "Year of acquisition (UTC)."}
        ],
        # Counted from the items, not from what happens to be staged. --data
        # sees only the years this run touched -- in CI usually one or two --
        # and a count of 2 for a twenty-seven year archive is the same class of
        # lie the extent used to tell before widen_from_items. The items are
        # one per published year, which is the number being asked for.
        "partition:file_count": max(
            len(partitions),
            sum(1 for _ in (Path(a.out).parent).glob("year=*/[0-9]*.json"))),
        "partition:glob": f"{S3}/detections/year=*/detections.parquet",
        "table:primary_geometry": "geometry",
        "table:row_count": n,
        "table:columns": [
            {"name": nm, "type": ty, "description": desc} for nm, ty, desc in COLUMNS
        ],
        "assets": assets,
        "links": [
            {"rel": "root", "href": "../catalog.json", "type": "application/json",
             "title": "NASA FIRMS Active Fire Detections"},
            {"rel": "parent", "href": "../catalog.json", "type": "application/json",
             "title": "NASA FIRMS Active Fire Detections"},
            {"rel": "agents", "href": "./AGENTS.md", "type": "text/markdown",
             "title": "Collection agent guide"},
            {"rel": "describedby", "href": "./README.md", "type": "text/markdown",
             "title": "Collection README"},
            {"rel": "via", "href": "https://firms.modaps.eosdis.nasa.gov/",
             "type": "text/html", "title": "NASA FIRMS (upstream source)"},
            # STAC uses  for a preview of the data itself, which is
            # what an interactive map is.  is reserved for
            # translations by the Language extension.
            {"rel": "preview", "href": APP, "type": "text/html",
             "title": "Interactive fire map"},
            {"rel": "pmtiles", "href": f"./{a.pmtiles}",
             "type": "application/vnd.pmtiles",
             "title": "Fire detections, aggregate bands plus raw points",
             "pmtiles:layers": [x for x in a.pmtiles_layers.split(",") if x]},
        ],
    }
    out = Path(a.out)
    # Item links belong to make_items.py, which appends one per published year
    # after this runs. They are carried across rather than rebuilt here: this
    # tool has no idea which years are published, so regenerating without them
    # silently emptied the collection's item list -- no error, no failed gate,
    # just a catalog that had stopped listing its items.
    if out.exists():
        try:
            prior = json.loads(out.read_text()).get("links", [])
        except (OSError, json.JSONDecodeError):
            prior = []
        col["links"] += [l for l in prior if l.get("rel") == "item"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(col, indent=2) + "\n")
    print(f"wrote {out}: {n:,} rows, {len(files)} partition(s), "
          f"bbox=[{minx:.2f},{miny:.2f},{maxx:.2f},{maxy:.2f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
