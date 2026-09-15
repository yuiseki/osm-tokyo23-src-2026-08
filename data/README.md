---
license: odbl
language:
- ja
- en
task_categories:
- table-question-answering
- question-answering
- text-generation
tags:
- openstreetmap
- tokyo
- geospatial
- text-to-sql
- text2sql
- overpass
- postgis
- duckdb
- frozen-snapshot
size_categories:
- 1M<n<10M
configs:
- config_name: planet_osm_point
  data_files: parquet/planet_osm_point.parquet
- config_name: planet_osm_line
  data_files: parquet/planet_osm_line.parquet
- config_name: planet_osm_polygon
  data_files: parquet/planet_osm_polygon.parquet
- config_name: planet_osm_roads
  data_files: parquet/planet_osm_roads.parquet
---

# osm-tokyo23-src-2026-08

A frozen cut of OpenStreetMap covering the 23 special wards of Tokyo, taken
from the planet file of 2026-08-31, together with everything needed to
rebuild the databases it was measured in.

The point is the freezing. A question about a city has an answer only against
a stated snapshot, and an answer computed today against the live API is not
reproducible tomorrow. Here the snapshot is one file with a checksum, and the
tools that read it are pinned by version.

How fast does it drift? A list of 216 OSM ids, resolved a year before this
snapshot and pointing at ordinary Tokyo landmarks, was checked against it:
2 no longer exist. About 1% a year, and the two are not obscure. One is
Shinjuku station, which lost one of its six nodes. The other is Yoyogi Park,
whose only id was deleted when the park was redrawn from a single way into a
multipolygon relation. Same park, same tags, new identity and a new element
type. An id written into a question in 2025 silently stops resolving,
and a distance measured to that park's boundary is measured against a different
polygon than before.

No schema of our own. The tables are osm2pgsql's, `planet_osm_point` and
its three siblings, because that is the layout that models have read for
fifteen years of web maps. Inventing a friendlier one would measure whether a
model can read a novel schema rather than whether it knows OSM and SQL.

## What is here

| file | |
|---|---|
| `tokyo23-260831.osm.pbf` | the frozen extract. md5 `44a4ba2182379c147f20a27ad1b513ef` |
| `tokyo23_osm_boundary.geojson` | the boundary it was cut with |
| `parquet/planet_osm_*.parquet` | the same records in the osm2pgsql schema |
| `provenance.yaml` | every version in the chain, and what was left out |
| `LICENSE` | the ODbL notice and the chain of derivation |

| | nodes | ways | relations |
|---|---|---|---|
| `tokyo23-260831.osm.pbf` | 9,659,934 | 1,776,817 | 19,663 |

| table | rows |
|---|---|
| `planet_osm_point` | 306,642 |
| `planet_osm_line` | 347,735 |
| `planet_osm_polygon` | 1,437,105 |
| `planet_osm_roads` | 33,113 |

## Three ways to query it, one answer

The pbf is the source. Two databases are built from it and the Parquet is a
third reading of the same records. The same question gets the same answer in
all three.

```
tokyo23-260831.osm.pbf
  ├─ PostGIS    osm2pgsql pgsql output    -> text-to-SQL
  │    └─ parquet/                        -> this viewer, and DuckDB
  └─ Overpass   osm3s                     -> text-to-Overpass QL
```

Counting cafes in Chiyoda ward:

```sql
-- PostGIS
SELECT count(*) FROM planet_osm_point p
JOIN planet_osm_polygon w ON ST_Within(p.way, w.way)
WHERE w.boundary='administrative' AND w.admin_level='7' AND w.name='千代田区'
  AND p.amenity='cafe';
```

```overpassql
area["name"="千代田区"]["admin_level"="7"]->.a;
node(area.a)["amenity"="cafe"];
out count;
```

```sql
-- DuckDB, straight off the Parquet
LOAD spatial;
CREATE VIEW planet_osm_point   AS SELECT * FROM read_parquet('planet_osm_point.parquet');
CREATE VIEW planet_osm_polygon AS SELECT * FROM read_parquet('planet_osm_polygon.parquet');
SELECT count(*) FROM planet_osm_point p
JOIN planet_osm_polygon w
  ON ST_Within(ST_GeomFromWKB(p.way), ST_GeomFromWKB(w.way))
WHERE w.boundary='administrative' AND w.admin_level='7' AND w.name='千代田区'
  AND p.amenity='cafe';
```

All three return 507. Run it for every ward and all 23 agree.

The ward polygons are already in `planet_osm_polygon`, tagged
`boundary=administrative` and `admin_level=7`. There is no separate ward table
and none is needed.

## Reading the Parquet

Two columns could not travel as they were. Everything else keeps the name and
type osm2pgsql gave it.

| column | PostGIS | Parquet |
|---|---|---|
| `way` | `geometry(*, 3857)` | WKB bytes, still EPSG:3857 |
| `tags` | `hstore` | JSON text |

Three things to know if you are porting a PostGIS query to DuckDB.

Wrap the geometry: `ST_GeomFromWKB(way)`.

Tags are JSON, so `->>` rather than `->`, and the parentheses are
required. `->>` binds looser than `=`, and without them DuckDB tries to cast
`tags` to a boolean and fails.

```sql
WHERE (tags->>'operator:en') = 'East Japan Railway'
```

DuckDB's spatial extension has no `geography` type and `ST_Distance_Sphere`
takes only points, so project to metres when you need a true distance. For
Tokyo, EPSG:32654.

## The trap in the coordinates

The geometry is EPSG:3857, which is what osm2pgsql produces unless told
otherwise. At Tokyo's latitude a Web Mercator metre is about 1.23 real metres,
so a naive distance comes out too long and an area too large by the square of
that.

| | measured properly | read straight off 3857 |
|---|---|---|
| JR Shibuya station to the edge of Yoyogi Park | 802.9 m | 991.0 m |
| Yoyogi Park | 533,447 m² | 810,035 m² |

Yoyogi Park really is about 54 hectares.

PostGIS at least refuses rather than lying: casting a 3857 geometry to
`geography` raises `Only lon/lat coordinate systems are supported in
geography`. Write `ST_Transform(way, 4326)::geography`.

## Multipolygons occupy several rows

The load used osm2pgsql's defaults, without `-G`, so a multipolygon relation
is stored as several rows sharing one `osm_id`, negated. Yoyogi Park,
`relation/19862716`, is two rows. Aggregate before you measure it.

```sql
SELECT ST_Union(way) FROM planet_osm_polygon WHERE osm_id = -19862716;
```

## Which 23 wards, and why it matters

Administrative relations at `admin_level=7` inside 東京都 whose name ends with
`区`. Inside Tokyo that is exactly the 23 wards, and the `admin_level` guard
keeps out the wards of other designated cities, such as Yokohama's 中区.

The boundary was derived from the same snapshot as the data. That is not
fussiness. An earlier cut used a boundary a year older, and lost
`way/1553541361`, created on 2026-08-30. One boundary way of Setagaya went
missing, its ring no longer closed, Overpass silently stopped generating an
area for that ward, and `area["name"="世田谷区"]` began returning zero with no
error. File size, node counts and every ward's place node all looked normal.
`provenance.yaml` records the check that catches it.

Tama and the islands are not here. They are part of Tokyo but not of the
wards, and the islands would stretch the bounding box across 18 degrees of
longitude.

## What the extract includes beyond the wards

The cut used `complete_ways`, so a way with even one node inside the wards is
kept whole. Nothing is chopped at the boundary, which is why the bounding box
reaches far past Tokyo: the Ogasawara ferry route starts at Takeshiba pier and
runs a thousand kilometres out to sea.

## Source

Code, Docker files and the verification scripts:
[yuiseki/osm-tokyo23-src-2026-08](https://github.com/yuiseki/osm-tokyo23-src-2026-08)

Cut from the planet file of 2026-08-31:

    https://planet.openstreetmap.org/pbf/planet-260831.osm.pbf

    94,612,383,571 bytes, md5 c67437924cf55de40e8708c7192f354d

OpenStreetMap keeps its dated planet files for about ten years, so that URL
should still fetch the same 94 GB the extract was cut from long after the
mirrors of this dataset have moved. Verify it against the checksum above and
you are holding the input; `provenance.yaml` has the rest of the chain.

## License

ODbL-1.0. (The Hub's license identifier for this is `odbl`.)

This dataset is a Derivative Database of
[OpenStreetMap](https://www.openstreetmap.org/):

    (c) OpenStreetMap contributors, available under the Open Database License.
    https://www.openstreetmap.org/copyright

The Parquet files are a reformatting of the same records, not new data, and
carry ODbL unchanged. So does anything built from them, including question and
answer pairs whose answers are computed from them. The full notice is in
`LICENSE` beside this file.
