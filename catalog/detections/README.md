# Active Fire Detections (MODIS and VIIRS)

Every NASA FIRMS active fire and thermal anomaly detection, global, as one
year-partitioned GeoParquet 2.0 table you can query in place.

Carries **MODIS** (Terra and Aqua, 1 km, from November 2000) and **VIIRS**
(Suomi-NPP from January 2012, NOAA-20 from April 2018, NOAA-21 from January
2024). Near-real-time rows are appended hourly. NASA replaces them with
science-quality rows roughly three to four months later, and the `quality`
column records which kind a row is.

## License

**CC0-1.0**, from NASA. Please carry the acknowledgement:

> We acknowledge the use of data and/or imagery from NASA's Fire Information for
> Resource Management System (FIRMS) (https://earthdata.nasa.gov/firms), part of
> NASA's Earth Observing System Data and Information System (EOSDIS).

## Provenance

Mirrored from the [FIRMS area API](https://firms.modaps.eosdis.nasa.gov/api/area/)
and the [FIRMS bulk NRT files](https://firms.modaps.eosdis.nasa.gov/active_fire/).
NASA LANCE FIRMS is the producer and the authoritative source. Columns are
carried through verbatim; the only additions are a UTC timestamp, expanded
satellite names, and two sort keys. Nothing is filtered or reclassified.

The yearly per-country archive zips FIRMS also offers are **not** used here:
they are clipped to country boundaries and drop offshore detections.

## Read this before querying

Three things will give you wrong answers if you skip them, all covered in
[AGENTS.md](AGENTS.md):

- `confidence` means different things for MODIS (0-100) and VIIRS (`l`/`n`/`h`).
- `type` is NULL for every near-real-time row.
- A detection is a flagged pixel, not a confirmed fire, and its coordinate is a
  pixel centre.

## Access

```sql
INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;

SELECT sensor, count(*) AS detections, round(avg(frp), 1) AS mean_frp_mw
FROM read_parquet(
  's3://portolan-mirrors/firms-catalog/detections/year=*/detections.parquet',
  hive_partitioning = true)
GROUP BY sensor ORDER BY detections DESC;
```

### Pre-aggregated grids

Every year also ships its detections summed onto an A5 grid, and the same
exists for the whole record. If your question is a sum over whole cells and
whole days, these answer it from megabytes instead of gigabytes — 2025 is a
1,032 MB detections file against a 2.0 MB `aggregate-r5.parquet`.

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
