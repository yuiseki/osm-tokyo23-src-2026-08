#!/usr/bin/env bash
# Step 1 of 3: cut a generous bounding box around the 23 wards out of the planet.
#
# This is the only pass over the planet. The box has about 11 km of margin on
# every side of the wards, so every way and relation that makes up a ward
# boundary is present complete, and the real boundary can then be derived from
# this file rather than from an outside source of a different vintage.
set -euo pipefail
cd "$(dirname "$0")/.."

# Override with PLANET=/path/to/planet-260831.osm.pbf
# https://planet.openstreetmap.org/pbf/planet-260831.osm.pbf
PLANET=${PLANET:-planet-260831.osm.pbf}
OUT=tmp/tokyo-bbox-260831.osm.pbf

test -r "$PLANET"
mkdir -p tmp

exec osmium extract \
  --bbox 139.45,35.38,140.02,35.92 \
  --strategy complete_ways \
  --progress \
  --overwrite \
  -o "$OUT" \
  "$PLANET"
