// A "Jump to" <select> for very long pages (site-long, #618): picking an option scrolls smoothly to the section
// with that id, matching option value to element id. Pair with a sticky table of contents on desktop (.toc-sticky
// in input.css). Usage: <select data-jump-to><option value="scoring-levels">Levels</option>...</select> next to
// <section id="scoring-levels">...
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('select[data-jump-to]').forEach(function (select) {
    select.addEventListener('change', function () {
      var target = document.getElementById(select.value);
      if (!target) return;
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      if (typeof target.focus === 'function') target.focus({ preventScroll: true });
    });
  });
});
