/**
 * Keep a MapLibre canvas the size of the element it sits in.
 *
 * MapLibre sizes its own canvas from the container, because `trackResize`
 * defaults to true. On a window dragged taller that tracking was measured to
 * miss. The sibling catalog's explorer caught it on a Retina MacBook: the
 * canvas held a 707x559 drawing buffer inside a 735x786 box, so the browser
 * stretched the buffer to fill the box and the basemap stretched by 40 % in y.
 *
 * The stretch is the whole fault on a page drawn by MapLibre alone. On a page
 * that also draws with deck.gl it is worse, because deck.gl reads the box and
 * draws at the true size. The two engines then place the same cell about
 * 114 px apart, and no pan clears it, because neither camera has moved.
 *
 * Both listeners are public API and `map.resize()` is idempotent, so a resize
 * the built-in tracking does catch costs one comparison.
 */

/**
 * Has the camera fallen out of step with the box it draws into?
 *
 * The comparison is also what stops the observer from seeing its own work:
 * `map.resize()` changes the canvas, not the container, so a sync that ran
 * leaves nothing for the next callback to do.
 *
 * MapLibre keeps its size as floats, and `clientWidth` is an integer, so the
 * camera is rounded before the two are compared. Without that a container on
 * a half pixel would resize on every frame.
 */
export function needsResize(transform, box) {
  return Math.round(transform.width) !== box.clientWidth
    || Math.round(transform.height) !== box.clientHeight;
}

/** Watch `map`'s container and resize the map whenever the two disagree. */
export function keepMapSized(map) {
  const sync = () => {
    if (needsResize(map.transform, map.getContainer())) map.resize();
  };
  window.addEventListener("resize", sync);
  new ResizeObserver(sync).observe(map.getContainer());
  sync();
  return sync;
}
