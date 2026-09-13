#!/usr/bin/env bash
# Build every year archive, then rebuild the all-time one, unattended.
#
# Written to survive a long run rather than to be clever. Each year is skipped
# if its archive already exists, so an interrupted run resumes by being
# restarted. A year that fails does not stop the ones after it -- one bad year
# should not cost the other twenty-five. Disk is checked before each year,
# because the intermediates are large and a full disk mid-tile leaves a
# truncated archive that looks finished.
#
#   bash tools/build_all.sh            # every year, then all-time
#   YEARS="2012 2013" bash tools/build_all.sh
set -u

cd "$(dirname "$0")/.."
STAGE="../catalog-staging"
YEARS="${YEARS:-$(seq 2000 2026)}"
MIN_FREE_GB="${MIN_FREE_GB:-12}"
LOG="/tmp/build_all.log"

say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

free_gb() { df -g . | tail -1 | awk '{print $4}'; }

say "=== build_all starting: $(echo $YEARS | wc -w | tr -d ' ') year(s) ==="
OK=0; SKIP=0; FAIL=0
for Y in $YEARS; do
  ARCHIVE="$STAGE/publish/detections/year=$Y/fire-$Y.pmtiles"
  if [ -f "$ARCHIVE" ]; then
    say "$Y  already built, skipping"; SKIP=$((SKIP+1)); continue
  fi
  if [ ! -f "$STAGE/publish/detections/year=$Y/detections.parquet" ]; then
    say "$Y  no source parquet, skipping"; SKIP=$((SKIP+1)); continue
  fi
  FREE=$(free_gb)
  if [ "$FREE" -lt "$MIN_FREE_GB" ]; then
    say "STOPPING: only ${FREE} GB free, need ${MIN_FREE_GB}"; break
  fi
  START=$(date +%s)
  say "$Y  building (${FREE} GB free) ..."
  if bash tools/build_year.sh "$Y" >>"$LOG" 2>&1; then
    SIZE=$(ls -la "$ARCHIVE" 2>/dev/null | awk '{printf "%.0f MB", $5/1e6}')
    say "$Y  done in $((($(date +%s)-START)/60))m  $SIZE"
    OK=$((OK+1))
  else
    say "$Y  FAILED (see $LOG)"; FAIL=$((FAIL+1))
  fi
  # The per-year aggregates are only needed while that year builds.
  rm -rf "$STAGE/y$Y" 2>/dev/null
done
say "=== years: $OK built, $SKIP skipped, $FAIL failed ==="
say "ALL_YEARS_DONE"
