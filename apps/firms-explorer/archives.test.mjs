#!/usr/bin/env node
/**
 * Unit gate for the explorer's archive-metadata logic.
 *
 * Covers the regressions the design names: band matching must floor the zoom,
 * bucket keys must come from metadata and never from a sampled feature, and
 * the domain-to-archive rule must fall back to `alltime` for a window that
 * straddles a year boundary. Nothing here touches MapLibre or the network.
 *
 * Run: node apps/firms-explorer/archives.test.mjs
 */
import {
  bandAt, bucketColumns, chooseArchive, keyDayRange, parseJSONish,
  pointsMinZoom, pyramid, shippedStops, timelineFromMetadata, vectorLayers,
} from "./archives.js";

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

// Metadata as the two published archives actually carry it, trimmed to the
// fields this module reads. `alltime` declares its axis; `fire-latest` does
// not, which is why the fallback path exists at all.
const ALLTIME = {
  "firms:timeline": JSON.stringify({
    buckets: 311, key_format: "count_YYYYMM",
    max: "202609", min: "200011", unit: "month"}),
  "gpio:pyramid": JSON.stringify({scheme: "a5", bands: [
    {layer: "aggregate", level: 4, minzoom: 0, maxzoom: 4},
    {layer: "aggregate", level: 6, minzoom: 5, maxzoom: null}]}),
  "firms:breaks": JSON.stringify({
    r4: {minzoom: 0, maxzoom: 4, metrics: {
      count: [852, 130390, 579160, 1445955, 3857521, 5371175]}},
    r6: {minzoom: 5, maxzoom: null, metrics: {
      count: [2477, 20640, 77306, 184769, 344529, 433352]}}}),
  vector_layers: [{id: "aggregate", fields: {
    a5_cell: "Number", count: "Number", avg_frp: "Number",
    count_200011: "Number", count_200012: "Number", count_200101: "Number",
    count_202609: "Number"}}],
};

const LATEST = {
  "gpio:pyramid": {scheme: "a5", bands: [
    {layer: "aggregate", level: 6, minzoom: 0, maxzoom: 6},
    {layer: "aggregate", level: 10, minzoom: 7, maxzoom: 9},
    {layer: "features", level: "features", minzoom: 10, maxzoom: null}]},
  json: JSON.stringify({vector_layers: [{id: "aggregate", fields: {
    a5_cell: "Number", count: "Number",
    count_20260901: "Number", count_20260902: "Number",
    count_20260908: "Number",
    count_d: "Number", count_n: "Number", count_modis: "Number"}}]}),
};

// ------------------------------------------------------------ parsing shapes

eq("a JSON string parses", parseJSONish('{"a":1}'), {a: 1});
eq("an object passes through", parseJSONish({a: 1}), {a: 1});
eq("malformed JSON is null, not a throw", parseJSONish("{oops"), null);
eq("absent metadata is null", parseJSONish(undefined), null);

eq("vector_layers is read from the top level",
   vectorLayers(ALLTIME).length, 1);
eq("vector_layers is read from the json blob when that is where it is",
   vectorLayers(LATEST).length, 1);
eq("no layers at all is an empty list, not a throw", vectorLayers({}), []);

// --------------------------------------------------------- the time axis

const a = timelineFromMetadata(ALLTIME);
ok("a declared axis is used", a.declared);
eq("the declared axis is passed through unchanged",
   [a.meta.unit, a.meta.min, a.meta.max, a.meta.buckets],
   ["month", "200011", "202609", 311]);
ok("the declaration wins over the four columns actually listed",
   a.meta.buckets === 311 && a.columns.length === 4);

const l = timelineFromMetadata(LATEST);
ok("an undeclared axis is reconstructed", !l.declared);
eq("the reconstruction reads unit from key width",
   [l.meta.unit, l.meta.key_format], ["day", "count_YYYYMMDD"]);
eq("the reconstruction spans the declared columns",
   [l.meta.min, l.meta.max, l.meta.buckets], ["20260901", "20260908", 3]);

// The regression the spec names twice: a bucket list must never come from a
// feature. These pivots are counts and sit beside the buckets in the same
// layer, so the digit test is the only thing keeping them out of the axis.
eq("day/night and sensor pivots are not time buckets",
   bucketColumns(LATEST), ["20260901", "20260902", "20260908"]);

eq("an archive with neither a declaration nor bucket columns has no axis",
   timelineFromMetadata({vector_layers: [{id: "aggregate", fields:
     {a5_cell: "Number", count: "Number"}}]}).meta, null);
eq("mixed key widths are refused rather than guessed",
   timelineFromMetadata({vector_layers: [{id: "aggregate", fields:
     {count_202001: "Number", count_20200102: "Number"}}]}).meta, null);

// --------------------------------------------------------------- band matching

const bands = pyramid(ALLTIME).bands;
eq("z0 is the coarse band", bandAt(bands, 0).level, 4);
eq("z4 is still the coarse band", bandAt(bands, 4).level, 4);
eq("z5 is the fine band", bandAt(bands, 5).level, 6);
eq("an open-ended band has no upper edge", bandAt(bands, 12).level, 6);
// The bug this exists for: MapLibre requests floor(zoom), so 4.9 is still a
// z4 tile. Matching the raw float finds neither band and the breaks vanish.
eq("a fractional zoom floors into the band below", bandAt(bands, 4.9).level, 4);
eq("4.0 exactly is the coarse band", bandAt(bands, 4.0).level, 4);
eq("5.001 is the fine band", bandAt(bands, 5.001).level, 6);
eq("a zoom below every band matches nothing", bandAt(bands, -1), null);
eq("no bands at all matches nothing", bandAt([], 3), null);

eq("an aggregate-only archive has no point zoom",
   pointsMinZoom(pyramid(ALLTIME).bands), Infinity);
eq("a features band names the zoom points start at",
   pointsMinZoom(pyramid(LATEST).bands), 10);

// ---------------------------------------------------------------- breaks

const PALETTE = [
  [1, "#2c3d5a"], [5, "#3f6d8f"], [20, "#59a1a0"], [75, "#a8c268"],
  [250, "#f2b134"], [1000, "#e8722c"], [4000, "#d1382a"],
];
const br = parseJSONish(ALLTIME["firms:breaks"]);

const full = shippedStops(br, 4, "count", PALETTE);
eq("unscaled stops keep the shipped thresholds",
   full.map(s => s[0]), [1, 852, 130390, 579160, 1445955, 3857521, 5371175]);
eq("the palette is kept", full.map(s => s[1]), PALETTE.map(s => s[1]));

const scaled = shippedStops(br, 4, "count", PALETTE, 1 / 311);
eq("scaling divides the thresholds",
   scaled.map(s => s[0]), [1, 3, 420, 1863, 4650, 12404, 17271]);

// A step expression whose stops do not ascend is rejected outright by
// MapLibre, and a hard scale can otherwise collapse several classes onto 1.
const tiny = shippedStops(br, 6, "count", PALETTE, 1e-6);
ok("a hard scale still ascends strictly",
   tiny.every((s, i) => i === 0 || s[0] > tiny[i - 1][0]), JSON.stringify(tiny));

eq("a level the archive does not describe has no stops",
   shippedStops(br, 8, "count", PALETTE), null);
eq("a metric the archive does not describe has no stops",
   shippedStops(br, 4, "avg_frp", PALETTE), null);
eq("no breaks at all has no stops", shippedStops(null, 4, "count", PALETTE), null);

// -------------------------------------------------------- archive selection

const AVAIL = {latest: {min: "20260903", max: "20260910"}, years: ["2003", "2020"]};

eq("a window inside the rolling seven days takes the rolling archive",
   chooseArchive({from: "20260905", to: "20260908"}, "day", AVAIL), "latest");
eq("the rolling window's own edges are inside it",
   chooseArchive({from: "20260903", to: "20260910"}, "day", AVAIL), "latest");
eq("one day earlier is not",
   chooseArchive({from: "20260902", to: "20260908"}, "day", AVAIL), "alltime");

eq("a window inside one published year takes that year",
   chooseArchive({from: "202003", to: "202008"}, "month", AVAIL), "2020");
eq("a whole published year takes that year",
   chooseArchive({from: "202001", to: "202012"}, "month", AVAIL), "2020");
eq("a year with no archive falls back",
   chooseArchive({from: "201903", to: "201908"}, "month", AVAIL), "alltime");

// The accepted limitation, spelled out: four months is narrower than a year
// but crosses a year boundary, so it cannot be served daily by one archive.
eq("November to February falls back to all-time",
   chooseArchive({from: "201911", to: "202002"}, "month", AVAIL), "alltime");
eq("the whole record is all-time",
   chooseArchive({from: "200011", to: "202609"}, "month", AVAIL), "alltime");
eq("with no rolling archive known, a recent week is not routed to one",
   chooseArchive({from: "20260905", to: "20260908"}, "day", {years: []}), "alltime");

// A month key covers its whole month, which is what makes the containment
// test comparable between a monthly domain and a daily archive.
eq("a February in a leap year ends on the 29th",
   keyDayRange("202002", "month"), ["20200201", "20200229"]);
eq("a February in a common year ends on the 28th",
   keyDayRange("202102", "month"), ["20210201", "20210228"]);
eq("a century that is not a leap year ends on the 28th",
   keyDayRange("190002", "month"), ["19000201", "19000228"]);
eq("a day key covers itself", keyDayRange("20200229", "day"),
   ["20200229", "20200229"]);

// ------------------------------------------------------------------- report

if (errors.length) {
  console.log(errors.map(e => `error  ${e}`).join("\n"));
  console.log(`\nFAILED: ${errors.length} of ${checked} checks`);
  process.exit(1);
}
console.log(`OK: ${checked} checks passed`);
