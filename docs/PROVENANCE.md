
## Class breaks and styles

Neither the legend thresholds nor the published styles are hand-chosen. Both
come from the data, and both are keyed by the a5 aggregate level rather than
by zoom.

`tools/make_breaks.py` takes quantiles of each metric over the cells of one
aggregate level, excluding cells with no detections, and writes them into the
tileset's own PMTiles metadata under `firms:breaks`:

```json
"firms:breaks": {
  "r6":  {"minzoom": 0, "maxzoom": 6, "metrics": {"count": [19, 86, ...]}},
  "r10": {"minzoom": 7, "maxzoom": 9, "metrics": {"count": [3, 9, ...]}}
}
```

The quantiles are 0.50, 0.75, 0.90, 0.96, 0.99 and 0.997 — weighted to the
upper tail, because counts per cell are heavily skewed and an even split puts
most of the classes inside the noise. Zero cells are left out: a cell with no
detections is not part of the distribution being classified, and including the
zeros drags every break down.

Keying by level, not by zoom, is deliberate. A level spans several zooms, so
keying by zoom lets the colours change while the cells on screen stay
identical — the same r8 cell reading as one class at z5 and another at z6.
Keyed by level, the classes change exactly when the cells change. The zoom
range each level occupies is copied in from `gpio:pyramid`, so a viewer matches
without knowing how the pyramid was built. Viewers must floor the zoom before
matching, since band edges are integers and MapLibre requests floor(zoom).

`tools/make_styles.py` then generates the style assets from those same breaks.
The fill colour steps on zoom first and on the metric second, giving each level
its own thresholds — necessary because the coarse level runs roughly thirty
times higher than the fine one, so a single threshold set saturates one end and
flattens the other. The output is ordinary MapLibre style JSON with no
extensions, so any viewer renders it, and it cannot drift from what the preview
app draws because both read the same numbers.
