# The PostGIS schema, as measured

What `scripts/05_load_postgis.sh` actually produces from
`data/tokyo23-260831.osm.pbf` (md5 `44a4ba2182379c147f20a27ad1b513ef`).

The schema is osm2pgsql's own classic pgsql output. Nothing here was designed.

## Pinned versions

| | version | pinned in |
|---|---|---|
| PostGIS image | `postgis/postgis:16-3.4` | `docker/compose.yml` |
| PostgreSQL | 16.4 | that image |
| PostGIS | 3.4 | that image |
| osm2pgsql | `1.11.0+ds-1` (ubuntu:24.04) | `docker/Dockerfile.osm2pgsql` |
| style | md5 `ca34bbc54e6113a4b01e588e8f085f15` | `docker/default.style`, in this repo |

The style file, not the osm2pgsql version, decides which tags become columns,
so it is kept in the repository and passed with `-S` rather than taken from
whatever the package happens to ship.

## Result

A clean load takes 28 seconds.

| table | rows | columns |
|---|---|---|
| `planet_osm_point` | 306,642 | 70 |
| `planet_osm_line` | 347,735 | 70 |
| `planet_osm_polygon` | 1,437,105 | 70 |
| `planet_osm_roads` | 33,113 | 70 |

Geometry is the `way` column, SRID 3857. `planet_osm_point` carries `capital`
and `ele`; the other three carry `tracktype` and `way_area` instead.

## Decisions, and why

### `--hstore` is required

The style promotes 106 tags to columns, but `opening_hours`, `start_date`,
`building:levels` and `operator:en` are not among them, and questions ask about
all four. Without the hstore `tags` column they are unreachable.

`operator` is a column; `operator:en` is not:

```sql
SELECT name, operator, tags->'operator:en' FROM planet_osm_point
WHERE railway='station' AND name='渋谷';
```

### The wards are already in the data

No separate ward table is needed.

```sql
SELECT count(*) FROM planet_osm_point p
JOIN planet_osm_polygon w ON ST_Within(p.way, w.way)
WHERE w.boundary='administrative' AND w.admin_level='7' AND w.name='千代田区'
  AND p.amenity='cafe';
-- 507
```

### The TIGER schema is dropped

The `postgis/postgis` image installs the US Census TIGER geocoder by default,
which creates 34 tables in a `tiger` schema and two more in `topology`. None of
it relates to OSM, and anything that shows a model this database's schema would
have to show those tables too. `docker/initdb/20-extensions.sql` removes them,
leaving `public` with the four OSM tables and PostGIS's own metadata.

That file also creates `hstore`, which the image does not.

## The 3857 trap, measured

The projection is left at osm2pgsql's default, EPSG:3857.

Shortest distance from JR Shibuya station to the boundary of Yoyogi Park:

| | |
|---|---|
| `ST_Transform(way,4326)::geography` | 802.9 m |
| `ST_Distance` on the raw 3857 geometry | 991.0 m |

The ratio is 1.234. At latitude 35.66, `1/cos` is 1.231.

Area of Yoyogi Park:

| | |
|---|---|
| `ST_Area(ST_Transform(way,4326)::geography)` | 533,447 m² |
| `sum(way_area)`, raw 3857 | 810,035 m² |

The ratio is 1.518, the square of the distance error. The park really is about
54 hectares, so the first figure is the right one.

There is one mercy. Casting a 3857 geometry straight to `geography` does not
quietly return a wrong number:

```
ERROR: Only lon/lat coordinate systems are supported in geography.
```

Write `ST_Transform(way, 4326)::geography`.

## Multipolygons occupy several rows

The load uses osm2pgsql's defaults, without `-G/--multi-geometry`, so a
multipolygon relation becomes several rows sharing one `osm_id`, negated.

Yoyogi Park, `relation/19862716`, is two rows. Its area is not in any one of
them, and neither is the distance to its boundary. Aggregate first:

```sql
SELECT ST_Union(way) FROM planet_osm_polygon WHERE osm_id = -19862716;
```

## Agreement with the other engines

The same question, asked three ways, gives the same answer.

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

Both 507, as does DuckDB over the Parquet. Run it for every ward and all 23
agree across all three.
