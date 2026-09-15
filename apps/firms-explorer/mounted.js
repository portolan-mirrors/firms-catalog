/**
 * The set of archives currently mounted, and the axis their union implies.
 *
 * The explorer used to mount one archive, which made the timeline exactly as
 * wide as whatever was loaded. That was fine while the landing archive covered
 * the whole record, and stopped being fine when landing moved to the rolling
 * window to make first paint cheap: eight days of tiles means eight days of
 * timeline, and a sixty-day default window clamps to it.
 *
 * So the axis is derived from a set instead. Each archive contributes the span
 * it covers; the axis spans their union; and a bucket inside the union that no
 * archive covers is a hole the caller draws as unloaded rather than as zero.
 * An empty bar reads as "no fires", which is a different and false claim.
 *
 * This module is deliberately free of the map, the DOM and the network: it
 * answers "what does this set of extents imply" so that question can be tested
 * without any of them.
 */

import {indexKey, keyIndex} from "./timeline.js";

/** Archives whose buckets are days, coarsest unit last. */
const UNIT_RANK = {day: 0, month: 1};

/**
 * Which mounted archive owns a bucket, where several cover it.
 *
 * Overlap is normal and the archives disagree where it happens: the rolling
 * window is rebuilt hourly and the year archive daily, so on the most recent
 * day the window is complete and the year is partial. Taking either "the first
 * mounted" or "the most specific" would pick the stale one half the time.
 *
 * Freshness decides, and `rebuilt` carries it. Ties break toward the narrower
 * archive, which is the one whose author was thinking about that span.
 */
export function ownerOf(mounted, key) {
  let best = null;
  for (const m of mounted) {
    if (!covers(m, key)) continue;
    if (!best) { best = m; continue; }
    const fresher = (m.rebuilt || 0) - (best.rebuilt || 0);
    if (fresher > 0 || (fresher === 0 && span(m) < span(best))) best = m;
  }
  return best;
}

export function covers(m, key) {
  if (m.unit !== unitOf(key)) return false;
  const i = keyIndex(key, m.unit);
  return i >= keyIndex(m.min, m.unit) && i <= keyIndex(m.max, m.unit);
}

const span = m => keyIndex(m.max, m.unit) - keyIndex(m.min, m.unit);
const unitOf = key => (String(key).length === 6 ? "month" : "day");

/**
 * The axis the mounted set implies: the union of their extents, at the finest
 * unit any of them uses.
 *
 * Mixing units is not a merge. A monthly archive beside a daily one describes
 * the same time at a coarser grain, and drawing its months as though they were
 * days would invent thirty bars from one number. So the finest unit wins and
 * coarser archives contribute no buckets -- they are still mounted, and still
 * own their span once the view zooms out far enough that they are the finest
 * thing present.
 */
export function axisFor(mounted) {
  const live = mounted.filter(m => m && m.min && m.max && m.unit);
  if (!live.length) return null;
  const unit = live
    .map(m => m.unit)
    .reduce((a, b) => (UNIT_RANK[a] <= UNIT_RANK[b] ? a : b));
  const same = live.filter(m => m.unit === unit);
  const min = same.reduce((a, m) => (keyIndex(m.min, unit) < keyIndex(a, unit) ? m.min : a), same[0].min);
  const max = same.reduce((a, m) => (keyIndex(m.max, unit) > keyIndex(a, unit) ? m.max : a), same[0].max);
  return {unit, min, max, buckets: keyIndex(max, unit) - keyIndex(min, unit) + 1};
}

/**
 * The spans inside the axis that no mounted archive covers, as [from, to]
 * bucket-index pairs.
 *
 * Returned as ranges rather than a per-bucket flag because that is what a
 * painter wants -- one hatched rectangle per gap, not one per bucket -- and
 * because the gaps are few and wide while the buckets are many.
 */
export function gapsIn(mounted, axis) {
  if (!axis) return [];
  const origin = keyIndex(axis.min, axis.unit);
  const out = [];
  let start = null;
  for (let i = 0; i < axis.buckets; i++) {
    const key = indexKey(origin + i, axis.unit);
    const held = mounted.some(m => m.unit === axis.unit && covers(m, key));
    if (!held && start === null) start = i;
    if (held && start !== null) { out.push([start, i - 1]); start = null; }
  }
  if (start !== null) out.push([start, axis.buckets - 1]);
  return out;
}

/**
 * Which archives should be mounted for a visible domain, and which dropped.
 *
 * The cap is what keeps this affordable. Daily archives are hundreds of
 * megabytes, and a drag that wandered across the record would mount all
 * twenty-seven; two years plus the rolling window bounds it to crossing one
 * New Year, which is the case that needs to be seamless. Wider than that is
 * not a wider daily view -- it is the monthly archive, whose whole purpose is
 * that span.
 *
 * Eviction is by distance from the visible domain, so the year being dragged
 * towards survives and the one left behind goes.
 */
export function planMounts(domain, {years, latest, maxYears = 2}) {
  const want = [];
  if (latest) want.push(latest);
  const [lo, hi] = [domain.fromYear, domain.toYear];
  const inView = years.filter(y => y >= lo && y <= hi).sort((a, b) => b - a);
  const mid = (lo + hi) / 2;
  const ranked = inView.sort((a, b) => Math.abs(a - mid) - Math.abs(b - mid));
  return {years: ranked.slice(0, maxYears).sort((a, b) => a - b), latest: want.length > 0};
}
