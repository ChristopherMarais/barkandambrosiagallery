// Press and hold: ``onLongPress(root, selector, onHold)`` calls ``onHold(el)`` when a finger, pen or mouse stays down
// on an element matching ``selector`` inside ``root`` for HOLD_MS. Moving more than a few pixels, a scroll, a second
// finger or letting go early cancels it, so a tap and a swipe do what they did before. After a hold the click that
// follows is swallowed, so the tap's own action never runs as well. The long-press menu and dragging the image are
// off on those elements (the page's CSS turns off the iOS callout and text selection).
(function () {
  const HOLD_MS = 500, SLOP_PX = 10, SWALLOW_MS = 600;
  let heldDown = false;   // a hold fired and its pointer is still down
  let swallowUntil = 0;   // the click after a hold's release

  document.addEventListener("pointerup", () => {
    if (heldDown) { heldDown = false; swallowUntil = performance.now() + SWALLOW_MS; }
  }, true);
  document.addEventListener("pointercancel", () => { heldDown = false; }, true);
  document.addEventListener("click", (e) => {
    if (performance.now() >= swallowUntil) return;
    swallowUntil = 0;
    e.preventDefault();
    e.stopImmediatePropagation();
  }, true);

  window.onLongPress = function (root, selector, onHold) {
    if (!root) return;
    let timer = 0, start = null;
    const cancel = () => { clearTimeout(timer); timer = 0; start = null; };
    root.addEventListener("pointerdown", (e) => {
      cancel();   // a second finger (a pinch) cancels the first one's hold too
      const el = e.target.closest && e.target.closest(selector);
      if (!e.isPrimary || !el || !root.contains(el) || (e.pointerType === "mouse" && e.button !== 0)) return;
      start = { x: e.clientX, y: e.clientY, id: e.pointerId };
      timer = setTimeout(() => {
        timer = 0; start = null;
        heldDown = true;
        onHold(el);
      }, HOLD_MS);
    });
    root.addEventListener("pointermove", (e) => {
      if (start && e.pointerId === start.id && Math.hypot(e.clientX - start.x, e.clientY - start.y) > SLOP_PX) cancel();
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach((type) => root.addEventListener(type, cancel));
    window.addEventListener("scroll", cancel, true);
    root.addEventListener("contextmenu", (e) => { if (e.target.closest && e.target.closest(selector)) e.preventDefault(); });
    root.addEventListener("dragstart", (e) => { if (e.target.closest && e.target.closest(selector)) e.preventDefault(); });
  };
})();
