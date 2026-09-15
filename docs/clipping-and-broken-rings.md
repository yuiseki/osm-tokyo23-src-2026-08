# How a clip loses one ward, and the check that finds it

A polygon clip can drop a single boundary way, and when it does, one
administrative area stops existing while every number you would normally look
at stays exactly as it should. This is how that happens and how to detect it.

## The mechanism

`osmium extract --strategy complete_ways` keeps a way if at least one of
its nodes falls inside the clip polygon, and when it keeps one it keeps all of
that way's nodes, including the ones outside. This is what you want: nothing is
chopped at the boundary.

The rule has an edge. A way with no node inside is dropped entirely.

Administrative boundaries run along the edge of the clip polygon by
construction, because the polygon is usually built from those very boundaries.
So the ways most exposed to this rule are the ones that define the region you
are cutting out. If the polygon and the data disagree about where the border
runs, even by a hundred metres, a short boundary way can land wholly outside
and disappear.

One missing way means the relation's ring no longer closes. Overpass builds
areas by a separate rules pass over the data, and that pass skips a relation it
cannot close. The area is not created. A query then behaves like this:

```overpassql
area["name"="世田谷区"]["admin_level"="7"]->.a;
node(area.a)["amenity"="cafe"];
out count;
```

```
total: 0
```

No error. No warning. Zero.

## Why nothing else shows it

When this happened here, every check that was in place passed.

- File size, node count, way count and relation count all looked normal
- All 23 wards still had their `place` node
- A list of 216 known OSM ids still resolved
- 22 of the 23 wards were completely unaffected
- Overpass returned a successful, empty response

The extract was 87 MB either way. One way out of 1,776,817 was missing.

## What caused it here

The clip polygon was built from a local Overpass instance whose data was a year
older than the planet file being cut. In that year the border between Setagaya
and its neighbour had been redrawn, and `way/1553541361`, created
2026-08-30T02:35:40Z, the day before the snapshot, sat about 160 metres
outside the older line. All twelve of its nodes fell outside the polygon, so
`complete_ways` dropped it, and Setagaya's ring went from 135 member ways to
134.

The specific gap was a year, but the size of the gap is not the point. Any
mask from a different vintage or a different source does the same thing in
proportion to how much it disagrees: a regional extract downloaded last month,
a hand-drawn polygon, a generalised boundary from another dataset.

## The fix

Derive the mask from the same snapshot as the data.

Cut a generous bounding box out of the planet first, build the boundary from
the administrative relations inside that box, then clip with it. The planet is
still read only once.

```
01_extract_bbox.sh          planet -> a bbox with ~11 km of margin
02_build_ward_boundary.py   the bbox -> the boundary
03_extract_tokyo23_pbf.sh   the bbox + the boundary -> the extract
```

## The check

Totals cannot see this. Count the members instead: for every boundary relation
you care about, every member way must be present in the extract.

`scripts/04_verify.py` does this.

```
== 区境界リングの完全性
  境界 way: 1191/1191
  23区すべての境界 way が完全
```

`1191/1191` is the assertion that matters. Before the fix it was `1190/1191`,
and that single digit was the only place the defect was visible.

The same check generalises: whenever you clip with a polygon and care about the
relations that define the region, compare each relation's member list against
what survived. A count of rows will not tell you.
