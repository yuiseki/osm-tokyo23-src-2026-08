#!/usr/bin/env python3
"""Turn the export of 07 into the published layout.

07 writes the four tables to tmp/parquet-export/ in whatever order PostGIS
hands them over, with `way` as bare WKB. This rewrites each one into
data/parquet/:

    rows    sorted by the Hilbert index of each geometry's bbox centre
            (over the whole EPSG:3857 square), then osm_id, then way
    bbox    a struct column (xmin, ymin, xmax, ymax) in EPSG:3857 metres,
            appended after the 70 osm2pgsql columns
    geo     GeoParquet 1.1.0 metadata: way is the primary geometry, WKB,
            its geometry types and extent, the bbox covering, and the
            EPSG:3857 PROJJSON
    groups  about 4 MiB uncompressed each (1-2 MB compressed), sized
            from the table's own bytes per row

Every value of every osm2pgsql column is unchanged; 09 checks that.

    uv run scripts/08_relayout_parquet.py
    uv run scripts/08_relayout_parquet.py --tables planet_osm_roads
"""
import argparse
import hashlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import parquet_layout as pl  # noqa: E402

TABLES = ["planet_osm_point", "planet_osm_line", "planet_osm_polygon",
          "planet_osm_roads"]
DATA = ROOT / "data"


def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_checksums(dst: Path) -> None:
    """data/parquet/checksums.md5, paths relative to data/ as the Hub has them."""
    lines = []
    for t in TABLES:
        p = dst / f"{t}.parquet"
        if p.exists():
            lines.append(f"{md5(p)}  {p.relative_to(DATA)}")
    boundary = DATA / "tokyo23_osm_boundary.geojson"
    if boundary.exists():
        lines.append(f"{md5(boundary)}  {boundary.name}")
    (dst / "checksums.md5").write_text("\n".join(sorted(lines, key=lambda s: s.split()[1]))
                                       + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=ROOT / "tmp" / "parquet-export")
    ap.add_argument("--dst", type=Path, default=DATA / "parquet")
    ap.add_argument("--tables", nargs="+", default=TABLES, choices=TABLES)
    ap.add_argument("--memory-limit", default="16GB")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--temp-directory", type=Path, default=ROOT / "tmp" / "duckdb_tmp")
    a = ap.parse_args()

    a.dst.mkdir(parents=True, exist_ok=True)
    a.temp_directory.mkdir(parents=True, exist_ok=True)
    con = pl.connect(a.memory_limit, a.threads, a.temp_directory)
    for t in a.tables:
        src, dst = a.src / f"{t}.parquet", a.dst / f"{t}.parquet"
        if src.resolve() == dst.resolve():
            raise SystemExit(f"{src}: refusing to rewrite the export in place")
        t0 = time.time()
        info = pl.relayout(con, src, dst)
        groups = con.sql(f"SELECT num_row_groups FROM parquet_file_metadata('{dst}')").fetchone()[0]
        print(f"  {t}: {dst.stat().st_size:,} bytes, {groups} row groups of "
              f"{info['rows_per_group']:,} rows, {info['geometry_types']}, "
              f"{time.time() - t0:.0f}s")
    write_checksums(a.dst)
    print(f"  wrote {a.dst / 'checksums.md5'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
