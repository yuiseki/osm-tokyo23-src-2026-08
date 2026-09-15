#!/usr/bin/env python3
"""Step 2 of 3: derive the 23 wards' boundary from the extract itself.

The rule is: administrative relations at admin_level=7 inside Tokyo whose name
ends with 区. Within Tokyo that matches exactly the 23 special wards, and the
admin_level guard keeps the wards of other designated cities out.

The boundary deliberately comes from the same snapshot as the data. Taking it
from anywhere else invites a vintage mismatch: a boundary drawn a year earlier
once dropped way/1553541361, created 2026-08-30, which broke Setagaya's ring
and with it every area query for that ward.
"""
import json
import subprocess
import sys
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tmp" / "tokyo-bbox-260831.osm.pbf"
OUT = ROOT / "data" / "tokyo23_osm_boundary.geojson"
TMP = ROOT / "tmp"


def ward_polygons() -> dict[str, list]:
    filtered = TMP / "admin7.osm.pbf"
    subprocess.run(
        ["osmium", "tags-filter", "-o", str(filtered), "--overwrite", str(SRC),
         "r/admin_level=7"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    seq = subprocess.run(
        ["osmium", "export", "-f", "geojsonseq", "--geometry-types=polygon",
         str(filtered)],
        check=True, capture_output=True, text=True).stdout

    wards: dict[str, list] = {}
    for line in seq.replace("\x1e", "").splitlines():
        line = line.strip()
        if not line:
            continue
        f = json.loads(line)
        p = f["properties"]
        name = p.get("name")
        if (p.get("boundary") == "administrative"
                and p.get("admin_level") == "7"
                and name and name.endswith("区")):
            wards.setdefault(name, []).append(shape(f["geometry"]))
    return wards


def main() -> int:
    wards = ward_polygons()
    if len(wards) != 23:
        print(f"expected 23 wards, got {len(wards)}: {sorted(wards)}", file=sys.stderr)
        return 1

    union = unary_union([g for parts in wards.values() for g in parts])
    if not union.is_valid:
        print("the union is not a valid geometry", file=sys.stderr)
        return 1

    out = {
        "type": "Feature",
        "properties": {
            "name": "東京都区部",
            "name_en": "Tokyo Special Wards",
            "rule": "admin_level=7 within 東京都 and name ends with 区",
            "count": len(wards),
            "source": "OpenStreetMap, derived from planet-260831 itself",
        },
        "geometry": mapping(union),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT}: {len(wards)} wards, "
          f"bounds={[round(v, 5) for v in union.bounds]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
