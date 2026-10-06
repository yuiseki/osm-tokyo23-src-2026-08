#!/usr/bin/env python3
"""Export the PostGIS tables to Parquet, keeping the osm2pgsql schema.

The Parquet files are a mirror for reading: the HuggingFace dataset viewer can
preview them and DuckDB can query them with no server. They are not the
evaluation target; PostGIS is. Two column types cannot travel unchanged:

    way   geometry -> WKB bytes, still EPSG:3857
    tags  hstore   -> JSON text

Every other column keeps its osm2pgsql name and type, so a query written
against planet_osm_point in PostGIS reads almost the same here.

DuckDB runs in its own pinned container (1.5.5), so the version is part of the
repository rather than whatever happens to be installed.

The export lands in tmp/parquet-export/, not in data/parquet/. It is in the
order PostGIS returns rows and has no GeoParquet metadata; 08 turns it into
the published layout and 09 checks the two against each other.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKER = ROOT / "docker"
OUT = ROOT / "tmp" / "parquet-export"
TABLES = ["planet_osm_point", "planet_osm_line", "planet_osm_polygon",
          "planet_osm_roads"]

# Inside the containers: the loader's bind mount and the compose network.
IN_CONTAINER_OUT = "/work/tmp/parquet-export"
DSN = "host=db port=5432 dbname=osm user=osm password=osm"


def psql(sql: str) -> str:
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "osm",
         "-d", "osm", "-tAc", sql],
        cwd=DOCKER, check=True, capture_output=True, text=True).stdout


def select_for(table: str) -> str:
    cols = psql("select column_name from information_schema.columns "
                f"where table_name='{table}' order by ordinal_position;").split()
    parts = []
    for c in cols:
        if c == "way":
            parts.append('ST_AsBinary("way") AS "way"')
        elif c == "tags":
            parts.append('hstore_to_json("tags")::text AS "tags"')
        else:
            parts.append(f'"{c}"')
    return f'SELECT {", ".join(parts)} FROM public."{table}"'


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    stmts = [f"ATTACH '{DSN}' AS pg (TYPE postgres, READ_ONLY);"]
    for t in TABLES:
        inner = select_for(t).replace("'", "''")
        stmts.append(
            f"COPY (SELECT * FROM postgres_query(pg, '{inner}')) "
            f"TO '{IN_CONTAINER_OUT}/{t}.parquet' "
            f"(FORMAT parquet, COMPRESSION zstd);")

    subprocess.run(
        ["docker", "compose", "run", "--rm", "duckdb",
         "duckdb", "-c", "\n".join(stmts)],
        cwd=DOCKER, check=True)

    for t in TABLES:
        p = OUT / f"{t}.parquet"
        print(f"  {p.name}: {p.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
