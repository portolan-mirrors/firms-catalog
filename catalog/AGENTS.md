# AGENTS.md — NASA FIRMS Active Fire Detections

Guidance for AI agents and automated clients working with this catalog.

**One rule survives every edit to this file.** Every claim here is either quoted
from a source or measured from the data. If you cannot point at where a fact
came from, it does not belong in this file.

## What this catalog holds

One collection. Public root:
`https://data.source.coop/portolan-mirrors/firms-catalog/catalog.json`

| collection | holds |
|---|---|
| [`detections`](detections/collection.json) | Every NASA FIRMS active fire detection, MODIS from November 2000 and VIIRS from January 2012, as one year-partitioned GeoParquet 2.0 table |

This is a **mirror**. NASA LANCE FIRMS produces the data and is the
authoritative source.

## Start here

The column meanings, the partition layout, the sort order, the traps, and
worked queries all live in the collection guide:
[`detections/AGENTS.md`](detections/AGENTS.md). Read it before writing a query.

The three traps that most often produce a confident wrong answer:

- `confidence` is encoded differently per instrument and is not comparable.
- `type` is NULL for every near-real-time row.
- Filtering on `acq_date` alone does not prune the partition. Filter on `year`.

## Join keys

The catalog holds one collection, so nothing joins across collections. FIRMS
publishes no per-detection identifier; a detection is identified only by
(`sensor`, `acq_datetime`, `geometry`).

## Structure

Assets and structural links resolve relative to the object that carries them.
