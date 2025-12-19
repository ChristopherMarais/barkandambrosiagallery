// Update sidenav and mobile menu icons based on current page
function updateIconsBasedOnPage() {
  // Selects links from the sideItems container we added ID to
  const links = document.querySelectorAll('#sideItems a, #mobile-menu a');
  const windowPath = window.location.pathname;
  const norm = (s) => (s || '').replace(/\/+$/, '') || '/';

  links.forEach((link) => {
    const span = link.querySelector('span');
    const linkUrl = link.getAttribute('data-url') || link.getAttribute('href');
    const isActive = norm(windowPath) === norm(linkUrl);

    if (isActive) {
      link.setAttribute('aria-current', 'page');
      if (span) span.classList.add('font-semibold');
    } else {
      link.removeAttribute('aria-current');
      if (span) span.classList.remove('font-semibold');
    }
  });
}
window.addEventListener('DOMContentLoaded', updateIconsBasedOnPage);