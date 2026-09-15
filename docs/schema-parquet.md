# The Parquet mirror

What `scripts/07_export_parquet.py` writes out of PostGIS.

The HuggingFace dataset viewer cannot read a pbf, so the records are also
published as Parquet. PostGIS is the evaluation target; this is a mirror for
reading. The rows are the same; two columns change shape because their types
have no Parquet equivalent.

## Pinned version

| | version | pinned in |
|---|---|---|
| DuckDB | 1.5.5 | `docker/Dockerfile.duckdb`, sha256 verified |

The 1.5 line is required. Earlier lines misbehave, so DuckDB runs from a
pinned container rather than from whatever is installed on the machine.

## Files

| file | rows | bytes |
|---|---|---|
| `parquet/planet_osm_point.parquet` | 306,642 | 9,914,808 |
| `parquet/planet_osm_line.parquet` | 347,735 | 25,952,829 |
| `parquet/planet_osm_polygon.parquet` | 1,437,105 | 118,645,387 |
| `parquet/planet_osm_roads.parquet` | 33,113 | 4,435,958 |

zstd compressed. Row counts match PostGIS in all four tables.

One file per table, because the schema is four tables. They cannot be
concatenated: `planet_osm_point` has `capital` and `ele` where the others have
`tracktype` and `way_area`, and the geometry types differ. The dataset card
declares them as four configs so the viewer offers them as a list.

## The two converted columns

| column | PostGIS | Parquet |
|---|---|---|
| `way` | `geometry(*, 3857)` | WKB bytes, still EPSG:3857 |
| `tags` | `hstore` | JSON text |

The other 68 columns keep the names and types osm2pgsql gave them, and the
projection is unchanged, so a query written for PostGIS reads almost the same
here.

## Reading it in DuckDB

```sql
LOAD spatial;
SET geometry_always_xy = true;
CREATE VIEW planet_osm_point   AS SELECT * FROM read_parquet('planet_osm_point.parquet');
CREATE VIEW planet_osm_polygon AS SELECT * FROM read_parquet('planet_osm_polygon.parquet');
```

Name the views after the osm2pgsql tables and most PostGIS SQL ports directly.

### Three differences

Geometry is WKB, so wrap it.

```sql
-- PostGIS
ST_Within(p.way, w.way)
-- DuckDB
ST_Within(ST_GeomFromWKB(p.way), ST_GeomFromWKB(w.way))
```

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
