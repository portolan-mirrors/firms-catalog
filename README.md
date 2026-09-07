# firms-catalog

A [Portolan](https://github.com/portolan-sdi/portolan-spec) catalog mirroring
**NASA FIRMS active fire detections** as cloud-native GeoParquet.

Catalog metadata lives in this repository. The data lives on
[Source Cooperative](https://source.coop/portolan-mirrors/firms-catalog).
CI validates every change to the metadata; the data is rebuilt on a schedule.

- **Published catalog**: https://source.coop/portolan-mirrors/firms-catalog
- **STAC root**: https://data.source.coop/portolan-mirrors/firms-catalog/catalog.json
- **Upstream**: https://firms.modaps.eosdis.nasa.gov/

## What it publishes

One collection, `detections`: every MODIS and VIIRS active fire detection from
November 2000 to today, about 575 million rows, as one year-partitioned table.

```
detections/year=<YYYY>/detections.parquet
```

GeoParquet 2.0 (native Parquet `GEOMETRY` logical type), zstd level 15,
100k-row row groups, rows ordered by `(_month, _hilbert)`.

## Design decisions worth knowing

**Year-only partitioning.** The path carries time and nothing else. A spatial
partition key was considered and rejected: hive pruning only fires when the query
filters on the partition column, and no client writes `WHERE zone = 2` in
response to a bounding-box filter. Spatial pruning comes from Hilbert ordering
plus row-group statistics, which an ordinary `ST_Within` predicate triggers for
free. Portolan's formats spec currently requires a spatial path structure for all
partitioned collections, which conflicts with the partition extension's own
`temporal` strategy; that is filed as
[portolan-spec#196](https://github.com/portolan-sdi/portolan-spec/issues/196).

**Sorted by `(_month, _hilbert)`, not pure Hilbert.** Pure Hilbert scatters every
month across the file and removes month-level pruning. Sorting by month first and
Hilbert within it keeps both. The two helper columns cost about 15% of file size
and are documented in `catalog/AGENTS.md`.

**The API, not the yearly country archives.** FIRMS publishes yearly per-country
zips that download much faster. They are clipped to country boundaries and drop
offshore detections — measured on 2000-11-05, 3 offshore rows against the API's
58. Fidelity wins over speed here.

**NRT and science-quality in one table.** FIRMS guarantees non-overlapping date
ranges per sensor between its `_SP` and `_NRT` sources, so the two cannot
double-count. A `quality` column marks which is which.

## The pipeline

| Script | Does |
|---|---|
| `tools/firms_fetch.py` | Fetches one API window per chunk and normalizes it to Parquet. Resumable: existing chunks are skipped. |
| `tools/firms_backfill.sh` | Drives every sensor over its full range, reading the date bounds from the FIRMS `data_availability` endpoint rather than hard-coding them. |
| `tools/firms_build.py` | Compacts chunks into per-year GeoParquet 2.0, sorted and row-grouped for cloud-native access. |
| `tools/publish.py` | Syncs `catalog/` to the bucket. Template-provided; never widened beyond `catalog/`. |
| `tools/upload_data.py` | Uploads staged data files. Template-provided. |

```bash
export FIRMS_MAP_KEY=...          # https://firms.modaps.eosdis.nasa.gov/api/map_key/
./tools/firms_backfill.sh ./catalog-staging/chunks
python3 tools/firms_build.py --chunks ./catalog-staging/chunks \
    --out ./catalog-staging/publish/detections
```

### API rate limits shape the schedule

The FIRMS map key allows 5,000 transactions per 10 minutes, and a request costs
far more than one transaction. Measured:

| request | transactions |
|---|---|
| small bbox, 1 day | 2 |
| world, 1 day | 36 |
| world, 5 days | 180 |

Cost scales with area × days, so batching days saves nothing. A full backfill is
roughly 678,000 transactions, about 22 hours of wall-clock at the default limit.
The published docs say the area API accepts a 10-day range; the server rejects
anything above 5.

## Working on this repository

```bash
python3 -m venv .venv && .venv/bin/pip install 'rashid>=0.1.8,<0.2.0'
python3 tests/run_all.py          # gates: conformance, links, STAC validity
python3 tools/publish.py          # dry run
python3 tools/publish.py --confirm
```

Data files never enter git. `.gitignore` blocks the common formats.

## License

Data: CC0-1.0, from NASA. See `catalog/README.md` for the acknowledgement NASA
asks you to carry. Repository code: see `LICENSE`.
