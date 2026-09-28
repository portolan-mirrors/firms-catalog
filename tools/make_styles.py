#!/usr/bin/env python3
"""Generate the catalog's MapLibre styles from a tileset's own class breaks.

A style written by hand carries one set of thresholds for the whole pyramid,
and a pyramid whose levels differ in cell area cannot be served by one set: in
this archive the coarse level runs about thirty times higher than the fine one,
so thresholds tuned for either end saturate or flatten the other. The styles
here therefore step on zoom first and on the metric second, taking both the
thresholds and the zoom ranges from `firms:breaks` in the archive.

Generating rather than hand-writing also keeps the plain styles agreeing with
the preview app, which reads the same breaks at runtime. They are ordinary
style JSON with no extension of any kind, so any MapLibre viewer renders them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

# Seven classes, cool to hot. The break count in the archive is one fewer.
PALETTE = ["#2c3d5a", "#3f6d8f", "#59a1a0", "#a8c268", "#f2b134", "#e8722c", "#d1382a"]
FRP_POINTS = ["step", ["coalesce", ["get", "frp"], 0],
              "#4a5bd4", 10, "#39a0a8", 50, "#c9cf4a", 200, "#f2b134",
              500, "#e8722c", 1000, "#d1382a"]

STYLES = {
    "default": ("count", "Fire detections{suffix} (density)"),
    "avg-frp": ("avg_frp", "Fire radiative power{suffix} (average)"),
}

# Styles that colour a cell by a SHARE of its own detections rather than by a
# count. They need no class breaks from the archive -- a share is already on a
# fixed 0-100 scale -- so the only question is whether the archive carries the
# pivot columns, which is read from its declared field list rather than
# assumed. `gpio process aggregate a5` pivots one categorical column per run,
# so an archive built before a pivot was added simply will not have it.
#
# A share also reads honestly at every zoom, which a count does not: the
# density styles need one class set per pyramid level because a coarse cell
# holds about thirty times what a fine one does, where "what fraction of this
# cell burned at night" means the same thing at r5 and at r10.
RATIO_STYLES = [
    {
        "name": "day-night",
        "title": "Day and night{suffix}",
        "of": ["count_n"],
        "breaks": [20, 40, 60, 80],
        # Diverging about an even split: gold where the detections are almost
        # all daytime, violet where they are almost all night.
        "palette": ["#f2b134", "#cfa65c", "#8b949e", "#6d78c0", "#4a5bd4"],
        "points": ("daynight", ["D", "#f2b134", "N", "#4a5bd4"], "#8b949e"),
    },
]

# A sensor-mix style belongs here and is deliberately absent. Colouring a cell
# by the share of its detections that came from VIIRS is a good map -- a low
# share means the fires are large and hot enough for MODIS to see them too, a
# high one means most of what is burning is small enough that only VIIRS at
# 375 m catches it -- but it cannot use fixed classes, because the share is set
# by which platforms flew that year as much as by what burned. Measured on the
# published r5 aggregates:
#
#   2024 (4 platforms)  MODIS share  p50 6.5%   p90 14.3%
#   2015 (2 platforms)  MODIS share  p50 17.3%  p90 32.0%
#
# and with the 20/40/60/80 classes the other styles use, 92.9% of 2024's cells
# land in a single VIIRS-share class. Breaks tuned for one fleet era flatten
# every other, which is the same problem make_breaks.py already solves for the
# density styles by taking quantiles from the data. The fix is to derive share
# breaks the same way and declare them in `firms:breaks`; until an archive
# carries them, shipping the style would mean shipping a one-colour map.


def declared_fields(md: dict, layer: str) -> set:
    """The field names an archive says a layer carries."""
    vl = md.get("vector_layers")
    if vl is None:
        vl = (json.loads(md.get("json", "{}")) or {}).get("vector_layers", [])
    for entry in vl or []:
        if entry.get("id") == layer:
            return set((entry.get("fields") or {}).keys())
    return set()


def share_expression(fields: list) -> list:
    """`fields` as a percentage of the cell's total count.

    Every read is coalesced because MVT omits zero-valued attributes: a cell
    with no night detections carries no `count_n` at all, and `["get", ...]`
    on a missing attribute is null, which poisons the arithmetic rather than
    reading as the zero it means. The denominator floors at 1 so a cell that
    somehow reports no detections divides to 0 instead of erroring.
    """
    reads = [["coalesce", ["get", f], 0] for f in fields]
    numerator = reads[0] if len(reads) == 1 else ["+"] + reads
    return ["*", 100, ["/", numerator,
                       ["max", 1, ["coalesce", ["get", "count"], 0]]]]


def ratio_style(spec: dict, tiles: str, suffix: str, stop: int | None) -> dict:
    """One share style: cells everywhere, and the raw points where they exist."""
    fill = ["step", share_expression(spec["of"]), spec["palette"][0]]
    for brk, colour in zip(spec["breaks"], spec["palette"][1:]):
        fill += [brk, colour]
    cells = {
        "id": "fire-cells", "type": "fill", "source": "data",
        "source-layer": "aggregate",
        "paint": {"fill-color": fill, "fill-opacity": 0.78,
                  "fill-outline-color": "rgba(0,0,0,0.2)"},
    }
    layers = [cells]
    if stop is not None:
        cells["maxzoom"] = stop
        field, pairs, fallback = spec["points"]
        layers.append({
            "id": "fire-points", "type": "circle", "source": "data",
            "source-layer": "features", "minzoom": stop,
            "paint": {"circle-color": ["match", ["get", field]] + pairs + [fallback],
                      "circle-radius": ["interpolate", ["linear"], ["zoom"],
                                        stop, 2.2, stop + 4, 6],
                      "circle-opacity": 0.9},
        })
    return {
        "version": 8,
        "name": spec["title"].format(suffix=suffix),
        "sources": {"data": {"type": "vector", "url": f"pmtiles://{tiles}"}},
        "layers": layers,
    }


def metadata(pmtiles: str) -> dict:
    out = subprocess.run(["pmtiles", "show", pmtiles, "--metadata"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out[out.index("{"):])


def steps(values: list) -> list:
    """A step expression over one metric: colour, break, colour, break, ..."""
    expr = [PALETTE[0]]
    for v, colour in zip(values, PALETTE[1:]):
        expr += [v, colour]
    return expr


def cell_layers(metric: str, bands: list, stop: int | None) -> list:
    """One fill layer per pyramid band, each stepping only on the metric.

    This used to be a single layer whose `fill-color` stepped on zoom first and
    on the metric second. It rendered correctly and read as nonsense. A legend
    reader takes the first fill layer's `fill-color` and expects
    `["step", <input>, colour, break, colour, ...]`; ours handed it
    `["step", ["zoom"], <step>, 6, <step>]`, so it drew the inner expressions
    as swatches and labelled them from the ZOOM breakpoint. The Portolan
    browser showed two identical red boxes reading "< 6" and "6+" for a layer
    whose classes actually run from 48 to 4,021 detections.

    A layer per band says the same thing in a shape a reader can follow: each
    carries one flat set of classes and the zoom window it applies to, which is
    also the more idiomatic way to write it. `stop` is the zoom where raw
    points take over, if the archive carries any.
    """
    read = ["coalesce", ["get", metric], 0]
    layers = []
    for i, b in enumerate(bands):
        # Bands declare inclusive integer zooms; a layer's maxzoom is the
        # exclusive upper bound, so it is the next band's floor.
        upper = bands[i + 1]["minzoom"] if i + 1 < len(bands) else stop
        layer = {
            # Named for the zoom it starts at, which is derivable from the
            # style itself -- so a reader, or a tool rewriting a published
            # style in place, lands on the same id the generator would.
            "id": f"fire-cells-z{b['minzoom']}",
            "type": "fill", "source": "data", "source-layer": "aggregate",
            "paint": {"fill-color": ["step", read] + steps(b["metrics"][metric]),
                      "fill-opacity": 0.78,
                      "fill-outline-color": "rgba(0,0,0,0.2)"},
        }
        if b["minzoom"]:
            layer["minzoom"] = b["minzoom"]
        if upper is not None:
            layer["maxzoom"] = upper
        layers.append(layer)
    return layers


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pmtiles", help="archive to read breaks and bands from")
    ap.add_argument("--out", required=True, help="directory to write styles into")
    ap.add_argument("--tiles", required=True, help="href of the archive, as the style should reference it")
    ap.add_argument("--suffix", default="", help="appended to each style name, e.g. ', 2020'")
    a = ap.parse_args()

    md = metadata(a.pmtiles)
    br = md.get("firms:breaks")
    if isinstance(br, str):
        br = json.loads(br)
    if not br:
        raise SystemExit(f"{a.pmtiles} carries no firms:breaks; run make_breaks.py first")
    # Only the aggregate levels are classified; the point band has no cells.
    bands = sorted((v for v in br.values() if v.get("metrics")),
                   key=lambda b: b["minzoom"])

    py = md.get("gpio:pyramid")
    if isinstance(py, str):
        py = json.loads(py)
    pt = next((b for b in (py or {}).get("bands", []) if b.get("level") == "features"), None)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, (metric, title) in STYLES.items():
        if not all(metric in b["metrics"] for b in bands):
            continue
        # Raw points exist only where the archive actually carries them. Where
        # they do, the cells stop rather than drawing underneath.
        layers = cell_layers(metric, bands, pt["minzoom"] if pt else None)
        if pt:
            layers.append({
                "id": "fire-points", "type": "circle", "source": "data",
                "source-layer": "features", "minzoom": pt["minzoom"],
                "paint": {"circle-color": FRP_POINTS,
                          "circle-radius": ["interpolate", ["linear"], ["zoom"],
                                            pt["minzoom"], 2.2, pt["minzoom"] + 4, 6],
                          "circle-opacity": 0.9},
            })
        style = {
            "version": 8,
            "name": title.format(suffix=a.suffix),
            "sources": {"data": {"type": "vector", "url": f"pmtiles://{a.tiles}"}},
            "layers": layers,
        }
        p = out / f"{name}.json"
        p.write_text(json.dumps(style, indent=2) + "\n")
        written.append(p)
        zr = ", ".join(f"r?@z{b['minzoom']}+" for b in bands)
        print(f"  {p}  ({metric}, {len(bands)} band(s): {zr})")

    have = declared_fields(md, "aggregate")
    for spec in RATIO_STYLES:
        # Any one of the pivots is enough to build the share; a pivot that is
        # absent is a category with no rows, which is a zero, not a gap. The
        # total has to be there, because it is the denominator.
        present = [f for f in spec["of"] if f in have]
        if not present or "count" not in have:
            print(f"  skipped {spec['name']}: archive declares none of "
                  f"{', '.join(spec['of'])}")
            continue
        style = ratio_style({**spec, "of": present}, a.tiles, a.suffix,
                            pt["minzoom"] if pt else None)
        p = out / f"{spec['name']}.json"
        p.write_text(json.dumps(style, indent=2) + "\n")
        written.append(p)
        print(f"  {p}  (share of {'+'.join(spec['of'])})")

    refresh_declared_bytes(out, written)
    return 0


def refresh_declared_bytes(out: Path, written: list[Path]) -> None:
    """Bring file:size and file:checksum back in line with the new bytes.

    Rewriting a style invalidates whatever the metadata declared about it, and
    a stale checksum is a validation error, so the tool that changed the bytes
    fixes the declaration rather than leaving it for the next full rebuild.
    """
    for meta in sorted(set(out.parent.glob("*.json"))):
        doc = json.loads(meta.read_text())
        assets = doc.get("assets") or {}
        touched = False
        for asset in assets.values():
            href = asset.get("href", "")
            for p in written:
                if href.endswith(f"/{p.name}") and p.name in href:
                    if "file:size" in asset or "file:checksum" in asset:
                        asset["file:size"] = p.stat().st_size
                        asset["file:checksum"] = "1220" + hashlib.sha256(
                            p.read_bytes()).hexdigest()
                        touched = True
        if touched:
            meta.write_text(json.dumps(doc, indent=2) + "\n")
            print(f"  refreshed declared bytes in {meta}")


if __name__ == "__main__":
    raise SystemExit(main())
