#!/usr/bin/env python3
"""Write the MapLibre styles the collection ships.

The Portolan browser derives a legend only from a `fill` layer whose
`fill-color` is a `match` or `step` expression. The A5 aggregate is a polygon
layer, so a `step` ramp over it produces a real legend. The raw points are a
`circle` layer and yield none, which is why the default style is an aggregate
style and the point layer rides along above the aggregate's zoom band.

Every style reads the single combined archive: `aggregate` at low zoom and
`features` from the points band up.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# Absolute, not relative. The Portolan browser fetches a style as JSON and
# hands MapLibre the object, so a relative source URL resolves against the
# viewer's own origin rather than the style's location: the browser rendered
# the legend and never requested the tiles. The generator writes this from the
# published base, so it stays correct on republish.
PUBLIC_BASE = ("https://data.source.coop/portolan-mirrors/firms-catalog/detections")
PMTILES = f"{PUBLIC_BASE}/fire.pmtiles"

COUNT = [[1, "#2c3d5a"], [5, "#3f6d8f"], [20, "#59a1a0"], [75, "#a8c268"],
         [250, "#f2b134"], [1000, "#e8722c"], [4000, "#d1382a"]]
AVGFRP = [[0, "#2c3d5a"], [5, "#3f6d8f"], [15, "#59a1a0"], [40, "#a8c268"],
          [100, "#f2b134"], [250, "#e8722c"], [500, "#d1382a"]]
FRP_PT = [[0, "#4a5bd4"], [10, "#39a0a8"], [50, "#c9cf4a"], [200, "#f0932b"],
          [1000, "#d63031"]]


def step(field, stops):
    e = ["step", ["coalesce", ["get", field], 0], stops[0][1]]
    for v, c in stops[1:]:
        e += [v, c]
    return e


def style(name, field, stops, pts_min_zoom):
    return {
        "version": 8,
        "name": name,
        "sources": {"data": {"type": "vector", "url": f"pmtiles://{PMTILES}"}},
        "layers": [
            {"id": "fire-cells", "type": "fill", "source": "data",
             "source-layer": "aggregate", "maxzoom": pts_min_zoom,
             "paint": {"fill-color": step(field, stops), "fill-opacity": 0.78,
                       "fill-outline-color": "rgba(0,0,0,0.2)"}},
            {"id": "fire-points", "type": "circle", "source": "data",
             "source-layer": "features", "minzoom": pts_min_zoom,
             "paint": {"circle-color": step("frp", FRP_PT),
                       "circle-radius": ["interpolate", ["linear"], ["zoom"],
                                         pts_min_zoom, 2.2, 14, 6],
                       "circle-opacity": 0.9}},
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--points-min-zoom", type=int, default=10)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    z = a.points_min_zoom

    written = []
    for fname, title, field, stops in [
        ("default.json", "Fire detections, last 7 days (density)", "count", COUNT),
        ("avg-frp.json", "Fire radiative power, last 7 days (average)", "avg_frp", AVGFRP),
    ]:
        p = out / fname
        p.write_text(json.dumps(style(title, field, stops, z), indent=2) + "\n")
        written.append(p.name)
    print(f"wrote {len(written)} style(s) to {out}: {', '.join(written)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
