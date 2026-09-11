/**
 * The timeline's stats sidecar: monthly counts over a coarse grid, no geometry.
 *
 * The timeline and the map want opposite things from one archive. The timeline
 * needs every time bucket across the whole grid; the map needs fine cells and
 * one selection at a time. Serving both from the tileset forces a zoomed-out
 * tile to carry 311 monthly columns on every cell, which is why the all-time
 * archive falls back to r4 there -- at r6 that tile is megabytes.
 *
 * So the timeline reads this instead: flat tables of cell id to counts, plus
 * one shared table of bounds. Bounds answer "is this cell in view", which is
 * all a histogram needs; the polygons stay in the tiles where the map wants
 * them. The result is the full r6 grid at any zoom for under two megabytes,
 * cached by the browser and scanned in single-digit milliseconds.
 *
 * Chunks are fetched newest first, because that is what the app paints on
 * arrival; the rest stream in behind it and are simply absent until they land.
 */

const HYPARQUET = "https://cdn.jsdelivr.net/npm/hyparquet@1.30.1/+esm";
const COMPRESSORS = "https://cdn.jsdelivr.net/npm/hyparquet-compressors@1.1.1/+esm";

let _lib = null;
async function lib() {
  if (!_lib) {
    // zstd is not in hyparquet's built-in codecs, and the files are written
    // with it, so the compressors package is not optional here.
    const [hq, hc] = await Promise.all([import(HYPARQUET), import(COMPRESSORS)]);
    _lib = {read: hq.parquetReadObjects, buffer: hq.asyncBufferFromUrl,
            compressors: hc.compressors};
  }
  return _lib;
}

async function readTable(url) {
  const {read, buffer, compressors} = await lib();
  const file = await buffer({url});
  return read({file, compressors});
}

/** Bucket keys a chunk carries, from its column names. */
function bucketColumns(row) {
  return Object.keys(row).filter(k => /^count_\d{6}$/.test(k));
}

export class Stats {
  constructor(base) {
    this.base = base.replace(/\/$/, "");
    // Parallel arrays, not a map of objects. Summing 22k cells across 311
    // months is about seven million reads, and doing that through object
    // property lookups took 687 ms; the same loop over typed arrays is a
    // couple of milliseconds. The tile path learned this already.
    this.ids = [];            // index -> a5_cell (string)
    this.bounds = null;       // Float64Array, 4 per cell
    this.idx = [];            // index -> Int32Array of bucket indices
    this.val = [];            // index -> Float64Array of counts
    this.keyIndex = new Map();// bucket key -> column in the dense axis
    this.keys = [];
    this.ready = false;
    this.pending = 0;
    this._row = new Map();    // a5_cell -> index, for absorbing later chunks
    this._acc = [];           // index -> [[bucket, value], ...] while loading
  }

  /**
   * Load the manifest, the bounds and the first chunk. Resolves as soon as
   * something is drawable; the remaining chunks continue in the background and
   * call `onChunk` as each lands.
   */
  async open(onChunk) {
    const manifest = await (await fetch(`${this.base}/stats.json`)).json();
    const [cells, first] = await Promise.all([
      readTable(`${this.base}/${manifest.cells}`),
      readTable(`${this.base}/${manifest.chunks[0].file}`),
    ]);
    // a5_cell arrives as BigInt; a Number would lose precision, so the string
    // form is the identity everywhere.
    this.bounds = new Float64Array(cells.length * 4);
    for (let i = 0; i < cells.length; i++) {
      const r = cells[i];
      this._row.set(String(r.a5_cell), i);
      this.ids.push(String(r.a5_cell));
      this.bounds[i * 4] = r.w; this.bounds[i * 4 + 1] = r.s;
      this.bounds[i * 4 + 2] = r.e; this.bounds[i * 4 + 3] = r.n;
      this._acc.push([]);
    }
    this.#absorb(first);
    this.ready = true;
    if (onChunk) onChunk(this);

    const rest = manifest.chunks.slice(1);
    this.pending = rest.length;
    for (const c of rest) {
      readTable(`${this.base}/${c.file}`)
        .then(rows => { this.#absorb(rows); this.pending--; if (onChunk) onChunk(this); })
        .catch(e => { this.pending--; console.warn("stats chunk failed", c.file, e); });
    }
    return this;
  }

  #absorb(rows) {
    if (!rows.length) return;
    const cols = bucketColumns(rows[0]);
    for (const c of cols) {
      const k = c.slice(6);
      if (!this.keyIndex.has(k)) {
        this.keyIndex.set(k, this.keys.length);
        this.keys.push(k);
      }
    }
    for (const r of rows) {
      const i = this._row.get(String(r.a5_cell));
      if (i === undefined) continue;   // a cell with counts but no bounds
      const acc = this._acc[i];
      for (const c of cols) {
        const v = r[c];
        if (v) acc.push(this.keyIndex.get(c.slice(6)), Number(v));
      }
    }
    this.#pack();
  }

  /** Freeze the accumulated pairs into typed arrays for scanning. */
  #pack() {
    for (let i = 0; i < this._acc.length; i++) {
      const a = this._acc[i];
      const n = a.length >> 1;
      const idx = new Int32Array(n), val = new Float64Array(n);
      for (let j = 0; j < n; j++) { idx[j] = a[j * 2]; val[j] = a[j * 2 + 1]; }
      this.idx[i] = idx; this.val[i] = val;
    }
  }

  /** Which months are loaded, sorted. */
  loadedKeys() { return [...this.keys].sort(); }

  /**
   * Sum every loaded month over the cells intersecting a bounding box.
   *
   * `inside` is passed in rather than reimplemented, so the antimeridian
   * handling matches the tile path exactly instead of nearly.
   */
  histogram(w, s, e, n, inside) {
    const total = new Float64Array(this.keys.length);
    const B = this.bounds;
    let cells = 0;
    for (let i = 0; i < this.ids.length; i++) {
      const o = i * 4;
      if (B[o + 1] > n || B[o + 3] < s) continue;
      if (!inside(B[o], B[o + 2], w, e)) continue;
      const idx = this.idx[i], val = this.val[i];
      if (!idx) continue;
      cells++;
      for (let j = 0; j < idx.length; j++) total[idx[j]] += val[j];
    }
    const buckets = {};
    for (let k = 0; k < this.keys.length; k++)
      if (total[k]) buckets[this.keys[k]] = total[k];
    return {buckets, cells};
  }
}
