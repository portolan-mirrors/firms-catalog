#!/usr/bin/env python3
"""Put a published aggregate into the shape the distribution guide asks for.

Following https://github.com/opengeospatial/geoparquet/blob/main/format-specs/
distributing-geoparquet.md. The aggregates were written by whatever `gpio
process aggregate` defaulted to at the time, which left the set inconsistent:
GeoParquet 1.0.0, 1.1.0 and 2.0.0 all appear across it.

What this applies, and why each:

* **Hilbert order.** The guide calls spatial ordering essential -- row-group
  statistics only prune if nearby rows sit together. Unordered rows give every
  row group a near-global bbox and no query skips anything.
* **zstd 22.** The guide says zstd at "minimum level 15, go as high as time
  permits". Decompression cost does not rise with the level, so the client
  never pays for it.
* **100,000-row row groups**, inside the guide's 50k-150k range.
* **GeoParquet 2.0**, so the geometry is a native Parquet GEOMETRY and carries
  geospatial statistics per row group.
* **A bbox covering column**, which the guide says to *omit* by default at 2.0
  -- native statistics already serve engines that understand them. It is added
  here for the exception the guide names: readers without native geospatial
  support. The browser reads these with hyparquet, which cannot use a native
  GEOMETRY's statistics but can filter four plain doubles, and the guide's
  caveat about bbox overhead applies to points rather than the polygons here.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ZSTD_LEVEL = 22
ROW_GROUP = 100_000


def normalise(path: Path, keep_going: bool = False) -> bool:
    t0 = time.monotonic()
    before = path.stat().st_size
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        out = Path(tmp) / path.name
        cmd = ["gpio", "sort", "hilbert", str(path), str(out),
               "--add-bbox",
               "--compression", "zstd", "--compression-level", str(ZSTD_LEVEL),
               "--geoparquet-version", "2.0",
               "--row-group-size", str(ROW_GROUP)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[fail] {path.name}: {r.stderr[-300:]}", file=sys.stderr, flush=True)
            return False
        # Move only after gpio has exited cleanly, so a failure leaves the
        # published file untouched rather than half-written.
        shutil.move(str(out), path)
    after = path.stat().st_size
    print(f"[done] {path.parent.name}/{path.name}: {before/1e6:.1f} -> {after/1e6:.1f} MB "
          f"({100*(after-before)/before:+.0f}%) in {time.monotonic()-t0:.0f}s", flush=True)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--keep-going", action="store_true")
    a = ap.parse_args()
    failed = [f.name for f in a.files if not normalise(f) and not a.keep_going]
    if failed:
        print(f"{len(failed)} failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
