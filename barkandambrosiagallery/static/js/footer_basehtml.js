// open and close desktop side navigation
function openSideNav() {
    const sidenav = document.getElementById('sidenav')
    sidenav.classList.toggle('w-20')
    sidenav.classList.toggle('w-48')

    const spans = document.querySelectorAll('aside div span')
    spans.forEach((span) => {
        span.classList.toggle('hidden')
        setTimeout(function () {
            span.classList.toggle('opacity-0')
            span.classList.toggle('opacity-100')
            span.classList.toggle('delay-200')
        }, 75)
    })
    localStorage.setItem('sidenavOpen', sidenav.classList.contains('w-48') ? 'open' : 'closed')
}

// open and close mobile menu
function menuToggle() {
    const mobileMenu = document.getElementById('mobile-menu')

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
    const menu = document.getElementById('mobileMenuWrap')
    const target = event.target
    const clickOutsideMenu = menu.contains(target)

    if (mobileMenu.getAttribute('data-open') == 'true' && !clickOutsideMenu) {
        menuToggle()
    }
})

// sticky navbar
function navbarToggle(clickedButton) {
    document.getElementById('stickyitems').classList.toggle('sticky')
    document.getElementById('stickyitems').classList.toggle('top-16')
    document.getElementById('sidenavWrap').classList.toggle('top-0')
    document.getElementById('sidenavWrap').classList.toggle('top-16')
    document.getElementById('navWrap').classList.toggle('sticky')
    document.getElementById('navbar').classList.toggle('shadow-md')

    let stickyToggleButtons = document.querySelectorAll('input[onclick="navbarToggle(this)"]');
    const isChecked = clickedButton.checked;

    stickyToggleButtons.forEach(function (button) {
        button.checked = isChecked;
    });

    localStorage.setItem('buttonState', isChecked);
}
document.addEventListener('DOMContentLoaded', function () {
    const stickyToggleButtonState = localStorage.getItem('buttonState');
    const stickyToggleButtons = document.querySelectorAll('input[onclick="navbarToggle(this)"]');

    if (stickyToggleButtonState === 'true') {
        stickyToggleButtons.forEach(function (button) {
            button.checked = true;
        });
        document.getElementById('stickyitems').classList.toggle('sticky')
        document.getElementById('stickyitems').classList.toggle('top-16')
        document.getElementById('sidenavWrap').classList.toggle('top-0')
        document.getElementById('sidenavWrap').classList.toggle('top-16')
        document.getElementById('navWrap').classList.toggle('sticky')
        document.getElementById('navbar').classList.toggle('shadow-md')
    }
});



// change theme between light and dark mode
function setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('color-theme', theme)
}

// Change the icons inside the button based on previous settings
var themeToggleDarkIcon = document.getElementById('theme-toggle-dark-icon')
var themeToggleLightIcon = document.getElementById('theme-toggle-light-icon')
if (localStorage.getItem('color-theme') === 'dark' || (!localStorage.getItem('color-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
    themeToggleLightIcon.classList.remove('hidden')
} else {
    themeToggleDarkIcon.classList.remove('hidden')
}

var themeToggleBtn = document.getElementById('theme-toggle')
themeToggleBtn.addEventListener('click', function () {
    // toggle icons inside button
    document.getElementById('theme-toggle-dark-icon').classList.toggle('hidden')
    document.getElementById('theme-toggle-light-icon').classList.toggle('hidden')

    // if set via local storage previously
    if (localStorage.getItem('color-theme')) {
        if (localStorage.getItem('color-theme') === 'light') {
            setTheme('dark')
        } else {
            setTheme('light')
        }
    } else {
        // if NOT set via local storage previously
        if (document.documentElement.getAttribute('data-theme') === 'light') {
            setTheme('dark')
        } else {
            setTheme('light')
        }
    }
})

// open and close sections
document.addEventListener('DOMContentLoaded', function () {
    const links = document.querySelectorAll('.section-link');
    const sections = document.querySelectorAll('.section');

    // Função para ocultar todas as seções
    function hideAllSections() {
        sections.forEach(section => {
            section.classList.add('hidden');
        });
    }

    // Mostrar a primeira seção ao carregar a página
    hideAllSections();
    document.getElementById('mission-section').classList.remove('hidden');

    // Adicionar o evento de clique nos links da navegação
    links.forEach(link => {
        link.addEventListener('click', function () {
            const targetId = this.getAttribute('data-target');
            // Esconder todas as seções
            hideAllSections();
            // Mostrar a seção clicada
            document.getElementById(targetId).classList.toggle('hidden');
        });
    });
});


// load launch status color 
document.addEventListener('DOMContentLoaded', function () {
    const statusLinks = document.querySelectorAll('.launch-status');

    statusLinks.forEach(status => {
        const launchStatusColor = status.getAttribute('data-status-color') || 'rgb(75 85 99)';

        // Aplicar a cor de fundo ao elemento
        status.style.backgroundColor = launchStatusColor;
    });
});