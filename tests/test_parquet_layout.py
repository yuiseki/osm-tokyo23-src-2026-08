"""Tests for the spatial relayout of the osm2pgsql Parquet export.

The fixture is a miniature export in the shape 07 writes: osm2pgsql column
names (one with a colon in it), a FLOAT way_area carrying -0.0, a JSON tags
column, and `way` as EPSG:3857 WKB in a BLOB with no `geo` metadata. Rows are
written in an order that is not spatial, and two rows share an osm_id the way
the pieces of a multipolygon do.
"""

import json
from pathlib import Path

import duckdb
import pytest

import parquet_layout as pl

# (osm_id, name, way_area, lon, lat). EPSG:3857 is made by DuckDB from these.
PLACES = [
    (101, "羽田", 12.5, 139.78, 35.55),
    (-202, "代々木公園の一部", -0.0, 139.695, 35.671),
    (-202, "代々木公園の一部", 0.0, 139.697, 35.672),
    (303, "東京駅", None, 139.7671, 35.6812),
    (404, None, 3.25, 139.70, 35.69),
    (505, "赤羽", -0.0, 139.7205, 35.7778),
    (606, "葛西", 7.0, 139.8597, 35.6636),
    (707, "田園調布", 1.0, 139.6675, 35.5878),
]


def _con() -> duckdb.DuckDBPyConnection:
    return pl.connect(memory_limit="1GB", threads=2)


@pytest.fixture
def reference(tmp_path: Path) -> Path:
    """A small unsorted export, polygons around each place."""
    con = _con()
    values = ", ".join(
        f"({i}, {oid}, {'NULL' if n is None else repr(n)}, "
        f"{'NULL' if a is None else ('-0.0' if str(a) == '-0.0' else a)}::FLOAT, {lon}, {lat})"
        for i, (oid, n, a, lon, lat) in enumerate(PLACES)
    )
    path = tmp_path / "ref" / "planet_osm_polygon.parquet"
    path.parent.mkdir()
    con.execute(f"""
        COPY (
          SELECT osm_id,
                 'yes' AS "addr:housenumber",
                 name,
                 i::INTEGER AS z_order,
                 way_area,
                 '{{"k": "v"}}' AS tags,
                 ST_AsWKB(ST_Buffer(ST_Transform(ST_Point(lon, lat), 'EPSG:4326',
                          'EPSG:3857', always_xy := true), 50 + i)) AS way
          FROM (VALUES {values}) t(i, osm_id, name, way_area, lon, lat)
          ORDER BY i
        ) TO '{path}' (FORMAT parquet, COMPRESSION zstd)
    """)
    return path


@pytest.fixture
def relaid(reference: Path, tmp_path: Path) -> Path:
    out = tmp_path / "new" / reference.name
    out.parent.mkdir()
    pl.relayout(_con(), reference, out, rows_per_group=2048)
    return out


def test_rows_per_group_targets_uncompressed_bytes():
    # 100 bytes a row plus the 32-byte bbox: 4 MiB holds 31,775 rows,
    # rounded down to DuckDB's 2,048-row vectors.
    assert pl.TARGET_ROW_GROUP_BYTES == 4 * 1024 * 1024
    assert pl.rows_per_group(uncompressed_bytes=100_000, num_rows=1_000) == 30_720
    # The Japan sibling's 32 MiB is still reachable through `target`.
    assert pl.rows_per_group(uncompressed_bytes=1_000_000, num_rows=1_000,
                             target=32 * 1024 * 1024) == 30_720
    assert pl.rows_per_group(uncompressed_bytes=10, num_rows=1) % 2048 == 0
    # Never below one vector, however wide the rows are.
    assert pl.rows_per_group(uncompressed_bytes=10**12, num_rows=1) == 2048


def test_geometry_type_names_follow_geoparquet():
    assert pl.geoparquet_type("POINT", False) == "Point"
    assert pl.geoparquet_type("LINESTRING", False) == "LineString"
    assert pl.geoparquet_type("MULTIPOLYGON", True) == "MultiPolygon Z"
    assert pl.geoparquet_type("GEOMETRYCOLLECTION", False) == "GeometryCollection"


def test_relayout_passes_verification(reference: Path, relaid: Path):
    assert pl.verify(_con(), reference, relaid, rows_per_group=2048) == []


def test_columns_and_types_unchanged_and_bbox_appended(reference: Path, relaid: Path):
    con = _con()
    con.execute("SET enable_geoparquet_conversion = false")
    before = con.sql(f"DESCRIBE SELECT * FROM read_parquet('{reference}')").fetchall()
    after = con.sql(f"DESCRIBE SELECT * FROM read_parquet('{relaid}')").fetchall()
    assert [r[:2] for r in after[:-1]] == [r[:2] for r in before]
    assert after[-1][:2] == (
        "bbox", "STRUCT(xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)")


def test_way_bytes_and_minus_zero_survive(reference: Path, relaid: Path):
    con = _con()
    con.execute("SET enable_geoparquet_conversion = false")
    q = ("SELECT osm_id, way, way_area::VARCHAR FROM read_parquet('{}') "
         "ORDER BY osm_id, way")
    assert con.sql(q.format(relaid)).fetchall() == con.sql(q.format(reference)).fetchall()
    n = con.sql(f"SELECT count(*) FROM read_parquet('{relaid}') "
                "WHERE way_area::VARCHAR = '-0.0'").fetchone()[0]
    assert n == 2


def test_rows_follow_the_hilbert_key(relaid: Path):
    con = _con()
    con.execute("SET enable_geoparquet_conversion = false")
    names = [r[0] for r in con.sql(
        f"SELECT name FROM read_parquet('{relaid}', file_row_number = true) "
        "ORDER BY file_row_number").fetchall()]
    # The input order was by insertion; the output is not.
    assert names != [p[1] for p in PLACES]
    keys = con.sql(f"""
        SELECT {pl.HILBERT_FROM_BBOX}, osm_id
        FROM read_parquet('{relaid}', file_row_number = true) ORDER BY file_row_number
    """).fetchall()
    assert keys == sorted(keys)


def test_covering_equals_extent(relaid: Path):
    con = _con()
    con.execute("SET enable_geoparquet_conversion = false")
    bad = con.sql(f"""
        SELECT count(*) FROM (SELECT bbox, ST_Extent(ST_GeomFromWKB(way)) e
                              FROM read_parquet('{relaid}'))
        WHERE bbox.xmin IS DISTINCT FROM ST_XMin(e) OR bbox.ymin IS DISTINCT FROM ST_YMin(e)
           OR bbox.xmax IS DISTINCT FROM ST_XMax(e) OR bbox.ymax IS DISTINCT FROM ST_YMax(e)
    """).fetchone()[0]
    assert bad == 0


def test_geo_metadata(relaid: Path):
    con = _con()
    rows = con.sql(f"SELECT decode(key), decode(value) FROM parquet_kv_metadata('{relaid}') "
                   "WHERE decode(key) = 'geo'").fetchall()
    assert len(rows) == 1
    geo = json.loads(rows[0][1])
    assert geo["version"] == "1.1.0"
    assert geo["primary_column"] == "way"
    col = geo["columns"]["way"]
    assert col["encoding"] == "WKB"
    assert col["geometry_types"] == ["Polygon"]
    assert col["crs"]["id"] == {"authority": "EPSG", "code": 3857}
    assert col["crs"]["type"] == "ProjectedCRS"
    assert col["covering"] == {"bbox": {k: ["bbox", k] for k in ("xmin", "ymin", "xmax", "ymax")}}
    xmin, ymin, xmax, ymax = col["bbox"]
    assert xmin < xmax and ymin < ymax
    # Den-en-chofu's west edge and Akabane's north edge, in metres: the
    # centre less or plus the buffer of 57 and 55 m.
    assert 15_547_600 < xmin < 15_547_700
    assert 4_270_100 < ymax < 4_270_200


def test_duckdb_reads_way_as_epsg3857_geometry(relaid: Path):
    con = _con()
    t = con.sql(f"SELECT typeof(way) FROM read_parquet('{relaid}') LIMIT 1").fetchone()[0]
    assert t == "GEOMETRY('EPSG:3857')"


def test_bbox_filter_prunes_with_the_covering(tmp_path: Path):
    # Three clusters of 4,096 points written interleaved, so that before the
    # relayout every row group holds all three places: Shinjuku, Tokyo
    # station and Haneda, a few kilometres apart.
    con = _con()
    src = tmp_path / "clusters.parquet"
    con.execute(f"""
        COPY (
          SELECT i::BIGINT AS osm_id, NULL::VARCHAR AS name,
                 ST_AsWKB(ST_Transform(ST_Point(c.lon + (i // 3) * 1e-6, c.lat), 'EPSG:4326',
                          'EPSG:3857', always_xy := true)) AS way
          FROM range(3 * 4096) r(i)
          JOIN (VALUES (0, 139.70, 35.69), (1, 139.7671, 35.6812), (2, 139.78, 35.55))
               c(k, lon, lat) ON i % 3 = c.k
          ORDER BY i
        ) TO '{src}' (FORMAT parquet, ROW_GROUP_SIZE 4096)
    """)
    out = tmp_path / "clusters_relaid.parquet"
    pl.relayout(con, src, out, rows_per_group=4096)
    tokyo = pl.bbox_3857(139.76, 35.67, 139.78, 35.69)
    hit = pl.row_groups_hit(con, out, tokyo)
    assert hit["total_row_groups"] == 3
    assert hit["row_groups"] == 1
    assert 0 < hit["bytes"] < hit["total_bytes"]
    n = con.sql(f"""SELECT count(*) FROM read_parquet('{out}')
                    WHERE bbox.xmin <= {tokyo[2]} AND bbox.xmax >= {tokyo[0]}
                      AND bbox.ymin <= {tokyo[3]} AND bbox.ymax >= {tokyo[1]}""").fetchone()[0]
    assert n == 4096


def test_bbox_3857_matches_proj():
    con = _con()
    box = pl.bbox_3857(139.7, 35.6, 139.8, 35.7)
    x0, y0 = con.sql("SELECT ST_X(p), ST_Y(p) FROM (SELECT ST_Transform(ST_Point(139.7, 35.6), "
                     "'EPSG:4326', 'EPSG:3857', always_xy := true) p)").fetchone()
    assert box[0] == pytest.approx(x0, abs=1e-6)
    assert box[1] == pytest.approx(y0, abs=1e-6)


def _rewrite(relaid: Path, out: Path, star: str = "*", order: str = "file_row_number",
             geo: bool = True) -> None:
    """Copy the relaid file through a change, keeping or dropping its `geo` key."""
    con = _con()
    con.execute("SET enable_geoparquet_conversion = false")
    kv = (f", KV_METADATA {{geo: '{pl.sql_str(json.dumps(pl.read_geo(con, relaid)))}'}}"
          if geo else "")
    con.execute(f"""
        COPY (SELECT {star} FROM (SELECT * EXCLUDE (file_row_number)
                                  FROM read_parquet('{relaid}', file_row_number = true)
                                  ORDER BY {order}))
        TO '{out}' (FORMAT parquet, ROW_GROUP_SIZE 2048{kv})
    """)


def test_verify_catches_a_changed_value(reference: Path, relaid: Path, tmp_path: Path):
    out = tmp_path / "tampered.parquet"
    _rewrite(relaid, out, "* REPLACE (CASE WHEN osm_id = 303 THEN '東京' ELSE name END AS name)")
    problems = pl.verify(_con(), reference, out, rows_per_group=2048)
    assert any("multiset" in p for p in problems)


def test_verify_catches_minus_zero_lost(reference: Path, relaid: Path, tmp_path: Path):
    out = tmp_path / "flat.parquet"
    _rewrite(relaid, out, "* REPLACE (abs(way_area) AS way_area)")
    problems = pl.verify(_con(), reference, out, rows_per_group=2048)
    assert any("-0.0" in p for p in problems)


def test_verify_catches_wrong_order(reference: Path, relaid: Path, tmp_path: Path):
    out = tmp_path / "shuffled.parquet"
    _rewrite(relaid, out, order="file_row_number DESC")
    problems = pl.verify(_con(), reference, out, rows_per_group=2048)
    assert any("order" in p for p in problems)
    assert not any("multiset" in p for p in problems)


def test_verify_catches_a_wrong_covering(reference: Path, relaid: Path, tmp_path: Path):
    out = tmp_path / "shifted.parquet"
    _rewrite(relaid, out, "* REPLACE (struct_pack(xmin := bbox.xmin + 1, ymin := bbox.ymin, "
                          "xmax := bbox.xmax, ymax := bbox.ymax) AS bbox)")
    problems = pl.verify(_con(), reference, out, rows_per_group=2048)
    assert any("covering" in p for p in problems)


def test_verify_catches_missing_geo_metadata(reference: Path, relaid: Path, tmp_path: Path):
    out = tmp_path / "bare.parquet"
    _rewrite(relaid, out, geo=False)
    problems = pl.verify(_con(), reference, out, rows_per_group=2048)
    assert any("geo" in p for p in problems)


def test_relayout_refuses_an_already_relaid_file(relaid: Path, tmp_path: Path):
    with pytest.raises(ValueError, match="already"):
        pl.relayout(_con(), relaid, tmp_path / "again.parquet", rows_per_group=2048)
