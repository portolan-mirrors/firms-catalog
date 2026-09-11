/**
 * Adaptive timeline for the FIRMS explorer.
 *
 * One track across the bottom of the app covering whatever span the mounted
 * archive declares, from seven days to twenty-seven years. The component knows
 * nothing about MapLibre, PMTiles or fire: it is handed a `firms:timeline`
 * object and a bucket -> value mapping, and it reports back which buckets the
 * user has selected.
 *
 * Everything above the `Timeline` class is pure and side-effect free, which is
 * what timeline.test.mjs exercises. The class is the only part that touches a
 * canvas.
 */

// ---------------------------------------------------------------- bucket keys

// A bucket key is the digits an archive appends to `count_`: YYYYMM on a
// monthly axis, YYYYMMDD on a daily one. Four-digit keys are deliberately not
// a unit -- `0101` could be a month-day or a year, and telling them apart
// would need a second field. tools/timeline_meta.py rejects them for the same
// reason, so the two ends of the contract agree.
const UNITS = {
  month: {digits: 6, keyFormat: "count_YYYYMM"},
  day: {digits: 8, keyFormat: "count_YYYYMMDD"},
};

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Days since 1970-01-01, by Hinnant's civil-calendar algorithm. Date is
// avoided on purpose: every path through it is timezone-sensitive, and a
// bucket key names a calendar day, not an instant. Getting that wrong shifts
// the whole axis by one bucket west of UTC.
export function daysFromCivil(y, m, d) {
  const yy = y - (m <= 2 ? 1 : 0);
  const era = Math.floor(yy / 400);
  const yoe = yy - era * 400;
  const doy = Math.floor((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5) + d - 1;
  const doe = yoe * 365 + Math.floor(yoe / 4) - Math.floor(yoe / 100) + doy;
  return era * 146097 + doe - 719468;
}

export function civilFromDays(z) {
  const zz = z + 719468;
  const era = Math.floor(zz / 146097);
  const doe = zz - era * 146097;
  const yoe = Math.floor(
    (doe - Math.floor(doe / 1460) + Math.floor(doe / 36524)
     - Math.floor(doe / 146096)) / 365);
  const y = yoe + era * 400;
  const doy = doe - (365 * yoe + Math.floor(yoe / 4) - Math.floor(yoe / 100));
  const mp = Math.floor((5 * doy + 2) / 153);
  const d = doy - Math.floor((153 * mp + 2) / 5) + 1;
  const m = mp + (mp < 10 ? 3 : -9);
  return [y + (m <= 2 ? 1 : 0), m, d];
}

/** Absolute ordinal of a bucket key: months since year 0, or days since epoch. */
export function keyIndex(key, unit) {
  const u = UNITS[unit];
  if (!u) throw new Error(`unknown bucket unit ${JSON.stringify(unit)}`);
  if (typeof key !== "string" || key.length !== u.digits || !/^\d+$/.test(key))
    throw new Error(
      `${unit} bucket key must be ${u.digits} digits, got ${JSON.stringify(key)}`);
  const y = +key.slice(0, 4), m = +key.slice(4, 6);
  if (m < 1 || m > 12) throw new Error(`bucket key ${key} has no month ${m}`);
  if (unit === "month") return y * 12 + (m - 1);
  const d = +key.slice(6, 8);
  if (d < 1 || d > 31) throw new Error(`bucket key ${key} has no day ${d}`);
  return daysFromCivil(y, m, d);
}

/** Inverse of keyIndex. */
export function indexKey(index, unit) {
  const i = Math.round(index);
  if (unit === "month") {
    const y = Math.floor(i / 12), m = i - y * 12 + 1;
    return String(y).padStart(4, "0") + String(m).padStart(2, "0");
  }
  if (unit !== "day") throw new Error(`unknown bucket unit ${JSON.stringify(unit)}`);
  const [y, m, d] = civilFromDays(i);
  return String(y).padStart(4, "0") + String(m).padStart(2, "0")
       + String(d).padStart(2, "0");
}

/**
 * Order two bucket keys. Keys of one unit are fixed-width and zero-padded, so
 * string order is calendar order; the width test only stops a monthly key
 * being compared against a daily one and silently winning.
 */
export function compareKeys(a, b) {
  if (a.length !== b.length)
    throw new Error(`cannot compare ${a} with ${b}: different bucket units`);
  return a < b ? -1 : a > b ? 1 : 0;
}

// ----------------------------------------------------------------------- axis

/**
 * The drawable axis for a `firms:timeline` declaration.
 *
 * `{unit, key_format, min, max, buckets}`, exactly as the archives carry it.
 * The axis is read from metadata and never from a sampled feature: MVT omits
 * zero-valued attributes, so `feats[0]` once yielded three months of a
 * twelve-month year.
 */
export function makeAxis(meta) {
  if (!meta || typeof meta !== "object")
    throw new Error("firms:timeline is missing");
  const unit = meta.unit;
  const u = UNITS[unit];
  if (!u) throw new Error(`firms:timeline declares unknown unit ${JSON.stringify(unit)}`);
  const declared = meta.key_format;
  if (declared != null && declared !== u.keyFormat)
    throw new Error(
      `firms:timeline says unit ${unit} but key_format ${declared}; ` +
      `expected ${u.keyFormat}`);
  const origin = keyIndex(meta.min, unit);
  const last = keyIndex(meta.max, unit);
  if (last < origin)
    throw new Error(`firms:timeline min ${meta.min} is after max ${meta.max}`);
  const count = last - origin + 1;
  return {
    unit,
    keyFormat: u.keyFormat,
    min: meta.min,
    max: meta.max,
    origin,
    count,
    // What the archive says it carries. It differs from `count` only when the
    // archive is sparse -- a month with no detections anywhere has no column --
    // and the axis still has to draw that gap, so the calendar span wins and
    // the declaration is kept for the caller to report.
    declaredBuckets: meta.buckets == null ? count : meta.buckets,
    keyAt: i => indexKey(origin + i, unit),
    indexOf: key => keyIndex(key, unit) - origin,
  };
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A bucket key as a person reads it: "Nov 2000", "5 Jul 2020". */
export function formatKey(key, unit) {
  if (key == null) return "";
  const k = String(key);
  if (unit === "month")
    return `${MONTHS[+k.slice(4, 6) - 1]} ${k.slice(0, 4)}`;
  return `${+k.slice(6, 8)} ${MONTHS[+k.slice(4, 6) - 1]} ${k.slice(0, 4)}`;
}

/**
 * A bucket key as an <input type="month"|"date"> wants it.
 *
 * Native inputs are used rather than a hand-built picker because they carry a
 * calendar widget, keyboard entry, locale-aware display and validation that
 * would otherwise all have to be written and none of which is the point here.
 */
export function keyToISO(key, unit) {
  const k = String(key);
  return unit === "month"
    ? `${k.slice(0, 4)}-${k.slice(4, 6)}`
    : `${k.slice(0, 4)}-${k.slice(4, 6)}-${k.slice(6, 8)}`;
}

/** The inverse. Returns null when the value is not a complete date. */
export function isoToKey(iso, unit) {
  if (!iso) return null;
  const digits = String(iso).replace(/-/g, "");
  const want = unit === "month" ? 6 : 8;
  return digits.length === want && /^\d+$/.test(digits) ? digits : null;
}

// --------------------------------------------------------------------- domain

// Two buckets is the floor. Zooming to a single bucket leaves nothing to pan
// against and makes the cursor invariant untestable, since every x maps to the
// same bucket.
export const MIN_SPAN = 2;

/** Bucket coordinate under a pixel. Bucket i covers the half-open [i, i+1). */
export function xToCoord(x, domain, width) {
  return domain[0] + (width > 0 ? x / width : 0) * (domain[1] - domain[0]);
}

/** Pixel for a bucket coordinate. */
export function coordToX(t, domain, width) {
  const span = domain[1] - domain[0];
  return span > 0 ? ((t - domain[0]) / span) * width : 0;
}

/** The bucket under a pixel. */
export function bucketAt(x, domain, width) {
  return Math.floor(xToCoord(x, domain, width));
}

/**
 * Keep the domain inside the data, preserving its span. Clamping shifts rather
 * than truncates so that panning into the edge slides to a stop instead of
 * silently zooming out.
 */
export function clampDomain(domain, count, minSpan = MIN_SPAN) {
  const full = Math.max(count, 1);
  const floor = Math.min(minSpan, full);
  let span = domain[1] - domain[0];
  if (!(span > 0)) span = floor;
  span = Math.min(Math.max(span, floor), full);
  let lo = domain[0];
  if (!(lo >= 0)) lo = 0;
  if (lo + span > full) lo = full - span;
  return [lo, lo + span];
}

/**
 * Zoom about a cursor. `factor > 1` zooms in.
 *
 * The bucket coordinate under `x` is held fixed by construction: it is
 * computed before the span changes and the new domain is placed so that it
 * lands back under the same pixel. Clamping at the ends of the data is the one
 * thing allowed to break that, because the alternative is a domain that runs
 * off the axis.
 */
export function zoomDomain(domain, x, width, factor, count, minSpan = MIN_SPAN) {
  const span = domain[1] - domain[0];
  const f = width > 0 ? Math.min(Math.max(x / width, 0), 1) : 0;
  const anchor = domain[0] + f * span;
  const full = Math.max(count, 1);
  const next = Math.min(Math.max(span / factor, Math.min(minSpan, full)), full);
  const lo = anchor - f * next;
  return clampDomain([lo, lo + next], count, minSpan);
}

/** Drag the track: moving the pointer right brings earlier buckets into view. */
export function panDomain(domain, dx, width, count, minSpan = MIN_SPAN) {
  const span = domain[1] - domain[0];
  const shift = width > 0 ? (-dx * span) / width : 0;
  return clampDomain([domain[0] + shift, domain[1] + shift], count, minSpan);
}

/** The whole buckets the domain touches, as inclusive axis indices. */
export function domainBuckets(domain, count) {
  const i0 = Math.max(0, Math.floor(domain[0]));
  const i1 = Math.min(count - 1, Math.ceil(domain[1]) - 1);
  return [i0, Math.max(i0, i1)];
}

// ------------------------------------------------------------------ selection

/**
 * Hold the selection inside `bounds` (inclusive bucket indices), keeping its
 * width where the bounds are wide enough. Squashing a dragged selection
 * against the edge instead of sliding it makes it impossible to get back.
 */
export function clampSelection(sel, bounds) {
  const [b0, b1] = bounds;
  let from = Math.round(sel.from), to = Math.round(sel.to);
  if (to < from) [from, to] = [to, from];
  const width = Math.min(to - from, b1 - b0);
  from = Math.min(Math.max(from, b0), b1 - width);
  return {from, to: from + width};
}

export function moveSelection(sel, delta, bounds) {
  return clampSelection({from: sel.from + delta, to: sel.to + delta}, bounds);
}

/**
 * Drag one edge to a bucket. Dragging an edge past the other flips which
 * handle is live, so the returned `edge` is what the drag must follow -- not
 * doing that makes the selection stick at zero width.
 */
export function resizeSelection(sel, edge, index, bounds) {
  const [b0, b1] = bounds;
  const i = Math.min(Math.max(Math.round(index), b0), b1);
  let from = edge === "from" ? i : sel.from;
  let to = edge === "to" ? i : sel.to;
  let active = edge;
  if (to < from) {
    [from, to] = [to, from];
    active = edge === "from" ? "to" : "from";
  }
  return {from, to, edge: active};
}

// -------------------------------------------------------- series and bar cost

/**
 * Prefix sums over the bucket values.
 *
 * This is what makes draw cost independent of bucket count. Without it a pixel
 * column that covers 30 buckets costs 30 reads and a full repaint is O(N); with
 * it every column is one subtraction and a repaint is O(W). The sums are built
 * once, when the values change.
 */
export function makeSeries(values) {
  const n = values.length;
  const prefix = new Float64Array(n + 1);
  for (let i = 0; i < n; i++) prefix[i + 1] = prefix[i] + (values[i] || 0);
  return {n, prefix};
}

/** Total over the inclusive bucket range, clipped to the series. */
export function sumRange(series, i0, i1) {
  const a = Math.max(0, Math.floor(i0));
  const b = Math.min(series.n - 1, Math.floor(i1));
  return b < a ? 0 : series.prefix[b + 1] - series.prefix[a];
}

/** Bucket values from a `{key: value}` or `{count_key: value}` mapping. */
export function seriesFromBuckets(axis, buckets) {
  const v = new Float64Array(axis.count);
  for (const [k, value] of Object.entries(buckets || {})) {
    const key = k.startsWith("count_") ? k.slice(6) : k;
    let i;
    try {
      i = axis.indexOf(key);
    } catch {
      continue;              // a sensor or day/night column, not a time bucket
    }
    if (i >= 0 && i < axis.count) v[i] = value;
  }
  return makeSeries(v);
}

/**
 * The bars to draw for `count` buckets across a track `width` pixels wide.
 *
 * Bars quantize to pixel columns, not to buckets. When there are at least as
 * many buckets as pixels each column is one pixel and aggregates the buckets
 * falling in it; when there are fewer, each bucket gets a bar `floor(W/N)`
 * pixels wide, placed at `floor(i*W/N)` so the bars still span the whole track
 * rather than leaving the remainder as a gap at the right-hand end.
 *
 * Either way the result is at most `W` entries, so a 311-bucket axis and a
 * 9,750-bucket axis cost the same to paint.
 */
export function quantize(width, count) {
  const W = Math.floor(width), N = Math.floor(count);
  if (!(W > 0) || !(N > 0)) return [];
  const cols = [];
  if (N >= W) {
    for (let p = 0; p < W; p++) {
      const i0 = Math.floor((p * N) / W);
      const i1 = Math.floor(((p + 1) * N) / W) - 1;
      cols.push({x: p, w: 1, i0, i1: Math.max(i0, i1)});
    }
  } else {
    const bw = Math.floor(W / N);
    for (let i = 0; i < N; i++)
      cols.push({x: Math.floor((i * W) / N), w: bw, i0: i, i1: i});
  }
  return cols;
}

// ---------------------------------------------------------------------- ticks

// Drawn width of a selection handle. The hit tolerance is separate and wider,
// because a 6px target is comfortable to see and uncomfortable to grab.
const HANDLE_W = 6;

// Opening view: select the last week, show the last nine months around it.
const DEFAULT_SELECT_DAYS = 7;
const DEFAULT_WINDOW_DAYS = 274;
// Breathing room either side when fitting the domain to the selection.
const FIT_MARGIN = 0.12;

// A label needs this much room before the next one, or they collide.
const MIN_LABEL_PX = 54;
// Month-axis steps, in months. Every one divides evenly into the absolute
// month ordinal (year*12 + month-1), so ticks land on January, or on quarters
// for the step of 3, without any extra alignment pass.
const MONTH_STEPS = [1, 3, 12, 24, 60, 120, 600];
// Day-axis steps wider than a week, in months.
const DAY_MONTH_STEPS = [1, 3, 12, 60];

function* monthStarts(fromDay, toDay) {
  let [y, m] = civilFromDays(Math.floor(fromDay));
  let d = daysFromCivil(y, m, 1);
  while (d <= toDay) {
    yield [d, y, m];
    if (++m > 12) { m = 1; y += 1; }
    d = daysFromCivil(y, m, 1);
  }
}

/**
 * Labelled ticks for the visible domain, in axis-relative bucket indices.
 *
 * Ticks are generated from the calendar rather than by scanning the visible
 * buckets, so the work is proportional to the number of labels drawn and not
 * to the span. At a 27-year view that is the difference between 30 iterations
 * and 9,750.
 */
export function ticks(axis, domain, width) {
  const span = domain[1] - domain[0];
  if (!(span > 0) || !(width > 0)) return [];
  const px = width / span;
  const a0 = axis.origin + domain[0], a1 = axis.origin + domain[1];
  const out = [];
  const push = (abs, label, major) => {
    if (abs >= a0 && abs < a1)
      out.push({index: abs - axis.origin, label, major});
  };

  if (axis.unit === "month") {
    const step = MONTH_STEPS.find(s => s * px >= MIN_LABEL_PX)
              || MONTH_STEPS[MONTH_STEPS.length - 1];
    for (let i = Math.ceil(a0 / step) * step; i < a1; i += step) {
      const y = Math.floor(i / 12), m = i - y * 12 + 1;
      push(i, step >= 12 ? String(y) : m === 1 ? `${MON[0]} ${y}` : MON[m - 1],
           m === 1);
    }
    return out;
  }

  if (px >= MIN_LABEL_PX) {
    for (let i = Math.ceil(a0); i < a1; i++) {
      const [y, m, d] = civilFromDays(i);
      push(i, d === 1 ? `${MON[m - 1]} ${d}` : String(d), d === 1);
    }
    return out;
  }
  if (px * 7 >= MIN_LABEL_PX) {
    // A day-of-month grid, not a rolling week: ticks that drift with the
    // domain make the axis look like it is sliding under the bars.
    for (const [start, , m] of monthStarts(a0, a1))
      for (const d of [1, 8, 15, 22])
        push(start + d - 1, d === 1 ? MON[m - 1] : String(d), d === 1);
    return out;
  }
  const step = DAY_MONTH_STEPS.find(s => s * 30.44 * px >= MIN_LABEL_PX)
            || DAY_MONTH_STEPS[DAY_MONTH_STEPS.length - 1];
  for (const [start, y, m] of monthStarts(a0, a1)) {
    if (step >= 12) {
      if (m === 1 && y % (step / 12) === 0) push(start, String(y), true);
    } else if ((m - 1) % step === 0) {
      push(start, m === 1 ? String(y) : MON[m - 1], m === 1);
    }
  }
  return out;
}

// -------------------------------------------------------------------- palette

// The preview app's palette, so the two apps look like one catalog. Kept as
// literals rather than read off CSS custom properties: the component draws to
// a canvas, where a var() is not a colour.
export const THEME = {
  panel: "#12171f", inset: "#0b0f15", line: "#2b3440",
  fg: "#e6edf3", dim: "#8b949e", acc: "#f78166",
};

// RAMPS.count from apps/firms-preview/index.html. The map and the timeline
// have to agree about what "hot" means, so the bars use the map's ramp.
export const COUNT_RAMP = [
  [1, "#2c3d5a"], [5, "#3f6d8f"], [20, "#59a1a0"], [75, "#a8c268"],
  [250, "#f2b134"], [1000, "#e8722c"], [4000, "#d1382a"],
];

export function rampColor(stops, v) {
  let c = stops[0][1];
  for (const [t, col] of stops) if (v >= t) c = col;
  return c;
}

const fmtNum = n =>
  n >= 1e6 ? (n / 1e6).toFixed(1) + "M"
  : n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k"
  : String(Math.round(n));

// ------------------------------------------------------------------ component

const AXIS_H = 15;      // label strip under the track
const GRIP = 6;         // px either side of an edge that grabs the handle
const WHEEL = 0.0035;   // radians of zoom per wheel unit
const PINCH = 0.012;    // ctrl+wheel arrives in much smaller units

export class Timeline {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {object} opts  onChange, ramp, metricLabel
   */
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.onChange = opts.onChange || (() => {});
    this.ramp = opts.ramp || COUNT_RAMP;
    this.metricLabel = opts.metricLabel || "detections";

    this.axis = null;
    this.series = makeSeries([]);
    this.domain = [0, 1];
    this.selection = {from: 0, to: 0};
    this.hover = null;

    this.width = 0;
    this.height = 0;
    this._frame = 0;
    this._drag = null;
    this._pointers = new Map();
    this._pinch = null;

    canvas.style.touchAction = "none";   // we own pinch and drag
    if (!canvas.hasAttribute("tabindex")) canvas.tabIndex = 0;

    this._on = {
      wheel: e => this._onWheel(e),
      down: e => this._onDown(e),
      move: e => this._onMove(e),
      up: e => this._onUp(e),
      leave: () => { this.hover = null; this.draw(); },
      key: e => this._onKey(e),
    };
    canvas.addEventListener("wheel", this._on.wheel, {passive: false});
    canvas.addEventListener("pointerdown", this._on.down);
    canvas.addEventListener("pointermove", this._on.move);
    canvas.addEventListener("pointerup", this._on.up);
    canvas.addEventListener("pointercancel", this._on.up);
    canvas.addEventListener("pointerleave", this._on.leave);
    canvas.addEventListener("keydown", this._on.key);

    this._ro = new ResizeObserver(() => this.resize());
    this._ro.observe(canvas);
    this.resize();
  }

  destroy() {
    const c = this.canvas;
    c.removeEventListener("wheel", this._on.wheel);
    c.removeEventListener("pointerdown", this._on.down);
    c.removeEventListener("pointermove", this._on.move);
    c.removeEventListener("pointerup", this._on.up);
    c.removeEventListener("pointercancel", this._on.up);
    c.removeEventListener("pointerleave", this._on.leave);
    c.removeEventListener("keydown", this._on.key);
    this._ro.disconnect();
  }

  /** Mount a `firms:timeline` declaration and its bucket values. */
  setSource(meta, buckets) {
    this.axis = makeAxis(meta);
    this.series = seriesFromBuckets(this.axis, buckets);
    this.openAtDefault();
  }

  /**
   * Open on the most recent activity rather than the whole record.
   *
   * Selecting everything shows a global average and hides every event in it,
   * and it also leaves the handles pinned to the frame with nothing to grab.
   * The defaults are expressed in real time, not bucket counts, so they mean
   * the same thing on a daily and a monthly axis: select the last week, show
   * the last nine months around it.
   *
   * A week is finer than one monthly bucket, so on a monthly axis the
   * selection clamps to the final bucket -- the same intent at the resolution
   * the archive actually has.
   */
  openAtDefault(selectDays = DEFAULT_SELECT_DAYS,
                windowDays = DEFAULT_WINDOW_DAYS) {
    if (!this.axis) return;
    const per = this.axis.unit === "month" ? 30.44 : 1;
    const c = this.axis.count;
    const sel = Math.max(1, Math.round(selectDays / per));
    const win = Math.max(sel + 1, Math.round(windowDays / per));
    this.domain = clampDomain([c - win, c], c);
    this.selection = clampSelection({from: c - sel, to: c - 1}, this.bounds());
    this._emit();
    this.draw();
  }

  /**
   * Fit the visible domain to the selection, with a margin.
   *
   * Without the margin the handles land exactly on the frame, where they are
   * neither visible nor grabbable -- the same reason the default selection is
   * not the whole axis.
   */
  fitToSelection() {
    if (!this.axis) return;
    const {from, to} = this.selection;
    const pad = Math.max(1, Math.round((to - from + 1) * FIT_MARGIN));
    this.domain = clampDomain([from - pad, to + 1 + pad], this.axis.count);
    this._emit();
    this.draw();
  }

  /** Replace the metric without disturbing the view. */
  setValues(buckets) {
    if (!this.axis) return;
    this.series = seriesFromBuckets(this.axis, buckets);
    this.draw();
  }

  /** Bucket range the user can drag a selection edge to: what is on screen. */
  bounds() {
    return this.axis ? domainBuckets(this.domain, this.axis.count) : [0, 0];
  }

  /**
   * Bucket range a selection must stay inside no matter what: the whole axis.
   *
   * Panning and zooming deliberately leave the selection alone. Re-clamping it
   * to the visible domain would drag the filter along with the view, so you
   * could never zoom out to see a narrow selection in context -- and, because
   * the selection would then always fill the track, there would be no
   * background left to drag on.
   */
  axisBounds() {
    return this.axis ? [0, this.axis.count - 1] : [0, 0];
  }

  selectionKeys() {
    if (!this.axis) return null;
    return {from: this.axis.keyAt(this.selection.from),
            to: this.axis.keyAt(this.selection.to)};
  }

  domainKeys() {
    if (!this.axis) return null;
    const [i0, i1] = domainBuckets(this.domain, this.axis.count);
    return {from: this.axis.keyAt(i0), to: this.axis.keyAt(i1)};
  }

  /**
   * Set the selection from bucket keys, for the date inputs.
   *
   * Reversed input is swapped rather than rejected: typing the later date
   * first is an ordinary way to fill two fields, and refusing it would leave
   * the control feeling broken mid-edit.
   */
  setSelectionKeys(from, to) {
    if (!this.axis) return;
    let a = this.axis.indexOf(from), b = this.axis.indexOf(to);
    if (a > b) [a, b] = [b, a];
    this.selection = clampSelection({from: a, to: b}, this.bounds());
    this._emit();
    this.draw();
  }

  setDomainKeys(from, to) {
    if (!this.axis) return;
    this.domain = clampDomain(
      [this.axis.indexOf(from), this.axis.indexOf(to) + 1], this.axis.count);
    this._emit();
    this.draw();
  }

  /** Show the last `n` buckets, for the preset spans. */
  setSpan(n) {
    if (!this.axis) return;
    const c = this.axis.count;
    this.domain = clampDomain([c - n, c], c);
    this._emit();
    this.draw();
  }

  zoomAt(x, factor) {
    if (!this.axis) return;
    this.domain = zoomDomain(
      this.domain, x, this.width, factor, this.axis.count);
    this._emit();
    this.draw();
  }

  /** Zoom about the middle of the track, for the buttons. */
  /**
   * Zoom about the selection, not the track centre.
   *
   * The buttons exist to get a closer look at what is selected. Anchoring them
   * to the middle of the track walks the selection off screen after a few
   * presses, which is the opposite of what pressing them means.
   */
  zoomBy(factor) {
    const mid = this.axis
      ? coordToX((this.selection.from + this.selection.to + 1) / 2,
                 this.domain, this.width)
      : this.width / 2;
    this.zoomAt(Math.min(Math.max(mid, 0), this.width), factor);
  }

  reset() {
    if (!this.axis) return;
    this.domain = [0, this.axis.count];
    this.selection = {from: 0, to: this.axis.count - 1};
    this._emit();
    this.draw();
  }

  resize() {
    const dpr = window.devicePixelRatio || 1;
    const r = this.canvas.getBoundingClientRect();
    this.width = Math.max(0, Math.round(r.width));
    this.height = Math.max(0, Math.round(r.height));
    this.canvas.width = Math.round(this.width * dpr);
    this.canvas.height = Math.round(this.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.draw();
  }

  // Coalesce to one paint per frame. Wheel and pointermove both fire far
  // faster than the display, and each paint walks the whole track.
  draw() {
    if (this._frame) return;
    this._frame = requestAnimationFrame(() => {
      this._frame = 0;
      this._paint();
    });
  }

  _emit() {
    this.onChange({
      domain: this.domain.slice(),
      selection: {...this.selection},
      keys: this.selectionKeys(),
      // True while a pointer gesture is still in flight. A listener doing
      // expensive work per event can throttle on this and catch up on the
      // final event, which is the only one the user waits to see.
      dragging: this._drag != null || this._pinch != null,
    });
  }

  // ------------------------------------------------------------------- paint

  _paint() {
    const {ctx} = this, W = this.width, H = this.height;
    if (!(W > 0) || !(H > 0)) return;
    const trackH = Math.max(0, H - AXIS_H);
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = THEME.inset;
    ctx.fillRect(0, 0, W, trackH);
    if (!this.axis) return;

    const cols = this._columns();
    const tk = ticks(this.axis, this.domain, W);

    // Gridlines first, so bars sit on top of them.
    for (const t of tk) {
      const x = Math.round(coordToX(t.index, this.domain, W)) + 0.5;
      ctx.strokeStyle = t.major ? THEME.line : "rgba(43,52,64,.55)";
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, trackH);
      ctx.stroke();
    }

    let max = 0;
    for (const c of cols) if (c.value > max) max = c.value;
    const body = trackH - 2;
    for (const c of cols) {
      if (c.value <= 0) continue;
      // A one-pixel floor: a bucket with a single detection and a bucket with
      // none must not look the same.
      const h = Math.max(1, Math.round((c.value / max) * body));
      // Height is the column total, so the area under the bars is conserved
      // as pixel columns swallow more buckets. Colour is the per-bucket mean,
      // which does not change with zoom, so a colour means the same thing at
      // every span.
      ctx.fillStyle = rampColor(this.ramp, c.value / (c.i1 - c.i0 + 1));
      ctx.fillRect(c.x, trackH - h - 1, c.w, h);
    }

    this._paintSelection(trackH);

    ctx.strokeStyle = THEME.line;
    ctx.strokeRect(0.5, 0.5, W - 1, trackH - 1);

    ctx.font = '10px ui-sans-serif,-apple-system,"Segoe UI",sans-serif';
    ctx.textBaseline = "top";
    ctx.fillStyle = THEME.dim;
    for (const t of tk) {
      const x = coordToX(t.index, this.domain, W);
      // A tick sitting exactly on the left edge is still worth labelling: it
      // is the one that names the year the axis starts in.
      if (x < 0 || x > W - 4) continue;
      ctx.fillStyle = t.major ? THEME.fg : THEME.dim;
      ctx.fillText(t.label, x + 3, trackH + 2);
    }

    if (this.hover != null) this._paintHover(trackH, cols);
  }

  /**
   * The bars for the current domain.
   *
   * Quantization runs over the whole buckets the domain touches, positioned by
   * the fractional domain, so a partly visible bucket at either end is drawn
   * in the right place and clipped by the canvas rather than snapping the view
   * to bucket boundaries.
   */
  _columns() {
    const W = this.width;
    const [i0, i1] = domainBuckets(this.domain, this.axis.count);
    const n = i1 - i0 + 1;
    const px = W / (this.domain[1] - this.domain[0]);
    const x0 = coordToX(i0, this.domain, W);
    const cols = quantize(n * px, n);
    for (const c of cols) {
      c.x += x0;
      c.i0 += i0;
      c.i1 += i0;
      c.value = sumRange(this.series, c.i0, c.i1);
    }
    return cols;
  }

  _selectionPixels() {
    const W = this.width;
    return [coordToX(this.selection.from, this.domain, W),
            coordToX(this.selection.to + 1, this.domain, W)];
  }

  /**
   * Where the two handles are grabbed and drawn.
   *
   * An edge scrolled off the track is pinned flush to the frame rather than
   * left unreachable. Without this the default whole-axis selection has both
   * handles outside the track and there is no way to narrow it by dragging at
   * all. A pinned handle is drawn differently so it does not claim to sit at
   * an interior bucket.
   */
  _handlePixels() {
    const W = this.width;
    const [sx0, sx1] = this._selectionPixels();
    const pin = (sx, edge) => ({x: Math.min(Math.max(sx, 0), W), edge,
                                pinned: sx < 0 || sx > W});
    return [pin(sx0, "from"), pin(sx1, "to")];
  }

  _paintSelection(trackH) {
    const {ctx} = this, W = this.width;
    const [sx0, sx1] = this._selectionPixels();
    // Dim outside rather than tint inside: tinting shifts every bar colour and
    // the timeline stops agreeing with the map's legend.
    ctx.fillStyle = "rgba(13,17,23,.62)";
    if (sx0 > 0) ctx.fillRect(0, 0, Math.min(sx0, W), trackH);
    if (sx1 < W) ctx.fillRect(Math.max(sx1, 0), 0, W - Math.max(sx1, 0), trackH);

    const a = Math.max(0, Math.min(sx0, W)), b = Math.max(0, Math.min(sx1, W));
    // An outline around the selected span. Dimming alone is invisible when the
    // selection covers the whole axis, which is the default, so the control
    // looked like a plain chart with no selector in it at all.
    ctx.strokeStyle = THEME.acc;
    ctx.lineWidth = 1;
    ctx.strokeRect(a + 0.5, 0.5, Math.max(1, b - a - 1), trackH - 1);

    // Handles, drawn as bars with a grip. A handle scrolled off the track is
    // pinned just inside the frame instead of vanishing: at the default
    // whole-axis selection both edges sit exactly on the frame, and a handle
    // drawn on the boundary is neither visible nor grabbable.
    const gh = Math.min(22, Math.round(trackH * 0.55));
    const gy = Math.round((trackH - gh) / 2);
    for (const h of this._handlePixels()) {
      const inward = h.edge === "from" ? 1 : -1;
      const x = Math.round(h.pinned ? h.x + inward * HANDLE_W / 2 : h.x);
      ctx.fillStyle = THEME.acc;
      ctx.fillRect(x - HANDLE_W / 2, 0, HANDLE_W, trackH);
      // Two notches, the conventional "this is a grab handle" mark.
      ctx.fillStyle = "rgba(13,17,23,.85)";
      ctx.fillRect(x - 2, gy, 1, gh);
      ctx.fillRect(x + 1, gy, 1, gh);
    }
  }

  _paintHover(trackH, cols) {
    const {ctx} = this, W = this.width;
    const i = bucketAt(this.hover, this.domain, W);
    if (i < 0 || i >= this.axis.count) return;
    ctx.fillStyle = "rgba(230,237,243,.28)";
    ctx.fillRect(Math.round(this.hover), 0, 1, trackH);
    const col = cols.find(c => i >= c.i0 && i <= c.i1);
    // A raw bucket key is an implementation detail; nobody reads 200802 as a
    // date. When a pixel column covers several buckets the tooltip names the
    // range it actually summed, so the number and the label agree.
    const span = col && col.i1 > col.i0
      ? `${formatKey(this.axis.keyAt(col.i0), this.axis.unit)} – ` +
        `${formatKey(this.axis.keyAt(col.i1), this.axis.unit)}`
      : formatKey(this.axis.keyAt(i), this.axis.unit);
    const label = `${span}   ` +
      `${fmtNum(col ? col.value : sumRange(this.series, i, i))} ${this.metricLabel}`;
    ctx.font = '10px ui-sans-serif,-apple-system,"Segoe UI",sans-serif';
    const w = ctx.measureText(label).width + 10;
    const x = Math.min(Math.max(this.hover + 8, 2), W - w - 2);
    ctx.fillStyle = "rgba(18,23,31,.92)";
    ctx.fillRect(x, 3, w, 15);
    ctx.strokeStyle = THEME.line;
    ctx.strokeRect(x + 0.5, 3.5, w - 1, 14);
    ctx.fillStyle = THEME.fg;
    ctx.textBaseline = "top";
    ctx.fillText(label, x + 5, 6);
  }

  // -------------------------------------------------------------- interaction

  _x(e) { return e.clientX - this.canvas.getBoundingClientRect().left; }

  _y(e) { return e.clientY - this.canvas.getBoundingClientRect().top; }

  /**
   * What a press grabs. Pan has to stay reachable at every zoom, so the label
   * strip always pans, and so does the inside of a selection whose handles are
   * both off the track: moving that would clamp on the first pixel, which is
   * indistinguishable from a dead gesture.
   */
  hitTest(x, y) {
    if (y != null && y > this.height - AXIS_H) return "pan";
    const [h0, h1] = this._handlePixels();
    if (Math.abs(x - h0.x) <= GRIP) return "from";
    if (Math.abs(x - h1.x) <= GRIP) return "to";
    if (x > h0.x && x < h1.x) return h0.pinned && h1.pinned ? "pan" : "body";
    return "pan";
  }

  _onWheel(e) {
    if (!this.axis) return;
    e.preventDefault();
    // deltaMode is lines on Firefox and pages on some remotes; normalise or a
    // single notch zooms three orders of magnitude.
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
    const dy = e.deltaY * unit, dx = e.deltaX * unit;

    // A two-finger trackpad swipe arrives as a wheel event carrying deltaX.
    // Route it to pan and leave deltaY to zoom -- but pick ONE per event.
    // A swipe is never perfectly horizontal, so acting on both axes would
    // zoom slightly on every pan and feel broken. ctrl means pinch-zoom
    // regardless, since that is how browsers report a trackpad pinch.
    if (!e.ctrlKey && Math.abs(dx) > Math.abs(dy)) {
      this.domain = panDomain(this.domain, -dx, this.width, this.axis.count);
      this._emit();
      this.draw();
      return;
    }
    this.zoomAt(this._x(e), Math.exp(-dy * (e.ctrlKey ? PINCH : WHEEL)));
  }

  _onDown(e) {
    if (!this.axis) return;
    this.canvas.focus();
    this._pointers.set(e.pointerId, this._x(e));
    // Capture after the pointer is registered, not before. setPointerCapture
    // throws for a pointer the browser does not think is down, and a throw
    // here used to abandon the whole gesture rather than just its capture.
    try { this.canvas.setPointerCapture(e.pointerId); } catch { /* uncaptured */ }
    if (this._pointers.size === 2) {
      const [a, b] = [...this._pointers.values()];
      this._pinch = {dist: Math.abs(a - b) || 1};
      this._drag = null;
      return;
    }
    const x = this._x(e);
    // Shift starts a fresh selection wherever the pointer is. Plain drag keeps
    // panning; this is the inverse of Perfetto, which selects on plain drag and
    // pans on shift, and is deliberate -- panning was here first.
    const mode = e.shiftKey ? "draw" : this.hitTest(x, this._y(e));
    if (mode === "draw") {
      const i = bucketAt(x, this.domain, this.width);
      this.selection = clampSelection({from: i, to: i}, this.bounds());
      this._drag = {mode, x, anchor: i, domain: this.domain.slice(),
                    selection: {...this.selection}, coord: 0};
      this._emit();
      this.draw();
      return;
    }
    this._drag = {
      mode, x, edge: mode === "from" || mode === "to" ? mode : null,
      domain: this.domain.slice(), selection: {...this.selection},
      coord: xToCoord(x, this.domain, this.width),
    };
  }

  _onMove(e) {
    if (!this.axis) return;
    const x = this._x(e);
    if (this._pointers.has(e.pointerId)) this._pointers.set(e.pointerId, x);

    if (this._pinch && this._pointers.size === 2) {
      const [a, b] = [...this._pointers.values()];
      const dist = Math.abs(a - b) || 1;
      this.zoomAt((a + b) / 2, dist / this._pinch.dist);
      this._pinch.dist = dist;
      return;
    }

    const d = this._drag;
    if (!d) {
      this.hover = x;
      const hit = this.hitTest(x, this._y(e));
      this.canvas.style.cursor =
        hit === "from" || hit === "to" ? "ew-resize"
        : hit === "body" ? "grab" : "default";
      this.draw();
      return;
    }
    this.hover = x;
    if (d.mode === "draw") {
      // Dragging either way from the anchor is normal; order the pair rather
      // than refusing a right-to-left drag.
      const i = bucketAt(x, this.domain, this.width);
      const [lo, hi] = i < d.anchor ? [i, d.anchor] : [d.anchor, i];
      this.selection = clampSelection({from: lo, to: hi}, this.bounds());
    } else if (d.mode === "pan") {
      this.domain = panDomain(d.domain, x - d.x, this.width, this.axis.count);
    } else if (d.mode === "body") {
      const delta = Math.round(xToCoord(x, this.domain, this.width) - d.coord);
      this.selection = moveSelection(d.selection, delta, this.bounds());
    } else {
      const r = resizeSelection(
        this.selection, d.edge, bucketAt(x, this.domain, this.width),
        this.bounds());
      d.edge = r.edge;
      this.selection = {from: r.from, to: r.to};
    }
    this._emit();
    this.draw();
  }

  _onUp(e) {
    this._pointers.delete(e.pointerId);
    if (this._pointers.size < 2) this._pinch = null;
    if (this._pointers.size === 0) this._drag = null;
  }

  _onKey(e) {
    if ((e.key === "f" || e.key === "F") && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      this.fitToSelection();
      return;
    }
    if (!this.axis) return;
    const step = e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0;
    if (!step) return;
    e.preventDefault();
    if (e.shiftKey) {
      const to = Math.max(this.selection.from, this.selection.to + step);
      this.selection = clampSelection({from: this.selection.from, to},
                                      this.bounds());
    } else {
      this.selection = moveSelection(this.selection, step, this.bounds());
    }
    this._emit();
    this.draw();
  }
}
