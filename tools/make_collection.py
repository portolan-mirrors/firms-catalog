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

import duckdb

PUBLIC = "https://data.source.coop/portolan-mirrors/firms-catalog"
S3 = "s3://portolan-mirrors/firms-catalog"
REPO = "https://github.com/portolan-mirrors/firms-catalog"

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
    ("_month", "uint8", "Month 1-12. Primary sort key, and a cheap month filter."),
    ("_hilbert", "uint32", "Hilbert index over the global extent. Secondary sort key that keeps each row group spatially tight."),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="detections/ directory holding year=*/")
    ap.add_argument("--out", required=True, help="collection.json path")
    a = ap.parse_args()

    data = Path(a.data)
    files = sorted(data.glob("year=*/detections.parquet"))
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

    col = {
        "type": "Collection",
        "stac_version": "1.1.0",
        "stac_extensions": [
            "https://schemas.portolan-sdi.org/portolan/v0.2.0/schema.json",
            "https://schemas.portolan-sdi.org/incubating/partition/v1.0.0/schema.json",
            "https://stac-extensions.github.io/table/v1.2.0/schema.json",
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
        "partition:glob": f"{S3}/detections/year=*/detections.parquet",
        "table:primary_geometry": "geometry",
        "table:row_count": n,
        "table:columns": [
            {"name": nm, "type": ty, "description": desc} for nm, ty, desc in COLUMNS
        ],
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
        ],
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(col, indent=2) + "\n")
    print(f"wrote {out}: {n:,} rows, {len(files)} partition(s), "
          f"bbox=[{minx:.2f},{miny:.2f},{maxx:.2f},{maxy:.2f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
