#!/usr/bin/env python3
"""Push the frozen extract and its card to the Hugging Face Hub.

The Parquet files go up as files, not through datasets.push_to_hub, because
push_to_hub re-encodes them. This dataset is managed by checksum, so the bytes
that were measured and recorded in provenance.yaml are the bytes that should
be published. The card declares the four tables as configs instead, and the
Hub reads the row counts out of the Parquet itself.

The card is data/README.md, kept in git and uploaded as-is, so what the Hub
shows is a file that can be reviewed and diffed rather than a string buried in
this script.

    python3 src/publish.py                 # dry run, lists what would go up
    python3 src/publish.py --push          # uploads
"""
import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "yuiseki/osm-tokyo23-src-2026-08"

# local path -> path in the repo
FILES = {
    "data/README.md": "README.md",
    "data/LICENSE": "LICENSE",
    "data/provenance.yaml": "provenance.yaml",
    "data/tokyo23-260831.osm.pbf": "tokyo23-260831.osm.pbf",
    "data/tokyo23-260831.osm.pbf.md5": "tokyo23-260831.osm.pbf.md5",
    "data/tokyo23_osm_boundary.geojson": "tokyo23_osm_boundary.geojson",
    "data/parquet/planet_osm_point.parquet": "parquet/planet_osm_point.parquet",
    "data/parquet/planet_osm_line.parquet": "parquet/planet_osm_line.parquet",
    "data/parquet/planet_osm_polygon.parquet": "parquet/planet_osm_polygon.parquet",
    "data/parquet/planet_osm_roads.parquet": "parquet/planet_osm_roads.parquet",
    "data/parquet/checksums.md5": "parquet/checksums.md5",
}


def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--push", action="store_true", help="actually upload")
    a = ap.parse_args()

    total = 0
    missing = []
    print(f"{a.repo}\n")
    for local, remote in FILES.items():
        p = ROOT / local
        if not p.exists():
            missing.append(local)
            continue
        size = p.stat().st_size
        total += size
        digest = md5(p) if p.suffix in (".pbf", ".parquet") else ""
        print(f"  {remote:<42} {size:>12,}  {digest}")
    if missing:
        raise SystemExit("missing: " + ", ".join(missing))
    print(f"\n  {'total':<42} {total:>12,}")

    # The recorded checksum must still describe the file being published.
    recorded = (ROOT / "data/tokyo23-260831.osm.pbf.md5").read_text().split()[0]
    actual = md5(ROOT / "data/tokyo23-260831.osm.pbf")
    if recorded != actual:
        raise SystemExit(f"checksum drift: recorded {recorded}, actual {actual}")
    print(f"  pbf checksum matches the recorded one: {actual}")

    if not a.push:
        print("\ndry run. pass --push to upload")
        return 0

    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(a.repo, repo_type="dataset", exist_ok=True, private=False)
    for local, remote in FILES.items():
        print(f"  uploading {remote}")
        api.upload_file(path_or_fileobj=str(ROOT / local), path_in_repo=remote,
                        repo_id=a.repo, repo_type="dataset")
    print(f"\npushed to https://huggingface.co/datasets/{a.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
