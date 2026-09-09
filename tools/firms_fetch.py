#!/usr/bin/env python3
"""Fetch NASA FIRMS active fire detections and normalize them to Parquet chunks.

One chunk is one API window for one source. A chunk is written once and skipped
on re-run, so an interrupted backfill resumes where it stopped.

The FIRMS area API caps a request at 5 days. The published docs say 10; the
server rejects anything above 5 with "Invalid day range. Expects [1..5]".

Science-quality (SP) and near-real-time (NRT) date ranges never overlap for a
given sensor, so chunks cannot double-count a detection.

Chunks are intermediate. tools/firms_build.py compacts them into the published
per-year GeoParquet 2.0 files.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from threading import Lock

import duckdb

API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
MAX_WINDOW = 5

# source -> (sensor, quality)
SOURCES = {
    "MODIS_SP":         ("MODIS",        "sp"),
    "MODIS_NRT":        ("MODIS",        "nrt"),
    "VIIRS_SNPP_SP":    ("VIIRS_SNPP",   "sp"),
    "VIIRS_SNPP_NRT":   ("VIIRS_SNPP",   "nrt"),
    "VIIRS_NOAA20_SP":  ("VIIRS_NOAA20", "sp"),
    "VIIRS_NOAA20_NRT": ("VIIRS_NOAA20", "nrt"),
    "VIIRS_NOAA21_NRT": ("VIIRS_NOAA21", "nrt"),
}

# VIIRS confidence reaches us in two encodings. The area API returns the
# documented single letters l/n/h. The bulk NRT feeds return the full words
# low/nominal/high. Both mean the same thing, so store the documented letters.
# MODIS publishes an integer 0-100 and is left exactly as published.
# Source: https://www.earthdata.nasa.gov/data/tools/firms/active-fire-data-attributes-modis-viirs
CONF_SQL = """CASE lower(trim(CAST(confidence AS VARCHAR)))
    WHEN 'low' THEN 'l' WHEN 'nominal' THEN 'n' WHEN 'high' THEN 'h'
    ELSE trim(CAST(confidence AS VARCHAR)) END"""

# Satellite codes as published, mapped to platform names.
SAT_SQL = """CASE upper(trim(CAST(satellite AS VARCHAR)))
    WHEN 'T' THEN 'Terra' WHEN 'TERRA' THEN 'Terra'
    WHEN 'A' THEN 'Aqua'  WHEN 'AQUA'  THEN 'Aqua'
    WHEN 'N' THEN 'Suomi-NPP'
    WHEN 'N20' THEN 'NOAA-20' WHEN '1' THEN 'NOAA-20'
    WHEN 'N21' THEN 'NOAA-21' WHEN '2' THEN 'NOAA-21'
    ELSE trim(CAST(satellite AS VARCHAR)) END"""

_write_lock = Lock()

# Pace requests to the published budget instead of discovering it by failing.
#
# The key allows 5000 transactions per 10 minutes, and a whole-globe request
# costs 36 transactions per day requested, so a 5-day window costs 180. That is
# 500 transactions a minute, or one 5-day window every 21.6 seconds. Firing
# faster only earns HTTP 400s and long waits, and the archive takes just as
# long either way.
TX_PER_DAY = 36
BUDGET_PER_MIN = 5000 / 10.0
_pace_lock = Lock()
_next_start = [0.0]


def pace(span: int, safety: float = 1.08) -> None:
    """Block until this request may start, keeping the global rate legal."""
    interval = (span * TX_PER_DAY) / BUDGET_PER_MIN * 60.0 * safety
    while True:
        with _pace_lock:
            now = time.monotonic()
            start = max(now, _next_start[0])
            _next_start[0] = start + interval
        wait = start - now
        if wait <= 0:
            return
        time.sleep(wait)
        return


# The server answers an exhausted budget with HTTP 400 and this body. It is not
# a transient network error: the quota window is 10 minutes, so backing off in
# seconds just burns retries and leaves gaps in the archive.
RATE_LIMITED = "exceeding allowed transaction limit"

# Connection-level failures mean the host or the network is gone, not that this
# window is bad. Retrying every window through an outage is pure waste: the
# pacer waits 23s before each attempt, so 73 doomed windows burned 2.4 hours of
# runner time on 2026-09-09. Give up on the whole slice instead and let the
# resume path collect it on a later run.
CONNECTION_ERRORS = (
    "network is unreachable", "connection refused", "temporary failure in name resolution",
    "nodename nor servname", "name or service not known", "no route to host",
    "connection reset by peer",
)


class NetworkDown(RuntimeError):
    """The host is unreachable, so the whole slice should stop."""


def fetch(key: str, source: str, day: date, span: int, tries: int = 6,
          rate_limit_waits: int = 40) -> str:
    """Fetch one window, waiting out the quota window rather than failing.

    Rate-limit rejections get their own generous budget because they always
    clear: the counter drains over 10 minutes. Real errors keep the short
    exponential backoff and a small retry count.
    """
    url = f"{API}/{key}/{source}/world/{span}/{day.isoformat()}"
    delay = 3.0
    attempt = 0
    waited = 0
    while True:
        pace(span)
        try:
            with urllib.request.urlopen(url, timeout=600) as r:
                body = r.read().decode("utf-8", "replace")
            if body.startswith("latitude"):
                return body
            head = body.strip()[:200]
            if head and not head.startswith("latitude"):
                raise RuntimeError(f"API said: {head}")
            return body
        except Exception as exc:  # noqa: BLE001
            text = ""
            if isinstance(exc, urllib.error.HTTPError):
                try:
                    text = exc.read().decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    text = ""
            blob = f"{exc} {text}".lower()
            if any(m in blob for m in CONNECTION_ERRORS):
                # The host is gone. Fail this window quickly so the caller can
                # decide the whole slice is doomed, instead of each window
                # spending 90 seconds discovering the same outage.
                attempt += 1
                if attempt >= 2:
                    raise
                time.sleep(2)
                continue
            if RATE_LIMITED in blob or "transaction limit" in blob:
                waited += 1
                if waited > rate_limit_waits:
                    raise RuntimeError("still rate limited after "
                                       f"{rate_limit_waits} waits") from exc
                time.sleep(45)
                continue
            attempt += 1
            if attempt >= tries:
                raise
            time.sleep(delay)
            delay *= 2


def normalize(con, body: str, source: str, out: Path) -> int:
    """Parse one CSV body in DuckDB and write a normalized Parquet chunk."""
    sensor, quality = SOURCES[source]
    lines = body.splitlines()
    if len(lines) < 2:
        out.write_bytes(b"")  # sentinel: fetched, zero detections
        return 0
    header = [c.strip() for c in lines[0].split(",")]

    def col(name, cast="DOUBLE"):
        return f"CAST(NULLIF(trim(CAST({name} AS VARCHAR)), '') AS {cast})" \
            if name in header else f"CAST(NULL AS {cast})"

    has_type = "type" in header
    # The bulk NRT feeds omit `instrument`. It is not guessed: each feed is a
    # single instrument on a single platform, named in the product
    # (SUOMI_VIIRS_C2, J1_VIIRS_C2, J2_VIIRS_C2, MODIS_C6_1), and the
    # `satellite` column in the same file confirms it (N/N20/N21 vs T/A).
    inst = ("trim(CAST(instrument AS VARCHAR))" if "instrument" in header
            else ("'MODIS'" if sensor == "MODIS" else "'VIIRS'"))
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tf:
        tf.write(body)
        tmp = tf.name
    try:
        CONF = CONF_SQL
        sql = f"""
        COPY (
          SELECT
            ST_Point(longitude, latitude) AS geometry,
            strptime(CAST(acq_date AS VARCHAR) || ' ' ||
                     lpad(CAST(CAST(acq_time AS INTEGER) AS VARCHAR), 4, '0'),
                     '%Y-%m-%d %H%M') AS acq_datetime,
            CAST(acq_date AS DATE) AS acq_date,
            '{sensor}' AS sensor,
            {SAT_SQL} AS satellite,
            {inst} AS instrument,
            '{quality}' AS quality,
            trim(CAST(version AS VARCHAR)) AS version,
            {col('brightness')} AS brightness,
            {col('bright_t31')} AS bright_t31,
            {col('bright_ti4')} AS bright_ti4,
            {col('bright_ti5')} AS bright_ti5,
            {col('scan')} AS scan,
            {col('track')} AS track,
            {col('frp')} AS frp,
            trim(CAST(daynight AS VARCHAR)) AS daynight,
            {CONF} AS confidence,
            TRY_CAST(trim(CAST(confidence AS VARCHAR)) AS INTEGER) AS confidence_pct,
            {col('type', 'INTEGER') if has_type else 'CAST(NULL AS INTEGER)'} AS type
          FROM read_csv('{tmp}', header=true, all_varchar=true,
                        types={{'latitude':'DOUBLE','longitude':'DOUBLE'}})
          WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        ) TO '{out}' (FORMAT PARQUET, COMPRESSION zstd)
        """
        with _write_lock:
            con.execute(sql)
            n = con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]
        return n
    finally:
        os.unlink(tmp)


def windows(start: date, end: date, span: int):
    d = start
    while d <= end:
        yield d, min(span, (end - d).days + 1)
        d += timedelta(days=span)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, choices=sorted(SOURCES))
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--key", default=os.environ.get("FIRMS_MAP_KEY", ""))
    a = ap.parse_args()
    if not a.key:
        print("error: set FIRMS_MAP_KEY or pass --key", file=sys.stderr)
        return 2

    outdir = Path(a.out) / a.source
    outdir.mkdir(parents=True, exist_ok=True)
    jobs = [(d, s) for d, s in windows(date.fromisoformat(a.start),
                                       date.fromisoformat(a.end), MAX_WINDOW)
            if not (outdir / f"{d.isoformat()}_{s}.parquet").exists()]
    print(f"{a.source}: {len(jobs)} windows -> {outdir}", flush=True)
    if not jobs:
        return 0

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("SET memory_limit='2GB'; SET threads=2;")
    done = total = failed = 0
    t0 = time.time()

    consecutive_conn_failures = [0]

    def work(job):
        """Fetch AND normalize inside the worker.

        The future must not carry the CSV body. A Future holds its result until
        it is dropped, so returning a 20 MB string per window makes the pool
        retain every body it ever fetched.
        """
        d, s_ = job
        body = fetch(a.key, a.source, d, s_)
        n = normalize(con, body, a.source, outdir / f"{d.isoformat()}_{s_}.parquet")
        del body
        return n

    # Submit in bounded batches so the futures dict stays small.
    BATCH = 64
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for i in range(0, len(jobs), BATCH):
            batch = jobs[i:i + BATCH]
            futs = {pool.submit(work, j): j for j in batch}
            for f in as_completed(futs):
                jd, js = futs[f]
                try:
                    total += f.result()
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    print(f"FAIL {a.source} {jd} +{js}d: {exc}", file=sys.stderr, flush=True)
                    blob = str(exc).lower()
                    if any(m in blob for m in CONNECTION_ERRORS):
                        consecutive_conn_failures[0] += 1
                        # Nothing has succeeded and the host keeps refusing:
                        # this is an outage, not a bad window.
                        if done == 0 and consecutive_conn_failures[0] >= 5:
                            print(f"{a.source}: aborting, host unreachable "
                                  f"({consecutive_conn_failures[0]} connection "
                                  f"failures, no window succeeded). "
                                  f"Re-run to resume.", file=sys.stderr, flush=True)
                            # Without this, leaving the pool blocks until every
                            # already-queued window finishes its own retries.
                            pool.shutdown(wait=False, cancel_futures=True)
                            return 2
                    else:
                        consecutive_conn_failures[0] = 0
                    continue
                done += 1
                consecutive_conn_failures[0] = 0
            futs.clear()
            rate = done / max(time.time() - t0, 1)
            eta = (len(jobs) - done) / max(rate, 1e-6) / 60
            print(f"  {a.source}: {done}/{len(jobs)} win, {total:,} rows, "
                  f"{rate:.2f} win/s, ETA {eta:.0f}m", flush=True)

    print(f"{a.source}: DONE {done}/{len(jobs)} windows, {total:,} rows, {failed} failed",
          flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
