#!/usr/bin/env python3
"""Write one STAC item per published year, and link them from the collection.

The partition extension allows items where the partitions are user-meaningful
units, and a year is one. Each item carries that year's real extent and row
count, so a client can pick a year without opening 460 MB of Parquet.

Statistics come from the Parquet footer and the GeoParquet metadata, never a
scan: row count from the file metadata, bounds from the `geo` key, and the time
range from row-group statistics on acq_datetime. That is three range requests
per year instead of a full read.

Which sensors a year holds is not read from the data either. It follows from
the FIRMS availability ranges, which is what decided the slices in the first
place.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import duckdb

PUBLIC = "https://data.source.coop/portolan-mirrors/firms-catalog"
AVAIL = "https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{key}/ALL"
SENSOR_OF = {"MODIS_SP": "MODIS", "MODIS_NRT": "MODIS",
             "VIIRS_SNPP_SP": "VIIRS_SNPP", "VIIRS_SNPP_NRT": "VIIRS_SNPP",
             "VIIRS_NOAA20_SP": "VIIRS_NOAA20", "VIIRS_NOAA20_NRT": "VIIRS_NOAA20",
             "VIIRS_NOAA21_NRT": "VIIRS_NOAA21"}


def availability(key: str) -> dict:
    raw = urllib.request.urlopen(AVAIL.format(key=key), timeout=60).read().decode()
    return {r["data_id"]: (r["min_date"], r["max_date"])
            for r in csv.DictReader(io.StringIO(raw))}


def sensors_for(year: int, avail: dict) -> list[str]:
    out = set()
    for source, (lo, hi) in avail.items():
        s = SENSOR_OF.get(source)
        if not s:
            continue
        if max(f"{year}-01-01", lo) <= min(f"{year}-12-31", hi):
            out.add(s)
    return sorted(out)


def stats(con, url: str) -> dict | None:
    """Row count, bounds and time range, from metadata only."""
    try:
        rows = con.execute("SELECT num_rows FROM parquet_file_metadata(?)", [url]).fetchone()[0]
    except Exception:
        return None
    kv = con.execute("SELECT value FROM parquet_kv_metadata(?) WHERE key='geo'", [url]).fetchall()
    geo = json.loads(kv[0][0]) if kv else {}
    bbox = ((geo.get("columns") or {}).get("geometry") or {}).get("bbox")
    t0, t1 = con.execute(
        "SELECT min(stats_min_value), max(stats_max_value) FROM parquet_metadata(?) "
        "WHERE path_in_schema='acq_datetime'", [url]).fetchone()
    return {"rows": rows, "bbox": bbox, "t0": t0, "t1": t1}


def iso(v) -> str | None:
    if v is None:
        return None
    s = str(v).replace(" ", "T")
    return s if s.endswith("Z") else s + "Z"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True, help="tracked catalog/ directory")
    ap.add_argument("--key", required=True, help="FIRMS map key, for availability")
    ap.add_argument("--years", help="comma list; default = every year that resolves")
    a = ap.parse_args()

    cat = Path(a.catalog)
    coll_path = cat / "detections" / "collection.json"
    coll = json.loads(coll_path.read_text())
    avail = availability(a.key)

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")

    years = ([int(y) for y in a.years.split(",")] if a.years
             else list(range(2000, 2027)))
    written, skipped = [], []
    for y in years:
        # live.parquet holds the rolling window; the archive part is per year.
        url = f"{PUBLIC}/detections/year={y}/detections.parquet"
        st = stats(con, url)
        if st is None:
            skipped.append(y)
            continue
        sensors = sensors_for(y, avail)
        bbox = st["bbox"] or [-180, -90, 180, 90]
        item = {
            "type": "Feature",
            "stac_version": "1.1.0",
            "stac_extensions": [
                "https://stac-extensions.github.io/table/v1.2.0/schema.json",
            ],
            "id": str(y),
            "collection": "detections",
            "bbox": [round(v, 4) for v in bbox],
            "geometry": {"type": "Polygon", "coordinates": [[
                [bbox[0], bbox[1]], [bbox[2], bbox[1]],
                [bbox[2], bbox[3]], [bbox[0], bbox[3]], [bbox[0], bbox[1]]]]},
            "properties": {
                "title": f"Active fire detections, {y}",
                "datetime": None,
                "start_datetime": iso(st["t0"]),
                "end_datetime": iso(st["t1"]),
                "table:row_count": st["rows"],
                "firms:sensors": sensors,
            },
            "assets": {
                "data": {
                    "href": "./detections.parquet",
                    "type": "application/vnd.apache.parquet",
                    "title": f"{y} detections, GeoParquet 2.0",
                    "roles": ["data"],
                },
            },
            "links": [
                {"rel": "root", "href": "../../catalog.json", "type": "application/json"},
                {"rel": "parent", "href": "../collection.json", "type": "application/json"},
                {"rel": "collection", "href": "../collection.json", "type": "application/json"},
                {"rel": "self", "href": f"{PUBLIC}/detections/year={y}/{y}.json",
                 "type": "application/geo+json"},
            ],
        }

        # A year is only tiled once its parquet is final, so the tile assets
        # appear on the item only when the styles have actually been generated.
        sdir = cat / "detections" / f"year={y}" / "styles"
        if (sdir / "default.json").exists():
            item["assets"]["pmtiles"] = {
                "href": f"./fire-{y}.pmtiles",
                "type": "application/vnd.pmtiles",
                "title": f"{y} detections, vector tiles",
                "roles": ["visual", "tiles"],
            }
            for st in sorted(sdir.glob("*.json")):
                item["assets"][f"style-{st.stem}"] = {
                    "href": f"./styles/{st.name}",
                    "type": "application/vnd.mapbox.style+json",
                    "title": json.loads(st.read_text()).get("name", st.stem),
                    "roles": ["style"],
                }
        d = cat / "detections" / f"year={y}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{y}.json").write_text(json.dumps(item, indent=2) + "\n")
        written.append(y)
        print(f"  {y}: {st['rows']:>11,} rows  {','.join(sensors)}")

    # Collection links: one per item, replacing any previous set.
    coll["links"] = [l for l in coll["links"] if l.get("rel") != "item"]
    for y in written:
        coll["links"].append({
            "rel": "item", "href": f"./year={y}/{y}.json",
            "type": "application/geo+json", "title": f"Active fire detections, {y}"})
    coll_path.write_text(json.dumps(coll, indent=2) + "\n")
    print(f"\n  {len(written)} item(s) written, {len(skipped)} year(s) not published yet")
    if skipped:
        print(f"  not published: {', '.join(str(y) for y in skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
