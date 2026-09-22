#!/usr/bin/env node
/**
 * Unit gate for the mounted-archive set.
 *
 * These failures are silent and visual: a bucket counted from the stale
 * archive still draws a bar, a bucket counted twice still draws a bar, and
 * both look exactly like the right answer. So the gates are numeric and the
 * fixtures are built to disagree on purpose.
 *
 * Run: node apps/firms-explorer/mounted.test.mjs
 */
import {axisFor, bestCover, covers, gapsIn, ownerOf, planMounts} from "./mounted.js";

const errors = [];
let checked = 0;
function ok(name, cond, detail = "") {
  checked++;
  if (!cond) errors.push(detail ? `${name}: ${detail}` : name);
}
function eq(name, a, b) {
  ok(name, JSON.stringify(a) === JSON.stringify(b),
     `got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
}

// The landing shape: the rolling window over the tail of the current year.
const latest = {id: "latest", unit: "day", min: "20260908", max: "20260915", rebuilt: 200};
const y2026  = {id: "2026",   unit: "day", min: "20260101", max: "20260914", rebuilt: 100};
const y2025  = {id: "2025",   unit: "day", min: "20250101", max: "20251231", rebuilt: 50};
const alltime = {id: "alltime", unit: "month", min: "200011", max: "202609", rebuilt: 10};

// --- the axis is the union ------------------------------------------------
eq("one archive gives its own extent", axisFor([latest]),
   {unit: "day", min: "20260908", max: "20260915", buckets: 8});
eq("two archives span their union", axisFor([latest, y2026]),
   {unit: "day", min: "20260101", max: "20260915", buckets: 258});
eq("a third year extends it further", axisFor([latest, y2026, y2025]),
   {unit: "day", min: "20250101", max: "20260915", buckets: 623});
ok("no archives means no axis", axisFor([]) === null);

// A monthly archive beside daily ones must not invent days.
eq("the finest unit wins and coarser archives add no buckets",
   axisFor([latest, y2026, alltime]),
   {unit: "day", min: "20260101", max: "20260915", buckets: 258});
eq("alone, the monthly archive is the axis", axisFor([alltime]),
   {unit: "month", min: "200011", max: "202609", buckets: 311});

// --- ownership where they overlap -----------------------------------------
// The whole point: the last days are in both, and they disagree.
eq("the freshest archive owns a day they share",
   ownerOf([y2026, latest], "20260912").id, "latest");
eq("order of mounting does not decide it",
   ownerOf([latest, y2026], "20260912").id, "latest");
eq("outside the window, the year owns it",
   ownerOf([latest, y2026], "20260301").id, "2026");
eq("the most recent day is the window's, where the year is partial",
   ownerOf([latest, y2026], "20260915").id, "latest");
ok("a day nobody covers has no owner",
   ownerOf([latest, y2026], "20240101") === null);
// Ties go to the narrower archive: whoever wrote it meant that span.
eq("a tie breaks toward the narrower archive",
   ownerOf([{...y2026, rebuilt: 100}, {...latest, rebuilt: 100}], "20260912").id,
   "latest");

// --- holes ----------------------------------------------------------------
// The landing state: sixty days of axis, eight of them loaded.
{
  const axis = {unit: "day", min: "20260717", max: "20260915", buckets: 61};
  const gaps = gapsIn([latest], axis);
  eq("one leading gap before the loaded window", gaps, [[0, 52]]);
  ok("the gap stops where the window starts", gaps[0][1] === 52);
}
eq("no gaps once the year is mounted",
   gapsIn([latest, y2026], axisFor([latest, y2026])), []);
{
  // The axis reaches gapsIn in two shapes. axisFor() counts its buckets in
  // `buckets`; makeAxis() counts the same buckets in `count`. Reading one
  // field means reading undefined from the other, and an undefined count
  // silently yields NO gaps -- which paints a wholly unloaded span as loaded,
  // the one wrong answer that looks right. Both shapes must agree.
  const span = {unit: "day", min: "20260101", max: "20260915"};
  eq("an axis that counts its buckets in `buckets` finds the gap",
     gapsIn([latest], {...span, buckets: 258}), [[0, 249]]);
  eq("an axis that counts them in `count` finds the same gap",
     gapsIn([latest], {...span, count: 258}), [[0, 249]]);
  eq("an axis that states neither still finds it",
     gapsIn([latest], span), [[0, 249]]);
}
{
  // Two years mounted with the year between them missing.
  const m = [y2025, {id: "2027", unit: "day", min: "20270101", max: "20271231", rebuilt: 60}];
  const gaps = gapsIn(m, axisFor(m));
  eq("a missing middle year is one interior gap", gaps.length, 1);
  eq("the gap is exactly 2026", gaps[0], [365, 729]);
}

// --- the mount plan -------------------------------------------------------
eq("a one-year view mounts that year",
   planMounts({fromYear: 2026, toYear: 2026}, {years: [2024, 2025, 2026], latest: true}).years,
   [2026]);
eq("a view across New Year mounts both",
   planMounts({fromYear: 2025, toYear: 2026}, {years: [2024, 2025, 2026], latest: true}).years,
   [2025, 2026]);
eq("a wider view is capped at two, nearest the middle",
   planMounts({fromYear: 2022, toYear: 2026}, {years: [2022, 2023, 2024, 2025, 2026], latest: true}).years,
   [2024, 2025]);
eq("years outside the view are never mounted",
   planMounts({fromYear: 2026, toYear: 2026}, {years: [2000, 2026], latest: true}).years,
   [2026]);


// --- which archive serves a whole selection ------------------------------
// Coverage first, freshness only to break a tie. Asking ownerOf about the
// selection's newest bucket instead looks equivalent and is not: the window
// owns today, so a sixty-day selection ending today chose an eight-day
// archive and under-reported fifty-two of them. Browser testing caught that;
// these assertions are why it cannot come back.
{
  const held = [y2026, latest];
  eq("a short recent selection uses the fresher archive",
     bestCover(held, "20260913", "20260915").id, "latest");
  eq("a selection the window exactly covers still uses the window",
     bestCover(held, "20260908", "20260915").id, "latest");
  eq("a selection wider than the window uses the year",
     bestCover(held, "20260717", "20260915").id, "2026");
  // The fixtures end a day apart -- the year at 0914, the window at 0915 --
  // so a selection reaching one day back is an eight-all tie that freshness
  // settles. Two days back is the first that the year covers outright.
  eq("an eight-all tie goes to the fresher archive",
     bestCover(held, "20260907", "20260915").id, "latest");
  eq("once the year covers more of it, the year takes it",
     bestCover(held, "20260901", "20260915").id, "2026");
  eq("a selection with no recent end uses the year",
     bestCover(held, "20260301", "20260331").id, "2026");
  ok("a selection nothing covers has no server",
     bestCover(held, "20240101", "20240131") === null);
}

if (errors.length) {
  console.log(`\nFAILED ${errors.length} of ${checked}:`);
  for (const e of errors) console.log("  " + e);
  process.exit(1);
}
console.log(`OK: ${checked} checks passed`);
