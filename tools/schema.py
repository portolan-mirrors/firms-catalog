#!/usr/bin/env python3
"""The detections table's schema, written once and read by every generator.

`table:columns` belongs on the collection AND on every item: a browser showing
one year does not walk up to the collection to find out what a column means,
so an item without it is a row count and nothing else. Two copies of the same
list in two generators is how they drift, which the documentation guidance
names as the most common defect in otherwise strong catalogs -- so the list
lives here and both `make_collection.py` and `make_items.py` import it.

The descriptions are the human-facing surface too. They carry links because a
column description is where a reader actually is when the question occurs to
them: `frp` is the moment to explain what fire radiative power measures, not
a paragraph in a README they have already scrolled past. AGENTS.md carries the
same facts at greater length for the agent that has already committed.
"""
from __future__ import annotations

# Upstream references, named once so prose and metadata cannot disagree.
FIRMS = "https://firms.modaps.eosdis.nasa.gov/"
FAQ = "https://www.earthdata.nasa.gov/data/tools/firms/faq"
MODIS_GUIDE = "https://modis-fire.umd.edu/files/MODIS_C6_C6.1_Fire_User_Guide_1.0.pdf"
MODIS_INSTRUMENT = "https://www.earthdata.nasa.gov/data/instruments/modis"
VIIRS_INSTRUMENT = "https://www.earthdata.nasa.gov/data/instruments/viirs"

# name -> (type, description). The same text the README schema table shows.
COLUMNS = [
    ("geometry", "geometry",
     "Detection centre point (CRS84). The centre of the flagged PIXEL, not the "
     "located fire: the true source can be anywhere inside the footprint that "
     "`scan` and `track` describe, and is often smaller than one pixel. Do not "
     "read the coordinate as a fire perimeter or a point ignition."),
    ("acq_datetime", "timestamp",
     "Acquisition time in UTC, composed here from the published acq_date and "
     "acq_time. This is the satellite OVERPASS time, so it records when the "
     "fire was seen, not when it started or ended."),
    ("acq_date", "date", "Acquisition date as published, UTC."),
    ("sensor", "string",
     "MODIS, VIIRS_SNPP, VIIRS_NOAA20 or VIIRS_NOAA21. The single most "
     "important grouping column: resolution, confidence encoding and the "
     "brightness columns all differ by sensor, so a query that mixes them "
     "without saying so is usually wrong. MODIS runs from November 2000, "
     f"VIIRS from January 2012. See {MODIS_INSTRUMENT} and {VIIRS_INSTRUMENT}."),
    ("satellite", "string",
     "Platform name: Terra, Aqua, Suomi-NPP, NOAA-20 or NOAA-21. Expanded here "
     "from the short codes FIRMS publishes. Each platform has its own overpass "
     "times, which is what makes detection counts a function of how many "
     "satellites were flying that year as much as of how much burned."),
    ("instrument", "string", "MODIS or VIIRS. The sensor family behind `sensor`."),
    ("quality", "string",
     "`sp` for science-quality, `nrt` for near-real-time. NRT rows are "
     "published within hours and are later replaced by SP rows, roughly three "
     "to four months behind. NRT geolocation is less accurate and NRT rows "
     "carry no `type`. Filter on this column whenever consistency matters more "
     "than freshness."),
    ("version", "string",
     "Collection and processing designation exactly as published, for example "
     "`2.0NRT` or `6.1`. Changes here mark upstream algorithm revisions, so a "
     "long time series can span more than one."),
    ("brightness", "double",
     "MODIS channel 21/22 brightness temperature, kelvin. NULL for VIIRS rows. "
     f"Defined in the MODIS fire user guide: {MODIS_GUIDE}"),
    ("bright_t31", "double",
     "MODIS channel 31 brightness temperature, kelvin. NULL for VIIRS rows. "
     "Used with `brightness` in the MODIS detection algorithm; the contrast "
     "between the two is what separates a fire from a hot background."),
    ("bright_ti4", "double",
     "VIIRS I-4 channel brightness temperature, kelvin. NULL for MODIS rows. "
     "The VIIRS analogue of `brightness`, and NOT interchangeable with it."),
    ("bright_ti5", "double",
     "VIIRS I-5 channel brightness temperature, kelvin. NULL for MODIS rows. "
     "The VIIRS analogue of `bright_t31`."),
    ("scan", "double",
     "Along-scan footprint of the pixel at the detection. Kilometres for "
     "MODIS, metres for VIIRS -- the units differ by sensor. Grows away from "
     "nadir, so a detection at the swath edge covers several times the ground "
     "a nadir detection does."),
    ("track", "double",
     "Along-track footprint of the pixel. Kilometres for MODIS, metres for "
     "VIIRS, as `scan`. Multiply the two for the approximate ground area the "
     "detection represents."),
    ("frp", "double",
     "Fire radiative power in megawatts: the rate at which the pixel radiates "
     "energy, which scales with how much biomass is burning per unit time. It "
     "is the closest thing in this table to fire intensity, and it is what to "
     "sum when you want a measure of fire ACTIVITY rather than a count of "
     "flagged pixels. Integrate FRP over time to approximate total radiated "
     "energy and hence fuel consumed. Not comparable pixel-for-pixel across "
     "sensors of different resolution, and suppressed by cloud and heavy "
     f"smoke. Derivation is in the MODIS fire user guide: {MODIS_GUIDE}"),
    ("daynight", "string",
     "`D` for a daytime overpass, `N` for night. Worth splitting on: night "
     "detections have a cleaner thermal background and behave differently from "
     "daytime ones, and the day/night mix shifts with latitude and season."),
    ("confidence", "string",
     "As published, and NOT comparable across instruments. MODIS gives an "
     "integer 0-100; VIIRS gives `l` (low), `n` (nominal) or `h` (high). No "
     "crosswalk is published here because none is documented upstream. It is a "
     "measure of how sure the ALGORITHM is that the pixel is a fire, not of "
     "how large or how real the fire is, and it is intended for filtering "
     "rather than for weighting. A common starting point is to drop `l` and "
     f"MODIS below 30 for analysis. Defined in the FIRMS FAQ: {FAQ}"),
    ("confidence_pct", "int32",
     "Numeric confidence 0-100, added here so MODIS rows can be filtered "
     "arithmetically. MODIS rows only; NULL for VIIRS, which has no numeric "
     "equivalent. Never coalesce this with a VIIRS category to make one scale."),
    ("type", "int32",
     "0 vegetation fire, 1 active volcano, 2 other static land source (gas "
     "flares, industrial heat), 3 offshore. NULL for EVERY near-real-time row, "
     "because FIRMS does not attribute type in near-real-time -- so a filter "
     "on `type = 0` silently drops the most recent months unless you also "
     f"allow NULL. Defined in the FIRMS FAQ: {FAQ}"),
]


def stac_columns() -> list[dict]:
    """`table:columns`, the shape the table extension wants."""
    return [{"name": n, "type": t, "description": d} for n, t, d in COLUMNS]
