-- Runs after the postgis image's own init scripts.

-- osm2pgsql --hstore needs the hstore type. The image does not create it.
CREATE EXTENSION IF NOT EXISTS hstore;

-- The image also installs the US Census TIGER geocoder, which brings 34
-- tables in a `tiger` schema plus a `topology` schema. None of it relates to
-- OSM, and anything that shows the model this database's schema would have to
-- show those tables too. Remove them so the schema is only the OSM tables.
DROP EXTENSION IF EXISTS postgis_tiger_geocoder CASCADE;
DROP EXTENSION IF EXISTS fuzzystrmatch CASCADE;
DROP EXTENSION IF EXISTS postgis_topology CASCADE;
