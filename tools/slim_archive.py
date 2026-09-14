#!/usr/bin/env python3
"""Strip the cumulative bucket columns out of a built archive.

Why this exists
---------------
`make_cumulative.py` adds a `count_c<key>` running total beside every
`count_<key>`, so a selection of any width costs two property reads in the
paint expression instead of one per selected bucket. That part works. What it
also does is double the number of attributes on every feature, and running
totals are large monotonic integers where the plain counts are ~95% zeros --
so the cumulative half costs far more per tile than the plain half it sits
next to. Measured on 2023 at z2: 740 keys and 8.41 MB with both, 5.74 MB with
only the cumulative columns, 2.63 MB with only the plain ones.

The map is bound by tile weight, not by expression arithmetic. Dragging a
selection over 8.41 MB of z2 tiles is visibly worse than summing three hundred
properties over 2.63 MB, so the columns come back out.

This works on a finished archive rather than re-running the aggregation, which
means it costs a couple of minutes per year instead of a rebuild. tile-join
drops the custom PMTiles metadata, so the gpio/firms keys are read off the
source first and written back with `pmtiles edit`.
"""

import argparse
import gzip
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CUMULATIVE = re.compile(r"^count_c\d+$")
# Metadata tile-join legitimately rewrites: it describes the new column set,
# so the source's copy would be a lie. Everything else is carried across.
REGENERATED = {
    "vector_layers", "tilestats", "generator", "generator_options",
    "strategies", "antimeridian_adjusted_bounds",
}


def sample_keys(path):
    """Attribute names from the biggest z2 tile, which is the densest sample."""
    from pmtiles.reader import MmapSource, Reader
    import mapbox_vector_tile as mvt

    with open(path, "rb") as fh:
        reader = Reader(MmapSource(fh))
        best = b""
        for x in range(4):
            for y in range(4):
                tile = reader.get(2, x, y)
                if tile and len(tile) > len(best):
                    best = tile
        if not best:
            raise SystemExit(f"{path}: no z2 tiles to sample")
        raw = gzip.decompress(best) if best[:2] == b"\x1f\x8b" else best
        layers = mvt.decode(raw)
    for layer in layers.values():
        if layer["features"]:
            return list(layer["features"][0]["properties"])
    raise SystemExit(f"{path}: z2 tiles carry no features")


def metadata(path):
    from pmtiles.reader import MmapSource, Reader

    with open(path, "rb") as fh:
        return Reader(MmapSource(fh)).metadata()


def slim(src: Path, dst: Path, keep_going=False):
    cols = [k for k in sample_keys(src) if CUMULATIVE.match(k)]
    if not cols:
        print(f"[skip] {src.name}: no cumulative columns")
        if src != dst:
            shutil.copy2(src, dst)
        return False

    before = metadata(src)
    with tempfile.TemporaryDirectory() as tmp:
        joined = Path(tmp) / "joined.pmtiles"
        # -pk: keep tiles whatever their size. The band layout was already
        # chosen against a budget; re-imposing one here would silently drop
        # cells and change what the map shows.
        cmd = ["tile-join", "-f", "-pk", "-o", str(joined)]
        for c in cols:
            cmd += ["-x", c]
        cmd.append(str(src))
        print(f"[join] {src.name}: dropping {len(cols)} cumulative columns")
        subprocess.run(cmd, check=True, capture_output=True)

        merged = dict(metadata(joined))
        for key, value in before.items():
            if key not in REGENERATED:
                merged.setdefault(key, value)
        # The contracts the app reads. setdefault is not enough: tile-join
        # writes its own "name"/"description", but these it omits entirely.
        for key in ("gpio:pyramid", "firms:breaks", "firms:timeline"):
            if key in before:
                merged[key] = before[key]

        meta_file = Path(tmp) / "metadata.json"
        meta_file.write_text(json.dumps(merged))
        subprocess.run(["pmtiles", "edit", f"--metadata={meta_file}", str(joined)],
                       check=True, capture_output=True)

        restored = metadata(joined)
        missing = [k for k in ("gpio:pyramid", "firms:breaks", "firms:timeline")
                   if k in before and k not in restored]
        if missing:
            raise SystemExit(f"{src.name}: metadata lost after edit: {missing}")

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(joined), dst)

    kept = [k for k in sample_keys(dst) if CUMULATIVE.match(k)]
    if kept:
        raise SystemExit(f"{dst.name}: {len(kept)} cumulative columns survived")
    print(f"[done] {dst.name}: {src.stat().st_size/1e9:.2f} GB -> "
          f"{dst.stat().st_size/1e9:.2f} GB")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archives", nargs="+", type=Path)
    ap.add_argument("--out-dir", type=Path,
                    help="write beside the source with this directory instead of in place")
    ap.add_argument("--keep-going", action="store_true",
                    help="carry on to the next archive after a failure")
    a = ap.parse_args()

    failed = []
    for src in a.archives:
        dst = (a.out_dir / src.name) if a.out_dir else src
        try:
            slim(src, dst, a.keep_going)
        except (subprocess.CalledProcessError, SystemExit) as exc:
            msg = exc.stderr.decode()[-400:] if isinstance(exc, subprocess.CalledProcessError) else str(exc)
            print(f"[fail] {src.name}: {msg}", file=sys.stderr)
            failed.append(src.name)
            if not a.keep_going:
                return 1
    if failed:
        print(f"\n{len(failed)} failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
