# Active Fire Detections (MODIS and VIIRS)

**631,469,093 detections. November 2000 to today. Global. Updated hourly.**

Every active fire and thermal anomaly NASA FIRMS has published, from five
satellites, as one year-partitioned GeoParquet 2.0 table you can query in
place without downloading anything.

- **See it** — [FIRMS explorer](https://portolan-mirrors.github.io/firms-catalog/),
  a map of the whole record with a scrubable timeline
- **Browse the metadata** — [in the Portolan browser](https://browser.portolan-sdi.org/#/external/data.source.coop/portolan-mirrors/firms-catalog/detections/collection.json)
- **Get the files** — [on Source Cooperative](https://source.coop/portolan-mirrors/firms-catalog)
- **Read the build** — [the repository](https://github.com/portolan-mirrors/firms-catalog)
- **Querying this?** [AGENTS.md](AGENTS.md) has the column semantics, the
  recipes, and the traps in more depth. Agents should start there.

Carries **MODIS** (Terra and Aqua, 1 km, from November 2000) and **VIIRS**
(Suomi-NPP from January 2012, NOAA-20 from April 2018, NOAA-21 from January
2024). Near-real-time rows are appended hourly; NASA replaces them with
science-quality rows roughly three to four months later, and the `quality`
column records which kind a row is.

## What a detection actually is

This is the thing most likely to lead you astray, so it comes first.

A row is **one pixel, in one satellite overpass, that the algorithm flagged as
containing something anomalously hot**. It is not a fire, not a fire's
location, and not a fire's size.

- **The coordinate is a pixel centre, not a fire.** The fire is somewhere
  inside a footprint of `scan` × `track` — about 1 km² for MODIS at nadir,
  about 0.14 km² for VIIRS, and several times larger at the edge of the swath.
  A flaming front only tens of metres across can light up a whole MODIS pixel.
- **A row is an observation, not an event.** One fire burning for a week across
  four satellites produces dozens of rows. Counting rows counts
  *pixel-observations*, and the number depends as much on how many satellites
  were overhead as on how much was burning.
- **"Thermal anomaly" includes things that are not wildfires.** Gas flares,
  volcanoes and industrial heat sources are detected and flagged in `type` —
  but only in science-quality rows (see below).
- **Absence is not absence of fire.** Clouds hide fires completely, heavy smoke
  attenuates the signal, and fires that are small, cool, or short-lived enough
  to fall between overpasses are never seen at all.

## Reading the three columns that matter

**`frp` — fire radiative power, in megawatts.** The rate at which the pixel
radiates energy, which scales with how much biomass is burning per unit time.
This is the closest thing in the table to *intensity*, and it is what to sum
when you want a measure of fire activity rather than a count of flagged
pixels. Integrating FRP over time approximates total radiated energy, and
hence fuel consumed — the basis on which satellite fire products feed
emissions models. Caveats: it is suppressed by cloud and heavy smoke, and it
is not comparable pixel-for-pixel between sensors of different resolution.
Derivation is in the
[MODIS fire user guide](https://modis-fire.umd.edu/files/MODIS_C6_C6.1_Fire_User_Guide_1.0.pdf).

**`confidence` — how sure the algorithm is, on two incompatible scales.**
MODIS publishes an integer 0–100. VIIRS publishes `l` (low), `n` (nominal) or
`h` (high). **There is no crosswalk**, and none is published upstream, so this
catalog does not invent one. `confidence_pct` carries the numeric value for
MODIS rows and is NULL for VIIRS — never coalesce the two into one scale.
Confidence describes the algorithm's certainty that the pixel is a fire; it
says nothing about how large or how consequential the fire is, and it is meant
for filtering rather than weighting. A common starting point is to drop VIIRS
`l` and MODIS below 30, then check how much that changed your answer. Defined
in the [FIRMS FAQ](https://www.earthdata.nasa.gov/data/tools/firms/faq).

**`type` — what kind of heat source, and NULL more often than you expect.**
`0` vegetation fire, `1` active volcano, `2` other static land source (gas
flares, industrial heat), `3` offshore. It is NULL for **every near-real-time
row**, because FIRMS does not attribute type in near-real-time. So
`WHERE type = 0` silently drops the most recent three to four months. Write
`WHERE type = 0 OR type IS NULL` when you want recent data too, and understand
that you are then including flares and volcanoes.

## The counting trap

**Raw detection counts are not comparable across years.** The fleet grew, and
every new satellite adds overpasses and therefore rows. From this catalog's own
per-year counts:

| transition | satellites | detections | change |
|---|---|---|---|
| 2011 → 2012 | 1 → 2 (VIIRS Suomi-NPP joins) | 4,722,658 → 25,951,560 | **5.50×** |
| 2017 → 2018 | 2 → 3 (NOAA-20 joins) | 24,565,794 → 37,980,401 | **1.55×** |
| 2023 → 2024 | 3 → 4 (NOAA-21 joins) | 47,904,858 → 69,183,241 | **1.44×** |

None of those jumps is fire. A trend line drawn through raw counts from 2000 to
today measures the satellite programme, not the planet. To compare across time,
**hold the sensor fixed** — `WHERE sensor = 'MODIS'` gives a consistent
26-year series — or work in FRP with the sensor mix accounted for. The
`firms:sensors` property on each year's item records which satellites were
flying, so you can check before you aggregate.

## Known biases and limitations

- **Overpass timing, not continuous watch.** Each satellite sees a given place
  roughly twice a day. Fires that start and burn out between overpasses are
  invisible. The day/night mix varies with latitude and season, and `daynight`
  lets you split on it.
- **Cloud cover.** Systematically removes detections, which biases wet seasons,
  tropical regions and frontal-weather fire regimes downward. A quiet map may
  be a cloudy one.
- **Detection limit.** Small, cool or smouldering fires fall below the
  threshold. Agricultural and understorey burning is undercounted relative to
  crown fire; VIIRS at 375 m catches substantially smaller fires than MODIS at
  1 km, which is another reason the sensors are not interchangeable.
- **Swath geometry.** Pixels grow away from nadir, so both detection
  probability and the area a row represents vary across the swath.
- **NRT versus science-quality.** Near-real-time rows have less accurate
  geolocation and no `type`. For anything where consistency matters more than
  freshness, filter `quality = 'sp'`.
- **Not burned area.** Detections say *something was hot here at this moment*.
  They do not measure how much land burned. Burned-area products exist for that
  question and are not this dataset.

## Suggested uses

What this kind of data actually supports, and what this catalog is shaped for:

- **Operational awareness and alerting** — the use FIRMS itself was built for,
  and why the near-real-time window is refreshed hourly here.
- **Fire-season climatology and anomaly detection** — comparing a season
  against its own history for a region, holding sensor constant.
- **Emissions and smoke modelling** — FRP integrated over time is the standard
  satellite input to fire emissions estimates.
- **Detecting burning regimes** — agricultural burning, deforestation fronts,
  and persistent industrial heat separate cleanly with `type` and the diurnal
  pattern in `daynight`.
- **Joining fire to other geographies** — the table is Hilbert-ordered within
  each month, so a spatial predicate prunes well; see
  [AGENTS.md](AGENTS.md) for the join recipes.

## Not for

- **Counting fires.** Rows are pixel-observations. Deriving fire events needs
  spatio-temporal clustering, which this catalog does not do for you.
- **Measuring burned area.** See above.
- **Legal, insurance or safety determinations about a specific property.** A
  detection places heat inside a pixel footprint, not on a parcel.
- **Cross-year trends from raw counts.** See the counting trap.

## Schema

19 columns. Full descriptions, including units and the per-sensor NULL
patterns, are carried in `table:columns` on the collection and on every year's
item, so a browser or an agent reads the same text you do. The ones that
decide whether a query is right:

| column | type | note |
|---|---|---|
| `geometry` | geometry | Pixel-centre point, CRS84 |
| `acq_datetime` | timestamp | UTC overpass time |
| `sensor` | string | `MODIS`, `VIIRS_SNPP`, `VIIRS_NOAA20`, `VIIRS_NOAA21` — group by this |
| `quality` | string | `sp` science-quality, `nrt` near-real-time |
| `frp` | double | Fire radiative power, MW |
| `confidence` | string | MODIS `0`–`100`; VIIRS `l`/`n`/`h`. Not comparable |
| `confidence_pct` | int32 | Numeric confidence, MODIS only |
| `type` | int32 | 0 vegetation, 1 volcano, 2 static, 3 offshore. NULL for all NRT |
| `daynight` | string | `D` or `N` |
| `scan`, `track` | double | Pixel footprint. **km for MODIS, m for VIIRS** |

## Quick start

```sql
INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;

SELECT sensor, count(*) AS detections, round(avg(frp), 1) AS mean_frp_mw
FROM read_parquet(
  's3://portolan-mirrors/firms-catalog/detections/year=*/detections.parquet',
  hive_partitioning = true)
GROUP BY sensor ORDER BY detections DESC;
```

### The pattern worth stealing: pre-aggregated grids

Every year ships its detections summed onto an [A5](https://a5geo.org) grid,
and the same exists for the whole record. If your question is a sum over whole
cells and whole days, these answer it from megabytes instead of gigabytes —
2025 is a 1,032 MB detections file against a 2.0 MB `aggregate-r5.parquet`.

```
detections/year=<YYYY>/aggregate-r5.parquet   coarse grid, one column per day
detections/year=<YYYY>/aggregate-r8.parquet   finer grid,  one column per day
detections/alltime-r5.parquet                 whole record, one column per month
detections/alltime-r8.parquet
detections/alltime-r10.parquet
```

```sql
-- Where has the most fire burned since 2000? Reads 3.2 MB.
SELECT a5_cell, count, round(sum_frp) AS frp
FROM 's3://portolan-mirrors/firms-catalog/detections/alltime-r5.parquet'
ORDER BY count DESC LIMIT 10;
```

Glob the filename, never `year=*/*.parquet`: a year directory holds the
detections, the grids and the rolling window, and they are different tables.
[AGENTS.md](AGENTS.md) has the column lists, the measured speed-ups, and what
these grids cannot answer.

## License

**CC0-1.0**, from NASA. Data from NASA-led missions carries no restrictions on
use or redistribution; see the
[ESDIS data use policy](https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-policy).

NASA asks that you acknowledge the source, and so do we:

> We acknowledge the use of data and/or imagery from NASA's Fire Information
> for Resource Management System (FIRMS) (https://earthdata.nasa.gov/firms),
> part of NASA's Earth Observing System Data and Information System (EOSDIS).

## Provenance

Mirrored from the
[FIRMS area API](https://firms.modaps.eosdis.nasa.gov/api/area/) and the
[FIRMS bulk NRT files](https://firms.modaps.eosdis.nasa.gov/active_fire/).
NASA LANCE FIRMS is the producer and remains the authoritative source. Columns
are carried through verbatim; the only additions are a UTC timestamp, expanded
satellite names, and two sort keys. Nothing is filtered or reclassified.

The yearly per-country archive zips FIRMS also offers are **not** used here:
they are clipped to country boundaries and drop offshore detections. Measured
against the API for 2000-11-05, the country archive held 3 offshore
(`type = 3`) detections where the API returned 58. A mirror that silently lost
every gas flare and most volcanoes would not be a mirror.
