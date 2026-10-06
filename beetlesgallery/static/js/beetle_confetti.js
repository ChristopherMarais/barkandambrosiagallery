/*
 * Confetti for Ambrosia Archive, made of the site's beetle logo (its silhouette, tinted). One call per celebration:
 *   beetleConfetti(canvas, "partial", size)    a few grey beetles: a beetle named correctly to some rank
 *   beetleConfetti(canvas, "validated", size)  brown beetles: a right species or a perfect grid; at full size a second wave
 *   beetleConfetti(canvas, "plain", size)      ordinary paper confetti: a strong answer on a beetle nobody checked yet
 *   beetleConfetti(canvas, "level")            the biggest: gold beetles from both sides and a centre burst with paper
 * ``size`` (0.2 to 1, from the answer's points) scales how many pieces there are, how big and how far they fly.
 * Bursts run side by side in one shared animation loop, so a daily-goal burst never wipes out a level-up. The total
 * is capped (lower on phones): the oldest pieces fade out early to make room. Nothing moves under reduced motion.
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
  const GRAVITY = 0.35;     // px per frame², at 60 frames a second
  const FADE = 0.35;        // the last share of a piece's life is spent fading out
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
    img.onload = () => {
      try {
        mask = buildMask(img);
        // tint every colour now, while nothing is celebrating, so the first big burst has no work to do
        ["partial", "validated", "level"].forEach((kind) => PALETTES[kind].forEach(silhouette));
      } catch (e) { mask = null; }
    };
    img.src = window.BEETLE_LOGO_URL;
  }

  // Stand-in until the logo has loaded: a simple drawn beetle.
  function drawBeetle(ctx, size, colour) {
    ctx.fillStyle = colour;
    ctx.beginPath(); ctx.ellipse(0, size * 0.18, size * 0.2, size * 0.34, 0, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.ellipse(0, -size * 0.24, size * 0.16, size * 0.15, 0, 0, Math.PI * 2); ctx.fill();
  }

  const rand = (lo, hi) => lo + Math.random() * (hi - lo);

  // Phones get fewer pieces in the air at once.
  function maxPieces() {
    const small = innerWidth < 640 || window.matchMedia("(pointer: coarse)").matches;
    return small ? 320 : 520;
  }

  /*
   * One piece. ``from`` is "left", "right" or "centre"; ``size`` scales piece size and speed; ``born`` and ``life``
   * (ms) say when it appears and how long it lasts. Speeds grow with the square root of the screen height, so a burst
   * fills a tall screen as well as a short one.
   */
  function piece(kind, size, from, born, life) {
    const beetle = kind !== "plain";
    const palette = PALETTES[kind];
    const base = { partial: 13, validated: 18, level: 26, plain: 6 }[kind];
    const vary = { partial: 7, validated: 12, level: 16, plain: 6 }[kind];
    const reach = Math.sqrt(Math.max(0.6, Math.min(1.6, innerHeight / 800)));
    const p = {
      beetle,
      size: (base + Math.random() * vary) * (0.65 + 0.35 * size),
      spin: Math.random() * Math.PI * 2,
      vs: rand(-0.5, 0.5) * (beetle ? 0.2 : 0.4),
      flip: Math.random() * Math.PI * 2,   // paper turns over as it falls
      colour: palette[(Math.random() * palette.length) | 0],
      born,
      end: born + life * rand(0.8, 1),
    };
    p.fade = (p.end - born) * FADE;
    if (from === "centre") {
      const angle = rand(-Math.PI * 0.85, -Math.PI * 0.15);   // a fan, mostly upwards
      const speed = rand(5, 9 + 7 * size) * reach;
      p.x = innerWidth / 2 + rand(-40, 40);
      p.y = innerHeight * 0.55;
      p.vx = Math.cos(angle) * speed * 1.1;
      p.vy = Math.sin(angle) * speed - 3;
    } else {
      const side = from === "left" ? -1 : 1;
      p.x = side < 0 ? -10 : innerWidth + 10;
      p.y = innerHeight * rand(0.55, 0.8);
      p.vx = -side * rand(5, 15) * reach * Math.min(1.4, Math.max(0.8, innerWidth / 900));
      p.vy = -rand(7, 18) * reach;
    }
    return p;
  }

  // What each kind of celebration throws: lists of [kind, count, from, delay ms, life ms].
  function waves(kind, size) {
    if (kind === "level") {
      return [
        ["level", 46, "left", 0, 3500], ["level", 46, "right", 0, 3500],
        ["level", 30, "centre", 120, 3300], ["plain", 70, "centre", 120, 3300],
        ["level", 18, "left", 650, 2800], ["level", 18, "right", 650, 2800],
      ];
    }
    if (kind === "partial") return [["partial", 6 + Math.round(14 * size), "centre", 0, 1500]];
    if (kind === "plain") return [["plain", 40 + Math.round(60 * size), "centre", 0, 2000]];
    const list = [["validated", 28 + Math.round(52 * size), "centre", 0, 2200]];
    if (size >= 0.95) list.push(["validated", 36, "centre", 380, 2000], ["plain", 30, "centre", 380, 2000]);
    return list;
  }

  const scenes = new WeakMap();   // canvas -> { ctx, pieces, running }

  // Match the canvas to the window; only when it differs (setting the width clears it, so never per burst).
  function fit(scene) {
    const canvas = scene.canvas;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.round(innerWidth * dpr), h = Math.round(innerHeight * dpr);
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    scene.dpr = dpr;
  }

  function draw(scene, p, alpha) {
    const ctx = scene.ctx, dpr = scene.dpr;
    const cos = Math.cos(p.spin) * dpr, sin = Math.sin(p.spin) * dpr;
    ctx.setTransform(cos, sin, -sin, cos, p.x * dpr, p.y * dpr);
    ctx.globalAlpha = alpha;
    if (p.beetle) {
      const shape = silhouette(p.colour);
      if (shape) {
        const w = p.size * shape.width / shape.height;
        ctx.drawImage(shape, -w / 2, -p.size / 2, w, p.size);
      } else {
        drawBeetle(ctx, p.size, p.colour);
      }
    } else {
      const h = (p.size / 2) * Math.max(0.15, Math.abs(Math.cos(p.flip)));
      ctx.fillStyle = p.colour;
      ctx.fillRect(-p.size / 2, -h / 2, p.size, h);
      if (p.colour === "#ffffff") { ctx.strokeStyle = "#9ca3af"; ctx.lineWidth = 0.5; ctx.strokeRect(-p.size / 2, -h / 2, p.size, h); }
    }
  }

  // The one loop per canvas: moves and draws every live piece of every burst, and stops when none are left.
  function run(scene) {
    if (scene.running) return;
    scene.running = true;
    let last = performance.now();
    function frame(now) {
      const step = Math.min(3, Math.max(0, (now - last) / (1000 / 60)));   // frames at 60 a second; steady on 120 Hz
      last = now;
      fit(scene);
      const ctx = scene.ctx;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.globalAlpha = 1;
      ctx.clearRect(0, 0, scene.canvas.width, scene.canvas.height);
      const bottom = innerHeight + 60;
      scene.pieces = scene.pieces.filter((p) => now < p.end && p.y < bottom);
      scene.pieces.forEach((p) => {
        if (now < p.born) return;
        const drag = p.beetle ? 0.995 : 0.985;
        p.vx *= Math.pow(drag, step);
        p.vy = Math.min(p.beetle ? 7 : 3.5, p.vy + GRAVITY * step);   // they float down, paper slowest
        p.x += p.vx * step + (p.beetle ? 0 : Math.sin(p.flip) * 0.6 * step);
        p.y += p.vy * step;
        p.spin += p.vs * step;
        p.flip += 0.12 * step;
        draw(scene, p, Math.max(0, Math.min(1, (p.end - now) / p.fade)));
      });
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.globalAlpha = 1;
      if (scene.pieces.length) {
        requestAnimationFrame(frame);
      } else {
        ctx.clearRect(0, 0, scene.canvas.width, scene.canvas.height);
        scene.running = false;
      }
    }
    requestAnimationFrame(frame);
  }

  window.beetleConfetti = function (canvas, kind, size) {
    if (!canvas || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    kind = PALETTES[kind] ? kind : (kind === "beetles" ? "validated" : "plain");
    size = Math.max(0.2, Math.min(1, Number(size) || 1));
    let scene = scenes.get(canvas);
    if (!scene) {
      scene = { canvas, ctx: canvas.getContext("2d"), pieces: [], running: false, dpr: 1 };
      scenes.set(canvas, scene);
    }
    fit(scene);
    const now = performance.now();
    waves(kind, size).forEach(([k, count, from, delay, life]) => {
      for (let i = 0; i < count; i++) scene.pieces.push(piece(k, size, from, now + delay, life));
    });
    // Over the cap: the oldest pieces (the front of the list) fade out within a quarter second.
    const extra = scene.pieces.length - maxPieces();
    for (let i = 0; i < extra; i++) {
      const p = scene.pieces[i];
      p.end = Math.min(p.end, now + 250);
      p.fade = Math.min(p.fade, 250);
    }
    run(scene);
  };
})();
