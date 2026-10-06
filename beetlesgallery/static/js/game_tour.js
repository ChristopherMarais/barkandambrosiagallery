// Help for new players on the game's play page (templates/beetles/includes/game_tour.html):
//  * a walkthrough of every button, the first time they play (or with ?tour=1);
//  * for their first few days, when a session starts, how to report a photo that looks wrong.
// Both wait for the first beetle ("game:item-shown"), and pause the game's keyboard shortcuts while open
// (data-overlay-open). The game's Esc and back button close them ("game:close-overlays").
(function () {
  const tour = document.getElementById("tour");
  const tip = document.getElementById("report-tip");
  if (!tour || !tip) return;
  const $ = (id) => document.getElementById(id);
  const game = document.getElementById("game");
  const mode = () => (game && game.dataset.mode) || "classify";

  const STEPS = [
    { el: "photos", text: "This is the beetle. Tap it to see the whole photo." },
    { el: "light-btn", text: "Photo too dark? Lighting makes it brighter or sharper." },
    { el: "ranks", text: "Name it as far as you're sure: subfamily, tribe, genus, species. Stop where you're unsure." },
    { el: "ladder", text: "Are A and B related? Pick the lowest line you're sure of." },
    { el: "rank-lock", text: "Start with the big groups. Tribe, genus and species open one by one after a few beetles." },
    { el: "find-btn-genus", text: "Long list? Tap the magnifier, or just start typing, to search it." },
    { el: "skip", text: "Not sure? Skip it. It costs very little." },
    { el: "back", text: "Back shows your last beetle and what you answered." },
    { el: "submit", text: "Next saves your answer and brings the next beetle. Naming a beetle earns the most points, but a sure tribe beats a wrong genus." },
    { el: "chip", text: "Beetles today against your daily goal. The flame is your streak: days in a row you reached the goal." },
    { el: "level-chip", text: "Your level and points. New levels unlock more of the game." },
    { el: "open-help", text: "How to play: the rules in short, any time." },
    { el: "toolbar", text: "Pick the game and a focus here once you've unlocked them." },
    { el: "report-chip-0", text: "Bad photo (too little of the beetle, blurry, not a beetle, a box that misses it)? Tap Report here, at the top right of the photo. You lose no points. A clear photo of a good part of the beetle from an unusual side isn't bad: name it as far as you can." },
    { el: "exit", text: "No timer: take your time, use keys or references, and leave any time. You'll see how the session went." },
  ];

  function visible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
  }

  let steps = [];
  let at = 0;
  function place() {
    const step = steps[at];
    const el = $(step.el);
    const spot = $("tour-spot");
    const bubble = $("tour-bubble");
    $("tour-text").textContent = typeof step.text === "function" ? step.text() : step.text;
    $("tour-count").textContent = (at + 1) + " / " + steps.length;
    $("tour-back").disabled = at === 0;
    $("tour-next").textContent = at === steps.length - 1 ? "Let's play" : "Next";
    const r = el.getBoundingClientRect();
    const pad = 6;
    Object.assign(spot.style, { left: r.left - pad + "px", top: r.top - pad + "px", width: r.width + 2 * pad + "px", height: r.height + 2 * pad + "px" });
    // the bubble goes where there is more room: below the element or above it
    const h = bubble.offsetHeight || 120;
    const below = r.bottom + pad + 12;
    const top = innerHeight - below >= h + 12 ? below : Math.max(12, r.top - pad - 12 - h);
    bubble.style.top = Math.min(top, innerHeight - h - 12) + "px";
    $("tour-next").focus({ preventScroll: true });
  }
  function openTour(custom) {
    steps = (custom || STEPS).filter((s) => visible($(s.el)));
    if (!steps.length) return;
    at = 0;
    tour.classList.remove("hidden");
    tour.setAttribute("data-overlay-open", "");
    place();
  }
  function closeTour() {
    tour.classList.add("hidden");
    tour.removeAttribute("data-overlay-open");
  }
  $("tour-next").addEventListener("click", () => { if (at < steps.length - 1) { at += 1; place(); } else closeTour(); });
  $("tour-back").addEventListener("click", () => { if (at > 0) { at -= 1; place(); } });
  $("tour-skip").addEventListener("click", closeTour);
  addEventListener("resize", () => { if (!tour.classList.contains("hidden")) place(); });

  // ---- the report tip: first few days, at most a few times on this device ----
  const TIP_KEY = "game-report-tip-shown";
  function tipCount() { try { return Number(localStorage.getItem(TIP_KEY)) || 0; } catch (e) { return 0; } }
  function openTip() {
    tip.classList.remove("hidden");
    tip.setAttribute("data-overlay-open", "");
    try { localStorage.setItem(TIP_KEY, String(tipCount() + 1)); } catch (e) { /* private mode */ }
    $("report-tip-ok").focus({ preventScroll: true });
  }
  function closeTip() {
    tip.classList.add("hidden");
    tip.removeAttribute("data-overlay-open");
  }
  $("report-tip-ok").addEventListener("click", closeTip);
  $("report-tip-show").addEventListener("click", () => {
    closeTip();
    // point at the Report button on the photo itself
    const chip = $("report-chip-0");
    if (!chip) return;
    chip.classList.add("tour-pulse");
    setTimeout(() => chip.classList.remove("tour-pulse"), 5000);
  });

  // Both close with the game's Esc and back button; their own keys don't reach the game
  document.addEventListener("game:close-overlays", () => { closeTour(); closeTip(); });
  addEventListener("keydown", (e) => {
    const open = !tour.classList.contains("hidden") ? "tour" : !tip.classList.contains("hidden") ? "tip" : "";
    if (!open) return;
    if (e.key === "Escape") { open === "tour" ? closeTour() : closeTip(); }
    else if (open === "tour" && (e.key === "Enter" || e.key === "ArrowRight")) $("tour-next").click();
    else if (open === "tour" && e.key === "ArrowLeft") $("tour-back").click();
    else if (open === "tip" && e.key === "Enter") closeTip();
    else return;
    e.preventDefault();
    e.stopImmediatePropagation();
  }, true);

  let started = false;
  document.addEventListener("game:item-shown", () => {
    if (started) return;
    started = true;
    // give the photos a moment to lay out
    setTimeout(() => {
      if (tour.dataset.first === "1") openTour();
      else if (tour.dataset.reportTip === "1" && tipCount() < 3) openTip();
    }, 350);
  });
  // A feature that just unlocked (a new level): point it out once, on this device. [{key, el, text}]
  function unlocked(list) {
    const fresh = list.filter((s) => {
      try {
        if (localStorage.getItem("game-unlock-tip-" + s.key)) return false;
        localStorage.setItem("game-unlock-tip-" + s.key, "1");
      } catch (e) { /* private window: show it anyway */ }
      return true;
    });
    if (fresh.length) setTimeout(() => openTour(fresh), 600);   // after the level-up toast and confetti
  }
  window.gameTour = { open: () => openTour(), unlocked };
})();
