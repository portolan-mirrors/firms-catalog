"""Asset descriptions shared by make_collection.py and make_items.py.

One home for each description, so the collection, the items, and any one-off
repair script carry the same text. Portolan v0.3 asks every asset for a title
and a description (PORTO-CORE-088).
"""

# Keyed by style filename stem. make_styles.py varies the display name per
# window ("last 7 days", a year, "all years"); what the style shows does not
# change, so the description is fixed per stem.
STYLE_DESCRIPTIONS = {
    "default": (
        "MapLibre style: aggregate cells colored by detection count, with "
        "individual detections drawn at high zoom."
    ),
    "avg-frp": (
        "MapLibre style: aggregate cells colored by mean fire radiative "
        "power in megawatts."
    ),
    "day-night": (
        "MapLibre style: aggregate cells colored by the share of night-time "
        "detections, with individual detections colored day or night at "
        "high zoom."
    ),
}

STYLE_FALLBACK = "MapLibre style for the collection's vector tiles."

PMTILES_LATEST_DESCRIPTION = (
    "Rolling seven-day window as vector tiles: aggregate cells at low zoom, "
    "individual detections from zoom 9. Rebuilt hourly."
)

PMTILES_ALLTIME_DESCRIPTION = (
    "Monthly aggregate cells for every month since November 2000. A coarse "
    "view of the whole record; it carries no individual detections."
)

THUMBNAIL_DESCRIPTION = (
    "Render of the default style over the rolling seven-day window."
)

LOGO_DESCRIPTION = "Mirror logo, for catalog listings."


def alltime_aggregate_description(lvl: int | str) -> str:
    return (
        f"The a5 resolution-{lvl} grid behind the all-time tileset, as "
        "GeoParquet: one row per cell, one count column per month since "
        "November 2000."
    )


def item_data_description(year: int | str) -> str:
    return (
        f"Every MODIS and VIIRS detection acquired in {year}, as one "
        "GeoParquet 2.0 file with rows ordered along a Hilbert curve."
    )


def item_pmtiles_description(year: int | str) -> str:
    return (
        f"Vector tiles for {year}: aggregate cells at low zoom, individual "
        "detections at high zoom."
    )


def item_aggregate_description(year: int | str, lvl: int | str) -> str:
    return (
        f"The a5 resolution-{lvl} grid behind the {year} tileset, as "
        "GeoParquet: one row per cell, one count column per day."
    )
