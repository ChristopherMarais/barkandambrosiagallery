// A "Jump to" <select> for very long pages (site-long, #618): picking an option scrolls smoothly to the section
// with that id, matching option value to element id. Pair with a sticky table of contents on desktop (.toc-sticky
// in input.css). Usage: <select data-jump-to><option value="scoring-levels">Levels</option>...</select> next to
// <section id="scoring-levels">...
// Long pages fold their sections into <details>: jumping to a section (from the select, a contents link, any
// "#id" link or a URL that ends in one) opens the <details> it sits in, and the one it starts with.
(function () {
  function reveal(target) {
    for (let el = target; el; el = el.parentElement) {
      if (el.tagName === 'DETAILS') el.open = true;
    }
    const own = target.tagName === 'DETAILS' ? target : target.querySelector(':scope > details');
    if (own) own.open = true;
  }

  function jump(id, smooth) {
    const target = id && document.getElementById(id);
    if (!target) return;
    reveal(target);
    target.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' });
    if (typeof target.focus === 'function') target.focus({ preventScroll: true });
  }
  window.jumpTo = jump;

  function hashId() {
    try { return decodeURIComponent(location.hash.slice(1)); } catch (_) { return ''; }
  }

  function init() {
    document.querySelectorAll('select[data-jump-to]').forEach(function (select) {
      select.addEventListener('change', function () {
        jump(select.value, true);
      });
    });
    // A plain "#id" link: open the folded section before the browser scrolls to it.
    document.addEventListener('click', function (e) {
      const link = e.target.closest && e.target.closest('a[href^="#"]');
      if (!link || link.getAttribute('href').length < 2) return;
      let target = null;
      try { target = document.getElementById(decodeURIComponent(link.getAttribute('href').slice(1))); } catch (_) {}
      if (target) reveal(target);
    }, true);
    window.addEventListener('hashchange', function () { jump(hashId(), true); });
    if (location.hash.length > 1) {
      const target = document.getElementById(hashId());
      if (target && target.closest('details:not([open])')) jump(hashId(), false);
      else if (target && target.querySelector(':scope > details:not([open])')) reveal(target);
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
