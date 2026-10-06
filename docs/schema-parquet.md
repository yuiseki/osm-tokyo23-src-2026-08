# The Parquet mirror

What `scripts/07_export_parquet.py` writes out of PostGIS, and what
`scripts/08_relayout_parquet.py` makes of it for publishing.

The HuggingFace dataset viewer cannot read a pbf, so the records are also
published as Parquet. PostGIS is the evaluation target; this is a mirror for
reading. The rows are the same; two columns change shape because their types
have no Parquet equivalent.

## Pinned version

| | version | pinned in |
|---|---|---|
| DuckDB, the export (07) | 1.5.5 | `docker/Dockerfile.duckdb`, sha256 verified |
| DuckDB, the relayout and its check (08, 09) | 1.5.6 | `pyproject.toml`, `uv.lock` |

The 1.5 line is required. Earlier lines misbehave, so DuckDB runs from a
pinned container or a locked environment rather than from whatever is
installed on the machine.

## Files

| file | rows | bytes, published | bytes, export of 07 |
|---|---|---|---|
| `parquet/planet_osm_point.parquet` | 306,642 | 17,099,597 | 9,914,808 |
| `parquet/planet_osm_line.parquet` | 347,735 | 34,177,913 | 25,952,829 |
| `parquet/planet_osm_polygon.parquet` | 1,437,105 | 151,115,214 | 118,645,387 |
| `parquet/planet_osm_roads.parquet` | 33,113 | 5,354,011 | 4,435,958 |

zstd compressed. Row counts match PostGIS in all four tables. The export of 07
goes to `tmp/parquet-export/` and is not published; 08 sorts it along a
Hilbert curve, adds a `bbox` column and GeoParquet 1.1.0 metadata, and writes
row groups of about 4 MiB. The difference in size is the `bbox` column, whose
doubles barely compress. The README has the layout and the measurements
behind the row group size.

One file per table, because the schema is four tables. They cannot be
concatenated: `planet_osm_point` has `capital` and `ele` where the others have
`tracktype` and `way_area`, and the geometry types differ. The dataset card
declares them as four configs so the viewer offers them as a list.

## The two converted columns

| column | PostGIS | Parquet |
|---|---|---|
| `way` | `geometry(*, 3857)` | WKB bytes, still EPSG:3857, declared in the GeoParquet metadata |
| `tags` | `hstore` | JSON text |

The other 68 columns keep the names and types osm2pgsql gave them, in the same
order, and the projection is unchanged, so a query written for PostGIS reads
almost the same here. The published files add one column after them, `bbox`,
a struct of `xmin`, `ymin`, `xmax`, `ymax` equal to `ST_Extent(way)`.

## Reading it in DuckDB

```sql
LOAD spatial;
SET geometry_always_xy = true;
CREATE VIEW planet_osm_point   AS SELECT * FROM read_parquet('planet_osm_point.parquet');
CREATE VIEW planet_osm_polygon AS SELECT * FROM read_parquet('planet_osm_polygon.parquet');
```

Name the views after the osm2pgsql tables and most PostGIS SQL ports directly.

### Three differences

Geometry needs no wrapping. The published files declare `way` in their
GeoParquet metadata, so DuckDB 1.5 reads it as `GEOMETRY('EPSG:3857')` and
`ST_Within(p.way, w.way)` is written as in PostGIS. `ST_GeomFromWKB(way)`, which
the export of 07 needed, is a type error against them. To see the raw WKB,
`SET enable_geoparquet_conversion = false` first.

Tags are JSON, not hstore, so `->>` rather than `->`, and the parentheses
are not optional. `->>` binds looser than `=`, and without them DuckDB tries to
cast `tags` to a boolean and fails with
`Could not convert string '{}' to BOOL`.

```sql
-- PostGIS
WHERE tags->'operator:en' = 'East Japan Railway'
-- DuckDB
WHERE (tags->>'operator:en') = 'East Japan Railway'
```

There is no `geography` type. `ST_Distance_Sphere` accepts only points, so
project to metres for a true distance. For Tokyo, EPSG:32654.

```sql
-- PostGIS
ST_Distance(a::geography, b::geography)
-- DuckDB
ST_Distance(ST_Transform(a,'EPSG:3857','EPSG:32654'),
            ST_Transform(b,'EPSG:3857','EPSG:32654'))
```

## Agreement across the three engines

Cafes per ward, `amenity=cafe` nodes:

| ward | PostGIS | Overpass | DuckDB |
|---|---|---|---|
| 千代田区 | 507 | 507 | 507 |
| 渋谷区 | 477 | 477 | 477 |
| 世田谷区 | 258 | 258 | 258 |
| 荒川区 | 44 | 44 | 44 |

All 23 wards agree.

JR Shibuya station to the boundary of Yoyogi Park, and the park's area:

| | PostGIS | DuckDB |
|---|---|---|
| distance, in metres | 802.9 m (geography) | 802.7 m (EPSG:32654) |
| distance, raw 3857 | 991.0 m | 991.0 m |
| area | 533,447 m² (geography) | 533,203 m² (EPSG:32654) |

The metric figures differ by the choice of projection: 0.2 m and 244 m². Read
off the raw 3857 geometry the two agree exactly.
