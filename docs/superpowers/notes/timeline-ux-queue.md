# Timeline interaction changes (queued behind the map slice)

Requested 2026-09-10. Held until `explorer-map` commits, to avoid two writers
in `apps/firms-explorer/timeline.js`.

1. **Default selection: last 7 days.** On a daily axis that is the last seven
   buckets. On the monthly all-time axis seven days is finer than one bucket,
   so it clamps to the last bucket rather than selecting nothing.
2. **Default visible domain: last 9 months.** Independent of the selection, so
   the selection sits inside a readable window rather than filling it.
3. **Remove Reset.** It does exactly what the "all" preset does.
4. **Two-finger horizontal swipe pans the domain.** Trackpad swipes arrive as
   wheel events carrying `deltaX`; the handler must route those to pan and
   leave `deltaY` and ctrl+wheel to zoom, or the two gestures fight.
5. **`+` zooms about the selection centre**, not the track centre, so zooming
   in keeps what you selected on screen.
6. **New "zoom to selection" button** fitting the domain to the selection, with
   a little padding so the handles stay grabbable.
7. **Shift+drag starts a new selection** instead of panning. Plain drag keeps
   panning.

   This is deliberately the inverse of Perfetto, which uses plain drag to make
   an area selection and shift+drag to pan. Raised and decided: keep plain drag
   panning so nothing existing changes.
8. **`F` fits the domain to the selection**, the same action as the new button.
   Taken from Perfetto, where `F` centres the selection and pressing it again
   fits it to the viewport.
