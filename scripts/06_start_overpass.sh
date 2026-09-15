#!/usr/bin/env bash
# Bring up the Overpass instance over the frozen extract.
#
# Three traps in this image, all worked around here.
#
# 1. OVERPASS_STOP_AFTER_INIT defaults to exiting: the container finishes the
#    import, prints "initialization complete. Exiting." and stops. The compose
#    file sets it to 'false'.
#
# 2. /api/status always returns 502. It crashes with
#      terminate called after throwing an instance of 'std::out_of_range'
#    while /api/interpreter is already answering 200. Never use /api/status as
#    a readiness probe; ask the interpreter a trivial question instead.
#
# 3. The named volume is created drwx------ and init_osm3s.sh makes /db/db the
#    same way, so the user running fcgiwrap cannot reach the dispatcher socket
#    and every query from outside fails with
#      runtime error: open64: 13 Permission denied /db/db//osm3s_osm_base
#    chmod after the import fixes it.
#
# Readiness is /db/init_done, not a log line: "ready to receive requests" is
# printed only on the run that does the import, never on a later start.
set -euo pipefail
cd "$(dirname "$0")/../docker"

URL=${URL:-http://localhost:12346/api/interpreter}

docker compose up -d overpass

echo "waiting for the import (first run takes about 10 minutes)"
until docker compose exec -T overpass test -f /db/init_done 2>/dev/null; do
  if ! docker compose ps --status running --services | grep -q '^overpass$'; then
    echo "the overpass container is not running; check 'docker compose logs overpass'" >&2
    exit 1
  fi
  sleep 15
done

docker compose exec -u root -T overpass chmod 755 /db /db/db
echo "permissions fixed"

echo "waiting for the interpreter to answer"
for _ in $(seq 60); do
  if curl -sf -m 30 --data-urlencode 'data=[out:json];out count;' "$URL" >/dev/null 2>&1; then
    echo "overpass is answering on $URL"
    exit 0
  fi
  sleep 5
done
echo "the interpreter did not answer in time" >&2
exit 1
