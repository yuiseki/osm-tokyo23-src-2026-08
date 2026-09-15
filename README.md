# osm-tokyo23-src-2026-08

Dataset: https://huggingface.co/datasets/yuiseki/osm-tokyo23-src-2026-08

A frozen cut of OpenStreetMap covering the 23 special wards of Tokyo, taken
from the planet file of 2026-08-31, and the code that rebuilds every database
it was measured in. This repository holds the code; the data is on the Hub.

A question about a city has an answer only against a stated snapshot. An
answer computed today against the live API is not reproducible tomorrow, so
here the snapshot is one file with a checksum and every tool that reads it is
pinned in a Dockerfile.

## Where things are

```
scripts/01_extract_bbox.sh          planet -> a generous bbox around the wards
scripts/02_build_ward_boundary.py   the bbox -> the ward boundary
scripts/03_extract_tokyo23_pbf.sh   the bbox + the boundary -> the extract
scripts/04_verify.py                the checks, including the ring check
scripts/05_load_postgis.sh          the extract -> PostGIS
scripts/06_start_overpass.sh        the extract -> Overpass
scripts/07_export_parquet.py        PostGIS -> Parquet
src/publish.py                      pushes the data and its card to the Hub

docker/Dockerfile.osm2pgsql         osm2pgsql 1.11.0+ds-1
docker/Dockerfile.overpass          wiktorn/overpass-api v0.7.62.11
docker/Dockerfile.duckdb            duckdb 1.5.5, sha256 verified
docker/default.style                decides which tags become columns
docker/compose.yml

data/README.md                      the dataset card. Uploaded as-is
data/LICENSE                        the ODbL notice and the chain of derivation
data/provenance.yaml                every version in the chain, and what was left out
data/tokyo23-260831.osm.pbf         generated, 83MB
data/parquet/                       generated, 152MB

docs/schema-osm2pgsql.md            the measured schema, and the 3857 distortion
docs/schema-parquet.md              what changes in Parquet, and DuckDB's quirks
docs/clipping-and-broken-rings.md   how a clip loses one ward, and the check that finds it
```

## Running it

Fetch the planet file first, then point the first script at it:

```sh
PLANET=/path/to/planet-260831.osm.pbf ./scripts/01_extract_bbox.sh
```

```sh
./scripts/01_extract_bbox.sh          # the only pass over the planet, ~26 min
./scripts/02_build_ward_boundary.py
./scripts/03_extract_tokyo23_pbf.sh
./scripts/04_verify.py
./scripts/05_load_postgis.sh
./scripts/06_start_overpass.sh
./scripts/07_export_parquet.py
python3 src/publish.py                # dry run; --push to upload
```

Everything after the first step takes minutes. The planet read is the cost.

## The result

| | |
|---|---|
| `data/tokyo23-260831.osm.pbf` | 87,399,745 bytes, md5 `44a4ba2182379c147f20a27ad1b513ef` |
| nodes / ways / relations | 9,659,934 / 1,776,817 / 19,663 |

| table | rows |
|---|---|
| `planet_osm_point` | 306,642 |
| `planet_osm_line` | 347,735 |
| `planet_osm_polygon` | 1,437,105 |
| `planet_osm_roads` | 33,113 |

## No schema of our own

The tables are osm2pgsql's classic pgsql output, because that is the layout
models have read for fifteen years of web maps. A friendlier schema of our own
design would measure whether a model can read a novel schema rather than
whether it knows OSM and SQL.

`--hstore` is not optional. The style promotes 106 tags to columns, but
`opening_hours`, `start_date`, `building:levels` and `operator:en` are not
among them, and questions ask about all four.

The ward polygons are already in `planet_osm_polygon`, tagged
`boundary=administrative` and `admin_level=7`. No separate ward table is
needed, and none was added.

## Three engines, one answer

The same question answered in PostGIS, in Overpass, and in DuckDB over the
Parquet. Counting `amenity=cafe` nodes per ward, all 23 wards agree across all
three: Chiyoda 507, Shibuya 477, Minato 429, Setagaya 258, Arakawa 44.

## Which 23 wards

Administrative relations at `admin_level=7` inside 東京都 whose name ends with
`区`. Inside Tokyo that is exactly the 23 wards, and the `admin_level` guard
keeps out the wards of other designated cities, such as Yokohama's 中区.

The boundary is derived from the same snapshot as the data. An earlier cut used
a boundary a year older and lost `way/1553541361`, created on 2026-08-30. One
boundary way of Setagaya went missing, its ring stopped closing, Overpass
silently stopped generating an area for that ward, and
`area["name"="世田谷区"]` began returning zero with no error. File size, node
counts and every ward's place node all looked normal.
`scripts/04_verify.py` counts relation members for exactly this reason;
`docs/clipping-and-broken-rings.md` is the write-up.

Tama and the islands are not here. They are part of Tokyo but not of the wards,
and the islands would stretch the bounding box across 18 degrees of longitude.

## Traps worth knowing

The geometry is EPSG:3857. At Tokyo's latitude a Web Mercator metre is about
1.23 real metres, so distances come out long and areas long squared. Measured
properly, JR Shibuya station is 802.9 m from the edge of Yoyogi Park; read
straight off 3857 it is 991.0 m. Write `ST_Transform(way, 4326)::geography`.

The load uses osm2pgsql's defaults, without `-G`, so a multipolygon relation is
several rows sharing one negated `osm_id`. Yoyogi Park, `relation/19862716`, is
two rows. Aggregate before measuring it.

`wiktorn/overpass-api` has three of its own, all handled in
`scripts/06_start_overpass.sh`: it exits after the import unless
`OVERPASS_STOP_AFTER_INIT=false`, its `/api/status` always returns 502 while
`/api/interpreter` answers fine, and its database directory is created
`drwx------` so fcgiwrap cannot reach the dispatcher socket until it is
chmodded.

## Source

Cut from the planet file of 2026-08-31:

    https://planet.openstreetmap.org/pbf/planet-260831.osm.pbf
    94,612,383,571 bytes, md5 c67437924cf55de40e8708c7192f354d

OpenStreetMap keeps its dated planet files for about ten years, so that URL is
the one link that makes the whole chain checkable.

## License

The code here is MIT. The data it produces is a Derivative Database of
OpenStreetMap and is ODbL:

    (c) OpenStreetMap contributors, available under the Open Database License.
    https://www.openstreetmap.org/copyright

See `data/LICENSE` for the full notice and the chain of derivation.
