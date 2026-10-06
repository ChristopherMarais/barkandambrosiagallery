// Small image first, then the full one (#607). An <img data-full-src="..."> starts with its thumbnail, sized by the
// page to the photo's own shape so nothing moves; the full photo loads in the background and replaces it once it is
// decoded, so on a slow connection the photo sharpens instead of staying blank. window.progressiveImages(root) does
// the same for images added later.
(function () {
  // Only a web address on http(s) is ever loaded (never javascript: or data:), resolved against the page
  function safeUrl(value) {
    try {
      const u = new URL(value, window.location.href);
      return u.protocol === "https:" || u.protocol === "http:" ? u.href : null;
    } catch (e) {
      return null;
    }
  }
  function upgrade(img) {
    const url = safeUrl(img.getAttribute("data-full-src") || "");
    img.removeAttribute("data-full-src");
    if (!url) return;
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
