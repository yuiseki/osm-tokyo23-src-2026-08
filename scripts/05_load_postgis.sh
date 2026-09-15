#!/usr/bin/env bash
# Load the frozen extract into PostGIS with osm2pgsql, in Docker.
#
# The schema is osm2pgsql's own classic pgsql output, not one of our design:
#   planet_osm_point / planet_osm_line / planet_osm_polygon / planet_osm_roads
#
# --hstore is required. The style promotes 106 tags to columns, but the
# question set also asks about opening_hours, start_date and building:levels,
# which it does not promote. Those live in the hstore `tags` column.
#
# No projection flag, so osm2pgsql's default EPSG:3857 applies.
set -euo pipefail
cd "$(dirname "$0")/../docker"

PBF=${PBF:-data/tokyo23-260831.osm.pbf}

docker compose up -d db
docker compose run --rm loader \
  osm2pgsql \
    --create \
    --database osm \
    --style /opt/osm2pgsql/default.style \
    --hstore \
    "$PBF"
