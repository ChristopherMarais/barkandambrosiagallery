/*
 * Confetti for Ambrosia Archive, made of the site's beetle logo (its silhouette, tinted). One call per celebration:
 *   beetleConfetti(canvas, "partial", size)    a few small grey beetles: a checked beetle named correctly to some rank
 *   beetleConfetti(canvas, "validated", size)  brown beetles: a checked beetle named correctly to the species
 *   beetleConfetti(canvas, "plain")            ordinary paper confetti: a strong answer on a beetle nobody checked yet
 *   beetleConfetti(canvas, "level")            gold beetles from both sides, with paper: a new level (only then)
 * ``size`` (0.25 to 1, the share of ranks named correctly) scales how many there are and how big.
 * The logo URL comes from window.BEETLE_LOGO_URL; until it has loaded, a drawn beetle stands in.
 */
(function () {
  const PALETTES = {
    partial: ["#6b7280", "#9ca3af", "#4b5563"],
    validated: ["#3f2a14", "#5b3a1e", "#7c4a1e", "#8b5a2b", "#a0522d"],
    level: ["#ca8a04", "#eab308", "#facc15", "#a16207", "#111827"],
    plain: ["#111827", "#374151", "#9ca3af", "#d1d5db", "#ffffff", "#16a34a"],
  };
  const MASK_HEIGHT = 96;
  let mask = null;          // the logo as a solid silhouette (alpha only), once loaded
  const tinted = {};        // colour -> canvas of the silhouette in that colour

  // The logo is black ink on white: everything not connected to the white border is the beetle, so fill it solid.
  function buildMask(img) {
    const h = MASK_HEIGHT;
    const w = Math.round(h * img.naturalWidth / img.naturalHeight);
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const ctx = c.getContext("2d");
    ctx.drawImage(img, 0, 0, w, h);
    const data = ctx.getImageData(0, 0, w, h);
    const px = data.data;
    const light = (i) => px[i * 4 + 3] < 40 || (px[i * 4] + px[i * 4 + 1] + px[i * 4 + 2]) / 3 > 200;
    const outside = new Uint8Array(w * h);
    const stack = [];
    for (let x = 0; x < w; x++) stack.push(x, (h - 1) * w + x);
    for (let y = 0; y < h; y++) stack.push(y * w, y * w + w - 1);
    while (stack.length) {
      const i = stack.pop();
      if (outside[i] || !light(i)) continue;
      outside[i] = 1;
      const x = i % w, y = (i / w) | 0;
      if (x > 0) stack.push(i - 1);
      if (x < w - 1) stack.push(i + 1);
      if (y > 0) stack.push(i - w);
      if (y < h - 1) stack.push(i + w);
    }
    for (let i = 0; i < w * h; i++) {
      px[i * 4] = px[i * 4 + 1] = px[i * 4 + 2] = 0;
      px[i * 4 + 3] = outside[i] ? 0 : 255;
    }
    ctx.putImageData(data, 0, 0);
    return c;
  }

  function silhouette(colour) {
    if (!mask) return null;
    if (!tinted[colour]) {
      const c = document.createElement("canvas");
      c.width = mask.width; c.height = mask.height;
      const ctx = c.getContext("2d");
      ctx.drawImage(mask, 0, 0);
      ctx.globalCompositeOperation = "source-in";
      ctx.fillStyle = colour;
      ctx.fillRect(0, 0, c.width, c.height);
      tinted[colour] = c;
    }
    return tinted[colour];
  }

  if (window.BEETLE_LOGO_URL) {
    const img = new Image();
    img.onload = () => { try { mask = buildMask(img); } catch (e) { mask = null; } };
    img.src = window.BEETLE_LOGO_URL;
  }

  // Stand-in until the logo has loaded: a simple drawn beetle.
  function drawBeetle(ctx, size, colour) {
    ctx.fillStyle = colour;
    ctx.beginPath(); ctx.ellipse(0, size * 0.18, size * 0.2, size * 0.34, 0, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.ellipse(0, -size * 0.24, size * 0.16, size * 0.15, 0, 0, Math.PI * 2); ctx.fill();
  }

  function piece(kind, size, origin) {
    const beetle = kind !== "plain";
    const palette = PALETTES[kind] || PALETTES.validated;
    const base = { partial: 12, validated: 18, level: 26, plain: 5 }[kind] || 16;
    const spread = { partial: 6, validated: 12, level: 18, plain: 6 }[kind] || 10;
    const fromSide = origin !== 0;
    return {
      beetle,
      x: fromSide ? (origin < 0 ? -10 : innerWidth + 10) : innerWidth / 2 + (Math.random() - 0.5) * 80,
      y: fromSide ? innerHeight * (0.55 + Math.random() * 0.2) : innerHeight * 0.55,
      vx: fromSide ? -origin * (5 + Math.random() * 9) : (Math.random() - 0.5) * 9,
      vy: -Math.random() * (fromSide ? 13 : 11) - 4,
      size: (base + Math.random() * spread) * (beetle ? 0.6 + 0.4 * size : 1),
      spin: Math.random() * Math.PI * 2,
      vs: (Math.random() - 0.5) * (beetle ? 0.2 : 0.4),
      colour: palette[(Math.random() * palette.length) | 0],
    };
  }

  window.beetleConfetti = function (canvas, kind, size) {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    kind = PALETTES[kind] ? kind : (kind === "beetles" ? "validated" : "plain");
    size = Math.max(0.25, Math.min(1, Number(size) || 1));
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    canvas.width = innerWidth * dpr;
    canvas.height = innerHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    let pieces;
    if (kind === "level") {
      // the level-up burst: gold beetles from both sides, with paper confetti in the middle
      pieces = [].concat(
        Array.from({ length: 28 }, () => piece("level", 1, -1)),
        Array.from({ length: 28 }, () => piece("level", 1, 1)),
        Array.from({ length: 50 }, () => piece("plain", 1, 0)),
      );
    } else {
      const count = { partial: 4 + Math.round(10 * size), validated: 14 + Math.round(26 * size), plain: 70 }[kind];
      pieces = Array.from({ length: count }, () => piece(kind, size, 0));
    }
    const duration = kind === "level" ? 2600 : kind === "partial" ? 1300 : 1800;
    const start = performance.now();
    (function frame(now) {
      const t = now - start;
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      pieces.forEach((p) => {
        p.vy += 0.35; p.x += p.vx; p.y += p.vy; p.spin += p.vs;
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.spin);
        ctx.globalAlpha = Math.max(0, 1 - t / duration);
        if (p.beetle) {
          const shape = silhouette(p.colour);
          if (shape) {
            const w = p.size * shape.width / shape.height;
            ctx.drawImage(shape, -w / 2, -p.size / 2, w, p.size);
          } else {
            drawBeetle(ctx, p.size, p.colour);
          }
        } else {
          ctx.fillStyle = p.colour;
          ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
          if (p.colour === "#ffffff") { ctx.strokeStyle = "#9ca3af"; ctx.lineWidth = 0.5; ctx.strokeRect(-p.size / 2, -p.size / 4, p.size, p.size / 2); }
        }
        ctx.restore();
      });
      if (t < duration) requestAnimationFrame(frame); else ctx.clearRect(0, 0, innerWidth, innerHeight);
    })(start);
  };
})();
