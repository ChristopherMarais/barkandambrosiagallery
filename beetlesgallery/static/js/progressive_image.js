// Small image first, then the full one (#607). An <img data-full-src="..."> starts with its thumbnail, sized by the
// page to the photo's own shape so nothing moves; the full photo loads in the background and replaces it once it is
// decoded, so on a slow connection the photo sharpens instead of staying blank. window.progressiveImages(root) does
// the same for images added later.
(function () {
  function upgrade(img) {
    const url = img.getAttribute("data-full-src");
    if (!url) return;
    img.removeAttribute("data-full-src");
    const full = new Image();
    full.src = url;
    const swap = () => { img.src = url; img.removeAttribute("data-thumb"); };
    (full.decode ? full.decode() : Promise.reject()).then(swap, () => { full.onload = swap; if (full.complete && full.naturalWidth) swap(); });
  }
  function run(root) {
    (root || document).querySelectorAll("img[data-full-src]").forEach(upgrade);
  }
  window.progressiveImages = run;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => run());
  else run();
})();
