/*
 * Confetti for Ambrosia Archive, made of the site's beetle logo (its silhouette, tinted). One call per celebration,
 * beetleConfetti(canvas, kind, size), the kind saying who agreed and so the colours (#572: green is correct, blue is
 * IBBI-AI, purple the players, grey a little; game_answer_review._celebrate picks it):
 *   "pop"               a few small grey beetles: very few points
 *   "partial"           grey beetles: a beetle named correctly to some rank
 *   "validated"         green beetles: a right species or a perfect grid on a checked beetle; at full size a second wave
 *   "validated_agreed"  green, blue and purple beetles: that, and IBBI-AI and the other players said the same
 *   "ai", "players", "ai_players"  grey with blue (IBBI-AI), purple (the players) or both: a beetle nobody checked yet
 *   "expert"            glowing purple beetles: a Naming expert among the players said the same
 *   "plain"             ordinary paper confetti
 *   "level"             the biggest: gold and brown beetles from both sides and a centre burst with paper; a fourth
 *                       argument, the new level's colour on the scale, adds a sprinkle of beetles in it
 * ``size`` (0.2 to 1, from the answer's points) scales how many pieces there are, how big and how far they fly.
 * Bursts run side by side in one shared animation loop, so a daily-goal burst never wipes out a level-up. The total
 * is capped (lower on phones): the oldest pieces fade out early to make room. Nothing moves under reduced motion.
 * The logo URL comes from window.BEETLE_LOGO_URL; until it has loaded, a drawn beetle stands in.
 */
(function () {
  const GREY = ["#6b7280", "#9ca3af", "#4b5563"];
  const GREEN = ["#15803d", "#16a34a", "#22c55e"];
  const BLUE = ["#1d4ed8", "#2563eb", "#3b82f6"];
  const PURPLE = ["#7e22ce", "#9333ea", "#a855f7"];
  const PALETTES = {
    pop: GREY,
    partial: GREY,
    validated: GREEN,
    validated_agreed: [...GREEN, ...GREEN, ...BLUE, ...PURPLE],
    ai: [...GREY, ...BLUE, ...BLUE],
    players: [...GREY, ...PURPLE, ...PURPLE],
    ai_players: [...GREY, ...BLUE, ...PURPLE],
    expert: PURPLE,
    level: ["#ca8a04", "#eab308", "#facc15", "#a16207", "#3f2a14", "#5b3a1e", "#7c4a1e", "#8b5a2b"],
    plain: ["#111827", "#374151", "#9ca3af", "#d1d5db", "#ffffff", "#16a34a"],
  };
  // How each kind's pieces look: [base size, how much it varies]; paper is the only kind that isn't beetles
  const LOOK = { pop: [10, 5], partial: [13, 7], level: [26, 16], plain: [6, 6], accent: [22, 12] };
  const BEETLE_LOOK = [18, 12];
  const GLOWS = { expert: "rgba(168, 85, 247, 0.9)" };   // these pieces shine
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
        Object.keys(PALETTES).filter((kind) => kind !== "plain").forEach((kind) => PALETTES[kind].forEach(silhouette));
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
  function piece(kind, size, from, born, life, palette) {
    const beetle = kind !== "plain";
    palette = palette || PALETTES[kind];
    const [base, vary] = LOOK[kind] || BEETLE_LOOK;
    const reach = Math.sqrt(Math.max(0.6, Math.min(1.6, innerHeight / 800)));
    const p = {
      beetle,
      size: (base + Math.random() * vary) * (0.65 + 0.35 * size),
      spin: Math.random() * Math.PI * 2,
      vs: rand(-0.5, 0.5) * (beetle ? 0.2 : 0.4),
      flip: Math.random() * Math.PI * 2,   // paper turns over as it falls
      colour: palette[(Math.random() * palette.length) | 0],
      glow: GLOWS[kind] || null,
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

  // What each kind of celebration throws: lists of [kind, count, from, delay ms, life ms]. ``accent``: the level's
  // colour, a sprinkle of beetles in it.
  function waves(kind, size, accent) {
    if (kind === "level") {
      const list = [
        ["level", 46, "left", 0, 3500], ["level", 46, "right", 0, 3500],
        ["level", 30, "centre", 120, 3300], ["plain", 70, "centre", 120, 3300],
        ["level", 18, "left", 650, 2800], ["level", 18, "right", 650, 2800],
      ];
      if (accent) list.push(["accent", 24, "centre", 300, 3200]);
      return list;
    }
    if (kind === "pop") return [["pop", 5 + Math.round(6 * size), "centre", 0, 1200]];
    if (kind === "partial") return [["partial", 6 + Math.round(14 * size), "centre", 0, 1500]];
    if (kind === "plain") return [["plain", 40 + Math.round(60 * size), "centre", 0, 2000]];
    if (kind === "ai" || kind === "players" || kind === "ai_players") return [[kind, 16 + Math.round(40 * size), "centre", 0, 2000]];
    if (kind === "expert") return [["expert", 14 + Math.round(36 * size), "centre", 0, 2400]];
    const list = [[kind, 28 + Math.round(52 * size), "centre", 0, 2200]];   // validated, validated_agreed
    if (size >= 0.95) list.push([kind, 36, "centre", 380, 2000], ["plain", 30, "centre", 380, 2000]);
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
    ctx.shadowBlur = p.glow ? 10 * dpr : 0;
    ctx.shadowColor = p.glow || "transparent";
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

  window.beetleConfetti = function (canvas, kind, size, accent) {
    if (!canvas || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    kind = PALETTES[kind] ? kind : (kind === "beetles" ? "validated" : "plain");
    accent = /^#[0-9a-f]{6}$/i.test(accent || "") ? accent : null;
    size = Math.max(0.2, Math.min(1, Number(size) || 1));
    let scene = scenes.get(canvas);
    if (!scene) {
      scene = { canvas, ctx: canvas.getContext("2d"), pieces: [], running: false, dpr: 1 };
      scenes.set(canvas, scene);
    }
    fit(scene);
    const now = performance.now();
    waves(kind, size, accent).forEach(([k, count, from, delay, life]) => {
      for (let i = 0; i < count; i++) scene.pieces.push(piece(k, size, from, now + delay, life, k === "accent" ? [accent] : null));
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
