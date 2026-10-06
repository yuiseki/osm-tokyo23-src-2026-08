"""Lay out the osm2pgsql Parquet export for spatial reading, and check it.

07 writes each table in whatever order PostGIS hands the rows over, with
`way` as bare WKB and nothing that says what the numbers in it mean. A bbox
query against that has no statistic to prune on and reads every row group.
This module rewrites a table so that it can be pruned, without changing any
value in it:

- Rows are sorted by the Hilbert index of the centre of each geometry's
  bounding box, then by osm_id, then by the WKB bytes. The curve covers the
  whole EPSG:3857 square, so the key means the same thing in every table and
  does not depend on the data; at 16 bits an axis a cell is about 611 m of
  Web Mercator, far finer than any row group.
- A `bbox` struct column (xmin, ymin, xmax, ymax, in EPSG:3857 metres) is
  appended after the osm2pgsql columns. Its row group statistics are what a
  reader prunes on.
- GeoParquet 1.1.0 `geo` metadata names `way` as the primary geometry, WKB,
  with its geometry types, its extent, the `bbox` covering, and the CRS as
  the PROJJSON of EPSG:3857 taken from the PROJ database inside DuckDB.

No sort key is a DOUBLE column. DuckDB 1.5.6 hands -0.0 back as 0.0 from a
DOUBLE used directly as an ORDER BY key, so the only keys are the UINTEGER
Hilbert index, the BIGINT osm_id and the BLOB way.

`way` stays a BLOB in the file. A reader that understands GeoParquet (DuckDB
1.5 included) will now present it as GEOMETRY('EPSG:3857'); the bytes are
unchanged, and `verify` compares them with the conversion switched off.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import duckdb

GEO_VERSION = "1.1.0"
GEOMETRY_COLUMN = "way"
COVERING_COLUMN = "bbox"
BBOX_FIELDS = ("xmin", "ymin", "xmax", "ymax")

# Half the side of the EPSG:3857 square: pi times the WGS 84 semi-major axis.
WEB_MERCATOR_HALF = math.pi * 6378137.0
HILBERT_BOUNDS = (
    f"ST_MakeBox2D(ST_Point({-WEB_MERCATOR_HALF!r}, {-WEB_MERCATOR_HALF!r}), "
    f"ST_Point({WEB_MERCATOR_HALF!r}, {WEB_MERCATOR_HALF!r}))"
)
# The sort key, computed from the covering column alone so that a reader can
# recompute it without parsing a geometry.
HILBERT_FROM_BBOX = (
    "ST_Hilbert(struct_pack(min_x := bbox.xmin, min_y := bbox.ymin, "
    f"max_x := bbox.xmax, max_y := bbox.ymax)::BOX_2D, {HILBERT_BOUNDS})"
)

# Row groups are sized by bytes, not by a fixed row count: the four tables
# range from about 120 to 500 uncompressed bytes a row, so one row count gives
# row groups that differ four-fold. 4 MiB uncompressed comes to roughly 1-2 MB
# compressed. The Japan sibling uses 32 MiB, but at that size a table of the
# 23 wards is 1 to 9 groups and a box query reads nearly all of it. Measured
# on the polygon table (1 km around Tokyo station, groups kept / bytes read):
# 32 MiB 4/9, 74 MB; 8 MiB 5/34, 23 MB; 4 MiB 6/71, 13 MB; 2 MiB 6/141, 7 MB.
# 2 MiB halves the bytes again but doubles the footer (452 KB to 863 KB), makes
# the files 5% larger, and on roads hits the 2,048-row floor; 4 MiB keeps the
# footer under 0.3% of each file.
TARGET_ROW_GROUP_BYTES = 4 * 1024 * 1024
BBOX_BYTES_PER_ROW = 4 * 8
DUCKDB_VECTOR = 2048

_TYPE_NAMES = {
    "POINT": "Point",
    "LINESTRING": "LineString",
    "POLYGON": "Polygon",
    "MULTIPOINT": "MultiPoint",
    "MULTILINESTRING": "MultiLineString",
    "MULTIPOLYGON": "MultiPolygon",
    "GEOMETRYCOLLECTION": "GeometryCollection",
}


def connect(memory_limit: str = "16GB", threads: int = 8,
            temp_directory: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("LOAD spatial")
    con.execute(f"SET memory_limit = '{memory_limit}'")
    con.execute(f"SET threads = {int(threads)}")
    if temp_directory is not None:
        con.execute(f"SET temp_directory = '{sql_str(str(temp_directory))}'")
    return con


def sql_str(s: str) -> str:
    """Escape a value for a single-quoted SQL string literal."""
    return s.replace("'", "''")


def ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def rows_per_group(uncompressed_bytes: int, num_rows: int,
                   target: int = TARGET_ROW_GROUP_BYTES) -> int:
    """Rows that fill `target` uncompressed bytes, bbox column included.

    The bytes a row are the average over the export, from its footer.

    Rounded down to DuckDB's 2,048-row vector, because the writer only cuts a
    row group at a vector boundary and would otherwise round up past target.
    """
    per_row = uncompressed_bytes / max(num_rows, 1) + BBOX_BYTES_PER_ROW
    rows = int(target // per_row) // DUCKDB_VECTOR * DUCKDB_VECTOR
    return max(rows, DUCKDB_VECTOR)


_rows_for_bytes = rows_per_group  # relayout's keyword argument shadows the name


def geoparquet_type(name: str, has_z: bool) -> str:
    return _TYPE_NAMES[name.upper()] + (" Z" if has_z else "")


def projjson_3857(con: duckdb.DuckDBPyConnection) -> dict:
    row = con.sql("SELECT projjson FROM duckdb_coordinate_systems() "
                  "WHERE auth_name = 'EPSG' AND auth_code = '3857'").fetchone()
    if row is None:
        raise RuntimeError("EPSG:3857 is not in DuckDB's PROJ database")
    return json.loads(row[0])


def bbox_3857(lon_min: float, lat_min: float, lon_max: float,
              lat_max: float) -> tuple[float, float, float, float]:
    """A lon/lat box in EPSG:3857 metres, for querying the covering column."""
    r = 6378137.0

    def x(lon: float) -> float:
        return r * math.radians(lon)

    def y(lat: float) -> float:
        return r * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))

    return (x(lon_min), y(lat_min), x(lon_max), y(lat_max))


def describe(con: duckdb.DuckDBPyConnection, path: Path) -> list[tuple[str, str]]:
    rows = con.sql(f"DESCRIBE SELECT * FROM read_parquet('{sql_str(str(path))}')").fetchall()
    return [(r[0], r[1]) for r in rows]


def read_geo(con: duckdb.DuckDBPyConnection, path: Path) -> dict | None:
    rows = geo_values(con, path)
    return json.loads(rows[0]) if rows else None


def geo_values(con: duckdb.DuckDBPyConnection, path: Path) -> list[str]:
    return [r[0] for r in con.sql(
        f"SELECT decode(value) FROM parquet_kv_metadata('{sql_str(str(path))}') "
        "WHERE decode(key) = 'geo'").fetchall()]


def geo_metadata(types: list[str], extent: tuple[float, float, float, float],
                 crs: dict) -> dict:
    return {
        "version": GEO_VERSION,
        "primary_column": GEOMETRY_COLUMN,
        "columns": {
            GEOMETRY_COLUMN: {
                "encoding": "WKB",
                "geometry_types": types,
                "crs": crs,
                "bbox": list(extent),
                "covering": {
                    "bbox": {f: [COVERING_COLUMN, f] for f in BBOX_FIELDS},
                },
            },
        },
    }


def _types_and_extent(con: duckdb.DuckDBPyConnection, path: Path,
                      ) -> tuple[list[str], tuple[float, float, float, float]]:
    src = sql_str(str(path))
    found = con.sql(f"""
        SELECT DISTINCT ST_GeometryType(g)::VARCHAR, ST_HasZ(g)
        FROM (SELECT ST_GeomFromWKB(way) g FROM read_parquet('{src}') WHERE way IS NOT NULL)
    """).fetchall()
    types = sorted(geoparquet_type(t, z) for t, z in found)
    extent = con.sql(f"""
        SELECT min(ST_XMin(e)), min(ST_YMin(e)), max(ST_XMax(e)), max(ST_YMax(e))
        FROM (SELECT ST_Extent(ST_GeomFromWKB(way)) e FROM read_parquet('{src}'))
    """).fetchone()
    return types, tuple(extent)


def relayout(con: duckdb.DuckDBPyConnection, src: Path, dst: Path,
             rows_per_group: int | None = None) -> dict:
    """Write `src` (an export of 07) to `dst` in Hilbert order, as GeoParquet."""
    con.execute("SET enable_geoparquet_conversion = false")
    s = sql_str(str(src))
    cols = describe(con, src)
    names = [n for n, _ in cols]
    if read_geo(con, src) is not None or COVERING_COLUMN in names:
        raise ValueError(f"{src} already has geo metadata or a bbox column; "
                         "relayout reads the plain export of 07")
    if dict(cols).get(GEOMETRY_COLUMN) != "BLOB" or "osm_id" not in names:
        raise ValueError(f"{src} has no BLOB `way` and osm_id")

    if rows_per_group is None:
        size, n = con.sql(f"""
            SELECT sum(total_uncompressed_size), any_value(f.num_rows)
            FROM parquet_metadata('{s}') m, parquet_file_metadata('{s}') f
        """).fetchone()
        rows_per_group = _rows_for_bytes(int(size), int(n))

    types, extent = _types_and_extent(con, src)
    geo = geo_metadata(types, extent, projjson_3857(con))

    col_list = ", ".join(ident(n) for n in names)
    bbox = ("CASE WHEN e IS NULL THEN NULL ELSE struct_pack(xmin := ST_XMin(e), "
            "ymin := ST_YMin(e), xmax := ST_XMax(e), ymax := ST_YMax(e)) END")
    tmp = dst.with_name(dst.name + ".part")
    con.execute(f"""
        COPY (
          SELECT {col_list}, bbox FROM (
            SELECT {col_list}, {bbox} AS bbox
            FROM (SELECT *, ST_Extent(ST_GeomFromWKB(way)) AS e FROM read_parquet('{s}')))
          ORDER BY {HILBERT_FROM_BBOX}, osm_id, way
        ) TO '{sql_str(str(tmp))}' (
          FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {int(rows_per_group)},
          KV_METADATA {{geo: '{sql_str(json.dumps(geo))}'}})
    """)
    tmp.replace(dst)
    return {"rows_per_group": rows_per_group, "geometry_types": types, "extent": extent}


def _row_digest(con: duckdb.DuckDBPyConnection, path: Path,
                cols: list[tuple[str, str]]) -> tuple:
    """Count and order-free digests over every column of every row.

    FLOAT and DOUBLE go through VARCHAR so that -0.0 and 0.0, which compare
    and hash equal as numbers, count as different values here.
    """
    parts = []
    for n, t in cols:
        parts.append(f"{ident(n)}::VARCHAR" if t in ("FLOAT", "DOUBLE") else ident(n))
    h = f"hash({', '.join(parts)})"
    return con.sql(f"""
        SELECT count(*), sum({h}::HUGEINT), bit_xor({h})
        FROM read_parquet('{sql_str(str(path))}')
    """).fetchone()


def verify(con: duckdb.DuckDBPyConnection, reference: Path, new: Path,
           rows_per_group: int | None = None) -> list[str]:
    """Problems with `new` as a relayout of `reference`; empty when it is one."""
    con.execute("SET enable_geoparquet_conversion = false")
    problems: list[str] = []
    r, n = sql_str(str(reference)), sql_str(str(new))

    ref_cols = describe(con, reference)
    new_cols = describe(con, new)
    want_bbox = (COVERING_COLUMN,
                 "STRUCT(xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)")
    if new_cols[:-1] != ref_cols or new_cols[-1:] != [want_bbox]:
        problems.append("schema: the osm2pgsql columns must be unchanged, "
                        "followed by one bbox struct of doubles")
        return problems

    a, b = _row_digest(con, reference, ref_cols), _row_digest(con, new, ref_cols)
    if a[0] != b[0]:
        problems.append(f"multiset: {a[0]:,} rows in the reference, {b[0]:,} in the new file")
    elif a != b:
        problems.append("multiset: same row count but different rows (row digests differ)")

    for name, t in ref_cols:
        if t not in ("FLOAT", "DOUBLE"):
            continue
        q = (f"SELECT count(*) FILTER (WHERE {ident(name)}::VARCHAR = '-0.0'), "
             f"count(*) FILTER (WHERE isnan({ident(name)})) FROM read_parquet('{{}}')")
        before = con.sql(q.format(r)).fetchone()
        after = con.sql(q.format(n)).fetchone()
        if before != after:
            problems.append(f"-0.0/NaN in {name}: reference {before}, new {after}")

    out_of_order = con.sql(f"""
        SELECT count(*) FROM (
          SELECT k, osm_id, lag(k) OVER w AS pk, lag(osm_id) OVER w AS po
          FROM (SELECT {HILBERT_FROM_BBOX} AS k, osm_id, file_row_number
                FROM read_parquet('{n}', file_row_number = true))
          WINDOW w AS (ORDER BY file_row_number))
        WHERE k < pk OR (k = pk AND osm_id < po)
    """).fetchone()[0]
    if out_of_order:
        problems.append(f"order: {out_of_order:,} rows break (Hilbert key, osm_id) order")

    bad_cover = con.sql(f"""
        SELECT count(*) FROM (
          SELECT bbox, ST_Extent(ST_GeomFromWKB(way)) e FROM read_parquet('{n}'))
        WHERE bbox.xmin IS DISTINCT FROM ST_XMin(e) OR bbox.ymin IS DISTINCT FROM ST_YMin(e)
           OR bbox.xmax IS DISTINCT FROM ST_XMax(e) OR bbox.ymax IS DISTINCT FROM ST_YMax(e)
    """).fetchone()[0]
    if bad_cover:
        problems.append(f"covering: {bad_cover:,} rows whose bbox is not ST_Extent(way)")

    problems += _check_geo(con, new)

    if rows_per_group is not None:
        sizes = [x[0] for x in con.sql(f"""
            SELECT row_group_num_rows FROM parquet_metadata('{n}')
            GROUP BY row_group_id, row_group_num_rows ORDER BY row_group_id
        """).fetchall()]
        if any(s != rows_per_group for s in sizes[:-1]) or (sizes and sizes[-1] > rows_per_group):
            problems.append(f"row groups: expected {rows_per_group:,} rows each, got {sizes}")
    return problems


def _check_geo(con: duckdb.DuckDBPyConnection, path: Path) -> list[str]:
    values = geo_values(con, path)
    if len(values) != 1:
        return [f"geo: expected exactly one geo metadata key, found {len(values)}"]
    try:
        geo = json.loads(values[0])
    except json.JSONDecodeError as e:
        return [f"geo: not JSON ({e})"]
    types, extent = _types_and_extent(con, path)
    want = geo_metadata(types, extent, projjson_3857(con))
    if geo != want:
        diff = sorted(k for k in set(geo["columns"].get(GEOMETRY_COLUMN, {}))
                      | set(want["columns"][GEOMETRY_COLUMN])
                      if geo["columns"].get(GEOMETRY_COLUMN, {}).get(k)
                      != want["columns"][GEOMETRY_COLUMN].get(k))
        return [f"geo: metadata does not describe the data (differs in {diff or 'top level'})"]
    return []


def row_groups_hit(con: duckdb.DuckDBPyConnection, path: Path,
                   box: tuple[float, float, float, float] | None = None,
                   equals: tuple[str, str] | None = None) -> dict:
    """Row groups a reader cannot skip, from the footer alone.

    `box` prunes on the min/max of the covering columns; `equals` (column,
    value) prunes on that column's min/max and then its bloom filter. Bytes
    are the compressed size of every column chunk in the surviving groups,
    which is what `SELECT *` reads.
    """
    p = sql_str(str(path))
    groups = con.sql(f"""
        SELECT row_group_id, sum(total_compressed_size)
        FROM parquet_metadata('{p}') GROUP BY 1 ORDER BY 1
    """).fetchall()
    keep = {g for g, _ in groups}
    if box is not None:
        xmin, ymin, xmax, ymax = box
        stats = con.sql(f"""
            SELECT row_group_id,
              max(CASE WHEN path_in_schema = 'bbox, xmin' THEN TRY_CAST(stats_min AS DOUBLE) END),
              max(CASE WHEN path_in_schema = 'bbox, ymin' THEN TRY_CAST(stats_min AS DOUBLE) END),
              max(CASE WHEN path_in_schema = 'bbox, xmax' THEN TRY_CAST(stats_max AS DOUBLE) END),
              max(CASE WHEN path_in_schema = 'bbox, ymax' THEN TRY_CAST(stats_max AS DOUBLE) END)
            FROM parquet_metadata('{p}') GROUP BY 1
        """).fetchall()
        for g, gxmin, gymin, gxmax, gymax in stats:
            if None in (gxmin, gymin, gxmax, gymax):
                continue  # no statistic, cannot skip
            if gxmin > xmax or gymin > ymax or gxmax < xmin or gymax < ymin:
                keep.discard(g)
    if equals is not None:
        col, val = equals
        v = sql_str(val)
        for g, lo, hi in con.sql(f"""
            SELECT row_group_id, stats_min, stats_max FROM parquet_metadata('{p}')
            WHERE path_in_schema = '{sql_str(col)}'
        """).fetchall():
            if lo is not None and hi is not None and (val < lo or val > hi):
                keep.discard(g)
        for g, excluded in con.sql(
                f"SELECT row_group_id, bloom_filter_excludes "
                f"FROM parquet_bloom_probe('{p}', '{sql_str(col)}', '{v}')").fetchall():
            if excluded:
                keep.discard(g)
    total = sum(b for _, b in groups)
    return {
        "row_groups": len(keep),
        "total_row_groups": len(groups),
        "bytes": sum(b for g, b in groups if g in keep),
        "total_bytes": total,
    }
