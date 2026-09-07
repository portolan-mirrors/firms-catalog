# NASA FIRMS Active Fire Detections

Global satellite active fire and thermal anomaly detections from NASA's Fire
Information for Resource Management System (FIRMS), republished as cloud-native
GeoParquet 2.0 that you can query in place without downloading anything.

This is a **mirror**. NASA LANCE FIRMS produces the data and remains the
authoritative source.

## What is here

One collection, `detections`, holding every FIRMS active fire detection from
**MODIS** (Terra and Aqua, 1 km, since November 2000) and **VIIRS**
(Suomi-NPP since January 2012, NOAA-20 since April 2018, NOAA-21 since January
2024). Coverage is global.

The data is one table, partitioned by year, so a single glob spans the whole
record from 2000 to today. Near-real-time detections are appended hourly and are
replaced by science-quality data as NASA finalizes it, roughly three to four
months behind. A `quality` column tells you which you are looking at.

GOES and Landsat fire products are published by FIRMS but are not mirrored here.

For column meanings, the sort order, and the traps, read
[AGENTS.md](AGENTS.md) — it is written for machine clients but the quirks
section is worth a human's time too.

## License

**CC0-1.0.** NASA's Earth Science Data and Information System states that data
from NASA-led missions carries no restrictions on use or redistribution. See the
[ESDIS data use policy](https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-policy).

NASA asks that you acknowledge the source, and so do we:

> We acknowledge the use of data and/or imagery from NASA's Fire Information for
> Resource Management System (FIRMS) (https://earthdata.nasa.gov/firms), part of
> NASA's Earth Observing System Data and Information System (EOSDIS).

## Provenance

Every detection comes from the
[FIRMS area API](https://firms.modaps.eosdis.nasa.gov/api/area/), requested as
whole-globe windows and normalized into a single schema. Nothing is interpolated,
filtered, or reclassified. The columns FIRMS publishes are carried through
verbatim; the only additions are a UTC timestamp, expanded satellite names, and
two sort-key columns, all documented in [AGENTS.md](AGENTS.md).

FIRMS also offers yearly per-country archive zips, which download far faster than
the API. **This catalog does not use them.** They are clipped to country
boundaries and drop offshore detections. Measured against the API for
2000-11-05, the country archive held 3 offshore (`type = 3`) detections where the
API returned 58, and 2,615 rows against the API's 2,670. A mirror that silently
lost every gas flare and most volcanoes would not be a mirror.

The pipeline that builds this catalog is open, and each step is a script:
[firms_fetch.py](https://github.com/portolan-mirrors/firms-catalog/blob/main/tools/firms_fetch.py)
fetches and normalizes,
[firms_build.py](https://github.com/portolan-mirrors/firms-catalog/blob/main/tools/firms_build.py)
compacts to per-year GeoParquet. See the
[repository README](https://github.com/portolan-mirrors/firms-catalog) for the
overview.

## Access

Query the whole record without downloading it:

```sql
INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;

SELECT sensor, count(*) AS detections, round(avg(frp), 1) AS mean_frp_mw
FROM read_parquet(
  's3://us-west-2.opendata.source.coop/portolan-mirrors/firms-catalog/detections/year=*/detections.parquet',
  hive_partitioning = true)
WHERE year = 2020 AND _month = 8
GROUP BY sensor
ORDER BY detections DESC;
```
