// A reusable drag-and-drop file picker (site-upload, #618), styled like IBBI-AI's upload box: see
// templates/beetles/includes/drop_zone.html. Clicking or dragging a file onto any "[data-drop-zone]" fills the
// file input named by its data-for id and fires a "change" event on it, so a page's own "a file was picked" code
// runs the same way it would for an ordinary <input type="file">.
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('[data-drop-zone]').forEach(function (zone) {
    var input = document.getElementById(zone.getAttribute('data-for'));
    if (!input) return;

    function openPicker() { input.click(); }
    zone.addEventListener('click', openPicker);
    zone.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openPicker(); }
    });

    ['dragenter', 'dragover'].forEach(function (name) {
      zone.addEventListener(name, function (e) {
        e.preventDefault();
        zone.classList.add('border-gray-500', 'bg-gray-100');
      });
    });
    ['dragleave', 'drop'].forEach(function (name) {
      zone.addEventListener(name, function (e) {
        e.preventDefault();
        zone.classList.remove('border-gray-500', 'bg-gray-100');
      });
    });
    zone.addEventListener('drop', function (e) {
      var files = e.dataTransfer && e.dataTransfer.files;
      if (!files || !files.length) return;
      input.files = files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
  });
});
