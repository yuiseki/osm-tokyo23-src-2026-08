#!/usr/bin/env python3
"""Check the published Parquet against the export of 07 it was made from.

For each table, against tmp/parquet-export/ as the reference:

    schema    the 70 osm2pgsql columns unchanged in name, type and position,
              then one bbox struct of doubles
    rows      the same multiset: row count, and the sum and xor of a hash over
              every column of every row (way as its WKB bytes, FLOAT columns
              as text so that -0.0 is not 0.0); -0.0 and NaN counted per
              FLOAT column as well
    order     (Hilbert key of bbox, osm_id) never decreases down the file
    covering  bbox equals ST_Extent(way) on every row
    geo       exactly one GeoParquet 1.1.0 `geo` key, and it equals the
              metadata recomputed from the data: types, extent, covering,
              EPSG:3857 PROJJSON
    groups    every row group but the last has the planned row count

The reference is read, never written. Exits 1 on any problem.

    uv run scripts/09_verify_parquet.py
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import parquet_layout as pl  # noqa: E402

TABLES = ["planet_osm_point", "planet_osm_line", "planet_osm_polygon",
          "planet_osm_roads"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", type=Path, default=ROOT / "tmp" / "parquet-export")
    ap.add_argument("--new", type=Path, default=ROOT / "data" / "parquet")
    ap.add_argument("--tables", nargs="+", default=TABLES, choices=TABLES)
    ap.add_argument("--memory-limit", default="16GB")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--temp-directory", type=Path, default=ROOT / "tmp" / "duckdb_tmp")
    a = ap.parse_args()

    a.temp_directory.mkdir(parents=True, exist_ok=True)
    con = pl.connect(a.memory_limit, a.threads, a.temp_directory)
    failed = False
    for t in a.tables:
        ref, new = a.reference / f"{t}.parquet", a.new / f"{t}.parquet"
        t0 = time.time()
        size, n = con.sql(f"""
            SELECT sum(total_uncompressed_size), any_value(f.num_rows)
            FROM parquet_metadata('{ref}') m, parquet_file_metadata('{ref}') f
        """).fetchone()
        problems = pl.verify(con, ref, new, rows_per_group=pl.rows_per_group(int(size), int(n)))
        con.execute("SET enable_geoparquet_conversion = true")
        seen_as = con.sql(f"SELECT typeof(way) FROM read_parquet('{new}') LIMIT 1").fetchone()[0]
        if seen_as != "GEOMETRY('EPSG:3857')":
            problems.append(f"interop: DuckDB reads way as {seen_as}, not GEOMETRY('EPSG:3857')")
        status = "ok" if not problems else "FAILED"
        print(f"== {t}: {status} ({n:,} rows, {time.time() - t0:.0f}s)")
        for p in problems:
            print(f"  {p}")
        failed |= bool(problems)
    print()
    print("すべて通過" if not failed else "検証に失敗した項目があります")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
