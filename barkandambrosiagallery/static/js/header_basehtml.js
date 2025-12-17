// load sidenav open or closed from on local storage
window.addEventListener('DOMContentLoaded', function () {
    const sidenav = document.getElementById('sidenav')
    const sidenavState = localStorage.getItem('sidenavOpen')
    const spans = document.querySelectorAll('aside div span')
    if (sidenavState === 'open') {
        sidenav.classList.add('w-48')
        spans.forEach((span) => {
            span.classList.remove('hidden')
            span.classList.remove('opacity-0')
            span.classList.add('opacity-100')
            span.classList.remove('delay-200')
        })
    } else {
        sidenav.classList.add('w-20')
    }
})

// load selected theme from local storage
function setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('color-theme', theme)
}
// It's best to inline this in `head` to avoid FOUC (flash of unstyled content) when changing pages or themes
// if (localStorage.getItem('color-theme') === 'dark' || (!localStorage.getItem('color-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
//    setTheme('dark')
//} else {
//    setTheme('light')
//}


// Update sidenav and mobile menu icons based on current page
function updateIconsBasedOnPage() {
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
window.addEventListener('DOMContentLoaded', updateIconsBasedOnPage)

