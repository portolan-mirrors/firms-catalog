#!/usr/bin/env python3
"""Derive an archive's declared time axis from the columns it actually has.

A viewer must not infer the axis by pattern-matching column names, and must not
read bucket keys off a sampled feature: MVT omits zero-valued attributes, so a
sampled feature reported three months of a twelve-month year. Instead every
archive carries `firms:timeline` in its PMTiles metadata, and this module is
the single place that decides what it says.

Bucket columns are `count_YYYYMM` or `count_YYYYMMDD`. Four-digit keys are
rejected on purpose: `count_0101` could be a month-day or a year, and telling
them apart would need a second field.
"""
from __future__ import annotations

import re
from typing import Iterable

BUCKET = re.compile(r"^count_(\d+)$")
# digits -> (unit, key_format)
WIDTHS = {6: ("month", "count_YYYYMM"), 8: ("day", "count_YYYYMMDD")}


def bucket_keys(columns: Iterable[str]) -> list[str]:
    """Sorted time-bucket suffixes. Non-time `count_` columns are excluded.

    `count_modis` and `count_d` are sensor and day/night breakdowns, not time,
    so the pattern requires digits.
    """
    keys = {m.group(1) for c in columns if (m := BUCKET.match(c))}
    return sorted(keys)


def declare(columns: Iterable[str]) -> dict:
    """The `firms:timeline` value for an archive with these columns."""
    keys = bucket_keys(columns)
    if not keys:
        raise ValueError("no count_<digits> bucket columns found")
    widths = {len(k) for k in keys}
    if len(widths) > 1:
        raise ValueError(f"bucket columns have mixed widths: {sorted(widths)}")
    width = widths.pop()
    if width not in WIDTHS:
        raise ValueError(
            f"{width}-digit bucket keys are ambiguous; use YYYYMM or YYYYMMDD")
    unit, key_format = WIDTHS[width]
    return {"unit": unit, "key_format": key_format,
            "min": keys[0], "max": keys[-1], "buckets": len(keys)}
