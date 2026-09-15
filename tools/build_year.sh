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
# State the handovers rather than letting the tile-size budget pick them. The
# budget judges one archive at a time, so it drifts as a year grows: 2026
# gained a week of data and its r8 band moved from z6 to z5, which is a cell
# size change against every other year at that zoom. BANDS is what keeps the
# set aligned; clear it to fall back to the budget.
BANDS="${BANDS:-$OVERVIEW:0,$BASE_RES:6}"
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
  --levels "$OVERVIEW" --features-min-zoom "$POINTS_Z" \
  ${BANDS:+--bands "$BANDS"}

# Check the pyramid is the one that was asked for, before spending minutes on
# breaks and styles built against it. A band plan that drifts is invisible in
# the output -- the build succeeds, the archive looks fine, and the cells
# simply change size against the other years at one zoom. 2026 was tiled twice
# before anyone read its gpio:pyramid back.
if [ -n "${BANDS:-}" ]; then
  echo "[$YEAR] verify bands"
  python3 - "$ARCHIVE" "$BANDS" <<'EOF'
import sys
from pmtiles.reader import MmapSource, Reader

archive, spec = sys.argv[1], sys.argv[2]
want = [(lvl, int(z)) for lvl, _, z in (p.partition(":") for p in spec.split(","))]
with open(archive, "rb") as fh:
    bands = Reader(MmapSource(fh)).metadata()["gpio:pyramid"]["bands"]
# The features band is appended by --include-features and is not part of the
# plan, so compare only the aggregate bands the plan names.
got = [(str(b["level"]), b["minzoom"]) for b in bands if b["level"] != "features"]
if got != [(str(l), z) for l, z in want]:
    sys.exit(f"band plan drifted: asked for {want}, archive has {got}")
print(f"  bands as planned: {got}")
EOF
fi

# The aggregates are published beside the archive, not left in the work
# directory. They are what the tiles were built from, and a GeoParquet of the
# grid answers questions the MVT cannot without decoding every tile -- but only
# if it ships. They were being built and deleted on every run until now.
echo "[$YEAR] publish aggregates"
cp "$WORK/cells.parquet"              "$TILES/aggregate-r$BASE_RES.parquet"
cp "$WORK/cells_r$OVERVIEW.parquet"   "$TILES/aggregate-r$OVERVIEW.parquet"
ls -la "$TILES"/aggregate-r*.parquet | awk '{printf "  %-46s %7.1f MB\n", $NF, $5/1e6}'

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
