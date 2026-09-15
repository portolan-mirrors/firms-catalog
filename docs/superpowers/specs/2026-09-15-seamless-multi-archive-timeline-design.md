# One seamless timeline across several archives

Status: design, not yet approved. Written by AI; needs human review.
Supersedes the archive-switching section of
[2026-09-10-adaptive-timeline-design.md](2026-09-10-adaptive-timeline-design.md).

## The problem

The explorer mounts exactly one archive. Switching is a teardown: the old
source is removed, the new one is read, the axis is rebuilt from the new
archive's `firms:timeline`, and the map blanks for whatever the round trip
costs. The timeline can therefore never show more time than the single loaded
archive holds.

That was tolerable while the landing archive was all-time, which covers the
whole record. It is not tolerable now. Landing on the rolling window is what
made first paint cheap — its z2 tiles total 0.27 MB against all-time's 3.98 MB,
because a rolling-window cell carries eight daily columns where an all-time
cell carries three hundred-odd monthly ones — but the rolling window holds
eight days, so the timeline shows eight days. The sixty-day default window
clamps to it.

Two things are wrong with the current model, and only the second is new:

1. **A switch is visible.** The map blanks, the axis jumps, the bar chart
   changes resolution under the cursor.
2. **An archive boundary is a wall.** A drag from December 2025 into January
   2026 crosses one, and there is no view that spans it.

## What we want

One timeline. It starts as whatever the cheapest archive can fill, and deepens
as more arrive, without the map blanking and without the axis jumping under a
drag in progress.

Concretely, on landing:

- Mount the rolling window. Draw immediately: five days selected, sixty shown.
- The fifty-two days the rolling window does not cover are **in the axis but
  not loaded**, and are drawn as such — hatched, not as empty bars. An empty
  bar says "no fires"; that would be a lie.
- Fetch the current year's archive in the background. As it arrives, those
  fifty-two days fill in with real bars.
- Reaching further back pulls the previous year in the same way.

## The model

Replace "the mounted archive" with **a set of mounted archives and a derived
axis**.

```
mounted: [latest, 2026, 2025]        each with its own extent and unit
axis:    union of their extents, at the finest unit among them
bars:    per bucket, from the archive that owns it
hatched: buckets in the axis that no mounted archive covers
```

### Which archive owns a bucket

Archives overlap: the rolling window and the current year both hold the last
few days, and they disagree, because the year archive is rebuilt daily while
the window is rebuilt hourly. The freshest wins, so **precedence is by
recency of build, not by position in the list**: the rolling window owns the
days it covers, the year archive owns everything else within its year.

This matters for the most recent day specifically, which is partial in the
year archive and complete in the window.

### Which archives may be mounted

Daily archives are large. The cap is **two year-archives plus the rolling
window**, which bounds a daily-resolution drag to roughly two years — enough
to cross a New Year, which is the case that matters, without letting a
zoomed-out drag mount twenty-seven.

Zooming out past two years is not a wider daily view; it is a switch to
all-time's monthly axis, which is what that archive is for. That switch stays
as it is today, because at that zoom the unit genuinely changes and pretending
otherwise would mean showing monthly bars on a daily axis.

### Eviction

When a third year would be mounted, drop the one furthest from the visible
domain. Dropping is removing a source and its layers; it costs nothing and the
archive is re-read if the user comes back.

## What this changes

| area | change |
|---|---|
| `mount()` | becomes `add()` / `drop()`; stops rebuilding the axis |
| axis | derived from the mounted set, not from one archive's metadata |
| `considerSwitch` | becomes "which archives should be mounted for this view" |
| histogram | walks every mounted source, not one |
| timeline paint | new state per bucket: loaded, loading, not covered |
| map layers | one pair of layers per mounted archive, not one pair total |

The last one is the sharpest edge. Two archives covering the same day would
draw the same cells twice, at different opacities, and the overlap would read
as a brighter region. **Only one archive may draw at a time**: the map shows
the owner of the *selection*, while the timeline aggregates across all of
them. That keeps the map honest and confines the blending problem to the bar
chart, where it is a sum rather than a composite.

## Testing

The failures here are visual and silent, so the gates have to be numeric.

- A bucket covered by two archives is counted **once**, from the owner.
  Build two fixtures that overlap and disagree; assert the total equals the
  owner's, not the sum.
- The axis is the union of mounted extents, at the finest unit present.
- Dropping an archive removes its buckets from the axis and leaves the
  remainder unchanged.
- The cap holds: mounting a third year evicts the furthest, never the nearest.
- A drag spanning two years yields a selection whose total equals the sum of
  the per-year totals over the same range, computed independently.

## Open questions

1. **Does the map really need one owner?** The alternative is drawing every
   mounted archive with a filter that excludes buckets it does not own. That
   is more faithful but multiplies the paint cost by the number mounted, and
   the paint cost is what this whole exercise has been spent reducing.
2. **What does "loading" look like?** Hatched is proposed for *not covered*.
   A bucket whose archive is in flight is a third state, and showing it the
   same as not-covered means the chart does not visibly settle.
3. **Does the rolling window keep its own entry in the picker?** Once it is
   always mounted, "Last 7 days" as a selectable archive is a different thing
   from the others, and may be better expressed as a time range than as an
   archive.
