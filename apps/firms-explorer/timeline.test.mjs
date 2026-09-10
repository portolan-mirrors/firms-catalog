#!/usr/bin/env node
/**
 * Unit gate for the explorer's timeline logic.
 *
 * Covers the three things the design calls out as already having produced
 * bugs or as load-bearing for performance: bucket-key parsing, pixel
 * quantization at every relation between bucket count and track width, and
 * the zoom-about-cursor invariant. Nothing here touches a canvas or a DOM, so
 * it runs under plain node with no dependencies.
 *
 * Failures accumulate and are reported once at the end, so one broken
 * assumption does not hide the next.
 *
 * Run: node apps/firms-explorer/timeline.test.mjs
 */
import {
  bucketAt, civilFromDays, clampDomain, clampSelection, compareKeys, coordToX,
  daysFromCivil, domainBuckets, indexKey, keyIndex, makeAxis, makeSeries,
  MIN_SPAN, moveSelection, panDomain, quantize, resizeSelection,
  seriesFromBuckets, sumRange, ticks, xToCoord, zoomDomain,
} from "./timeline.js";

const errors = [];
let checked = 0;

function ok(name, cond, detail = "") {
  checked++;
  if (!cond) errors.push(detail ? `${name}: ${detail}` : name);
}

function eq(name, actual, expected) {
  const a = JSON.stringify(actual), b = JSON.stringify(expected);
  ok(name, a === b, `got ${a}, want ${b}`);
}

function close(name, actual, expected, tol = 1e-9) {
  ok(name, Math.abs(actual - expected) <= tol,
     `got ${actual}, want ${expected} +/- ${tol}`);
}

function throws(name, fn, needle) {
  checked++;
  try {
    fn();
    errors.push(`${name}: expected a throw, got none`);
  } catch (e) {
    if (needle && !String(e.message).includes(needle))
      errors.push(`${name}: threw ${JSON.stringify(e.message)}, wanted ` +
                  `something containing ${JSON.stringify(needle)}`);
  }
}

// ---------------------------------------------------------------- bucket keys

eq("month key 200011", keyIndex("200011", "month"), 2000 * 12 + 10);
eq("month keys are one apart",
   keyIndex("202001", "month") - keyIndex("201912", "month"), 1);
eq("month round trip", indexKey(keyIndex("202609", "month"), "month"), "202609");

eq("epoch day", keyIndex("19700101", "day"), 0);
eq("day round trip", indexKey(keyIndex("20200229", "day"), "day"), "20200229");
eq("2020 is a leap year",
   keyIndex("20201231", "day") - keyIndex("20200101", "day") + 1, 366);
eq("2021 is not", keyIndex("20211231", "day") - keyIndex("20210101", "day") + 1, 365);
eq("month end to month start",
   keyIndex("20200301", "day") - keyIndex("20200229", "day"), 1);
eq("civil round trip through 1899-12-31",
   civilFromDays(daysFromCivil(1899, 12, 31)), [1899, 12, 31]);

// Widths are the contract with tools/timeline_meta.py: six digits or eight,
// and a four-digit key is ambiguous rather than a short year.
throws("four-digit key rejected", () => keyIndex("0101", "month"), "6 digits");
throws("daily key on a monthly axis rejected",
       () => keyIndex("20200101", "month"), "6 digits");
throws("month 13 rejected", () => keyIndex("202013", "month"), "no month");
throws("day 00 rejected", () => keyIndex("20200100", "day"), "no day");
throws("non-numeric rejected", () => keyIndex("20200a", "month"), "6 digits");
throws("unknown unit rejected", () => keyIndex("202001", "week"), "unknown");

eq("keys compare in calendar order", compareKeys("200011", "202609"), -1);
eq("equal keys compare equal", compareKeys("202001", "202001"), 0);
throws("keys of different units do not compare",
       () => compareKeys("202001", "20200101"), "different bucket units");

// ----------------------------------------------------------------------- axis

const ALLTIME = {
  unit: "month", key_format: "count_YYYYMM",
  min: "200011", max: "202609", buckets: 311,
};
const YEAR2020 = {
  unit: "day", key_format: "count_YYYYMMDD",
  min: "20200101", max: "20201231", buckets: 366,
};

const monthly = makeAxis(ALLTIME);
const daily = makeAxis(YEAR2020);

eq("alltime spans 311 months", monthly.count, 311);
eq("alltime declaration agrees with its span",
   monthly.declaredBuckets, monthly.count);
eq("alltime first key", monthly.keyAt(0), "200011");
eq("alltime last key", monthly.keyAt(monthly.count - 1), "202609");
eq("alltime index of a middle key", monthly.indexOf("201001"), 110);
eq("2020 spans 366 days", daily.count, 366);
eq("2020 last key", daily.keyAt(365), "20201231");

// A sparse archive still draws its gap: the calendar span wins and the
// declaration is kept so the caller can say so.
const sparse = makeAxis({unit: "month", key_format: "count_YYYYMM",
                         min: "202001", max: "202012", buckets: 9});
eq("sparse axis draws the whole span", sparse.count, 12);
eq("sparse axis remembers what was declared", sparse.declaredBuckets, 9);

throws("unit and key_format must agree",
       () => makeAxis({unit: "month", key_format: "count_YYYYMMDD",
                       min: "202001", max: "202012"}),
       "expected count_YYYYMM");
throws("unknown unit rejected at the axis",
       () => makeAxis({unit: "week", min: "202001", max: "202012"}), "unknown unit");
throws("reversed range rejected",
       () => makeAxis({unit: "month", min: "202012", max: "202001"}), "is after");
throws("missing declaration rejected", () => makeAxis(null), "missing");

// -------------------------------------------------------- pixel quantization

/** Columns must tile the buckets exactly once, in order, with no gaps. */
function coversBuckets(cols, n) {
  if (!cols.length) return n === 0;
  if (cols[0].i0 !== 0 || cols[cols.length - 1].i1 !== n - 1) return false;
  for (let i = 0; i < cols.length; i++) {
    if (cols[i].i1 < cols[i].i0) return false;
    if (i && cols[i].i0 !== cols[i - 1].i1 + 1) return false;
  }
  return true;
}

/** Bars must run left to right and stay on the track. */
function fitsTrack(cols, w) {
  let x = -1;
  for (const c of cols) {
    if (c.x < x || c.w < 1 || c.x + c.w > w) return false;
    x = c.x;
  }
  return true;
}

// N > W: one column per pixel, each aggregating the buckets under it.
const many = quantize(100, 1000);
eq("N > W gives one column per pixel", many.length, 100);
ok("N > W tiles every bucket", coversBuckets(many, 1000));
ok("N > W stays on the track", fitsTrack(many, 100));
eq("N > W columns are one pixel wide",
   many.every(c => c.w === 1 && c.x === many.indexOf(c)), true);
eq("N > W first column covers ten buckets", [many[0].i0, many[0].i1], [0, 9]);

// N == W: the boundary case, one bucket per pixel.
const same = quantize(256, 256);
eq("N == W gives one column per bucket", same.length, 256);
ok("N == W tiles every bucket", coversBuckets(same, 256));
eq("N == W maps bucket i to pixel i",
   same.every((c, i) => c.x === i && c.w === 1 && c.i0 === i && c.i1 === i), true);

// N < W: one bar per bucket, floor(W/N) wide.
const few = quantize(1000, 311);
eq("N < W gives one column per bucket", few.length, 311);
ok("N < W tiles every bucket", coversBuckets(few, 311));
ok("N < W stays on the track", fitsTrack(few, 1000));
eq("N < W bars are floor(W/N) wide", few.every(c => c.w === 3), true);
eq("N < W bars span the whole track",
   few[few.length - 1].x + few[few.length - 1].w >= 1000 - 3, true);
eq("N < W bars hold exactly one bucket",
   few.every((c, i) => c.i0 === i && c.i1 === i), true);

// A track narrower than one bar per bucket still fills every pixel.
const tight = quantize(7, 3);
eq("uneven division keeps floor width", tight.map(c => [c.x, c.w]),
   [[0, 2], [2, 2], [4, 2]]);

// Degenerate inputs draw nothing rather than throwing or looping.
eq("no buckets draws nothing", quantize(800, 0), []);
eq("no width draws nothing", quantize(0, 311), []);
eq("negative width draws nothing", quantize(-10, 311), []);
eq("fractional sub-pixel width draws nothing", quantize(0.4, 311), []);
eq("fractional width floors", quantize(100.9, 1000).length, 100);

// Draw cost is bounded by the track, not the axis. This is the whole point of
// quantizing to pixels: a 311-bucket and a 9,750-bucket axis paint the same.
for (const n of [311, 366, 9750, 250000])
  eq(`draw cost at N=${n} is bounded by W`, quantize(1024, n).length <= 1024, true);
eq("311 and 9750 buckets cost the same",
   quantize(1024, 311).length <= 1024 && quantize(1024, 9750).length === 1024, true);

// ------------------------------------------------------------ series and sums

const values = Array.from({length: 311}, (_, i) => (i * 37) % 101);
const series = makeSeries(values);
const total = values.reduce((a, b) => a + b, 0);

eq("sum of the whole series", sumRange(series, 0, 310), total);
eq("sum of one bucket", sumRange(series, 5, 5), values[5]);
eq("sum of an empty range", sumRange(series, 7, 6), 0);
eq("sums clip below zero", sumRange(series, -20, 2), values[0] + values[1] + values[2]);
eq("sums clip past the end", sumRange(series, 308, 900),
   values[308] + values[309] + values[310]);
eq("empty series sums to zero", sumRange(makeSeries([]), 0, 10), 0);

// Aggregating into pixel columns must not lose or duplicate a detection.
for (const w of [37, 311, 800]) {
  const cols = quantize(w, 311);
  const summed = cols.reduce((a, c) => a + sumRange(series, c.i0, c.i1), 0);
  eq(`quantized total is conserved at W=${w}`, summed, total);
}

const fromKeys = seriesFromBuckets(monthly, {
  count_200011: 4, count_201001: 9, "202609": 2,
  count_modis: 999, count_d: 111,      // sensor and day/night, not time
});
eq("bucket values land on their key", sumRange(fromKeys, 0, 0), 4);
eq("prefixed and bare keys both read", sumRange(fromKeys, 310, 310), 2);
eq("non-time count_ columns are ignored", sumRange(fromKeys, 0, 310), 15);

// ------------------------------------------------------------- zoom and domain

const W = 800, N = 311;

// The invariant: zooming about x leaves the bucket under x where it was.
for (const domain of [[100, 200], [0.5, 40.25], [200, 311]]) {
  for (const factor of [1.05, 1.4, 2, 8, 0.9, 0.5]) {
    for (let x = 0; x <= W; x += 47) {
      const before = xToCoord(x, domain, W);
      const wanted = Math.min(
        Math.max((domain[1] - domain[0]) / factor, MIN_SPAN), N);
      // Where the invariant says the domain has to land, worked out from the
      // requirement and not from what zoomDomain returned -- deriving it from
      // the result would make this loop agree with any implementation.
      const idealLo = before - (x / W) * wanted;
      // Skip only the cases the clamp is entitled to override: a domain pushed
      // off either end of the data has to be moved back inside it.
      if (idealLo < -1e-9 || idealLo + wanted > N + 1e-9) continue;
      const next = zoomDomain(domain, x, W, factor, N);
      const span = next[1] - next[0];
      ok(`zoom lands where the invariant requires at x=${x} factor ${factor} ` +
         `on [${domain}]`, Math.abs(next[0] - idealLo) < 1e-9,
         `got ${next[0]}, want ${idealLo}`);
      close(`zoom holds the coordinate under x=${x} at factor ${factor} ` +
            `on [${domain}]`, xToCoord(x, next, W), before, 1e-9);
      eq(`zoom holds the bucket under x=${x} at factor ${factor} ` +
         `on [${domain}]`, bucketAt(x, next, W), bucketAt(x, domain, W));
      ok(`zoom reaches the requested span at x=${x} factor ${factor}`,
         Math.abs(span - wanted) < 1e-9, `got ${span}, want ${wanted}`);
    }
  }
}

// Zooming right at an edge cannot hold the cursor and stay in the data. The
// clamp wins, and it slides rather than truncating.
eq("zooming out past the left edge slides back in",
   zoomDomain([0, 50], W, W, 0.5, N), [0, 100]);
eq("zooming out past the right edge slides back in",
   zoomDomain([N - 50, N], 0, W, 0.5, N), [N - 100, N]);

eq("zooming out far stops at the whole axis",
   zoomDomain([100, 200], W / 2, W, 1e-6, N), [0, N]);
eq("zooming in far stops at the minimum span",
   zoomDomain([0, N], 0, W, 1e6, N)[1] - zoomDomain([0, N], 0, W, 1e6, N)[0],
   MIN_SPAN);

eq("clamp leaves an interior domain alone", clampDomain([100, 200], N), [100, 200]);
eq("clamp slides a domain off the left edge", clampDomain([-30, 20], N), [0, 50]);
eq("clamp slides a domain off the right edge",
   clampDomain([N - 10, N + 40], N), [N - 50, N]);
eq("clamp widens a span below the floor", clampDomain([10, 10.5], N),
   [10, 10 + MIN_SPAN]);
eq("clamp narrows a span wider than the data", clampDomain([-50, 900], N), [0, N]);

// Pan: dragging right brings earlier buckets into view, and stops at the data.
eq("drag right pans earlier", panDomain([100, 200], 80, W, N), [90, 190]);
eq("drag left pans later", panDomain([100, 200], -80, W, N), [110, 210]);
eq("pan stops at the left edge", panDomain([0, 100], 400, W, N), [0, 100]);
eq("pan stops at the right edge", panDomain([N - 100, N], -400, W, N), [N - 100, N]);
close("pan is the inverse of itself",
      panDomain(panDomain([100, 200], 37, W, N), -37, W, N)[0], 100);

eq("pixel mapping round trips", coordToX(xToCoord(613, [100, 200], W),
   [100, 200], W), 613);
eq("the visible whole buckets of a fractional domain",
   domainBuckets([100.4, 103.2], N), [100, 103]);
eq("visible buckets clip to the axis", domainBuckets([-5, 3], N), [0, 2]);

// ------------------------------------------------------------------ selection

const bounds = [10, 20];
eq("selection inside its bounds is untouched",
   clampSelection({from: 12, to: 15}, bounds), {from: 12, to: 15});
eq("selection off the left slides in, keeping its width",
   clampSelection({from: 5, to: 8}, bounds), {from: 10, to: 13});
eq("selection off the right slides in, keeping its width",
   clampSelection({from: 18, to: 25}, bounds), {from: 13, to: 20});
eq("selection wider than its bounds fills them",
   clampSelection({from: 0, to: 100}, bounds), {from: 10, to: 20});
eq("a reversed selection is put back in order",
   clampSelection({from: 15, to: 12}, bounds), {from: 12, to: 15});
eq("selection edges snap to whole buckets",
   clampSelection({from: 12.4, to: 15.6}, bounds), {from: 12, to: 16});

eq("move shifts the selection", moveSelection({from: 12, to: 15}, 3, bounds),
   {from: 15, to: 18});
eq("move stops at the edge without squashing",
   moveSelection({from: 12, to: 15}, 99, bounds), {from: 17, to: 20});
eq("move stops at the other edge too",
   moveSelection({from: 12, to: 15}, -99, bounds), {from: 10, to: 13});

eq("resize drags one edge",
   resizeSelection({from: 12, to: 15}, "to", 18, bounds),
   {from: 12, to: 18, edge: "to"});
eq("resize clamps to the bounds",
   resizeSelection({from: 12, to: 15}, "to", 99, bounds),
   {from: 12, to: 20, edge: "to"});
eq("dragging an edge past the other flips the live handle",
   resizeSelection({from: 12, to: 15}, "to", 11, bounds),
   {from: 11, to: 12, edge: "from"});
eq("and the same the other way",
   resizeSelection({from: 12, to: 15}, "from", 19, bounds),
   {from: 15, to: 19, edge: "to"});

// ---------------------------------------------------------------------- ticks

const monthTicks = ticks(monthly, [0, monthly.count], 900);
ok("a 27-year month axis gets a readable number of labels",
   monthTicks.length >= 4 && monthTicks.length <= 20,
   `got ${monthTicks.length}`);
ok("month labels at that span are years",
   monthTicks.every(t => /^\d{4}$/.test(t.label)),
   monthTicks.map(t => t.label).join(","));
ok("ticks are inside the domain and in order",
   monthTicks.every((t, i) =>
     t.index >= 0 && t.index < monthly.count &&
     (i === 0 || t.index > monthTicks[i - 1].index)));
ok("labels are far enough apart to read",
   monthTicks.every((t, i) => i === 0 ||
     ((t.index - monthTicks[i - 1].index) * 900) / monthly.count >= 40));

const zoomed = ticks(monthly, [110, 122], 900);
ok("a one-year month view labels months",
   zoomed.some(t => t.label === "Mar"), zoomed.map(t => t.label).join(","));

const dayTicks = ticks(daily, [0, daily.count], 900);
eq("a one-year day axis labels twelve months", dayTicks.length, 12);
eq("January is labelled with its year", dayTicks[0].label, "2020");
ok("a two-week day view labels days",
   ticks(daily, [0, 14], 900).length >= 10);

eq("a zero-width track has no ticks", ticks(monthly, [0, 311], 0), []);
eq("an empty domain has no ticks", ticks(monthly, [5, 5], 900), []);

// ------------------------------------------------------------------- report

if (errors.length) {
  console.log(errors.map(e => `error  ${e}`).join("\n"));
  console.log(`\nFAILED: ${errors.length} of ${checked} checks`);
  process.exit(1);
}
console.log(`OK: ${checked} checks passed`);
