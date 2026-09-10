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

import duckdb

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="detections/ directory holding year=*/")
    ap.add_argument("--out", required=True, help="collection.json path")
    ap.add_argument("--pmtiles", default="fire.pmtiles")
    ap.add_argument("--pmtiles-layers", default="aggregate,features")
    # Filenames only. Each asset title is read from the style's own "name", so
    # the name lives in one place instead of drifting between the two tools.
    ap.add_argument("--styles", default="default.json,avg-frp.json")
    ap.add_argument("--thumbnail", default="detections.thumb.jpg")
    a = ap.parse_args()

    data = Path(a.data)
    files = sorted(data.glob("year=*/*.parquet"))
    if not files:
        raise SystemExit(f"no year partitions under {data}")
    glob_all = ",".join(f"'{f}'" for f in files)

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    minx, miny, maxx, maxy, t0, t1, n = con.execute(f"""
        SELECT min(ST_X(geometry)), min(ST_Y(geometry)),
               max(ST_X(geometry)), max(ST_Y(geometry)),
               min(acq_datetime), max(acq_datetime), count(*)
        FROM read_parquet([{glob_all}])""").fetchone()
    years = [int(f.parent.name.split("=")[1]) for f in files]
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

    assets["thumbnail"] = {
        "href": f"./{a.thumbnail}",
        "type": "image/jpeg",
        "title": "Global fire detection density, rendered from the default style",
        "roles": ["thumbnail"],
        **file_meta(a.thumbnail),
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
        "partition:file_count": len(files),
        "partition:glob": f"{S3}/detections/year=*/*.parquet",
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
