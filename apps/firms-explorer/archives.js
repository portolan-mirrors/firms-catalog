/**
 * Reading a FIRMS archive's own description of itself.
 *
 * Everything here is pure: it takes the PMTiles metadata object (or a STAC
 * collection) and answers questions about it. Nothing touches MapLibre, the
 * network or the DOM, which is what archives.test.mjs exercises.
 *
 * The rule this file exists to enforce is that the viewer never guesses. The
 * time axis, the zoom bands and the class breaks are all declared by the
 * archive; inferring any of them from a sampled feature has already produced
 * bugs here, because MVT omits zero-valued attributes and a sampled cell
 * therefore carries only the months in which it happened to burn.
 */

// ------------------------------------------------------------------ metadata

/**
 * PMTiles metadata values arrive either as objects or as JSON strings,
 * depending on which tool wrote them. Accept both and never throw: a malformed
 * block should cost the feature that reads it, not the whole app.
 */
export function parseJSONish(v) {
  if (v == null) return null;
  if (typeof v !== "string") return v;
  try {
    return JSON.parse(v);
  } catch {
    return null;
  }
}

/** The `vector_layers` array, whichever of the two places it was written to. */
export function vectorLayers(md) {
  if (Array.isArray(md && md.vector_layers)) return md.vector_layers;
  const j = parseJSONish(md && md.json);
  return (j && Array.isArray(j.vector_layers)) ? j.vector_layers : [];
}

// A bucket column is `count_` plus six digits (YYYYMM) or eight (YYYYMMDD).
// The aggregate layer also carries `count_d`, `count_n` and `count_<sensor>`
// pivots, which are counts but not time buckets, so the digit test is what
// separates them.
const BUCKET_COL = /^count_(\d{6}|\d{8})$/;

/** Bucket key suffixes present in the aggregate layer's declared fields. */
export function bucketColumns(md, layerId = "aggregate") {
  const layer = vectorLayers(md).find(l => l.id === layerId);
  if (!layer) return [];
  return Object.keys(layer.fields || {})
    .filter(k => BUCKET_COL.test(k))
    .map(k => k.slice(6))
    .sort();
}

/**
 * The archive's `firms:timeline`, or a reconstruction of it.
 *
 * The declaration is authoritative and is what the spec requires every archive
 * to carry, but `fire-latest.pmtiles` does not yet have one. Falling back to
 * the *declared field list* is safe in a way that sampling a feature is not:
 * `vector_layers[].fields` is written from the whole table, so a month that is
 * zero in one cell still appears. The caller is told which happened so it can
 * say so rather than pretending the archive declared an axis it did not.
 *
 * @returns {{meta: object|null, declared: boolean, columns: string[]}}
 */
export function timelineFromMetadata(md) {
  const columns = bucketColumns(md);
  const declared = parseJSONish(md && md["firms:timeline"]);
  if (declared && declared.unit && declared.min && declared.max)
    return {meta: declared, declared: true, columns};
  if (!columns.length) return {meta: null, declared: false, columns};

  const width = columns[0].length;
  // Mixed widths mean two different units in one layer, which no build
  // produces and which there is no correct axis for. Refuse rather than pick.
  if (columns.some(c => c.length !== width))
    return {meta: null, declared: false, columns};
  const unit = width === 6 ? "month" : width === 8 ? "day" : null;
  if (!unit) return {meta: null, declared: false, columns};

  return {
    meta: {
      unit,
      key_format: unit === "month" ? "count_YYYYMM" : "count_YYYYMMDD",
      min: columns[0],
      max: columns[columns.length - 1],
      buckets: columns.length,
    },
    declared: false,
    columns,
  };
}

// --------------------------------------------------------------------- bands

/**
 * The pyramid band covering a zoom.
 *
 * Band edges are integers and MapLibre requests `floor(zoom)`, so the zoom has
 * to be floored before it is matched. Comparing 4.9 against a band ending at 4
 * and one starting at 5 matches neither, and the shipped breaks then silently
 * vanish for the whole fractional part of every zoom.
 */
export function bandAt(bands, zoom) {
  const iz = Math.floor(zoom);
  return (bands || []).find(
    b => iz >= b.minzoom && (b.maxzoom == null || iz <= b.maxzoom)) || null;
}

/** The pyramid, or an empty one. */
export function pyramid(md) {
  const py = parseJSONish(md && md["gpio:pyramid"]);
  return (py && Array.isArray(py.bands)) ? py : {bands: [], scheme: null};
}

/**
 * Lowest zoom at which the archive carries raw points.
 *
 * `Infinity` for an aggregate-only archive: there is no zoom at which its
 * points exist, and defaulting to 10 would hide the cells above z10 and draw
 * nothing in their place.
 */
export function pointsMinZoom(bands) {
  const f = (bands || []).find(b => b.layer === "features");
  return f ? f.minzoom : Infinity;
}

// -------------------------------------------------------------------- breaks

/**
 * Class thresholds for a metric at a zoom, from the archive's `firms:breaks`.
 *
 * `firms:breaks` is keyed by a5 level so the classes hold steady across every
 * zoom that draws the same cells, and only change when the cells themselves
 * do. It ships six thresholds; the palette has seven colours, the first of
 * which starts at the ramp's own floor so a cell with any detections is still
 * coloured.
 *
 * `scale` exists because the breaks describe a cell's total over the WHOLE
 * archive. Selecting one month of 311 and colouring by the unscaled breaks
 * puts every cell in the bottom class and paints the map one flat colour,
 * which says nothing and makes the selection look broken. Scaling the
 * thresholds by the share of the record on screen keeps the shipped
 * classification's shape while leaving something to see.
 */
export function shippedStops(breaks, level, metric, palette, scale = 1) {
  if (!breaks || level == null) return null;
  const byLevel = breaks["r" + level];
  const vals = byLevel && byLevel.metrics && byLevel.metrics[metric];
  if (!Array.isArray(vals) || !vals.length) return null;
  const out = [[palette[0][0], palette[0][1]]];
  vals.slice(0, palette.length - 1).forEach((v, i) => {
    // Round up, and keep the ladder strictly increasing. A heavily scaled
    // break set otherwise collapses several classes onto the same integer and
    // MapLibre rejects a `step` whose stops do not ascend.
    const want = Math.max(1, Math.ceil(v * scale));
    const prev = out[out.length - 1][0];
    out.push([Math.max(want, prev + 1), palette[i + 1][1]]);
  });
  return out;
}

// --------------------------------------------------------- archive selection

// Calendar helpers on bucket keys. Only ever used to widen a domain to the
// days it covers, so month keys expand to the whole month.
const DAYS_IN = (y, m) =>
  m === 2 ? ((y % 4 === 0 && y % 100 !== 0) || y % 400 === 0 ? 29 : 28)
          : [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1];

/** First and last calendar day a bucket key covers, as YYYYMMDD. */
export function keyDayRange(key, unit) {
  const k = String(key);
  if (unit === "day") return [k, k];
  const y = +k.slice(0, 4), m = +k.slice(4, 6);
  return [k + "01", k + String(DAYS_IN(y, m)).padStart(2, "0")];
}

/**
 * Which archive should serve a visible domain, per the spec's containment rule.
 *
 * The *domain* selects the archive, not the selection: the archive decides
 * what temporal resolution is available, and that is a property of what is on
 * screen. The selection then filters within whatever is mounted.
 *
 * The rule is calendar-year containment, which is predictable and keeps
 * exactly one source mounted. Its accepted limitation is that a sub-year
 * window straddling a year boundary — November to February — falls to
 * `alltime` and therefore to monthly bars, because serving it daily would mean
 * mounting two year archives at once and a cell present in both would draw
 * twice.
 *
 * @param {{from: string, to: string}} domain  bucket keys, inclusive
 * @param {string} unit  "month" | "day"
 * @param {{latest?: {min: string, max: string}, years?: string[]}} available
 * @returns {string} an archive id: "latest", a four-digit year, or "alltime"
 */
export function chooseArchive(domain, unit, available = {}) {
  const [d0] = keyDayRange(domain.from, unit);
  const [, d1] = keyDayRange(domain.to, unit);
  const latest = available.latest;
  if (latest && d0 >= latest.min && d1 <= latest.max) return "latest";
  const y = d0.slice(0, 4);
  if (y === d1.slice(0, 4) && (available.years || []).includes(y)) return y;
  return "alltime";
}
