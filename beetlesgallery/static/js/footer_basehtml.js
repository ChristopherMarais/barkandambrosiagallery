// open and close mobile menu
function menuToggle() {
    const mobileMenu = document.getElementById('mobile-menu')

    // Note: The element #mobile-menu is currently missing from base.html
    if (!mobileMenu) return;

    if (mobileMenu.getAttribute('data-open') == 'false') {
        mobileMenu.classList.remove('hidden')
        setTimeout(function () {
            mobileMenu.classList.toggle('opacity-0')
            mobileMenu.classList.toggle('translate-y-10')
            mobileMenu.classList.toggle('translate-y-0')
        }, 75)
        mobileMenu.setAttribute('data-open', 'true')
    } else {
        mobileMenu.classList.toggle('opacity-0')
        mobileMenu.classList.toggle('translate-y-10')
        mobileMenu.classList.toggle('translate-y-0')
        setTimeout(function () {
            mobileMenu.classList.add('hidden')
        }, 200)
        mobileMenu.setAttribute('data-open', 'false')
    }
}

// close mobile menu when clicking outside
document.addEventListener('click', function (event) {
    const mobileMenu = document.getElementById('mobile-menu')
    const menu = document.getElementById('mobileMenuWrap') // This ID also seems missing in base.html

    if (!mobileMenu || !menu) return;

    const target = event.target
    const clickOutsideMenu = menu.contains(target)

    if (mobileMenu.getAttribute('data-open') == 'true' && !clickOutsideMenu) {
        menuToggle()
    }
})

// open and close sections (used for internal page navigation if elements exist)
document.addEventListener('DOMContentLoaded', function () {
    const links = document.querySelectorAll('.section-link');
    const sections = document.querySelectorAll('.section');

    function hideAllSections() {
        sections.forEach(section => {
            section.classList.add('hidden');
        });
    }

    if (sections.length > 0) {
        hideAllSections();
        // Show the first section if mission-section exists, otherwise whatever is first
        const mission = document.getElementById('mission-section');
        if (mission) mission.classList.remove('hidden');
    }

    links.forEach(link => {
        link.addEventListener('click', function () {
            const targetId = this.getAttribute('data-target');
            hideAllSections();
            const target = document.getElementById(targetId);
            if (target) target.classList.toggle('hidden');
        });
    });
});

// load launch status color 
document.addEventListener('DOMContentLoaded', function () {
    const statusLinks = document.querySelectorAll('.launch-status');

    statusLinks.forEach(status => {
        const launchStatusColor = status.getAttribute('data-status-color') || 'rgb(75 85 99)';
        status.style.backgroundColor = launchStatusColor;
    });
});