/**
 * The deck.gl explorer's cell logic: selection counts, colours and the tile
 * cache that stands in for querySourceFeatures.
 *
 * MapLibre answers "what is in the loaded tiles" and "colour this cell by an
 * expression" on its own. deck.gl does neither: tiles arrive through
 * onTileLoad and leave through onTileUnload, and a cell's colour is whatever
 * JavaScript returns from the accessor. So the page keeps the tiles itself and
 * computes each cell's count in one place, and that place is here, where it
 * runs in node without deck.gl.
 */

import {rampColor} from "./timeline.js";

// ---------------------------------------------------------------- tile zoom

/**
 * The tile zoom deck.gl requests for a viewport zoom.
 *
 * MapLibre requests floor(zoom); deck.gl's TileLayer requests round(zoom). The
 * pyramid bands are keyed by the tile zoom, so everything that asks "which
 * band is on screen" has to round the same way deck.gl does.
 */
export function tileZoom(viewZoom) {
  return Math.round(viewZoom);
}

// ------------------------------------------------------------------- bounds

const MAX_LAT = 85.0511287798066;
const mercY = lat => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI / 180) / 2));
const latOfMerc = y => (2 * Math.atan(Math.exp(y)) - Math.PI / 2) * 180 / Math.PI;

/**
 * A tile-local coordinate as longitude and latitude.
 *
 * deck.gl parses MVT geometry in tile-local space, 0..1 from the north-west
 * corner, and renders it through a model matrix. The histogram needs
 * lon/lat, so the local coordinate is placed on the tile's bbox: linearly in
 * longitude, and linearly in mercator y, which is not linear in latitude.
 */
export function localToLngLat(x, y, bbox) {
  const n = mercY(Math.min(bbox.north, MAX_LAT));
  const s = mercY(Math.max(bbox.south, -MAX_LAT));
  return [bbox.west + x * (bbox.east - bbox.west), latOfMerc(n + y * (s - n))];
}

/** A feature's bounding box in lon/lat, from its tile-local geometry. */
export function featureBounds(feature, bbox) {
  let x0 = 1, x1 = 0, y0 = 1, y1 = 0;
  const walk = c => {
    if (typeof c[0] === "number") {
      if (c[0] < x0) x0 = c[0]; if (c[0] > x1) x1 = c[0];
      if (c[1] < y0) y0 = c[1]; if (c[1] > y1) y1 = c[1];
    } else for (const q of c) walk(q);
  };
  walk(feature.geometry.coordinates);
  const [w, n] = localToLngLat(x0, y0, bbox);
  const [e, s] = localToLngLat(x1, y1, bbox);
  return {w, s, e, n};
}

/** Longitude-cyclic overlap, so world copies do not drop cells off the edge. */
export function lngOverlaps(cw, ce, w, e) {
  if (e - w >= 360) return true;
  const k = Math.floor((w - cw) / 360) * 360;
  return (cw + k <= e && ce + k >= w) || (cw + k + 360 <= e && ce + k + 360 >= w);
}

// ------------------------------------------------------------ point buckets

/**
 * A point's bucket index on the axis, or -1.
 *
 * Points carry `day` as YYYYMMDD and `acq_date` as YYYY-MM-DD. Either is
 * enough; a monthly axis takes the first six digits.
 */
export function bucketIndex(props, axis) {
  let key = props.day != null ? String(props.day)
          : props.acq_date ? props.acq_date.replace(/-/g, "") : null;
  if (!key) return -1;
  if (axis.unit === "month") key = key.slice(0, 6);
  let i;
  try { i = axis.indexOf(key); } catch { return -1; }
  return i >= 0 && i < axis.count ? i : -1;
}

// ------------------------------------------------------------ selected count

/**
 * Everything the per-cell count needs, worked out once per selection change
 * rather than once per cell. The accessor then does at most two property
 * reads, or one Map lookup, per cell.
 *
 * @param {object} o
 * @param {object} o.axis      from makeAxis
 * @param {{from:number,to:number}} o.sel  bucket indices, inclusive
 * @param {Set<string>} o.columns  bucket keys the archive carries a column for
 * @param {boolean} [o.cumulative]  the archive carries count_c* running totals
 * @param {Array} [o.statsSums]  [id, total, id, total, ...] from the sidecar
 */
export function selectionPlan({axis, sel, columns, cumulative = false, statsSums = null}) {
  const whole = sel.from <= 0 && sel.to >= axis.count - 1;
  // Buckets the selection covers, for scaling the shipped breaks. Counted on
  // the axis, not on the columns present: a selection over months nothing
  // burned in is still that wide.
  const nsel = whole ? axis.declaredBuckets : sel.to - sel.from + 1;
  if (statsSums) {
    const byId = new Map();
    for (let i = 0; i < statsSums.length; i += 2) byId.set(statsSums[i], statsSums[i + 1]);
    return {whole, nsel, mode: "stats", byId};
  }
  if (whole) return {whole, nsel, mode: "whole"};
  const cols = [];
  for (let i = sel.from; i <= sel.to; i++) {
    const k = axis.keyAt(i);
    if (columns.has(k)) cols.push(k);
  }
  if (!cols.length) return {whole, nsel, mode: "none"};
  if (cumulative) {
    const before = axis.indexOf(cols[0]) - 1;
    return {whole, nsel, mode: "cum",
            hi: "count_c" + cols[cols.length - 1],
            lo: before >= 0 ? "count_c" + axis.keyAt(before) : null};
  }
  return {whole, nsel, mode: "sum", keys: cols.map(k => "count_" + k)};
}

/** A cell's detections within the planned selection. */
export function countFor(plan, p) {
  switch (plan.mode) {
    case "whole": return p.count || 0;
    case "stats": return plan.whole ? (p.count || 0) : (plan.byId.get(p.a5_cell) || 0);
    case "cum": return (p[plan.hi] || 0) - (plan.lo ? p[plan.lo] || 0 : 0);
    case "sum": {
      let s = 0;
      for (const k of plan.keys) s += p[k] || 0;
      return s;
    }
    default: return 0;
  }
}

// ------------------------------------------------------------------ colours

const TRANSPARENT = [0, 0, 0, 0];
const _rgba = new Map();

function rgba(hex) {
  let c = _rgba.get(hex);
  if (!c) {
    c = [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16),
         parseInt(hex.slice(5, 7), 16), 255];
    _rgba.set(hex, c);
  }
  return c;
}

/**
 * The fill for a count under a set of stops: transparent below the first
 * stop, otherwise the colour of the highest stop reached. Arrays are shared,
 * so the accessor allocates nothing per cell.
 */
export function fillFor(count, stops) {
  if (!(count >= stops[0][0])) return TRANSPARENT;
  return rgba(rampColor(stops, count));
}

// --------------------------------------------------------------- tile cache

const BUCKET_KEY = /^\d{6}$|^\d{8}$/;

/**
 * The loaded tiles, and per-cell records derived from them.
 *
 * A cell record is its lon/lat bounds and its non-zero buckets as parallel
 * index/value arrays, the same shape the MapLibre page caches. The bounds are
 * the union over every tile piece the cell has arrived in: a cell on a tile
 * seam is clipped, and the first piece alone can lie entirely outside a view
 * the whole cell is in.
 */
export class TileCache {
  constructor(axis) {
    this.axis = axis;
    this.tiles = new Map();   // tile id -> {z, bbox, features}
    this.cells = new Map();   // a5_cell -> record
  }

  add(tile) {
    this.tiles.set(tile.id, {z: tile.index.z, bbox: tile.bbox,
                             features: tile.content || []});
  }

  remove(tile) {
    this.tiles.delete(tile.id);
  }

  /** Properties of the first feature in any tile at those zooms, or null. */
  firstProps(z0, z1) {
    for (const t of this.tiles.values()) {
      if (t.z < z0 || t.z > z1) continue;
      if (t.features.length) return t.features[0].properties;
    }
    return null;
  }

  #record(f, bbox) {
    const p = f.properties, idx = [], val = [], axis = this.axis;
    for (const k in p) {
      if (!k.startsWith("count_")) continue;
      // Reject count_c*, count_d, count_modis on the first character before
      // the regex is reached; the regex per key was measured to matter.
      const c0 = k.charCodeAt(6);
      if (c0 < 48 || c0 > 57) continue;
      const key = k.slice(6);
      if (!BUCKET_KEY.test(key)) continue;
      const v = p[k];
      if (!v) continue;
      let i;
      try { i = axis.indexOf(key); } catch { continue; }
      if (i >= 0 && i < axis.count) { idx.push(i); val.push(v); }
    }
    return {...featureBounds(f, bbox),
            idx: Int32Array.from(idx), val: Float64Array.from(val)};
  }

  #cell(f, bbox) {
    const id = f.properties.a5_cell;
    let rec = this.cells.get(id);
    if (!rec) {
      rec = this.#record(f, bbox);
      rec.pieces = new Set([bbox.west + ":" + bbox.north]);
      this.cells.set(id, rec);
      return rec;
    }
    // Another piece of a known cell widens its bounds; the buckets are the
    // cell's own and are the same in every piece.
    const piece = bbox.west + ":" + bbox.north;
    if (!rec.pieces.has(piece)) {
      rec.pieces.add(piece);
      const b = featureBounds(f, bbox);
      if (b.w < rec.w) rec.w = b.w; if (b.e > rec.e) rec.e = b.e;
      if (b.s < rec.s) rec.s = b.s; if (b.n > rec.n) rec.n = b.n;
    }
    return rec;
  }

  /**
   * Sum the bucket columns over the cells in view, from tiles at zooms z0..z1.
   *
   * Restricting to one zoom range is what keeps two bands from being counted
   * together: MapLibre had to wait for tiles to settle to avoid that, and this
   * does not.
   */
  histogram(z0, z1, view) {
    const total = new Float64Array(this.axis.count);
    const seen = new Set();
    let cells = 0;
    for (const t of this.tiles.values()) {
      if (t.z < z0 || t.z > z1) continue;
      if (t.bbox.south > view.n || t.bbox.north < view.s ||
          !lngOverlaps(t.bbox.west, t.bbox.east, view.w, view.e)) {
        // Still absorb the pieces, so a cell's bounds are complete before it
        // is next tested, but nothing off screen is summed.
        for (const f of t.features) if (f.properties.a5_cell != null) this.#cell(f, t.bbox);
        continue;
      }
      for (const f of t.features) {
        const id = f.properties.a5_cell;
        if (id == null) continue;                    // a point, not a cell
        // Absorb every piece before the seen test, so the bounds are whole
        // by the time a later piece would have been the one in view.
        const rec = this.#cell(f, t.bbox);
        if (seen.has(id)) continue;
        seen.add(id);
        if (rec.s > view.n || rec.n < view.s || !lngOverlaps(rec.w, rec.e, view.w, view.e))
          continue;
        cells++;
        const {idx, val} = rec;
        for (let i = 0; i < idx.length; i++) total[idx[i]] += val[i];
      }
    }
    const buckets = {};
    for (let i = 0; i < total.length; i++)
      if (total[i]) buckets[this.axis.keyAt(i)] = total[i];
    return {buckets, cells};
  }

  /**
   * The same histogram, counted from raw detections in view.
   *
   * Points carry no id to dedupe on and are clipped to their tile, so a
   * detection on a seam can count twice. That is a rounding error against
   * thousands of points.
   */
  pointHistogram(z0, z1, view) {
    const total = new Float64Array(this.axis.count);
    let points = 0;
    for (const t of this.tiles.values()) {
      if (t.z < z0 || t.z > z1) continue;
      if (t.bbox.south > view.n || t.bbox.north < view.s ||
          !lngOverlaps(t.bbox.west, t.bbox.east, view.w, view.e)) continue;
      for (const f of t.features) {
        const c = f.geometry && f.geometry.coordinates;
        if (!c || typeof c[0] !== "number") continue;
        const [lng, lat] = localToLngLat(c[0], c[1], t.bbox);
        if (lat < view.s || lat > view.n || !lngOverlaps(lng, lng, view.w, view.e)) continue;
        const i = bucketIndex(f.properties, this.axis);
        if (i < 0) continue;
        total[i]++;
        points++;
      }
    }
    const buckets = {};
    for (let i = 0; i < total.length; i++)
      if (total[i]) buckets[this.axis.keyAt(i)] = total[i];
    return {buckets, points};
  }
}
