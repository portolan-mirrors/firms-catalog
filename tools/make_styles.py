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


def fill_color(metric: str, bands: list) -> list:
    """Step on zoom, then on the metric, so each level uses its own classes."""
    read = ["coalesce", ["get", metric], 0]
    if len(bands) == 1:
        return ["step", read] + steps(bands[0]["metrics"][metric])
    expr = ["step", ["zoom"], ["step", read] + steps(bands[0]["metrics"][metric])]
    for b in bands[1:]:
        expr += [b["minzoom"], ["step", read] + steps(b["metrics"][metric])]
    return expr


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
    for name, (metric, title) in STYLES.items():
        if not all(metric in b["metrics"] for b in bands):
            continue
        cells = {
            "id": "fire-cells", "type": "fill", "source": "data",
            "source-layer": "aggregate",
            "paint": {"fill-color": fill_color(metric, bands),
                      "fill-opacity": 0.78,
                      "fill-outline-color": "rgba(0,0,0,0.2)"},
        }
        layers = [cells]
        # Raw points exist only where the archive actually carries them. Where
        # they do, the cells stop rather than drawing underneath.
        if pt:
            cells["maxzoom"] = pt["minzoom"]
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
        zr = ", ".join(f"r?@z{b['minzoom']}+" for b in bands)
        print(f"  {p}  ({metric}, {len(bands)} band(s): {zr})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
