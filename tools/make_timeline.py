#!/usr/bin/env python3
"""Write `firms:timeline` into a PMTiles archive, derived from its own columns.

Deriving rather than accepting the axis on the command line is the point: a
declaration passed in by hand can disagree with the tiles, and this one cannot.

Run: python3 tools/make_timeline.py path/to/archive.pmtiles
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from timeline_meta import declare  # noqa: E402

AGGREGATE_LAYER = "aggregate"


def metadata(archive: str) -> dict:
    out = subprocess.run(["pmtiles", "show", archive, "--metadata"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out[out.index("{"):])


def aggregate_columns(md: dict) -> list[str]:
    layers = md.get("vector_layers") or json.loads(
        md.get("json", "{}")).get("vector_layers", [])
    for layer in layers:
        if layer.get("id") == AGGREGATE_LAYER:
            return list((layer.get("fields") or {}).keys())
    raise SystemExit(f"no '{AGGREGATE_LAYER}' layer in the archive")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pmtiles", help="archive to annotate in place")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    md = metadata(a.pmtiles)
    axis = declare(aggregate_columns(md))
    print(f"  {a.pmtiles}: {axis['buckets']} {axis['unit']} bucket(s) "
          f"{axis['min']}..{axis['max']}")
    if a.dry_run:
        return 0

    md["firms:timeline"] = axis
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(md, f)
        tmp = f.name
    try:
        subprocess.run(["pmtiles", "edit", a.pmtiles, f"--metadata={tmp}"],
                       check=True)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
