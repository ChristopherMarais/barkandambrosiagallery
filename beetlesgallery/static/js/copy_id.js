// A long id shown in passing (site-uuid, #618): any ".copy-id" button with a data-copy-id attribute copies that
// value to the clipboard and shows a checkmark for 1.5s. Used by templates/beetles/includes/short_id.html.
// (Unrelated to any page's own ".copy-uuid" buttons, such as data_management.html's, which keep their own JS.)
document.addEventListener('click', async function (e) {
  var btn = e.target.closest('.copy-id');
  if (!btn) return;
  var value = btn.getAttribute('data-copy-id');
  if (!value) return;
  e.preventDefault();
  try {
    await navigator.clipboard.writeText(value);
  } catch (err) {
    return;
  }
  var icon = btn.querySelector('[data-copy-id-icon]');
  if (!icon) return;
  var original = icon.className;
  icon.className = original.replace(/fi-rr-[a-z0-9-]+/, 'fi-rr-check');
  setTimeout(function () { icon.className = original; }, 1500);
});
