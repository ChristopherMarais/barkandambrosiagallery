// The specimen page's photo (owner, round 7): the whole photo opens over the page, like the game's photo viewer
// (zoom with the buttons, the wheel, a pinch or a double click, drag when zoomed; brightness and contrast; the flag,
// wired by the page's own script). On the page itself, the mouse wheel zooms the photo where the pointer is and a drag
// moves it (owner, replacing the round lens), and a Lighting button sets its brightness and contrast.
(function () {
  const $ = (id) => document.getElementById(id);

  // --- Zoom, as the game's makeZoomable (game_play.html): the frame is measured once per gesture, the transform is
  // written at most once per frame, and the browser's own image drag and text selection never take over a drag. ---
  const ZOOM_STEP = 2.5, ZOOM_MAX = 5, BUTTON_STEP = 1.5, TAP_MS = 300, DRAG_SLOP = 4;
  const zoomEase = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // ``opts.inline`` (the photo on the page): a mouse only (a phone opens the whole photo instead), the pointer is caught
  // only while zoomed (so a click on another beetle's box still follows its link), a click is left alone unless it
  // ended a drag, and a wheel turned down on the unzoomed photo scrolls the page as usual.
  function makeZoomable(frame, layer, onChange, opts = {}) {
    let z = 1, tx = 0, ty = 0, lastTap = 0, drag = null, pinch = null, moved = false;
    let rect = null, raf = 0, wheelEnd = null;
    const pointers = new Map();   // pointerId -> [clientX, clientY]
    const measure = () => { rect = frame.getBoundingClientRect(); return rect; };
    const local = (x, y) => { const r = rect || measure(); return [x - r.left, y - r.top]; };
    const render = () => {
      raf = 0;
      layer.style.transform = z > 1 ? `translate3d(${tx}px, ${ty}px, 0) scale(${z})` : "";
      frame.classList.toggle("zoomed", z > 1);
      onChange(z);
    };
    // keep the photo covering the frame, then draw on the next frame (``animate``: a short ease, for steps only)
    const update = (animate) => {
      const r = rect || measure();
      tx = Math.min(0, Math.max(r.width - r.width * z, tx));
      ty = Math.min(0, Math.max(r.height - r.height * z, ty));
      layer.style.transition = animate && zoomEase ? "transform 0.18s ease-out" : "";
      if (!raf) raf = requestAnimationFrame(render);
    };
    const moving = (on) => { layer.style.willChange = on ? "transform" : ""; };
    const zoomAt = (nz, cx, cy, animate) => {
      nz = Math.min(ZOOM_MAX, Math.max(1, nz));
      tx = cx - (cx - tx) * nz / z;
      ty = cy - (cy - ty) * nz / z;
      z = nz;
      if (z === 1) { tx = 0; ty = 0; }
      update(animate);
    };
    const startDrag = ([x, y]) => { drag = { x: x - tx, y: y - ty, sx: x, sy: y }; };
    const startPinch = () => {
      const [a, b] = [...pointers.values()];
      const [mx, my] = local((a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
      pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]) || 1, z, mx, my, tx, ty };
      drag = null;
    };
    frame.addEventListener("dragstart", (e) => e.preventDefault());
    frame.addEventListener("wheel", (e) => {
      if (opts.inline && z <= 1 && e.deltaY >= 0) return;   // not zoomed and scrolling down: the page scrolls
      e.preventDefault();
      measure();
      moving(true);
      clearTimeout(wheelEnd);
      wheelEnd = setTimeout(() => { if (!pointers.size) moving(false); }, 200);
      const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
      zoomAt(z * Math.min(1.25, Math.max(0.8, Math.exp(-dy * 0.002))), ...local(e.clientX, e.clientY));
    }, { passive: false });
    frame.addEventListener("pointerdown", (e) => {
      if (e.button > 0) return;
      if (opts.inline && (e.pointerType !== "mouse" || z <= 1)) { moved = false; return; }
      if (!pointers.size) { measure(); moved = false; }
      pointers.set(e.pointerId, [e.clientX, e.clientY]);
      // inline: no capture, so a click still reaches the box under it (a link to that beetle); the window hears the release
      if (!opts.inline) try { frame.setPointerCapture(e.pointerId); } catch (err) { /* already gone */ }
      if (pointers.size === 2) { startPinch(); moved = true; }
      else if (pointers.size === 1 && z > 1) startDrag([e.clientX, e.clientY]);
      if (pinch || drag) moving(true);
    });
    frame.addEventListener("pointermove", (e) => {
      if (!pointers.has(e.pointerId)) return;
      pointers.set(e.pointerId, [e.clientX, e.clientY]);
      if (pinch && pointers.size >= 2) {
        const [a, b] = [...pointers.values()];
        const nz = Math.min(ZOOM_MAX, Math.max(1, pinch.z * Math.hypot(a[0] - b[0], a[1] - b[1]) / pinch.d));
        const [mx, my] = local((a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
        z = nz;
        tx = mx - (pinch.mx - pinch.tx) * nz / pinch.z;
        ty = my - (pinch.my - pinch.ty) * nz / pinch.z;
        if (z === 1) { tx = 0; ty = 0; }
        update();
      } else if (drag) {
        if (!moved && Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) > DRAG_SLOP) moved = true;
        tx = e.clientX - drag.x;
        ty = e.clientY - drag.y;
        frame.classList.add("dragging");
        update();
      }
    });
    const up = (e) => {
      if (!pointers.delete(e.pointerId)) return;
      if (pinch && pointers.size === 1) {
        pinch = null;
        if (z > 1) startDrag([...pointers.values()][0]);   // the finger left on the photo carries on as a drag
      }
      if (!pointers.size) { drag = null; pinch = null; frame.classList.remove("dragging"); moving(false); }
    };
    ["pointerup", "pointercancel", "lostpointercapture"].forEach((t) => (opts.inline ? window : frame).addEventListener(t, up));
    if (opts.inline) {
      // a click that ended a drag is not a click (no link, no boxes switch); every other click does what it did
      frame.addEventListener("click", (e) => { if (moved) { e.preventDefault(); e.stopPropagation(); moved = false; } }, true);
    } else
    // a double click (or double tap) zooms in at that spot, or back out
    frame.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();   // a click on the photo never closes the viewer
      if (moved) return;
      const now = Date.now();
      if (now - lastTap < TAP_MS) {
        lastTap = 0;
        measure();
        zoomAt(z > 1 ? 1 : ZOOM_STEP, ...local(e.clientX, e.clientY), true);
        return;
      }
      lastTap = now;
    });
    return {
      zoom: () => z,
      // the buttons and keys zoom about the middle of the photo
      zoomBy: (factor) => { const r = measure(); zoomAt(z * factor, r.width / 2, r.height / 2, true); },
      reset: () => {
        z = 1; tx = 0; ty = 0;
        cancelAnimationFrame(raf);
        layer.style.transition = "";
        render();
      },
    };
  }

  // --- The whole photo over the page ---
  const viewer = $("photo-viewer");
  const opener = $("roi-fullsize");
  if (viewer && opener) {
    const img = $("pv-img"), photo = $("roi-photo");
    const zoomIn = $("pv-zoom-in"), zoomOut = $("pv-zoom-out"), level = $("pv-zoom-level");
    const lightBtn = $("pv-light-btn"), lightPanel = $("pv-light-panel");
    const brightness = $("pv-brightness"), contrast = $("pv-contrast");
    let returnFocus = null, bodyOverflow = "";

    const zoom = makeZoomable($("pv-frame"), $("pv-zoom"), (z) => {
      level.textContent = Math.round(z * 100) + "%";
      zoomOut.disabled = z <= 1;
      zoomIn.disabled = z >= ZOOM_MAX;
    });

    function setLight(open) {
      lightPanel.classList.toggle("hidden", !open);
      lightBtn.setAttribute("aria-expanded", String(open));
      if (open) brightness.focus();
    }
    function applyLight() {
      const b = Number(brightness.value), c = Number(contrast.value);
      viewer.style.setProperty("--pv-filter", b !== 100 || c !== 100 ? `brightness(${b}%) contrast(${c}%)` : "none");
    }
    function resetLight() {
      brightness.value = 100;
      contrast.value = 100;
      applyLight();
    }

    function open() {
      if (!img.getAttribute("src")) img.src = viewer.dataset.src;   // the page's photo: usually in the cache already
      if (photo && photo.dataset.box) viewer.dataset.box = photo.dataset.box;   // the boxes as on the page
      returnFocus = document.activeElement;
      bodyOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";   // the page never scrolls under the viewer
      viewer.classList.remove("hidden");
      zoom.reset();
      $("pv-close").focus();
    }
    function close() {
      if (viewer.classList.contains("hidden")) return;
      viewer.classList.add("hidden");
      setLight(false);
      document.body.style.overflow = bodyOverflow;
      if (returnFocus && returnFocus.focus) returnFocus.focus();
    }

    opener.addEventListener("click", (e) => {
      if (e.button > 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;   // a new tab, as a link would
      e.preventDefault();
      open();
    });
    $("pv-close").addEventListener("click", close);
    viewer.addEventListener("click", (e) => { if (e.target === viewer) close(); });   // outside the photo and its bar
    zoomIn.addEventListener("click", () => zoom.zoomBy(BUTTON_STEP));
    zoomOut.addEventListener("click", () => zoom.zoomBy(1 / BUTTON_STEP));
    lightBtn.addEventListener("click", (e) => { e.stopPropagation(); setLight(lightPanel.classList.contains("hidden")); });
    [brightness, contrast].forEach((input) => input.addEventListener("input", applyLight));
    $("pv-light-reset").addEventListener("click", resetLight);

    // Keys while it is open: Esc closes (the page's script closes an open flag menu first and stops it there),
    // + and - zoom, 0 goes back to the whole photo, Tab stays inside the viewer.
    window.addEventListener("keydown", (e) => {
      if (viewer.classList.contains("hidden")) return;
      if (e.key === "Escape") {
        if (!lightPanel.classList.contains("hidden")) { setLight(false); lightBtn.focus(); }
        else close();
        e.preventDefault();
        return;
      }
      if (e.target && e.target.matches && e.target.matches("input")) return;   // the sliders keep their keys
      if (e.key === "+" || e.key === "=") { zoom.zoomBy(BUTTON_STEP); e.preventDefault(); }
      else if (e.key === "-") { zoom.zoomBy(1 / BUTTON_STEP); e.preventDefault(); }
      else if (e.key === "0") { zoom.reset(); e.preventDefault(); }
      else if (e.key === "Tab") {
        const stops = [...viewer.querySelectorAll("button, input")].filter((el) => !el.disabled && el.offsetParent !== null);
        if (!stops.length) return;
        const first = stops[0], last = stops[stops.length - 1];
        if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); }
        else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
      }
    });
  }

  // --- The photo on the page: the wheel zooms where the pointer is, a drag moves it; Lighting (owner) ---
  const pageFrame = $("roi-frame"), pageLayer = $("roi-zoom");
  if (pageFrame && pageLayer) {
    const pageZoom = makeZoomable(pageFrame, pageLayer, (z) => {
      const reset = $("roi-zoom-reset");
      if (reset) reset.classList.toggle("hidden", z <= 1);
    }, { inline: true });
    const reset = $("roi-zoom-reset");
    if (reset) reset.addEventListener("click", () => pageZoom.reset());
  }
  const pageLightBtn = $("roi-light-btn"), pageLightPanel = $("roi-light-panel");
  if (pageLightBtn && pageLightPanel && pageFrame) {
    const pb = $("roi-brightness"), pc = $("roi-contrast");
    const setPageLight = (open) => {
      pageLightPanel.classList.toggle("hidden", !open);
      pageLightBtn.setAttribute("aria-expanded", String(open));
    };
    const applyPageLight = () => {
      const b = Number(pb.value), c = Number(pc.value);
      pageFrame.style.setProperty("--roi-filter", b !== 100 || c !== 100 ? `brightness(${b}%) contrast(${c}%)` : "none");
    };
    pageLightBtn.addEventListener("click", (e) => { e.stopPropagation(); setPageLight(pageLightPanel.classList.contains("hidden")); });
    [pb, pc].forEach((input) => input.addEventListener("input", applyPageLight));
    $("roi-light-reset").addEventListener("click", () => { pb.value = 100; pc.value = 100; applyPageLight(); });
    document.addEventListener("click", (e) => {
      if (!pageLightPanel.classList.contains("hidden") && !e.target.closest("#roi-light-panel, #roi-light-btn")) setPageLight(false);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !pageLightPanel.classList.contains("hidden")) { setPageLight(false); pageLightBtn.focus(); }
    });
  }
})();
