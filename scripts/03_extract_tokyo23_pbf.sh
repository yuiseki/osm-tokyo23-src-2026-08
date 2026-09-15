#!/usr/bin/env bash
# Step 3 of 3: clip the bounding box down to the wards themselves.
set -euo pipefail
cd "$(dirname "$0")/.."

SRC=tmp/tokyo-bbox-260831.osm.pbf
POLY=data/tokyo23_osm_boundary.geojson
OUT=data/tokyo23-260831.osm.pbf

test -r "$SRC"
test -r "$POLY"

osmium extract \
  --polygon "$POLY" \
  --strategy complete_ways \
  --overwrite \
  -o "$OUT" \
  "$SRC"

md5sum "$OUT" > "$OUT.md5"
cat "$OUT.md5"
