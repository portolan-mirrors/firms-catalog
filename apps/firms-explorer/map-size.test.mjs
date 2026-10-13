#!/usr/bin/env node
/**
 * Unit gate for the canvas size check.
 *
 * This failure is silent and visual. A camera that keeps the size it had
 * still draws a map, and the map still pans and zooms. It is only wrong
 * against the window around it, which no numeric check in the page notices.
 *
 * The gate therefore pins the two things the predicate has to get right: it
 * fires when the box has changed, and it stays quiet when the box has not.
 * A predicate that always fired would resize on every frame, and a predicate
 * that never fired would be the bug it fixes.
 *
 * Run: node apps/firms-explorer/map-size.test.mjs
 */
import {needsResize} from "./map-size.js";

const errors = [];
let checked = 0;
function ok(name, cond, detail = "") {
  checked++;
  if (!cond) errors.push(detail ? `${name}: ${detail}` : name);
}

const box = (w, h) => ({clientWidth: w, clientHeight: h});

// Agreement is the common case: the observer fires, nothing needs doing.
ok("same size is quiet", !needsResize({width: 735, height: 786}, box(735, 786)));

// The measured fault: a window dragged taller, the camera left behind.
ok("a taller box needs a resize",
  needsResize({width: 707, height: 559}, box(735, 786)));

// Height alone, which is the axis the report came in on.
ok("height alone needs a resize",
  needsResize({width: 735, height: 559}, box(735, 786)));

// Width alone. The same fault, and the one that looked fine because the
// stretch was 4 % rather than 40 %.
ok("width alone needs a resize",
  needsResize({width: 707, height: 786}, box(735, 786)));

// MapLibre keeps floats and clientWidth is an integer. A camera already
// rounded to the box must not resize, or every frame would resize.
ok("a sub-pixel camera on the same box is quiet",
  !needsResize({width: 734.6, height: 786.4}, box(735, 786)));
ok("a sub-pixel camera on a different box resizes",
  needsResize({width: 734.6, height: 786.4}, box(735, 900)));

// A collapsed container reports zero. Resizing to it is correct: MapLibre
// handles a zero-sized map, and the next resize brings it back.
ok("a collapsed box needs a resize",
  needsResize({width: 735, height: 786}, box(0, 0)));
ok("a camera already at zero is quiet",
  !needsResize({width: 0, height: 0}, box(0, 0)));

if (errors.length) {
  console.log(`\nFAILED ${errors.length} of ${checked}:`);
  for (const e of errors) console.log("  " + e);
  process.exit(1);
}
console.log(`OK: ${checked} checks passed`);
