# Adaptive timeline for the FIRMS explorer

Status: design, approved in outline 2026-09-10. Written by AI; needs human review.

## Scope: a second app, not a replacement

This describes a **new explorer**, built to work across the whole record. The
existing preview app at `apps/firms-preview/` stays, keeps being maintained,
and is updated to consume the renamed archives described below. It remains the
small, fast thing that answers "what is burning now"; the new app is the one
that answers "how has this place burned over twenty-seven years".

Keeping both is deliberate. The preview doubles as the catalog's `preview`
link and its thumbnail source, it is the page a Portolan browser lands on, and
it exercises the plain published styles. Folding it into a heavier application
would lose all of that. The two share the archives and the metadata contracts
below, and nothing else.

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

**Built, the numbers held.** All 27 published years joined to 22,208 cells
across 317 columns for 6.6 MB of Parquet -- the cell union landing within a few
percent of the ~20k the convergence curve predicted from seven samples, and
growing by only 380 cells when the last two years were added.

The tiled archive is 22.6 MB, not the ~8 MB implied above: that figure was the
Parquet projection, and PMTiles costs about 3.4x more because cells repeat at
every zoom of a band and each tile carries its own key dictionary. Still small
enough that the conclusion is unchanged, but the projection to quote for a
tiled archive is the Parquet size times roughly three, not the Parquet size.

One number to watch: the z0 tile is 1.25 MB, which is the whole world at r4
carrying every month.

The fix is NOT the band split used on the year archives. That split gave the
coarse band monthly columns and the fine band daily ones, taking a z0 tile
from 4,175 KB to 858 KB with the same 15,623 cells -- a 4.9x win bought purely
by carrying 12 columns per cell instead of 366. But it buys that by chaining
temporal resolution to spatial zoom, which is exactly what this design removed:
applied here, zooming the map out past z5 would silently collapse a monthly
timeline to yearly.

The right lever is fewer cells, not fewer time columns. An extra coarse band
covering only z0 and z1 -- r3 preferred, r2 acceptable -- cuts the cell count
by roughly an order of magnitude while every cell keeps all its monthly
columns, so the timeline stays monthly at every map zoom.

Deferred until the map exists, and it may never be needed. PMTiles is
range-requested, so a tile that is never requested costs nothing: if the app
opens at z2 or closer, the z0 tile is never fetched and its size is moot. Fix
the default zoom before fixing the tile.

## Architecture

Three archives, each owning one temporal band and exactly one bucket
granularity. One is mounted at a time.

| archive | cells | buckets | raw points | est. size | serves |
|---|---|---|---|---|---|
| `alltime.pmtiles` | r4 + r6 | 311 monthly, `count_YYYYMM` | no | ~8 MB | all time down to a calendar year |
| `fire-<year>.pmtiles` | r6 + r8 + points | 366 daily, `count_MMDD` | yes, z10+ | ~615 MB | one calendar year |
| `fire-latest.pmtiles` | r6 + r10 + points | 7-8 daily, `count_YYYYMMDD` | yes, z10+ | ~107 MB | last seven days |

### Renaming the rolling window

`fire.pmtiles` becomes `fire-latest.pmtiles`. With per-year archives it is the
only name that does not say which period it covers. The rename touches the
collection's `pmtiles` asset, the `rel:pmtiles` link, the `pmtiles://` URL
inside both generated styles, and the preview app, so it is a re-upload and a
regeneration rather than a copy.

One granularity per archive is a deliberate simplification. The existing
`fire-2020-split.pmtiles` carries monthly on its coarse band and daily on its
fine band, which ties temporal resolution to *spatial* zoom — you cannot see
daily bars while looking at a continent. Since r6-daily costs 2.9 MB, every
band in a year archive carries daily and the coupling disappears.

### Raw points in every year

Today only the rolling window carries raw detections, in a `features` band at
z10; every year archive is aggregate-only, so zooming into 2003 just yields
bigger cells with nothing underneath. Year archives gain a `features` band so
the explorer behaves the same at any depth in time.

Measured from the rolling window — 1,915,961 detections producing 42.0 MB of
point tiles, or 21.9 MB per million points:

    2003   5.2M points   ~114 MB
    2020  25.2M points   ~552 MB
    all 27 years         ~11 GB

A 552 MB archive is not a 552 MB download. PMTiles is range-requested, so a
z10 view fetches a few hundred kilobytes whatever the file size. This is a
storage and build-time cost, not a runtime one.

### Selecting the archive

The timeline's **visible domain** selects the archive, not its selection. The
rule is calendar-year containment, which is predictable and keeps exactly one
source mounted:

- domain contained in the last seven days -> `fire-latest.pmtiles`
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

Height and colour must not read the same number, though. A pixel column at a
wide span aggregates dozens of buckets, and its *total* lands in the top
quantile class every time, which would paint the whole axis red and say
nothing. Height is therefore the column total, so area is conserved and a busy
month looks busy; colour is the per-bucket mean, which is stable as the span
changes. Zooming then alters the shape of the chart without recolouring it.

**Selection versus domain.** "The selection is clamped to the domain" applies
to moving and resizing it — you cannot drag a handle to a pixel that is not
there. It must NOT be re-applied when the domain itself changes. Re-clamping on
zoom would make the selection always fill the view, which destroys both
drag-to-pan and the ability to zoom out for context around a fixed window. A
selection that falls off-screen stays where it is and the map keeps filtering
by it; the track marks which edge it went off.

**Bucket count versus calendar span.** `firms:timeline.buckets` is the number
of bucket columns actually present, which for a sparse archive is fewer than
the number of periods between `min` and `max`. The axis is drawn from the
calendar span so time reads linearly, with absent buckets simply empty. A
viewer that sizes its axis by `buckets` will compress time wherever data is
missing, so it must not.

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

## Updating

Rewrite cost is very uneven, and the measurements make the cheap path obvious.

| archive | rewritten when | frequency | bytes rewritten |
|---|---|---|---|
| `alltime.pmtiles` | the current month's column changes | daily | ~8 MB |
| `fire-latest.pmtiles` | NRT refresh | hourly | ~107 MB |
| `fire-<current year>` | aggregates as data firms up | daily | ~65 MB |
| `fire-<current year>` | points | weekly | ~615 MB |
| `fire-<past year>` | never; immutable once complete | - | 0 |

Because the all-time archive is only about 8 MB, it is rewritten whole every
day and no incremental update machinery is needed for it. Past years are
write-once: a completed year changes only if the upstream record is reprocessed
or a sensor was missing when it was published. Steady state is therefore one
small daily rewrite plus the hourly rolling window.

The current year is the awkward case, because rebuilding it with points is
~615 MB of upload. Its aggregates rebuild daily and its points weekly, which is
acceptable because the last seven days of points are already served by
`fire-latest.pmtiles`; the gap is only points between seven days and one week
of staleness.

### Credentials

The hourly refresh cannot work with Source Cooperative's temporary STS
credentials: they last about 54 minutes and the job runs every hour, so it only
ever succeeds in the window following a manual login. Every scheduled run
between logins fails. This needs permanent access keys (being obtained) or
OIDC federation. Nothing else in this design is blocked on it, but "always the
latest" is.

### Reaching FIRMS from CI

Both the backfill and the hourly refresh have failed with
`<urlopen error [Errno 101] Network is unreachable>` against
`firms.modaps.eosdis.nasa.gov`, sometimes for every window in a slice. The host
is dual-stack (`198.118.194.34` and `2001:4d0:241a:40c0::34`) and GitHub
runners generally have no IPv6 route, which fits. It is intermittent rather
than constant, so retries help but do not cure it. The fetchers should force
`AF_INET` rather than depend on fallback.

### Uploading

Publishing 2022 and 2023 failed after their data had been rebuilt, with HTTP
524 on `UploadPart` — the CDN reporting an origin timeout. botocore's standard
retry mode does not treat 524 as retryable, and the client had no retry
configuration. Uploads now retry on gateway-class statuses and use 64 MB parts,
which takes a 460 MB file from 58 parts to 8.

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
year archive rather than monthly on the coarse band, to include a `features`
band via `--include-features`, and to run the breaks and styles steps per
year.

The rename to `fire-latest.pmtiles` is a single pass over the collection
asset, the `rel:pmtiles` link, both generated styles and the preview app,
followed by a re-upload and a delete of the old key.

The existing preview app is updated in the same pass: new archive names, and
the year switcher fed from the per-year STAC items rather than a hardcoded
list. It gains nothing else -- the timeline is the new app's.

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

Adding a timeline to the existing preview app is also out of scope. It keeps
its simple day filter; the two apps share archives, not interface.
