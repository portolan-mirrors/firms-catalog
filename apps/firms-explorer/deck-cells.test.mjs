#!/usr/bin/env node
/**
 * Unit gate for the deck.gl explorer's cell logic.
 *
 * deck.gl has no querySourceFeatures and no paint expressions, so the page
 * keeps its own tile cache and computes each cell's colour in JavaScript.
 * Everything that computes is here, and nothing here touches deck.gl, the
 * network or the DOM.
 *
 * Run: node apps/firms-explorer/deck-cells.test.mjs
 */
import {makeAxis} from "./timeline.js";
import {
  TileCache, bucketIndex, countFor, featureBounds, fillFor, lngOverlaps,
  selectionPlan, tileZoom,
} from "./deck-cells.js";

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

function near(name, actual, expected, tol = 1e-6) {
  ok(name, Math.abs(actual - expected) <= tol, `got ${actual}, want ${expected}`);
}

// ---------------------------------------------------------------- tile zoom

// The page sets deck.gl's tile size so that it requests floor(zoom), as
// MapLibre does, and everything that asks which band is on screen agrees.
eq("tile zoom floors", [tileZoom(4.4), tileZoom(4.5), tileZoom(4.99)], [4, 4, 4]);
eq("a whole zoom is itself", tileZoom(5), 5);

// ------------------------------------------------------------------- bounds

// A z1 tile covering the north-west quadrant of the world.
const NW = {west: -180, east: 0, north: 85.0511287798066, south: 0};

// Local coordinates run 0..1 from the tile's north-west corner; y is
// mercator-linear, not latitude-linear.
const square = {
  type: "Feature",
  geometry: {type: "Polygon", coordinates: [[[0, 0], [0.5, 0], [0.5, 1], [0, 1], [0, 0]]]},
  properties: {},
};
const b = featureBounds(square, NW);
near("west edge", b.w, -180);
near("east edge is half way", b.e, -90);
near("north edge is the tile's north", b.n, NW.north);
near("south edge is the equator", b.s, 0);

const half = featureBounds({
  type: "Feature",
  geometry: {type: "Point", coordinates: [0.25, 0.5]},
  properties: {},
}, NW);
near("local y 0.5 is half way in mercator, not latitude", half.n, 66.51326044311186, 1e-9);
near("a point has zero width", half.e - half.w, 0);

// Multipolygons are walked to any depth.
const mp = featureBounds({
  type: "Feature",
  geometry: {type: "MultiPolygon",
             coordinates: [[[[0.1, 0.1], [0.2, 0.1], [0.2, 0.2], [0.1, 0.1]]],
                           [[[0.8, 0.8], [0.9, 0.8], [0.9, 0.9], [0.8, 0.8]]]]},
  properties: {},
}, NW);
near("multipolygon west", mp.w, -180 + 0.1 * 180);
near("multipolygon east", mp.e, -180 + 0.9 * 180);

ok("a box across the antimeridian still overlaps",
   lngOverlaps(170, 190, -180, -170));
ok("a box outside the view does not overlap", !lngOverlaps(10, 20, 30, 40));

// ------------------------------------------------------------ point buckets

const DAILY = makeAxis({unit: "day", min: "20200101", max: "20201231", buckets: 366});
const MONTHLY = makeAxis({unit: "month", min: "200011", max: "202609"});

eq("a point's day property maps to its axis index",
   bucketIndex({day: 20200103}, DAILY), 2);
eq("a string day works too", bucketIndex({day: "20200103"}, DAILY), 2);
eq("acq_date is the fallback", bucketIndex({acq_date: "2020-01-03"}, DAILY), 2);
eq("a day lands in its month on a monthly axis",
   bucketIndex({acq_date: "2000-12-25"}, MONTHLY), 1);
eq("a point with no date is out of range", bucketIndex({}, DAILY), -1);

// ------------------------------------------------------------ selected count

const COLS = new Set(["20200101", "20200102", "20200103", "20200104"]);
const cell = {count: 100, count_20200101: 10, count_20200102: 20,
              count_20200103: 30, count_20200104: 40,
              count_c20200101: 10, count_c20200102: 30,
              count_c20200103: 60, count_c20200104: 100};

let plan = selectionPlan({axis: DAILY, sel: {from: 0, to: 365}, columns: COLS});
ok("the whole axis is recognised", plan.whole);
eq("the whole axis reads the total", countFor(plan, cell), 100);

plan = selectionPlan({axis: DAILY, sel: {from: 1, to: 2}, columns: COLS});
eq("a narrow selection sums its columns", countFor(plan, cell), 50);
eq("the plan knows how many buckets it covers", plan.nsel, 2);
eq("a cell with none of the columns counts zero", countFor(plan, {count: 5}), 0);

plan = selectionPlan({axis: DAILY, sel: {from: 1, to: 2}, columns: COLS,
                      cumulative: true});
eq("running totals turn the sum into a difference", countFor(plan, cell), 50);
plan = selectionPlan({axis: DAILY, sel: {from: 0, to: 1}, columns: COLS,
                      cumulative: true});
eq("a selection from the first bucket reads one running total",
   countFor(plan, cell), 30);

plan = selectionPlan({axis: DAILY, sel: {from: 200, to: 210}, columns: COLS});
eq("a selection over absent columns counts zero", countFor(plan, cell), 0);
eq("absent columns still count toward the selection's width for scaling",
   plan.nsel, 11);

// The stats sidecar hands over [id, total] pairs for the selection; the plan
// answers by cell id, matching on the Number the tile carries.
plan = selectionPlan({axis: MONTHLY, sel: {from: 3, to: 5}, columns: new Set(),
                      statsSums: [8630163523437068288, 7, 42, 9]});
eq("a stats-driven plan reads the sidecar's total",
   countFor(plan, {a5_cell: 8630163523437068288}), 7);
eq("a cell the sidecar did not list counts zero", countFor(plan, {a5_cell: 1}), 0);
eq("a stats plan's width is the selection's", plan.nsel, 3);

// ------------------------------------------------------------------ colours

const STOPS = [[1, "#2c3d5a"], [5, "#3f6d8f"], [20, "#59a1a0"]];
eq("below the first stop is transparent", fillFor(0, STOPS), [0, 0, 0, 0]);
eq("the first class is the first colour", fillFor(1, STOPS), [0x2c, 0x3d, 0x5a, 255]);
eq("a value takes the highest stop it reaches", fillFor(19, STOPS), [0x3f, 0x6d, 0x8f, 255]);
eq("the top class is open ended", fillFor(1e9, STOPS), [0x59, 0xa1, 0xa0, 255]);
ok("the same stops give the same array back", fillFor(1, STOPS) === fillFor(2, STOPS));

// --------------------------------------------------------------- tile cache

const tile = (z, x, y, bbox, features) => ({id: `${z}-${x}-${y}`, index: {z, x, y}, bbox, content: features});
const cellFeature = (id, x0, x1, props) => ({
  type: "Feature",
  geometry: {type: "Polygon", coordinates: [[[x0, 0.4], [x1, 0.4], [x1, 0.6], [x0, 0.6], [x0, 0.4]]]},
  properties: {a5_cell: id, ...props},
});

const cache = new TileCache(DAILY);
const NE = {west: 0, east: 180, north: 85.0511287798066, south: 0};
// The same cell straddles the z1 tile seam: half of it in each tile.
cache.add(tile(1, 0, 0, NW, [
  cellFeature(1, 0.9, 1, {count_20200101: 5, count_20200102: 3}),
  cellFeature(2, 0.1, 0.2, {count_20200103: 7}),
]));
cache.add(tile(1, 1, 0, NE, [
  cellFeature(1, 0, 0.1, {count_20200101: 5, count_20200102: 3}),
]));

let h = cache.histogram(1, 1, {w: -180, s: -90, e: 180, n: 90});
eq("a cell split across tiles is counted once", h.cells, 2);
eq("the histogram sums each cell's buckets",
   h.buckets, {20200101: 5, 20200102: 3, 20200103: 7});

// The view covers only the eastern half of the split cell. Its first piece
// was in the western tile, so a bbox from that piece alone would miss it.
h = cache.histogram(1, 1, {w: 1, s: -90, e: 30, n: 90});
eq("the split cell's bounds are the union of its pieces", h.cells, 1);
eq("only the split cell is in the eastern view", h.buckets, {20200101: 5, 20200102: 3});

// A tile holds cells and points together; a point has no cell id and is
// neither a cell nor a piece of one.
cache.add(tile(1, 0, 1, {west: -180, east: 0, north: 0, south: -85.0511287798066}, [
  {type: "Feature", geometry: {type: "Point", coordinates: [0.5, 0.5]},
   properties: {day: 20200102}},
]));
h = cache.histogram(1, 1, {w: -180, s: -90, e: 180, n: 90});
eq("a point in a cell tile is not counted as a cell", h.cells, 2);

// Tiles from another zoom are a different band and do not mix in.
cache.add(tile(3, 0, 0, NW, [cellFeature(3, 0, 1, {count_20200110: 99})]));
h = cache.histogram(1, 1, {w: -180, s: -90, e: 180, n: 90});
eq("a tile outside the zoom range is ignored", h.cells, 2);
h = cache.histogram(0, 3, {w: -180, s: -90, e: 180, n: 90});
eq("a zoom range spanning both counts both", h.cells, 3);

eq("the first properties seen at a zoom are available for sniffing",
   cache.firstProps(3, 3).count_20200110, 99);
eq("no tile at a zoom gives null", cache.firstProps(7, 9), null);

cache.remove(tile(3, 0, 0, NW, []));
h = cache.histogram(0, 3, {w: -180, s: -90, e: 180, n: 90});
eq("a removed tile no longer contributes", h.cells, 2);

// Points are bucketed by their own date and never deduped.
const pts = new TileCache(DAILY);
pts.add(tile(10, 0, 0, NW, [
  {type: "Feature", geometry: {type: "Point", coordinates: [0.5, 0.5]},
   properties: {day: 20200102}},
  {type: "Feature", geometry: {type: "Point", coordinates: [0.6, 0.5]},
   properties: {day: 20200102}},
  {type: "Feature", geometry: {type: "Point", coordinates: [0.99, 0.5]},
   properties: {acq_date: "2020-01-05"}},
]));
h = pts.pointHistogram(10, 10, {w: -180, s: -90, e: -20, n: 90});
eq("points in view are counted per bucket", h.buckets, {20200102: 2});
eq("points in view are counted", h.points, 2);

// ------------------------------------------------------------------- report

if (errors.length) {
  console.log(errors.map(e => `error  ${e}`).join("\n"));
  console.log(`\nFAILED: ${errors.length} of ${checked} checks`);
  process.exit(1);
}
console.log(`OK: ${checked} checks passed`);
