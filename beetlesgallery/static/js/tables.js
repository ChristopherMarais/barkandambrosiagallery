// Wide tables on a phone (site-tables, #618; the CSS is in input.css).
//  - table.table-cards: each cell without a data-label gets its column's name from the <th> above it, so the
//    card layout below 640px can show it. A <th data-label="..."> overrides the text; only the header's first line
//    is used, so a sub-line under a column name doesn't end up in every cell. Rows a script adds later can be
//    labelled with window.labelTableCards(table).
//  - .table-scroll: marked .is-cut while part of the table is still out of sight to the right, which fades that edge.
(function () {
  function headerLabel(th) {
    if (th.dataset.label !== undefined) return th.dataset.label;
    for (const node of th.childNodes) {
      const text = (node.nodeType === Node.TEXT_NODE ? node.textContent
        : node.classList && node.classList.contains('sr-only') ? '' : node.textContent).trim();
      if (text) return text;
    }
    return '';
  }

  function labelTableCards(table) {
    const head = table.tHead && table.tHead.rows[table.tHead.rows.length - 1];
    if (!head) return;
    const labels = [];
    Array.from(head.cells).forEach((th) => {
      for (let i = 0; i < (th.colSpan || 1); i++) labels.push(headerLabel(th));
    });
    Array.from(table.tBodies).forEach((body) => {
      Array.from(body.rows).forEach((row) => {
        let col = 0;
        Array.from(row.cells).forEach((cell) => {
          if (cell.colSpan === 1 && !cell.hasAttribute('data-label') && labels[col]) {
            cell.setAttribute('data-label', labels[col]);
          }
          col += cell.colSpan || 1;
        });
      });
    });
  }
  window.labelTableCards = labelTableCards;

  const updates = [];
  function watchScroll(box) {
    const update = () => box.classList.toggle('is-cut', box.scrollLeft + box.clientWidth < box.scrollWidth - 1);
    box.addEventListener('scroll', update, { passive: true });
    updates.push(update);
    update();
  }
  const updateAll = () => updates.forEach((update) => update());

  function init() {
    document.querySelectorAll('table.table-cards').forEach(labelTableCards);
    document.querySelectorAll('.table-scroll').forEach(watchScroll);
    window.addEventListener('resize', updateAll);
    // a table inside a closed <details> has no width until it is opened ("toggle" doesn't bubble: capture it)
    document.addEventListener('toggle', updateAll, true);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
