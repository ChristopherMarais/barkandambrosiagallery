// The one upload component (site-upload, #618), styled like IBBI-AI's upload box: see
// templates/beetles/includes/drop_zone.html. Clicking or dragging a file onto any "[data-drop-zone]" fills the
// file input named by its data-for id and fires a "change" event on it, so a page's own "a file was picked" code
// runs the same way it would for an ordinary <input type="file">. The chosen file's name shows inside the box, and
// the button named by data-submit (if any) is enabled only while a file is chosen. A form reset clears the name.
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('[data-drop-zone]').forEach(function (zone) {
    var input = document.getElementById(zone.getAttribute('data-for'));
    if (!input) return;
    var nameEl = zone.querySelector('[data-drop-zone-filename]');
    var submitId = zone.getAttribute('data-submit');
    var submitBtn = submitId ? document.getElementById(submitId) : null;

    function sync() {
      var files = Array.from(input.files || []);
      if (nameEl) {
        nameEl.textContent = files.map(function (f) { return f.name; }).join(', ');
        nameEl.classList.toggle('hidden', !files.length);
      }
      if (submitBtn) submitBtn.disabled = !files.length;
    }

    zone.addEventListener('click', function (e) {
      if (e.target === input) return;   // the input's own click bubbling up
      input.click();
    });
    zone.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
    });
    input.addEventListener('change', sync);
    if (input.form) input.form.addEventListener('reset', function () { setTimeout(sync, 0); });

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

    sync();
  });
});
