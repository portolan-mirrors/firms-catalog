# AGENTS.md — Active Fire Detections collection

Guidance for AI agents and automated clients working with this catalog.

**One rule survives every edit to this file.** Every claim here is either quoted
from a source or measured from the data. If you cannot point at where a fact
came from, it does not belong in this file. An agent acting on an invented join
key or an invented column name produces a confident wrong answer, and nothing
downstream catches it.

Each factual claim below is tagged with how it was established:
**[attested]** the publisher states it, with a link;
**[researched]** found outside the source's own metadata, with a link;
**[derived]** computed from the published data, with the query that computed it.

## What this collection holds

Global satellite active fire and thermal
anomaly detections. Public root:
`https://data.source.coop/portolan-mirrors/firms-catalog/catalog.json`

This is a **mirror**. NASA LANCE FIRMS is the producer and the authoritative
source. See [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/).

Sensors carried **[attested,
[data_availability API](https://firms.modaps.eosdis.nasa.gov/api/data_availability/)]**:

| sensor | platform(s) | resolution | from |
|---|---|---|---|
| `MODIS` | Terra, Aqua | 1 km | 2000-11-01 |
| `VIIRS_SNPP` | Suomi-NPP | 375 m | 2012-01-20 |
| `VIIRS_NOAA20` | NOAA-20 | 375 m | 2018-04-01 |
| `VIIRS_NOAA21` | NOAA-21 | 375 m | 2024-01-17 |

GOES and Landsat are published by FIRMS but are **not** carried here. GOES is
geostationary at a 10-minute cadence and would dominate the archive by volume.
Landsat NRT covers only the US and Canada.

## Layout and how to exploit it

```
detections/year=<YYYY>/detections.parquet
```

One GeoParquet 2.0 file per year, hive-partitioned on `year` only. Rows inside
each file are ordered by `(_month, _hilbert)`. That ordering is deliberate:

- `year=` in the path prunes whole files without reading them.
- Ordering by `_month` first makes Parquet row-group statistics on
  `acq_datetime` prune to a single month **[derived]**. Measured on the 2020
  file: row group 0 spans 2020-01-01..2020-01-31, row group 40 spans
  2020-03-01..2020-03-31.
- Ordering by `_hilbert` inside each month keeps each row group spatially tight,
  so a bounding-box filter prunes as well **[derived]**: row group 40 covers
  0.5% of the globe's Hilbert range.

A pure Hilbert sort would scatter every month across the whole file and remove
month pruning entirely. That trade-off is why both keys exist.

Filter on `year` explicitly when you can. A predicate on `acq_date` alone does
not prune the partition, because the reader cannot map a date onto the path key.

## Columns

`sensor`, `quality` and `type` are columns rather than path keys. Each is
constant or near-constant within a file, so row-group statistics prune them
without deepening the directory tree.

| column | type | meaning | source |
|---|---|---|---|
| `geometry` | Point, CRS84 | detection centre, **not** the actual fire location | [attested](https://www.earthdata.nasa.gov/data/tools/firms/active-fire-data-attributes-modis-viirs) |
| `acq_datetime` | TIMESTAMP (UTC) | acquisition time, built from `acq_date` + `acq_time` | derived |
| `acq_date` | DATE | acquisition date | attested |
| `sensor` | VARCHAR | `MODIS`, `VIIRS_SNPP`, `VIIRS_NOAA20`, `VIIRS_NOAA21` | derived from the API source name |
| `satellite` | VARCHAR | `Terra`, `Aqua`, `Suomi-NPP`, `NOAA-20`, `NOAA-21` | derived; see the mapping below |
| `instrument` | VARCHAR | `MODIS` or `VIIRS`, as published | attested |
| `quality` | VARCHAR | `sp` science-quality, `nrt` near-real-time | derived from the API source name |
| `version` | VARCHAR | collection and processing designation, as published | attested |
| `brightness` | DOUBLE | MODIS channel 21/22 brightness temperature, K. **NULL for VIIRS** | attested |
| `bright_t31` | DOUBLE | MODIS channel 31 brightness temperature, K. **NULL for VIIRS** | attested |
| `bright_ti4` | DOUBLE | VIIRS I-4 brightness temperature, K. **NULL for MODIS** | attested |
| `bright_ti5` | DOUBLE | VIIRS I-5 brightness temperature, K. **NULL for MODIS** | attested |
| `scan`, `track` | DOUBLE | actual pixel size along scan and track | attested |
| `frp` | DOUBLE | fire radiative power, MW | attested |
| `daynight` | VARCHAR | `D` day, `N` night | attested |
| `confidence` | VARCHAR | **as published, and it means different things per instrument** | attested |
| `confidence_pct` | INTEGER | numeric confidence 0-100. **MODIS only, NULL for VIIRS** | derived |
| `type` | INTEGER | 0 vegetation fire, 1 active volcano, 2 other static land source, 3 offshore. **NULL for all NRT rows** | attested |
| `_month` | UTINYINT | 1-12, the sort key. Cheap month filter | derived |
| `_hilbert` | UINTEGER | Hilbert index over the global extent, the spatial sort key | derived |

`latitude` and `longitude` are **not** carried. `geometry` holds the same
information. Recover them with `ST_X(geometry)` and `ST_Y(geometry)`.

### Satellite code mapping

FIRMS publishes short codes. This catalog expands them **[attested for the code
meanings](https://www.earthdata.nasa.gov/data/tools/firms/active-fire-data-attributes-modis-viirs),
derived for the expansion]**:

| published | stored |
|---|---|
| `T` / `Terra` | `Terra` |
| `A` / `Aqua` | `Aqua` |
| `N` | `Suomi-NPP` |
| `N20` / `1` | `NOAA-20` |
| `N21` / `2` | `NOAA-21` |

The mapping lives in `SAT_SQL` in
[`tools/firms_fetch.py`](https://github.com/portolan-mirrors/firms-catalog/blob/main/tools/firms_fetch.py).

## Transforms applied to the upstream data

Nothing is filtered, interpolated, or reclassified. Every row FIRMS publishes is
carried. These are the complete changes made between the upstream response and
the published file, in the order they happen. The code is
[`tools/firms_fetch.py`](https://github.com/portolan-mirrors/firms-catalog/blob/main/tools/firms_fetch.py)
and [`tools/firms_build.py`](https://github.com/portolan-mirrors/firms-catalog/blob/main/tools/firms_build.py).

| # | Transform | Why, and what it is based on |
|---|---|---|
| 1 | `acq_date` + `acq_time` composed into `acq_datetime` (UTC) | FIRMS publishes the date and an integer HHMM separately. Both original columns are still carried; `acq_time` is dropped because `acq_datetime` holds it exactly. |
| 2 | Satellite short codes expanded to platform names | `T`→`Terra`, `A`→`Aqua`, `N`→`Suomi-NPP`, `N20`→`NOAA-20`, `N21`→`NOAA-21`. The codes are [documented upstream](https://www.earthdata.nasa.gov/data/tools/firms/active-fire-data-attributes-modis-viirs); only the expansion is ours. |
| 3 | `instrument` supplied where the feed omits it | The bulk near-real-time feeds ship 13 columns and no `instrument`; the area API ships 14 and has it. This is **not a guess**: each bulk feed is one instrument on one platform, named in the product (`SUOMI_VIIRS_C2`, `J1_VIIRS_C2`, `J2_VIIRS_C2`, `MODIS_C6_1`), and the `satellite` column in the same file confirms it. |
| 4 | VIIRS `confidence` normalised to the documented letters | The area API returns `l`/`n`/`h`. The bulk feeds return `low`/`nominal`/`high` for the same thing. Storing both would put two encodings in one column, so the documented letters win. MODIS integers are untouched. |
| 5 | `confidence_pct` added | An integer copy of `confidence` for MODIS rows, so a numeric filter does not need a cast. NULL for VIIRS. **No MODIS-to-VIIRS crosswalk is published**, because none is documented upstream. |
| 6 | `latitude` and `longitude` dropped | `geometry` carries the same values. Recover them with `ST_X(geometry)` and `ST_Y(geometry)`. |
| 7 | Rows physically ordered along a Hilbert curve | Keeps row-group bounds spatially tight so a bounding-box filter prunes without reading data. **No sort-key column is published** — `gpio sort hilbert` computes the curve internally. |
| 8 | Written as GeoParquet 2.0, zstd level 15, 100k-row row groups | Native Parquet `GEOMETRY` logical type, CRS84. |

Near-real-time and science-quality rows are never mixed within a sensor and date:
FIRMS publishes non-overlapping date ranges for its `_NRT` and `_SP` sources, so
the two cannot double-count. The `quality` column records which a row came from.

### Derived aggregates

The A5 hexagon aggregates published alongside the detections are built by
[`tools/firms_aggregate.py`](https://github.com/portolan-mirrors/firms-catalog/blob/main/tools/firms_aggregate.py).
`gpio` pivots exactly one categorical column per run, so day, sensor and
day/night are aggregated separately and joined on `a5_cell`. That join is safe
because every run uses the same resolution and therefore the same cells.
**The cells hold no cross-tabs**: there is a `count_modis` and a
`count_20260903`, but no `count_modis_20260903`. A query that needs one sensor
on one day must go to the detections, not the cells.


## Quirks that produce silently wrong answers

**`confidence` is not comparable across instruments.** MODIS publishes an
integer 0-100. VIIRS publishes `l`, `n`, or `h` **[attested]**. Both live in one
VARCHAR column. VIIRS values are normalised to the documented letters, because
the bulk feeds spell them out as `low`/`nominal`/`high`; see transform 4. `confidence_pct` holds the numeric value for
MODIS rows only. This catalog deliberately publishes **no crosswalk** between
the two, because no authoritative threshold table was found. Do not invent one.
`WHERE confidence > 80` silently excludes every VIIRS row.

**`type` is NULL for every NRT row.** The FIRMS FAQ states NRT data "does not
attribute the static sources/inferred hotspot 'type'" **[attested,
[FAQ](https://www.earthdata.nasa.gov/data/tools/firms/faq)]**, and the NRT CSV
has 14 columns against the science-quality 15 **[derived]**. `WHERE type = 0`
therefore drops all recent data. Write `WHERE type = 0 OR type IS NULL`.

**NRT and science-quality rows coexist and differ in accuracy.** Filter on
`quality`. NASA states NRT MODIS/Aqua locations can be off by "several
kilometers" after spacecraft manoeuvres **[attested, FAQ]**.

**A detection is not a fire.** It is a pixel flagged as a thermal anomaly. Gas
flares, volcanoes, and industrial heat sources are included, distinguished by
`type` in science-quality data only.

**Coordinates are pixel centres, not fire locations** **[attested]**. MODIS
pixels are 1 km; do not treat a point as a precise ignition site.

## Worked queries

Every query below was run before it was written down. They were verified
against a locally built year file with the identical layout while the catalog
was being prepared; the `s3://` paths resolve once the data is published.

Count everything, one glob across the whole record:

```sql
INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;
SELECT count(*) FROM read_parquet(
  's3://us-west-2.opendata.source.coop/portolan-mirrors/firms-catalog/detections/year=*/detections.parquet',
  hive_partitioning = true);
```

One year, one month, prunes on both the path key and row-group statistics:

```sql
SELECT sensor, count(*) n, round(avg(frp), 1) mean_frp
FROM read_parquet('.../detections/year=*/detections.parquet', hive_partitioning = true)
WHERE year = 2020 AND _month = 8
GROUP BY sensor ORDER BY n DESC;
```

High-power night-time detections in a bounding box. This one returns the
January 2020 Australian fires:

```sql
SELECT acq_datetime, satellite, frp, ST_X(geometry) lon, ST_Y(geometry) lat
FROM read_parquet('.../detections/year=*/detections.parquet', hive_partitioning = true)
WHERE year = 2020 AND daynight = 'N' AND frp > 100
  AND ST_Within(geometry, ST_MakeEnvelope(140, -40, 155, -25))
ORDER BY frp DESC LIMIT 5;
```

```
2020-01-04 15:21:00  Suomi-NPP  1761.2   149.293  -37.155
2020-01-31 15:14:00  Suomi-NPP  1304.21  148.958  -35.775
2020-01-04 15:21:00  Suomi-NPP   535.65  149.291  -37.164
```

## Join keys

The catalog holds one collection, so nothing joins to anything else here. There
is no stable per-detection identifier: FIRMS does not publish one, and a
detection is uniquely identified only by the tuple
(`sensor`, `acq_datetime`, `geometry`).

## Structure

Assets and structural links resolve relative to the object that carries them.
