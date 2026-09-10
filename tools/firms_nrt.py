#!/usr/bin/env python3
"""Build the rolling near-real-time window from the FIRMS bulk CSV files.

These files need no MAP_KEY and cost no API transactions, so this can run as
often as FIRMS republishes them (measured: sub-hourly). That is why the hourly
refresh uses this path and not the area API.

Their schema differs from the API: there is no `instrument` column and no
`type` column. tools/firms_fetch.normalize handles both shapes.
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent))
from firms_fetch import normalize  # noqa: E402
from net import force_ipv4  # noqa: E402

# FIRMS is dual-stack and CI has no IPv6 route; see tools/net.py.
force_ipv4()

BULK = "https://firms.modaps.eosdis.nasa.gov/data/active_fire"

# api-source-name -> bulk CSV path. The source name selects the sensor and the
# nrt quality flag inside normalize().
FEEDS = {
    "VIIRS_SNPP_NRT":   "suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Global_{w}.csv",
    "VIIRS_NOAA20_NRT": "noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_{w}.csv",
    "VIIRS_NOAA21_NRT": "noaa-21-viirs-c2/csv/J2_VIIRS_C2_Global_{w}.csv",
    "MODIS_NRT":        "modis-c6.1/csv/MODIS_C6_1_Global_{w}.csv",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", default="7d", choices=["24h", "48h", "7d"])
    ap.add_argument("--out", required=True, help="chunk directory")
    ap.add_argument("--retries", type=int, default=6)
    a = ap.parse_args()

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; SET memory_limit='4GB';")
    total = failed = 0
    for source, path in FEEDS.items():
        url = f"{BULK}/{path.format(w=a.window)}"
        outdir = Path(a.out) / source
        outdir.mkdir(parents=True, exist_ok=True)
        # These feeds are occasionally unreachable for minutes at a time. An
        # unretried failure loses the whole refresh, and the schedule then just
        # waits an hour to try again.
        body = None
        delay = 5.0
        for attempt in range(a.retries):
            try:
                with urllib.request.urlopen(url, timeout=600) as r:
                    body = r.read().decode("utf-8", "replace")
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == a.retries - 1:
                    failed += 1
                    print(f"FAIL {source} after {a.retries} tries: {exc}",
                          file=sys.stderr, flush=True)
                else:
                    print(f"  retry {source} ({exc})", file=sys.stderr, flush=True)
                    time.sleep(delay)
                    delay = min(delay * 2, 120)
        if body is None:
            continue
        try:
            n = normalize(con, body, source, outdir / f"bulk_{a.window}.parquet")
            total += n
            print(f"  {source}: {n:,} rows", flush=True)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {source} (parse): {exc}", file=sys.stderr, flush=True)
    print(f"NRT {a.window}: {total:,} rows, {failed} feed(s) failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
