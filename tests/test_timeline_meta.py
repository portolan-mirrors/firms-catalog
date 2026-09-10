#!/usr/bin/env python3
"""The time axis an archive declares must match the columns it actually has.

A viewer that guesses the axis from column names, or reads bucket keys off one
feature, gets it wrong: MVT omits zero-valued attributes, so a sampled feature
showed three months of a twelve-month year. This gate covers the derivation
that replaces the guessing.

Run: python3 tests/test_timeline_meta.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from timeline_meta import bucket_keys, declare  # noqa: E402

errors: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


NON_BUCKET = ["a5_cell", "count", "sum_frp", "avg_frp", "max_frp", "geometry"]

# --- monthly ---
monthly = NON_BUCKET + ["count_202001", "count_202002", "count_201912"]
d = declare(monthly)
check(d["unit"] == "month", f"six digits is monthly, got {d['unit']}")
check(d["key_format"] == "count_YYYYMM", "monthly key format is named")
check(d["min"] == "201912", f"min is the earliest bucket, got {d['min']}")
check(d["max"] == "202002", f"max is the latest bucket, got {d['max']}")
check(d["buckets"] == 3, f"buckets counts the columns, got {d['buckets']}")

# --- daily ---
daily = NON_BUCKET + ["count_20200101", "count_20201231"]
d = declare(daily)
check(d["unit"] == "day", f"eight digits is daily, got {d['unit']}")
check(d["key_format"] == "count_YYYYMMDD", "daily key format is named")
check(d["buckets"] == 2, "daily buckets counted")

# --- ordering is by value, not by string position in the input ---
shuffled = NON_BUCKET + ["count_202010", "count_202002", "count_202001"]
check(bucket_keys(shuffled) == ["202001", "202002", "202010"],
      "bucket keys come back sorted")

# --- rejections ---
# Mixed widths cannot be one axis, and silently picking one would mislabel it.
try:
    declare(NON_BUCKET + ["count_202001", "count_20200101"])
    check(False, "mixed bucket widths must raise")
except ValueError:
    check(True, "")

# An archive with no bucket columns has no axis to declare.
try:
    declare(NON_BUCKET)
    check(False, "no bucket columns must raise")
except ValueError:
    check(True, "")

# Four-digit keys are the ambiguous form the spec forbids.
try:
    declare(NON_BUCKET + ["count_0101", "count_0102"])
    check(False, "four-digit bucket keys must raise")
except ValueError:
    check(True, "")

# --- other count_ columns must not be mistaken for buckets ---
withdims = NON_BUCKET + ["count_modis", "count_d", "count_n", "count_202001"]
check(bucket_keys(withdims) == ["202001"],
      "sensor and daynight breakdowns are not time buckets")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print("OK: timeline declaration matches the columns")
