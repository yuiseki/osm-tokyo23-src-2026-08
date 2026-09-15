#!/usr/bin/env python3
"""Verify the 23-ward extract.

The third check is the one that matters most. A clip can look fine by every
count and still have dropped a single boundary way, which silently costs you
that ward's Overpass area: the ring no longer closes, area generation skips it,
and `area["name"="世田谷区"]` quietly returns nothing instead of failing.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PBF = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "tokyo23-260831.osm.pbf"
TMP = ROOT / "tmp"

WARDS = {
    "1543055": "杉並区", "1543056": "中野区", "1758858": "新宿区",
    "1758878": "文京区", "1758888": "台東区", "1758891": "墨田区",
    "1758897": "中央区", "1758936": "目黒区", "1758947": "大田区",
    "1759474": "世田谷区", "1759477": "渋谷区", "1759506": "豊島区",
    "1760038": "北区", "1760040": "荒川区", "1760078": "板橋区",
    "1760119": "練馬区", "1760124": "足立区", "1761717": "港区",
    "1761718": "葛飾区", "1761742": "千代田区", "1761743": "江戸川区",
    "3554015": "江東区", "3554304": "品川区",
}


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def fileinfo() -> None:
    for line in run(["osmium", "fileinfo", "-e", str(PBF)]).splitlines():
        line = line.strip()
        if line.startswith(("Number of ", "Bounding box:")):
            print(f"  {line}")


def place_nodes() -> bool:
    out = TMP / "verify_places.osm.pbf"
    subprocess.run(
        ["osmium", "tags-filter", "-o", str(out), "--overwrite", str(PBF),
         "n/place=city,town,village,suburb"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    seq = run(["osmium", "export", "-f", "geojsonseq", str(out)])
    names = set()
    for line in seq.replace("\x1e", "").splitlines():
        if line.strip():
            names.add(json.loads(line)["properties"].get("name"))
    missing = [w for w in WARDS.values() if w not in names]
    print(f"  place ノードのある区: {23 - len(missing)}/23")
    if missing:
        print(f"  欠落: {missing}")
    return not missing


def ward_rings() -> bool:
    rels = TMP / "verify_admin7.osm.pbf"
    subprocess.run(
        ["osmium", "tags-filter", "-o", str(rels), "--overwrite", "-R",
         str(PBF), "r/admin_level=7"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    members: dict[str, list[str]] = {}
    for line in run(["osmium", "cat", "-f", "opl", str(rels)]).splitlines():
        if not line.startswith("r"):
            continue
        rid = line.split()[0][1:]
        if rid not in WARDS:
            continue
        m = re.search(r" M(\S*)", line)
        members[rid] = (
            [x[1:].split("@")[0] for x in m.group(1).split(",") if x.startswith("w")]
            if m else []
        )

    if len(members) != 23:
        found = {WARDS[r] for r in members}
        print(f"  リレーションが足りない: {sorted(set(WARDS.values()) - found)}")
        return False

    wanted = sorted({w for ws in members.values() for w in ws})
    idfile = TMP / "verify_ward_ways.txt"
    idfile.write_text("\n".join("w" + w for w in wanted) + "\n")
    hits = TMP / "verify_ward_ways.osm.pbf"
    # Not check=True: osmium getid exits non-zero when an id is not found, and
    # that is precisely the case this function exists to report. Letting it
    # raise would turn the one detection into a traceback.
    subprocess.run(
        ["osmium", "getid", f"--id-file={idfile}", "-o", str(hits),
         "--overwrite", str(PBF)],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not hits.exists():
        print("  osmium getid produced nothing", file=sys.stderr)
        return False
    have = {
        line.split()[0][1:]
        for line in run(["osmium", "cat", "-f", "opl", str(hits)]).splitlines()
        if line.startswith("w")
    }

    ok = True
    for rid, ways in sorted(members.items(), key=lambda kv: WARDS[kv[0]]):
        missing = [w for w in ways if w not in have]
        if missing:
            ok = False
            print(f"  {WARDS[rid]} (r{rid}): way {len(missing)}/{len(ways)} 欠落 -> {missing[:5]}")
    print(f"  境界 way: {len(have)}/{len(wanted)}")
    if ok:
        print("  23区すべての境界 way が完全")
    return ok


def main() -> int:
    print("== ファイル情報")
    fileinfo()
    print("== place ノード")
    a = place_nodes()
    print("== 区境界リングの完全性")
    b = ward_rings()
    print()
    if a and b:
        print("すべて通過")
        return 0
    print("検証に失敗した項目があります")
    return 1


if __name__ == "__main__":
    sys.exit(main())
