#!/usr/bin/env bash
# Backfill the whole FIRMS record. Resumable: re-run to continue after a stop.
# Ranges come from the FIRMS data_availability endpoint, not from hard-coded dates.
set -uo pipefail
: "${FIRMS_MAP_KEY:?set FIRMS_MAP_KEY}"
STAGE="${1:-./catalog-staging/chunks}"
WORKERS="${WORKERS:-8}"
HERE="$(cd "$(dirname "$0")" && pwd)"

avail=$(curl -s "https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/${FIRMS_MAP_KEY}/ALL")
echo "$avail" | head -20

for src in MODIS_SP VIIRS_SNPP_SP VIIRS_NOAA20_SP VIIRS_NOAA21_NRT \
           MODIS_NRT VIIRS_SNPP_NRT VIIRS_NOAA20_NRT; do
  line=$(echo "$avail" | grep "^${src}," || true)
  [ -z "$line" ] && { echo "SKIP $src (not in data_availability)"; continue; }
  start=$(echo "$line" | cut -d, -f2); end=$(echo "$line" | cut -d, -f3)
  echo "=== $src  $start .. $end ==="
  python3 "$HERE/firms_fetch.py" --source "$src" --start "$start" --end "$end" \
      --out "$STAGE" --workers "$WORKERS"
done
echo "BACKFILL COMPLETE"
