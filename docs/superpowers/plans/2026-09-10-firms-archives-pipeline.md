# FIRMS Archive & Pipeline Implementation Plan (Plan 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the three archives the adaptive timeline needs — an all-time monthly archive, per-year archives with daily buckets and raw points, and a renamed rolling window — each declaring its time axis in metadata, and bring the existing preview app onto them with a working year selector.

**Architecture:** Every archive owns exactly one bucket granularity and declares it in PMTiles metadata under `firms:timeline`, so no viewer has to infer the axis from column names. A shared pure module derives that declaration from the columns actually present, which makes it unit-testable and impossible to drift from the data. The build tools stay small CLIs composed by workflows, matching the existing `tools/` layout.

**Tech Stack:** Python 3.12, DuckDB (+spatial, httpfs), `geoparquet-io` (`gpio`) from main, PMTiles CLI, tippecanoe via `gpio pmtiles pyramid`, boto3, plain-JS/MapLibre for the preview app.

**Spec:** `docs/superpowers/specs/2026-09-10-adaptive-timeline-design.md`

## Global Constraints

- **Plan 2 (the new explorer app) is out of scope here.** This plan must leave the catalog shippable on its own.
- **The existing preview app at `apps/firms-preview/` is kept and updated.** It does not get a timeline.
- Tests are standalone scripts, not pytest-collected. Each appends to an `errors` list via `check(cond, msg)` and reports **once at the very end** of the file. Register new tests in `tests/run_all.py`.
- Run the suite as `CI_LIGHT=1 python3 tests/run_all.py`. Without `CI_LIGHT=1`, `test_links.py` fails locally because data bytes live in object storage, not git.
- Lint gate is `pyflakes tools/*.py tests/*.py`.
- GeoParquet 2.0, zstd, `ZSTD_LEVEL = 22` for published outputs.
- Bucket column names are **`count_YYYYMM`** (6 digits, monthly) or **`count_YYYYMMDD`** (8 digits, daily). Never `count_MMDD` — 4-digit keys are ambiguous against 6- and 8-digit ones and would need a separate `year` field to resolve.
- Any HTTP request to `data.source.coop` **must set a `User-Agent`**. Its CDN answers 403 to the default `Python-urllib`, and a rejected client is indistinguishable from a missing file at the call site.
- Public base URL: `https://data.source.coop/portolan-mirrors/firms-catalog`. Config lives in `catalog.publish.yaml` (`publish_dir: catalog`, `data_dir: ../catalog-staging/publish`).
- Commit after every task.

---

### Task 1: The `firms:timeline` metadata contract

The viewer must not pattern-match column names or read bucket keys off a sampled feature — MVT omits zero-valued attributes, so a sampled feature once yielded 3 months of a 12-month year. Each archive declares its own axis.

**Files:**
- Create: `tools/timeline_meta.py`
- Create: `tools/make_timeline.py`
- Create: `tests/test_timeline_meta.py`
- Modify: `tests/run_all.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `timeline_meta.declare(columns: Iterable[str]) -> dict` returning
    `{"unit": "month"|"day", "key_format": str, "min": str, "max": str, "buckets": int}`, raising `ValueError` on mixed or absent bucket columns.
  - `timeline_meta.bucket_keys(columns: Iterable[str]) -> list[str]` returning sorted bucket suffixes.
  - CLI `python3 tools/make_timeline.py <archive.pmtiles>`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_timeline_meta.py`:

```python
#!/usr/bin/env python3
"""The time axis an archive declares must match the columns it actually has.

A viewer that guesses the axis from column names, or reads bucket keys off one
feature, gets it wrong: MVT omits zero-valued attributes, so a sampled feature
showed three months of a twelve-month year. This gate covers the derivation
that replaces the guessing.

Run: python3 tests/test_timeline_meta.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from timeline_meta import bucket_keys, declare  # noqa: E402

errors: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


NON_BUCKET = ["a5_cell", "count", "sum_frp", "avg_frp", "max_frp", "geometry"]

# --- monthly ---
monthly = NON_BUCKET + ["count_202001", "count_202002", "count_201912"]
d = declare(monthly)
check(d["unit"] == "month", f"six digits is monthly, got {d['unit']}")
check(d["key_format"] == "count_YYYYMM", "monthly key format is named")
check(d["min"] == "201912", f"min is the earliest bucket, got {d['min']}")
check(d["max"] == "202002", f"max is the latest bucket, got {d['max']}")
check(d["buckets"] == 3, f"buckets counts the columns, got {d['buckets']}")

# --- daily ---
daily = NON_BUCKET + ["count_20200101", "count_20201231"]
d = declare(daily)
check(d["unit"] == "day", f"eight digits is daily, got {d['unit']}")
check(d["key_format"] == "count_YYYYMMDD", "daily key format is named")
check(d["buckets"] == 2, "daily buckets counted")

# --- ordering is by value, not by string position in the input ---
shuffled = NON_BUCKET + ["count_202010", "count_202002", "count_202001"]
check(bucket_keys(shuffled) == ["202001", "202002", "202010"],
      "bucket keys come back sorted")

# --- rejections ---
# Mixed widths cannot be one axis, and silently picking one would mislabel it.
try:
    declare(NON_BUCKET + ["count_202001", "count_20200101"])
    check(False, "mixed bucket widths must raise")
except ValueError:
    check(True, "")

# An archive with no bucket columns has no axis to declare.
try:
    declare(NON_BUCKET)
    check(False, "no bucket columns must raise")
except ValueError:
    check(True, "")

# Four-digit keys are the ambiguous form the spec forbids.
try:
    declare(NON_BUCKET + ["count_0101", "count_0102"])
    check(False, "four-digit bucket keys must raise")
except ValueError:
    check(True, "")

# --- other count_ columns must not be mistaken for buckets ---
withdims = NON_BUCKET + ["count_modis", "count_d", "count_n", "count_202001"]
check(bucket_keys(withdims) == ["202001"],
      "sensor and daynight breakdowns are not time buckets")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print("OK: timeline declaration matches the columns")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 tests/test_timeline_meta.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'timeline_meta'`

- [ ] **Step 3: Write the module**

Create `tools/timeline_meta.py`:

```python
#!/usr/bin/env python3
"""Derive an archive's declared time axis from the columns it actually has.

A viewer must not infer the axis by pattern-matching column names, and must not
read bucket keys off a sampled feature: MVT omits zero-valued attributes, so a
sampled feature reported three months of a twelve-month year. Instead every
archive carries `firms:timeline` in its PMTiles metadata, and this module is
the single place that decides what it says.

Bucket columns are `count_YYYYMM` or `count_YYYYMMDD`. Four-digit keys are
rejected on purpose: `count_0101` could be a month-day or a year, and telling
them apart would need a second field.
"""
from __future__ import annotations

import re
from typing import Iterable

BUCKET = re.compile(r"^count_(\d+)$")
# digits -> (unit, key_format)
WIDTHS = {6: ("month", "count_YYYYMM"), 8: ("day", "count_YYYYMMDD")}


def bucket_keys(columns: Iterable[str]) -> list[str]:
    """Sorted time-bucket suffixes. Non-time `count_` columns are excluded.

    `count_modis` and `count_d` are sensor and day/night breakdowns, not time,
    so the pattern requires digits.
    """
    keys = {m.group(1) for c in columns if (m := BUCKET.match(c))}
    return sorted(keys)


def declare(columns: Iterable[str]) -> dict:
    """The `firms:timeline` value for an archive with these columns."""
    keys = bucket_keys(columns)
    if not keys:
        raise ValueError("no count_<digits> bucket columns found")
    widths = {len(k) for k in keys}
    if len(widths) > 1:
        raise ValueError(f"bucket columns have mixed widths: {sorted(widths)}")
    width = widths.pop()
    if width not in WIDTHS:
        raise ValueError(
            f"{width}-digit bucket keys are ambiguous; use YYYYMM or YYYYMMDD")
    unit, key_format = WIDTHS[width]
    return {"unit": unit, "key_format": key_format,
            "min": keys[0], "max": keys[-1], "buckets": len(keys)}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python3 tests/test_timeline_meta.py`
Expected: `OK: timeline declaration matches the columns`

- [ ] **Step 5: Write the CLI that stamps it into an archive**

Create `tools/make_timeline.py`:

```python
#!/usr/bin/env python3
"""Write `firms:timeline` into a PMTiles archive, derived from its own columns.

Deriving rather than accepting the axis on the command line is the point: a
declaration passed in by hand can disagree with the tiles, and this one cannot.

Run: python3 tools/make_timeline.py path/to/archive.pmtiles
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from timeline_meta import declare

AGGREGATE_LAYER = "aggregate"


def metadata(archive: str) -> dict:
    out = subprocess.run(["pmtiles", "show", archive, "--metadata"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out[out.index("{"):])


def aggregate_columns(md: dict) -> list[str]:
    layers = md.get("vector_layers") or json.loads(
        md.get("json", "{}")).get("vector_layers", [])
    for layer in layers:
        if layer.get("id") == AGGREGATE_LAYER:
            return list((layer.get("fields") or {}).keys())
    raise SystemExit(f"no '{AGGREGATE_LAYER}' layer in the archive")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pmtiles", help="archive to annotate in place")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    md = metadata(a.pmtiles)
    axis = declare(aggregate_columns(md))
    print(f"  {a.pmtiles}: {axis['buckets']} {axis['unit']} bucket(s) "
          f"{axis['min']}..{axis['max']}")
    if a.dry_run:
        return 0

    md["firms:timeline"] = axis
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(md, f)
        tmp = f.name
    try:
        subprocess.run(["pmtiles", "edit", a.pmtiles, f"--metadata={tmp}"],
                       check=True)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
```

- [ ] **Step 6: Register the test**

In `tests/run_all.py`, add `"test_timeline_meta.py",` to the `TESTS` list, after `"test_upload_data.py",`.

- [ ] **Step 7: Run the full suite and the linter**

Run: `pyflakes tools/*.py tests/*.py && CI_LIGHT=1 python3 tests/run_all.py`
Expected: no pyflakes output; every gate `OK`.

- [ ] **Step 8: Commit**

```bash
git add tools/timeline_meta.py tools/make_timeline.py tests/test_timeline_meta.py tests/run_all.py
git commit -m "Declare each archive's time axis in its own metadata

A viewer that infers the axis from column names, or reads bucket keys off a
sampled feature, gets it wrong: MVT omits zero-valued attributes, so one
sampled feature reported three months of a twelve-month year. Derive the
declaration from the columns actually present instead, in one pure function
that cannot drift from the tiles."
```

---

### Task 2: Rename `fire.pmtiles` to `fire-latest.pmtiles`

With per-year archives it is the only name that does not say which period it covers.

**Files:**
- Modify: `tools/firms_aggregate.py:146`
- Modify: `tools/make_collection.py:54`
- Modify: `apps/firms-preview/index.html:137,154`
- Modify: `.github/workflows/refresh-nrt.yml:58`
- Modify: `.github/workflows/pages.yml:6`
- Regenerate: `catalog/detections/collection.json`, `catalog/detections/styles/*.json`

**Interfaces:**
- Consumes: nothing.
- Produces: published key `detections/fire-latest.pmtiles`; `make_collection.py --pmtiles` default becomes `fire-latest.pmtiles`.

- [ ] **Step 1: Change the producer and the defaults**

In `tools/firms_aggregate.py`, line 146, change:

```python
        archive = tiles / "fire.pmtiles"
```

to:

```python
        # Named for the period it covers, like every other archive. The
        # rolling window is the only one that ever lacked that.
        archive = tiles / "fire-latest.pmtiles"
```

In `tools/make_collection.py`, line 54, change the default:

```python
    ap.add_argument("--pmtiles", default="fire-latest.pmtiles")
```

In `.github/workflows/refresh-nrt.yml`, line 58, the `mv` is a no-op renaming a file to itself. Replace that whole line with nothing (delete it) — `firms_aggregate.py` now writes the final name directly. Edit as text; do **not** round-trip the YAML through PyYAML, which turns `on:` into `true:` and strips comments.

In `.github/workflows/pages.yml`, line 6, update the comment text `?tiles=./fire.pmtiles` to `?tiles=./fire-latest.pmtiles`.

- [ ] **Step 2: Point the preview app at the new name**

In `apps/firms-preview/index.html`, line 137 (comment) and line 154:

```javascript
    tiles: qs.get("tiles") || `${BASE}/fire-latest.pmtiles`,
```

and in the comment above, `?tiles=./fire.pmtiles` becomes `?tiles=./fire-latest.pmtiles`.

- [ ] **Step 3: Regenerate the collection and styles**

```bash
python3 tools/make_collection.py \
  --data ../catalog-staging/publish/detections \
  --out catalog/detections/collection.json
python3 tools/make_styles.py ../catalog-staging/preview/fire-latest.pmtiles \
  --out catalog/detections/styles --tiles ../fire-latest.pmtiles \
  --suffix ", last 7 days"
```

If `../catalog-staging/preview/fire-latest.pmtiles` does not exist yet, rename the local build first: `mv ../catalog-staging/preview/fire.pmtiles ../catalog-staging/preview/fire-latest.pmtiles`.

- [ ] **Step 4: Verify nothing still references the old name**

Run: `grep -rn "fire\.pmtiles" tools/ apps/ catalog/ .github/ docs/`
Expected: no output. (`docs/` may legitimately mention it in historical narrative; if so, confirm each hit is describing the past, not instructing the present.)

- [ ] **Step 5: Run the suite**

Run: `CI_LIGHT=1 python3 tests/run_all.py`
Expected: every gate `OK`. `test_links.py` proves the collection's `pmtiles` asset and `rel:pmtiles` link agree with the new name.

- [ ] **Step 6: Publish the rename and delete the old key**

```bash
eval "$(source-coop creds --format env)"
python3 tools/upload_data.py --confirm
python3 tools/publish.py --confirm
aws s3 rm s3://portolan-mirrors/firms-catalog/detections/fire.pmtiles \
  --endpoint-url https://data.source.coop
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Rename fire.pmtiles to fire-latest.pmtiles

With per-year archives it was the only name that did not say which period it
covers. Also drops a mv in refresh-nrt.yml that renamed a file to itself."
```

---

### Task 3: Year archives carry daily buckets on every band, plus raw points

Today `fire-2020-split.pmtiles` puts monthly on its coarse band and daily on its fine band, tying temporal resolution to *spatial* zoom — you cannot see daily bars while looking at a continent. Measured: r6-daily is 2.9 MB, so decoupling is nearly free. Year archives are also aggregate-only today, so zooming into 2003 yields bigger cells with nothing underneath.

**Files:**
- Modify: `tools/firms_aggregate.py`
- Test: `tests/test_timeline_meta.py` (already covers the derivation; no new test here)

**Interfaces:**
- Consumes: `timeline_meta.declare` (Task 1).
- Produces: `firms_aggregate.py --year <YYYY>` writing `fire-<YYYY>.pmtiles` with an `aggregate` layer carrying `count_YYYYMMDD` columns on every band and a `features` layer from z10.

- [ ] **Step 1: Emit 8-digit daily keys**

`tools/firms_aggregate.py` already stages `strftime(acq_date, '%Y%m%d') AS day` (line ~90), which is the 8-digit form the constraint requires. Confirm with:

Run: `grep -n "strftime(acq_date" tools/firms_aggregate.py`
Expected: `%Y%m%d`. No change needed — this step is a check, not an edit.

- [ ] **Step 2: Raise the day breakdown limit**

`DIMENSIONS = {"day": 40, ...}` caps the pivot at 40 values, which is fine for a 7-day window and truncates a year. Change the dict to:

```python
# breakdown column -> how many pivoted values to allow
# A year needs 366 day columns; the rolling window uses far fewer but the
# limit only caps, so one number serves both.
DIMENSIONS = {"day": 366, "sensor": 8, "daynight": 4}
```

- [ ] **Step 3: Add a --year option that names the archive and scopes the input**

In `main()`, after the existing `--features-min-zoom` argument, add:

```python
    ap.add_argument("--year", type=int,
                    help="build one calendar year; names the archive "
                         "fire-<year>.pmtiles and reads only that partition")
```

Then change the input glob. Replace:

```python
    data, out = Path(a.data), Path(a.out)
    if not sorted(data.glob("year=*/*.parquet")):
        raise SystemExit(f"no year partitions under {data}")
```

with:

```python
    data, out = Path(a.data), Path(a.out)
    part = f"year={a.year}" if a.year else "year=*"
    if not sorted(data.glob(f"{part}/*.parquet")):
        raise SystemExit(f"no {part} partition under {data}")
```

and in the `COPY ... FROM read_parquet(...)` staging query, replace
`'{data}/year=*/*.parquet'` with `'{data}/{part}/*.parquet'`.

- [ ] **Step 4: Name the archive after the year**

Replace the archive-naming line from Task 2:

```python
        archive = tiles / "fire-latest.pmtiles"
```

with:

```python
        name = f"fire-{a.year}.pmtiles" if a.year else "fire-latest.pmtiles"
        archive = tiles / name
```

- [ ] **Step 5: Stamp the time axis after building**

At the end of the `if a.tiles:` block, after the size print, add:

```python
        run(["python3", str(Path(__file__).with_name("make_timeline.py")),
             str(archive)], quiet=False)
```

- [ ] **Step 6: Build one year and verify both bands carry daily keys**

```bash
python3 tools/firms_aggregate.py \
  --data ../catalog-staging/publish/detections \
  --out ../catalog-staging/y2020 \
  --tiles ../catalog-staging/preview \
  --year 2020 --resolution 8 --levels 6
```

Then verify the coarse band has daily columns, which is the whole point:

```bash
python3 - <<'EOF'
import subprocess, gzip, math, re, json
import mapbox_vector_tile as mvt
def props(z, lon=20, lat=-5):
    n=2**z; x=int((lon+180)/360*n)
    la=math.radians(lat); y=int((1-math.log(math.tan(la)+1/math.cos(la))/math.pi)/2*n)
    b=subprocess.run(["pmtiles","tile","../catalog-staging/preview/fire-2020.pmtiles",
                      str(z),str(x),str(y)],capture_output=True).stdout
    if len(b)<100: return f"z{z}: no tile"
    raw=gzip.decompress(b) if b[:2]==b'\x1f\x8b' else b
    d=mvt.decode(raw); out=[]
    for name,layer in d.items():
        ks=sorted(layer["features"][0]["properties"]) if layer["features"] else []
        tk=[k for k in ks if re.match(r'^count_\d{8}$',k)]
        out.append(f"{name}: n={len(layer['features'])} daily_keys={len(tk)}")
    return f"z{z}: " + " | ".join(out)
for z in (2,4,6,8,10): print(props(z))
md=json.loads(subprocess.run(["pmtiles","show",
  "../catalog-staging/preview/fire-2020.pmtiles","--metadata"],
  capture_output=True,text=True).stdout.partition("{")[1:] and
  subprocess.run(["pmtiles","show","../catalog-staging/preview/fire-2020.pmtiles",
  "--metadata"],capture_output=True,text=True).stdout[
  subprocess.run(["pmtiles","show","../catalog-staging/preview/fire-2020.pmtiles",
  "--metadata"],capture_output=True,text=True).stdout.index("{"):])
print("firms:timeline =", md.get("firms:timeline"))
EOF
```

Expected: every aggregate band reports `daily_keys` in the hundreds (not 12, not 0); a `features` layer appears at z10; and `firms:timeline` reads `unit: day`, `buckets: 366`, `min: 20200101`, `max: 20201231`.

- [ ] **Step 7: Commit**

```bash
git add tools/firms_aggregate.py
git commit -m "Give year archives daily buckets on every band and raw points

The split archive put monthly on the coarse band and daily on the fine one,
which tied temporal resolution to spatial zoom: you could not see daily bars
while looking at a continent. r6-daily measures 2.9 MB, so decoupling is
nearly free. Year archives were also aggregate-only, so zooming into 2003 gave
bigger cells with nothing underneath."
```

---

### Task 4: Per-year breaks and styles

`fire-2020-split.pmtiles` has neither `gpio:pyramid` nor `firms:breaks`. Promoting per-year archives to a first-class tier means the year build produces both, plus its own styles.

**Files:**
- Create: `tools/build_year.sh`
- Modify: `tools/make_items.py` (already advertises tile assets when `styles/default.json` exists — verify, do not rewrite)

**Interfaces:**
- Consumes: `firms_aggregate.py --year` (Task 3); `make_breaks.py`; `make_styles.py`; `make_timeline.py` (Task 1).
- Produces: `tools/build_year.sh <YEAR>` leaving `fire-<YEAR>.pmtiles` annotated and `catalog/detections/year=<YEAR>/styles/{default,avg-frp}.json` written.

- [ ] **Step 1: Write the year build script**

Create `tools/build_year.sh`:

```bash
#!/usr/bin/env bash
# Build one year's archive end to end: aggregate, tile, class breaks, styles.
#
# Breaks are keyed by a5 level and take their zoom ranges from gpio:pyramid,
# so they change exactly when the cells change and never in between. Styles
# are generated from those same breaks, which is what keeps the plain
# published styles agreeing with any app that reads the metadata.
set -euo pipefail

YEAR="${1:?usage: build_year.sh YEAR}"
DATA="${DATA:-../catalog-staging/publish/detections}"
WORK="${WORK:-../catalog-staging/y$YEAR}"
TILES="${TILES:-../catalog-staging/preview}"
CATALOG="${CATALOG:-catalog/detections/year=$YEAR}"
ARCHIVE="$TILES/fire-$YEAR.pmtiles"

python3 tools/firms_aggregate.py --data "$DATA" --out "$WORK" \
  --tiles "$TILES" --year "$YEAR" --resolution 8 --levels 6

# --band takes the a5 level and the aggregate it was tiled from; the zoom
# range comes from the archive's own pyramid, not the command line.
python3 tools/make_breaks.py "$ARCHIVE" \
  --band "6:$WORK/cells_r6.parquet" \
  --band "8:$WORK/cells.parquet"

python3 tools/make_styles.py "$ARCHIVE" \
  --out "$CATALOG/styles" --tiles "../fire-$YEAR.pmtiles" --suffix ", $YEAR"

echo "built $ARCHIVE"
```

Then: `chmod +x tools/build_year.sh`

- [ ] **Step 2: Build 2020 with it**

Run: `bash tools/build_year.sh 2020`
Expected: breaks printed for `r6` and `r8` with their zoom ranges; two style files written under `catalog/detections/year=2020/styles/`.

- [ ] **Step 3: Verify the archive declares everything a first-class tier needs**

```bash
pmtiles show --metadata ../catalog-staging/preview/fire-2020.pmtiles \
  | python3 -c "
import json,sys
raw=sys.stdin.read(); md=json.loads(raw[raw.index('{'):])
for key in ('gpio:pyramid','firms:breaks','firms:timeline'):
    print(f'{key}: {\"present\" if key in md else \"MISSING\"}')
"
```

Expected: all three `present`.

- [ ] **Step 4: Regenerate items so the year advertises its tiles and styles**

```bash
python3 tools/make_items.py --catalog catalog --key "$FIRMS_MAP_KEY"
```

Then confirm: `python3 -c "import json;print(list(json.load(open('catalog/detections/year=2020/2020.json'))['assets']))"`
Expected: `['data', 'pmtiles', 'style-avg-frp', 'style-default']`

- [ ] **Step 5: Run the suite**

Run: `CI_LIGHT=1 python3 tests/run_all.py`
Expected: every gate `OK`, `rashid` reporting 0 errors.

- [ ] **Step 6: Commit**

```bash
git add tools/build_year.sh catalog/detections/
git commit -m "Build each year's breaks and styles with its archive

fire-2020-split.pmtiles had neither gpio:pyramid nor firms:breaks, which is
fine for a scratch experiment and not fine for a tier the explorer swaps to.
One script now produces the archive, its level-keyed breaks and its styles
together, so they cannot be built apart."
```

---

### Task 5: The all-time monthly archive

Measured at ~7–8 MB for 27 years: the r6 cell union converges (new cells decay 13,558 → 375 across seven spread years, because fire recurs in the same places) and the marginal cost is a flat 0.261 MB/year.

**Files:**
- Create: `tools/make_alltime.py`

**Interfaces:**
- Consumes: published `detections/year=*/detections.parquet`; `make_timeline.py`, `make_breaks.py`, `make_styles.py`.
- Produces: `alltime.pmtiles` with `count_YYYYMM` columns on r4 and r6 bands, plus `--years-dir` cache of per-year aggregates.

- [ ] **Step 1: Write the tool**

Create `tools/make_alltime.py`:

```python
#!/usr/bin/env python3
"""Build the all-time monthly archive: every month since 2000 on coarse cells.

Measured rather than assumed. Three numbers decided this shape:

  * coarse cells with fine time columns are cheap -- r6 x 366 daily = 2.9 MB
  * the r6 cell union converges, new cells decaying 13,558 -> 375 across seven
    spread years, because fire recurs in the same places
  * so 27 years of monthly columns costs about 7-8 MB, at a flat marginal
    0.261 MB per added year

Each year is aggregated independently and cached, so the archive can be built
from the years published today and extended as the rest land: adding a year is
a re-join, not a re-read.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

import duckdb

PUBLIC = "https://data.source.coop/portolan-mirrors/firms-catalog/detections"
BASE_RES = 6
OVERVIEWS = "4"
ZSTD_LEVEL = 22


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-1500:], r.stderr[-1500:], file=sys.stderr)
        raise SystemExit(f"failed: {' '.join(cmd[:6])}")


def year_cells(con, year: int, cache: Path) -> Path:
    """One year aggregated to r6 with a month breakdown, cached on disk."""
    out = cache / f"cells_{year}_m.parquet"
    if out.exists():
        return out
    # Only geometry and the month are needed, so column pruning keeps this to
    # a fraction of the 460 MB file.
    src = cache / f"{year}_min.parquet"
    con.execute(f"""
        COPY (SELECT geometry, strftime(acq_datetime, '%Y%m') AS month, frp
              FROM read_parquet('{PUBLIC}/year={year}/detections.parquet'))
        TO '{src}' (FORMAT parquet, COMPRESSION zstd)""")
    run(["gpio", "process", "aggregate", "a5", str(src), str(out),
         "--resolution", str(BASE_RES), "--metric", "sum:frp,avg:frp,max:frp",
         "--breakdown", "month", "--breakdown-limit", "12",
         "--out-geometry", "polygon", "--geoparquet-version", "2.0",
         "--compression-level", str(ZSTD_LEVEL)])
    src.unlink(missing_ok=True)
    return out


def join_years(con, parts: dict[int, Path], out: Path) -> None:
    """One row per cell, one column per month, across every year."""
    sel = ["u.a5_cell", "any_value(u.geometry) AS geometry"]
    frm = []
    for i, (year, path) in enumerate(sorted(parts.items())):
        cols = sorted(
            c[0] for c in con.execute(f"DESCRIBE SELECT * FROM '{path}'").fetchall()
            if c[0].startswith("count_"))
        # gpio names the pivot from the value, which is already YYYYMM here.
        sel += [f't{i}."{c}" AS "{c}"' for c in cols]
        frm.append(f"LEFT JOIN '{path}' t{i} ON t{i}.a5_cell = u.a5_cell")
    union = " UNION ".join(
        f"SELECT a5_cell, geometry FROM '{p}'" for p in parts.values())
    con.execute(f"""
        COPY (SELECT {', '.join(sel)}
              FROM (SELECT a5_cell, any_value(geometry) AS geometry
                    FROM ({union}) GROUP BY a5_cell) u
              {' '.join(frm)} GROUP BY ALL)
        TO '{out}' (FORMAT parquet, COMPRESSION zstd,
                    COMPRESSION_LEVEL {ZSTD_LEVEL})""")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", required=True,
                    help="comma list, e.g. 2000,2001,...  or 2000-2026")
    ap.add_argument("--cache", default="../catalog-staging/alltime")
    ap.add_argument("--tiles", default="../catalog-staging/preview")
    ap.add_argument("--catalog", default="catalog/detections")
    a = ap.parse_args()

    if "-" in a.years and "," not in a.years:
        lo, hi = (int(x) for x in a.years.split("-"))
        years = list(range(lo, hi + 1))
    else:
        years = [int(y) for y in a.years.split(",")]

    cache = Path(a.cache)
    cache.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")

    parts: dict[int, Path] = {}
    for y in years:
        try:
            parts[y] = year_cells(con, y, cache)
            print(f"  {y}: {parts[y].stat().st_size / 1e6:.2f} MB")
        except Exception as exc:  # noqa: BLE001 - an unpublished year is normal
            print(f"  {y}: skipped ({str(exc)[:70]})")
    if not parts:
        raise SystemExit("no years aggregated")

    combined = cache / "alltime.parquet"
    join_years(con, parts, combined)
    print(f"joined {len(parts)} year(s) -> {combined} "
          f"({combined.stat().st_size / 1e6:.1f} MB)")

    run(["gpio", "process", "overview", str(combined), "--levels", OVERVIEWS,
         "--force"])

    tiles = Path(a.tiles)
    tiles.mkdir(parents=True, exist_ok=True)
    archive = tiles / "alltime.pmtiles"
    run(["gpio", "pmtiles", "pyramid", str(combined), str(archive),
         "--levels", OVERVIEWS, "-f"])
    print(f"  {archive} ({archive.stat().st_size / 1e6:.1f} MB)")

    here = Path(__file__).resolve().parent
    run(["python3", str(here / "make_timeline.py"), str(archive)])
    run(["python3", str(here / "make_breaks.py"), str(archive),
         "--band", f"{BASE_RES}:{combined}",
         "--band", f"{OVERVIEWS}:{cache / 'alltime_r4.parquet'}"])
    run(["python3", str(here / "make_styles.py"), str(archive),
         "--out", str(Path(a.catalog) / "styles-alltime"),
         "--tiles", "../alltime.pmtiles", "--suffix", ", all years"])
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
```

- [ ] **Step 2: Build it from the published years**

Run: `python3 tools/make_alltime.py --years 2000-2026`
Expected: per-year sizes near 1.0–1.3 MB each, unpublished years reported as `skipped`, and a joined archive in the 7–10 MB range. If it lands far above that, stop and re-measure before continuing — the spec's whole architecture rests on this number.

- [ ] **Step 3: Verify the declared axis**

```bash
pmtiles show --metadata ../catalog-staging/preview/alltime.pmtiles \
  | python3 -c "
import json,sys
raw=sys.stdin.read(); md=json.loads(raw[raw.index('{'):])
print('timeline:', md.get('firms:timeline'))
print('breaks levels:', list((md.get('firms:breaks') or {}).keys()))
"
```

Expected: `unit: month`, `min` at the earliest published month, `buckets` equal to the number of months spanned; breaks keyed `r4` and `r6`.

- [ ] **Step 4: Commit**

```bash
git add tools/make_alltime.py catalog/detections/styles-alltime
git commit -m "Build the all-time monthly archive

Measured, not assumed: coarse cells with fine time columns are cheap, the r6
cell union converges as fire recurs in the same places, and 27 years of
monthly columns therefore costs 7-8 MB at a flat 0.261 MB per added year. An
earlier naive scaling from per-year file sizes suggested ~79 MB and was wrong.

Years are aggregated independently and cached, so the archive builds from what
is published today and extends as the rest land."
```

---

### Task 6: Advertise the new archives in the catalog

**Files:**
- Modify: `tools/make_collection.py`
- Regenerate: `catalog/detections/collection.json`

**Interfaces:**
- Consumes: `alltime.pmtiles` (Task 5), `fire-<year>.pmtiles` (Task 4).
- Produces: collection assets `pmtiles` (latest), `pmtiles-alltime`, and style assets for both.

- [ ] **Step 1: Add the all-time archive as a collection asset**

In `tools/make_collection.py`, after the argument definitions, add:

```python
    ap.add_argument("--alltime", default="alltime.pmtiles",
                    help="all-time archive filename, or empty to omit")
```

Then where the `pmtiles` asset is built, add a sibling. Locate the block that
creates the `"pmtiles"` asset and add immediately after it:

```python
    # The all-time archive is a second visual asset, not a replacement: it is
    # the only one spanning every year, and the only one a viewer can open to
    # see the whole record at once.
    if a.alltime:
        assets["pmtiles-alltime"] = {
            "href": f"./{a.alltime}",
            "type": "application/vnd.pmtiles",
            "title": "All years, monthly aggregate, vector tiles",
            "roles": ["visual", "tiles", "overview"],
            **file_meta(a.alltime),
        }
```

If `file_meta` raises for a missing local file, guard with
`if a.alltime and (data_root(config) / a.alltime).exists():` — match whatever
pattern the surrounding code already uses for the existing `pmtiles` asset.

- [ ] **Step 2: Regenerate and validate**

```bash
python3 tools/make_collection.py \
  --data ../catalog-staging/publish/detections \
  --out catalog/detections/collection.json
python3 tools/make_items.py --catalog catalog --key "$FIRMS_MAP_KEY"
CI_LIGHT=1 python3 tests/run_all.py
```

Expected: every gate `OK`; `rashid` 0 errors.

- [ ] **Step 3: Commit**

```bash
git add tools/make_collection.py catalog/
git commit -m "Advertise the all-time archive on the collection

It is the only asset spanning every year, so a viewer that opens the
collection can reach the whole record without knowing which years exist."
```

---

### Task 7: Bring the preview app onto the new archives, with a year selector

The app currently hardcodes two datasets — `live` and a literal `"2020"` pointing at a local file. The catalog now lists every published year as a STAC item, so the selector should come from the data.

**Files:**
- Modify: `apps/firms-preview/index.html`

**Interfaces:**
- Consumes: `collection.json` `rel:item` links; item `assets.pmtiles`; `firms:timeline` metadata.
- Produces: no exports; this is a leaf.

- [ ] **Step 1: Replace the hardcoded DATASETS with a built one**

In `apps/firms-preview/index.html`, replace the `const DATASETS = { ... };` block (around lines 151–163) with:

```javascript
// Datasets are discovered, not hardcoded. The collection lists one item per
// published year, each naming its own archive, so a newly published year
// appears here without an edit. An unreachable archive is dropped at load
// rather than registered: a source that never loads stops MapLibre reaching
// "loaded", and every idle-driven readout then freezes at its initial value.
const DATASETS = {
  live: {
    label: "Last 7 days",
    tiles: qs.get("tiles") || `${BASE}/fire-latest.pmtiles`,
    collection: `${BASE}/collection.json`,
  },
};

async function discoverYears() {
  let coll;
  try {
    coll = await (await fetch(`${BASE}/collection.json`)).json();
  } catch (e) {
    console.warn("collection unavailable; year list stays empty", e);
    return;
  }
  const items = (coll.links || []).filter(l => l.rel === "item");
  const loaded = await Promise.all(items.map(async l => {
    try {
      const href = new URL(l.href, `${BASE}/collection.json`).toString();
      const item = await (await fetch(href)).json();
      const tiles = (item.assets || {}).pmtiles;
      if (!tiles) return null;               // year published, not yet tiled
      const p = item.properties || {};
      const n = p["table:row_count"];
      return {
        id: item.id,
        label: item.id,
        tiles: new URL(tiles.href, href).toString(),
        summary: `${(p.start_datetime || "").slice(0, 10)} → ` +
                 `${(p.end_datetime || "").slice(0, 10)}` +
                 (n ? ` · ${n.toLocaleString()} detections` : ""),
      };
    } catch (e) { return null; }
  }));
  for (const y of loaded.filter(Boolean).sort((a, b) => b.id.localeCompare(a.id))) {
    DATASETS[y.id] = y;
  }
}
```

- [ ] **Step 2: Render the selector from whatever was discovered**

Find the code that renders the dataset buttons (it reads `DATASETS` to build the
`DATASET` control). Replace its body with a rebuild function, and call it after
discovery:

```javascript
function renderDatasetControl() {
  const host = document.getElementById("dsbtns");
  if (!host) return;
  host.innerHTML = "";
  for (const [key, d] of Object.entries(DATASETS)) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = d.label;
    b.className = key === S.dataset ? "on" : "";
    b.disabled = S.available[key] === false;
    b.onclick = () => selectDataset(key);
    host.appendChild(b);
  }
}
```

If the existing markup has no `#dsbtns` container, add one where the two
hardcoded buttons currently live, keeping the surrounding classes intact. With
more than about eight years, swap the buttons for a `<select>` — a row of
twenty-seven buttons does not fit the panel:

```javascript
function renderDatasetControl() {
  const host = document.getElementById("dsbtns");
  if (!host) return;
  host.innerHTML = "";
  const keys = Object.keys(DATASETS);
  if (keys.length > 8) {
    const sel = document.createElement("select");
    for (const key of keys) {
      const o = document.createElement("option");
      o.value = key; o.textContent = DATASETS[key].label;
      o.selected = key === S.dataset;
      o.disabled = S.available[key] === false;
      sel.appendChild(o);
    }
    sel.onchange = () => selectDataset(sel.value);
    host.appendChild(sel);
    return;
  }
  for (const key of keys) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = DATASETS[key].label;
    b.className = key === S.dataset ? "on" : "";
    b.disabled = S.available[key] === false;
    b.onclick = () => selectDataset(key);
    host.appendChild(b);
  }
}
```

- [ ] **Step 3: Run discovery before the first mount**

In the startup path, before the first `mountDataset()` call, add:

```javascript
  await discoverYears();
  renderDatasetControl();
```

Make the enclosing function `async` if it is not already.

- [ ] **Step 4: Read the slider's unit from the declared axis**

The app currently labels the time control by inspecting key length. Replace that
inference with the declaration. Where `loadBands` reads metadata, add:

```javascript
    // The archive says what its buckets are. Guessing from key length worked
    // only because two formats happened to differ in width.
    S.timeline = md["firms:timeline"] || null;
    if (typeof S.timeline === "string") {
      try { S.timeline = JSON.parse(S.timeline); } catch { S.timeline = null; }
    }
```

Then in the granularity label added earlier, replace the length-based guess:

```javascript
  const gran = S.timeline
    ? (S.timeline.unit === "month" ? "Months" : "Days")
    : "Time";
  const gl = document.getElementById("daylabel");
  if (gl) gl.textContent = gran;
```

- [ ] **Step 5: Verify in the browser**

```bash
cd ../catalog-staging/preview && python3 serve.py &
```

Open `http://127.0.0.1:8899/index.html`. Check, in order:
1. the dataset control lists **Last 7 days plus every published year**, newest first;
2. selecting a year loads its archive and the header shows that year's date range and detection count;
3. the time control reads **Months** on the all-time/monthly data and **Days** on a year archive and on the live window;
4. the legend says "(data breaks)" and its classes change **only** when the reported a5 level changes — hold at one level across several zooms and confirm the numbers hold still;
5. the console shows no `404`, and the "cells in view" readout is non-zero.

- [ ] **Step 6: Copy to staging and commit**

```bash
cp apps/firms-preview/index.html ../catalog-staging/preview/index.html
git add apps/firms-preview/index.html
git commit -m "Discover datasets from the catalog instead of hardcoding them

The app listed two datasets, one of them a literal 2020 pointing at a local
file. Every published year is now a STAC item naming its own archive, so the
selector is built from the collection and a newly published year needs no
edit. The time control also takes its unit from the archive's declared axis
rather than guessing from the width of a bucket key."
```

---

### Task 8: Build and publish the archives from CI

**Files:**
- Create: `.github/workflows/build-archives.yml`

**Interfaces:**
- Consumes: `tools/build_year.sh` (Task 4), `tools/make_alltime.py` (Task 5).
- Produces: published `alltime.pmtiles` and `fire-<year>.pmtiles`.

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/build-archives.yml`. Write it as text; never round-trip
this file through PyYAML, which rewrites `on:` as `true:` and strips comments.

```yaml
name: build-archives

# Build the tile archives the explorer reads.
#
# A completed year is immutable, so its archive is built once and never again.
# The all-time archive is about 8 MB and is rewritten whole, which is cheaper
# than any incremental scheme would be.
#
# These jobs read the published parquet over HTTP and need no FIRMS key.

on:
  workflow_dispatch:
    inputs:
      years:
        description: "Comma list of years to tile, e.g. 2020,2021. Blank = none."
        required: false
        default: ""
      alltime:
        description: "Also rebuild alltime.pmtiles"
        type: boolean
        default: true

permissions:
  contents: read

jobs:
  year:
    if: inputs.years != ''
    runs-on: ubuntu-latest
    timeout-minutes: 180
    strategy:
      max-parallel: 2
      fail-fast: false
      matrix:
        year: ${{ fromJson(format('[{0}]', inputs.years)) }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: |
          pip install --quiet duckdb boto3 \
            'git+https://github.com/geoparquet/geoparquet-io.git@main'
          curl -sSL https://github.com/protomaps/go-pmtiles/releases/latest/download/go-pmtiles_Linux_x86_64.tar.gz \
            | sudo tar -xz -C /usr/local/bin pmtiles
      - name: Build year ${{ matrix.year }}
        run: bash tools/build_year.sh ${{ matrix.year }}
      - name: Upload
        env:
          AWS_ACCESS_KEY_ID: ${{ secrets.SOURCE_COOP_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.SOURCE_COOP_SECRET_ACCESS_KEY }}
          AWS_SESSION_TOKEN: ${{ secrets.SOURCE_COOP_SESSION_TOKEN }}
        run: python3 tools/upload_data.py --confirm --data-dir ./staging/publish

  alltime:
    if: inputs.alltime
    runs-on: ubuntu-latest
    timeout-minutes: 240
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: |
          pip install --quiet duckdb boto3 \
            'git+https://github.com/geoparquet/geoparquet-io.git@main'
          curl -sSL https://github.com/protomaps/go-pmtiles/releases/latest/download/go-pmtiles_Linux_x86_64.tar.gz \
            | sudo tar -xz -C /usr/local/bin pmtiles
      - name: Build alltime.pmtiles
        run: python3 tools/make_alltime.py --years 2000-2026
      - name: Validate before uploading anything
        run: CI_LIGHT=1 python3 tests/run_all.py
      - name: Upload
        env:
          AWS_ACCESS_KEY_ID: ${{ secrets.SOURCE_COOP_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.SOURCE_COOP_SECRET_ACCESS_KEY }}
          AWS_SESSION_TOKEN: ${{ secrets.SOURCE_COOP_SESSION_TOKEN }}
        run: python3 tools/upload_data.py --confirm --data-dir ./staging/publish
```

- [ ] **Step 2: Check the paths the workflow assumes**

`build_year.sh` and `make_alltime.py` default to `../catalog-staging/...`, which
does not exist on a runner. Set the env in both jobs so they write inside the
workspace, adding above each build step:

```yaml
        env:
          DATA: ./staging/publish/detections
          WORK: ./staging/work
          TILES: ./staging/publish/detections
```

and for the all-time job pass the flags explicitly:

```yaml
        run: |
          python3 tools/make_alltime.py --years 2000-2026 \
            --cache ./staging/work/alltime \
            --tiles ./staging/publish/detections
```

`build_year.sh` reads `DATA`, `WORK`, `TILES` from the environment already.
`make_alltime.py` needs the flags because its defaults are relative paths.

- [ ] **Step 3: Lint the workflow**

Run: `zizmor .github/workflows/build-archives.yml` (the repo already carries
`zizmor.yml`).
Expected: no findings above the configured threshold.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/build-archives.yml
git commit -m "Build the tile archives in CI

A completed year is immutable, so its archive is built once. The all-time
archive is small enough to rewrite whole, which is cheaper than any
incremental scheme."
```

---

### Task 9: Force IPv4 when talking to FIRMS

Both the backfill and the hourly refresh have failed with
`<urlopen error [Errno 101] Network is unreachable>`, sometimes for every window
in a slice — 73 windows, 0 rows. The host is dual-stack and GitHub runners
generally have no IPv6 route.

**Files:**
- Create: `tools/net.py`
- Modify: `tools/firms_fetch.py`, `tools/firms_nrt.py`
- Create: `tests/test_net.py`
- Modify: `tests/run_all.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `net.force_ipv4() -> None`, idempotent.

- [ ] **Step 1: Write the failing test**

Create `tests/test_net.py`:

```python
#!/usr/bin/env python3
"""Outbound HTTP resolves over IPv4 only.

Both the backfill and the hourly refresh have failed with
"[Errno 101] Network is unreachable" against a dual-stack NASA host, at times
for every window in a slice. GitHub runners generally have no IPv6 route, so
resolution must not hand back an AAAA address.

No network: the gate inspects what getaddrinfo returns, not what connects.

Run: python3 tests/test_net.py
"""
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from net import force_ipv4  # noqa: E402

errors: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


fake_calls: list[tuple] = []


def fake_getaddrinfo(host, port, family=0, *args, **kwargs):
    fake_calls.append(family)
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", port))]


socket.getaddrinfo = fake_getaddrinfo
force_ipv4()
socket.getaddrinfo("example.invalid", 443)
check(fake_calls and fake_calls[-1] == socket.AF_INET,
      f"family is pinned to AF_INET, saw {fake_calls}")

# Applying it twice must not stack wrappers or change behaviour.
force_ipv4()
socket.getaddrinfo("example.invalid", 443)
check(fake_calls[-1] == socket.AF_INET, "still AF_INET after a second call")
check(len(fake_calls) == 2, f"no extra resolution happened, saw {len(fake_calls)}")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print("OK: outbound resolution is IPv4-only")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 tests/test_net.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'net'`

- [ ] **Step 3: Write the module**

Create `tools/net.py`:

```python
#!/usr/bin/env python3
"""Pin outbound resolution to IPv4.

firms.modaps.eosdis.nasa.gov is dual-stack and GitHub runners generally have
no IPv6 route, which surfaces as "[Errno 101] Network is unreachable" -- once
for all 73 windows of a slice, fetching zero rows. Rather than widen retries
against a route that cannot work, resolve A records only.

Idempotent: calling it twice does not stack wrappers.
"""
from __future__ import annotations

import socket

_ORIGINAL = None


def force_ipv4() -> None:
    global _ORIGINAL
    if _ORIGINAL is not None:
        return
    _ORIGINAL = socket.getaddrinfo

    def ipv4_only(host, port, family=0, *args, **kwargs):
        return _ORIGINAL(host, port, socket.AF_INET, *args, **kwargs)

    socket.getaddrinfo = ipv4_only
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python3 tests/test_net.py`
Expected: `OK: outbound resolution is IPv4-only`

- [ ] **Step 5: Call it from both fetchers**

In `tools/firms_fetch.py` and `tools/firms_nrt.py`, after the existing imports, add:

```python
from net import force_ipv4

force_ipv4()
```

Both already run with `tools/` on `sys.path` when invoked as `python3 tools/<name>.py`; if either fails to import, add
`sys.path.insert(0, str(Path(__file__).resolve().parent))` before the import,
matching the pattern in `tests/test_publish.py`.

- [ ] **Step 6: Prove real resolution still works**

```bash
python3 -c "
import sys; sys.path.insert(0,'tools')
from net import force_ipv4; force_ipv4()
import socket, urllib.request
print(socket.getaddrinfo('firms.modaps.eosdis.nasa.gov',443)[0][4])
r=urllib.request.urlopen(urllib.request.Request(
  'https://firms.modaps.eosdis.nasa.gov/',
  headers={'User-Agent':'firms-catalog-tools/1.0'}), timeout=30)
print('HTTP', r.status)
"
```

Expected: an IPv4 tuple such as `('198.118.194.34', 443)` and `HTTP 200`.

- [ ] **Step 7: Register the test, run everything, commit**

Add `"test_net.py",` to `TESTS` in `tests/run_all.py`.

Run: `pyflakes tools/*.py tests/*.py && CI_LIGHT=1 python3 tests/run_all.py`
Expected: no pyflakes output; every gate `OK`.

```bash
git add tools/net.py tools/firms_fetch.py tools/firms_nrt.py tests/test_net.py tests/run_all.py
git commit -m "Resolve FIRMS over IPv4 only

Both the backfill and the hourly refresh failed with [Errno 101] Network is
unreachable against a dual-stack NASA host -- one slice burned all 73 windows
and fetched nothing. GitHub runners generally have no IPv6 route, so widening
retries cannot help; the address family is the problem."
```

---

## Self-Review

**Spec coverage.** Every section of the spec maps to a task: the three-archive
architecture (3, 5), the `fire-latest` rename (2), raw points per year (3),
`firms:timeline` (1), class breaks and styles per year (4), the update cadence
(8), the IPv4 finding (9), the upload-retry finding (already committed as
`ed87da9`), and the existing app staying and being updated (7). The credential
finding is a decision for the operator, not code, and is recorded in the spec.

**Deferred to Plan 2**, all belonging to the new explorer: timeline rendering
and interaction, domain-driven archive swapping, and the per-tile histogram
cache. Plan 1 leaves the catalog complete and the preview app working.

**Type consistency.** `declare()` and `bucket_keys()` are used with those exact
names in `make_timeline.py` and `tests/test_timeline_meta.py`. `force_ipv4()`
matches across `net.py`, both fetchers and `test_net.py`. `build_year.sh` passes
`--band <level>:<parquet>` and `--out/--tiles/--suffix`, which match the current
signatures of `make_breaks.py` and `make_styles.py`. Bucket keys are 8-digit
`count_YYYYMMDD` in year archives and 6-digit `count_YYYYMM` in the all-time
archive, consistent with the Global Constraints and with `WIDTHS`.

**Known risk, flagged deliberately.** Task 5 Step 2 says to stop if the
all-time archive lands far above ~8 MB. The whole architecture rests on that
measurement, and an executor should escalate rather than continue building on a
number that has moved.
