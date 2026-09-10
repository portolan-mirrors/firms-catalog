# Adaptive timeline for the FIRMS explorer

Status: design, approved in outline 2026-09-10. Written by AI; needs human review.

## The problem

The current preview filters by a "days" slider whose meaning changes silently
with the dataset: eight daily buckets on the rolling window, twelve monthly
buckets on a year, both labelled "days". It cannot express a span wider than
the loaded archive, and there is no archive covering more than one year, so
the twenty-seven year record has no view at all.

What we want instead is one timeline across the bottom of a new explorer that
covers 2000 to now, zooms continuously in time, lets the selection be dragged
and resized, and whose bars mean something — count or intensity of what is
currently on the map.

## What the data forces

A viewport-filtered histogram can only resolve to the time buckets carried by
the archive currently loaded. The counts live inside the tiles as `count_*`
columns on aggregate cells; there is no separate time index. So temporal
resolution is a property of the archive, and changing resolution means
changing archive.

That framing led to the central question: what does an all-time archive cost?
Three measurements answered it.

**Coarse cells with fine time columns are cheap.** Rolling 2020's daily
aggregate up from r8:

    r6   15,626 cells x 366 day columns    2.9 MB   (3.1s)
    r4    1,620 cells x 366 day columns    0.8 MB

**The cell union converges.** Distinct r6 cells across seven spread years:

    year    cells      new    union
    2003   13,558   13,558   13,558
    2008   13,254    1,039   14,597
    2012   15,459    1,657   16,254
    2015   15,411      750   17,004
    2017   15,440      530   17,534
    2020   15,479      467   18,001
    2021   15,361      375   18,376

New cells decay toward zero: fire recurs in the same places. Twenty-seven
years should land near twenty thousand r6 cells, not a multiple of that.

**So an all-time monthly archive is small.** Joining those seven years into
one table, one column per (year, month):

    years  cols     size   marginal/yr
        1    12   0.66 MB      0.659
        3    36   1.12 MB      0.229
        5    60   1.64 MB      0.261
        7    84   2.16 MB      0.261     <- flat

    per-column projection -> 8.3 MB      linear fit -> 7.2 MB

About 7-8 MB for all twenty-seven years at monthly resolution. The matrix is
roughly 50% dense, so this is not sparsity doing the work: it is zstd-22 on
integer counts over a converged cell set.

An earlier guess of ~79 MB was wrong, having scaled naively from per-year file
size. The measurement removed the reason for the fallback design.

## Architecture

Three archives, each owning one temporal band and exactly one bucket
granularity. One is mounted at a time.

| archive | cells | buckets | est. size | serves |
|---|---|---|---|---|
| `alltime.pmtiles` | r4 + r6 | 311 monthly (one per month since 2000-11), `count_YYYYMM` | ~8 MB | all time down to a calendar year |
| `fire-<year>.pmtiles` | r6 + r8 | 366 daily, `count_MMDD` | ~65 MB | one calendar year |
| `fire.pmtiles` | r6 + r10 + points | 7-8 daily, `count_YYYYMMDD` | ~107 MB | last seven days, raw detections |

One granularity per archive is a deliberate simplification. The existing
`fire-2020-split.pmtiles` carries monthly on its coarse band and daily on its
fine band, which ties temporal resolution to *spatial* zoom — you cannot see
daily bars while looking at a continent. Since r6-daily costs 2.9 MB, every
band in a year archive carries daily and the coupling disappears.

### Selecting the archive

The timeline's **visible domain** selects the archive, not its selection. The
rule is calendar-year containment, which is predictable and keeps exactly one
source mounted:

- domain contained in the last seven days -> `fire.pmtiles`
- else domain contained in one calendar year -> `fire-<year>.pmtiles`
- else -> `alltime.pmtiles`

The selection then filters within whatever is mounted.

**Accepted limitation.** A sub-year window straddling a year boundary — say
November to February — falls to `alltime` and therefore to monthly bars, even
though it is only four months wide. Serving it daily would mean mounting two
year archives at once, and a cell present in both would draw twice, stacking
opacity and misreporting colour. The histogram could sum two sources safely,
but the map could not, so the map would disagree with the timeline. Rather
than ship that inconsistency, the seam is visible: the axis is labelled with
its unit and the subdivision animates when it changes.

### Declaring the axis in metadata

The app must not infer the time axis by pattern-matching column names, and must
not read bucket keys off a sampled feature. Both have already produced bugs
here: MVT omits zero-valued attributes, so reading keys from `feats[0]`
yielded three months of a twelve-month year. Each archive declares its axis:

```json
"firms:timeline": {
  "unit": "month",
  "key_format": "count_YYYYMM",
  "min": "200011",
  "max": "202609",
  "buckets": 311
}
```

The viewer reads `unit` and the range to draw the axis and to decide swaps. A
build-time check asserts the declaration matches the columns actually present.

### Class breaks

Breaks stay keyed by a5 level, as `firms:breaks` already is, with each level's
zoom range copied from `gpio:pyramid`. Viewers must floor the zoom before
matching a band, since band edges are integers and MapLibre requests
`floor(zoom)`; not doing so silently drops the data breaks at fractional
zooms. `fire-2020-split.pmtiles` currently has neither `gpio:pyramid` nor
`firms:breaks`, so promoting per-year archives to a first-class tier means
folding `make_breaks.py` and `make_styles.py` into the per-year build.

## The timeline component

### Rendering

Canvas 2D, not DOM. Bars quantize to **pixel columns**, not to buckets: for a
track `W` pixels wide and `N` buckets, when `N > W` each pixel column
aggregates the buckets falling in it, and when `N < W` each bucket spans
`floor(W/N)` pixels. Draw cost is therefore proportional to `W` and constant
across zoom, which is how Perfetto stays fluid on far larger traces. A
311-bucket and a 9,750-bucket axis cost the same to paint.

Bar height encodes the active metric — count by default, switchable to summed
or mean FRP — using the same shipped quantile breaks the map uses, so the
timeline and the map agree about what "hot" means.

### Interaction

- wheel and ctrl+wheel zoom the domain about the cursor; two-finger pinch does
  the same on touch
- drag on the track background pans the domain
- drag the selection body to move it; drag either handle to resize
- explicit zoom in/out and reset buttons, plus preset spans (7d, 30d, 1y, all)
- left/right arrows step the selection, shift+arrows extend it

Buttons matter alongside gestures: coarse jumps across twenty-seven years are
tedious by wheel.

### Computing the histogram

Sum `count_*` over aggregate features intersecting the viewport, via
`querySourceFeatures` on the mounted source. This is the same query the "cells
in view" readout already performs, so the histogram is viewport-accurate by
construction with no second data path.

The naive form is too slow: 18k features times 311 columns is millions of
property reads. Two things fix it.

1. **Cache per tile.** Sum each tile's features once on load, keyed by tile id,
   and combine only the tiles intersecting the viewport. Panning then costs a
   handful of vector adds instead of a rescan.
2. **Recompute on settle, not on move.** Bind to `idle` and `moveend`, never to
   `move`. Note that a source which fails to load keeps the map from ever
   reaching `loaded`, so `idle` never fires and every idle-driven readout
   freezes at its initial value — this exact failure has already happened here
   with a 404 source. Any source that cannot be read must be dropped from the
   style rather than left broken.

Budget: a full recompute at global zoom on `alltime` should stay under 150 ms.

## Build pipeline

New `tools/make_alltime.py`, following the measured recipe:

1. per year, read only `geometry` and month from the published parquet
   (~36 s/yr) and aggregate to a5 r6 with a month breakdown (~3 s/yr)
2. join all years on `a5_cell` into one table of `count_YYYYMM` columns
3. `gpio process overview --levels 4`
4. `gpio pmtiles pyramid`
5. `make_breaks.py` then `make_styles.py`

Each year is independent, so the archive can be built from the years published
today and extended as the rest land; step 2 is a re-join, not a re-read.

`tools/firms_aggregate.py` changes to emit daily buckets on every band of a
year archive rather than monthly on the coarse band, and the per-year build
gains the breaks and styles steps.

Note for any tool talking to Source Cooperative: its CDN answers 403 to the
default Python user-agent, and a rejected client is indistinguishable from a
missing file at the call site. Requests must name themselves.

## Testing

- unit: bucket-key parsing; domain-to-archive selection including the
  year-boundary case; pixel quantization at `N > W`, `N < W`, `N == W`
- contract: every archive declares `firms:timeline`, and its `buckets` and
  `key_format` match the columns actually present
- regression, for bugs already hit: band matching must floor the zoom; bucket
  keys must come from metadata and never from a sampled feature; a source that
  fails to load must not strand the map short of `idle`
- performance: histogram recompute under 150 ms at global zoom on `alltime`

## Out of scope

Sub-daily buckets; animated playback; comparing two periods side by side;
per-sensor or day/night breakdowns on the timeline (the map keeps its own
filters). Cross-year daily resolution is out of scope for the reason given
above, and should be revisited only if the map can render two year archives
without double-drawing shared cells.
