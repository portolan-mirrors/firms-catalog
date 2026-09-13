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

# Pin the base resolution rather than inherit the default. The breaks below
# have to name the levels the pyramid actually contains, and a script that
# guesses its own output gets this wrong the moment the default moves -- which
# it did: build_year.sh asked for band 8 against a pyramid of 4, 6 and 10.
BASE_RES="${BASE_RES:-8}"
OVERVIEW="${OVERVIEW:-5}"
# Raw detections from z9 rather than z10: r8 cells are already coarse by then,
# and the points are what a year archive is for.
POINTS_Z="${POINTS_Z:-9}"

# r5 -> r8 -> points, matching the all-time archive band for band, so switching
# between them at a given zoom does not change the cell size under the cursor.
# That was the whole point of unifying them: four different schemes meant the
# map visibly coarsened when the timeline crossed an archive boundary.
echo "[$YEAR] aggregate + tile (r$OVERVIEW -> r$BASE_RES -> points z$POINTS_Z)"
python3 tools/firms_aggregate.py --data "$DATA" --out "$WORK" \
  --tiles "$TILES" --year "$YEAR" --resolution "$BASE_RES" \
  --levels "$OVERVIEW" --features-min-zoom "$POINTS_Z" --cumulative

# --band takes the a5 level and the aggregate it was tiled from. The zoom range
# each level covers is read from the archive, never passed in, so the breaks
# cannot disagree with the geometry they describe.
echo "[$YEAR] class breaks"
python3 tools/make_breaks.py "$ARCHIVE" \
  --band "$BASE_RES:$WORK/cells.parquet" \
  --band "$OVERVIEW:$WORK/cells_r$OVERVIEW.parquet"

echo "[$YEAR] styles"
python3 tools/make_styles.py "$ARCHIVE" \
  --out "$CATALOG/styles" --tiles "./fire-$YEAR.pmtiles" --suffix ", $YEAR"

# The declared axis is what lets a viewer draw the timeline without inferring
# it from column names, and re-tiling silently drops it.
echo "[$YEAR] declared axis"
python3 tools/make_timeline.py "$ARCHIVE"

echo "[$YEAR] done: $ARCHIVE"
