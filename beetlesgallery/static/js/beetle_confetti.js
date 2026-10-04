/*
 * Confetti for the Beetle ID game (issue #425).
 *   beetleConfetti(canvas, "beetles")  little bark beetles: a validated beetle named right
 *   beetleConfetti(canvas, "plain")    ordinary confetti: a strong answer on a beetle nobody has checked yet
 * The beetles are drawn (body, head, legs) so they cost nothing to load; images can replace them later through
 * BEETLE_SPRITES (a list of image URLs), which are used instead when they are set and loaded.
 */
(function () {
  const BROWNS = ["#3f2a14", "#5b3a1e", "#7c4a1e", "#8b5a2b", "#a0522d"];
  const PLAIN = ["#111827", "#374151", "#9ca3af", "#d1d5db", "#ffffff", "#16a34a"];
  const sprites = (window.BEETLE_SPRITES || []).map((url) => { const img = new Image(); img.src = url; return img; });

  function drawBeetle(ctx, size, colour) {
    // legs
    ctx.strokeStyle = colour;
    ctx.lineWidth = Math.max(1, size * 0.06);
    for (const side of [-1, 1]) {
      for (const y of [-0.15, 0.1, 0.35]) {
        ctx.beginPath();
        ctx.moveTo(side * size * 0.18, y * size);
        ctx.lineTo(side * size * 0.42, (y + 0.12) * size);
        ctx.stroke();
      }
    }
    ctx.fillStyle = colour;
    // elytra (the long body), pronotum, head
    ctx.beginPath(); ctx.ellipse(0, size * 0.18, size * 0.22, size * 0.36, 0, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.ellipse(0, -size * 0.25, size * 0.18, size * 0.16, 0, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.ellipse(0, -size * 0.43, size * 0.1, size * 0.07, 0, 0, Math.PI * 2); ctx.fill();
    // the line between the wing covers
    ctx.strokeStyle = "rgba(255,255,255,.25)";
    ctx.lineWidth = Math.max(0.5, size * 0.03);
    ctx.beginPath(); ctx.moveTo(0, -size * 0.1); ctx.lineTo(0, size * 0.52); ctx.stroke();
  }

  window.beetleConfetti = function (canvas, kind) {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const beetles = kind === "beetles";
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    canvas.width = innerWidth * dpr;
    canvas.height = innerHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const ready = sprites.filter((img) => img.complete && img.naturalWidth);
    const pieces = Array.from({ length: beetles ? 36 : 70 }, () => ({
      x: innerWidth / 2 + (Math.random() - 0.5) * 80,
      y: innerHeight * 0.55,
      vx: (Math.random() - 0.5) * 9,
      vy: -Math.random() * 11 - 4,
      size: beetles ? 12 + Math.random() * 10 : 5 + Math.random() * 6,
      spin: Math.random() * Math.PI * 2,
      vs: (Math.random() - 0.5) * (beetles ? 0.2 : 0.4),
      colour: (beetles ? BROWNS : PLAIN)[(Math.random() * (beetles ? BROWNS : PLAIN).length) | 0],
      sprite: beetles && ready.length ? ready[(Math.random() * ready.length) | 0] : null,
    }));
    const start = performance.now();
    (function frame(now) {
      const t = now - start;
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      pieces.forEach((p) => {
        p.vy += 0.35; p.x += p.vx; p.y += p.vy; p.spin += p.vs;
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.spin);
        ctx.globalAlpha = Math.max(0, 1 - t / 1800);
        if (p.sprite) {
          ctx.drawImage(p.sprite, -p.size / 2, -p.size / 2, p.size, p.size);
        } else if (beetles) {
          drawBeetle(ctx, p.size, p.colour);
        } else {
          ctx.fillStyle = p.colour;
          ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
          if (p.colour === "#ffffff") { ctx.strokeStyle = "#9ca3af"; ctx.lineWidth = 0.5; ctx.strokeRect(-p.size / 2, -p.size / 4, p.size, p.size / 2); }
        }
        ctx.restore();
      });
      if (t < 1800) requestAnimationFrame(frame); else ctx.clearRect(0, 0, innerWidth, innerHeight);
    })(start);
  };
})();
