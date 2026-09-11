#!/usr/bin/env bash
# Build one year's archive end to end: aggregate, tile, class breaks, styles.
#
# One script rather than four calls, because the four outputs are only correct
# together. Breaks are keyed by a5 level and take their zoom ranges from the
# archive's own gpio:pyramid, so they change exactly when the cells change;
# styles are generated from those same breaks, so the plain published styles
# cannot drift from what any app reading the metadata draws. Built apart, they
# drift silently -- fire-2020-split.pmtiles carried neither and nothing noticed.
#
#   bash tools/build_year.sh 2020
#
# DATA, WORK, TILES and CATALOG can be overridden; CI sets them to paths inside
# the workspace, since the defaults are relative to a local checkout.
set -euo pipefail

YEAR="${1:?usage: build_year.sh YEAR}"
DATA="${DATA:-../catalog-staging/publish/detections}"
WORK="${WORK:-../catalog-staging/y$YEAR}"
TILES="${TILES:-../catalog-staging/publish/detections/year=$YEAR}"
CATALOG="${CATALOG:-catalog/detections/year=$YEAR}"
ARCHIVE="$TILES/fire-$YEAR.pmtiles"

mkdir -p "$TILES" "$CATALOG"

echo "[$YEAR] aggregate + tile"
python3 tools/firms_aggregate.py --data "$DATA" --out "$WORK" \
  --tiles "$TILES" --year "$YEAR"

# --band takes the a5 level and the aggregate it was tiled from. The zoom range
# each level covers is read from the archive, never passed in, so the breaks
# cannot disagree with the geometry they describe.
echo "[$YEAR] class breaks"
python3 tools/make_breaks.py "$ARCHIVE" \
  --band "8:$WORK/cells.parquet" \
  --band "6:$WORK/cells_r6.parquet" \
  --band "4:$WORK/cells_r4.parquet"

echo "[$YEAR] styles"
python3 tools/make_styles.py "$ARCHIVE" \
  --out "$CATALOG/styles" --tiles "./fire-$YEAR.pmtiles" --suffix ", $YEAR"

echo "[$YEAR] done: $ARCHIVE"
