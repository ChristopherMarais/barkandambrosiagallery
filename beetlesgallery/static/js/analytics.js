// Google Analytics (GA4) and the choice about it. Nothing non-essential runs before the visitor chooses:
// the analytics script is downloaded only after Accept (Google Consent Mode v2: storage denied until then).
// Reject, or changing the choice from "Cookie settings", withdraws consent as easily as giving it and clears the
// Google Analytics cookies. The choice itself is stored in one essential cookie for a year, and only once chosen.
// base.html renders this script (with data-ga-id and data-ga-page) only when GA_MEASUREMENT_ID is set.
// See docs/analytics.md.
(function () {
    "use strict";

    var tag = document.querySelector("script[data-ga-id]");
    if (!tag) return;

    var id = tag.getAttribute("data-ga-id");
    var page = tag.getAttribute("data-ga-page") || "/";   // the URL pattern, e.g. /game/rounds/:id/ (no real IDs)
    var COOKIE = "ga_consent";
    var YEAR = 365 * 24 * 60 * 60;

    // The only events, and the only fields each may carry. Never a name, an account, an ID or an address.
    var EVENTS = {
        game_start: ["game"],          // the game's short key, e.g. "odd"
        game_round_done: ["answers"],  // how many answers the round had
        sign_up_started: [],
        sign_up_done: []
    };

    window.dataLayer = window.dataLayer || [];
    function gtag() { window.dataLayer.push(arguments); }

    // Consent Mode v2: nothing is stored or sent until the visitor says yes.
    gtag("consent", "default", {
        analytics_storage: "denied",
        ad_storage: "denied",
        ad_user_data: "denied",
        ad_personalization: "denied"
    });

    var loaded = false;   // gtag.js has been requested

    function allow() {
        gtag("consent", "update", { analytics_storage: "granted" });
        if (loaded) return;
        loaded = true;

        var referrer = "";
        try { referrer = document.referrer ? new URL(document.referrer).origin : ""; } catch (e) { referrer = ""; }

        // Only the page pattern and the site's origin are reported: no query string, no ID in the path.
        var pageLocation = location.origin + page;
        gtag("set", { page_location: pageLocation, page_title: page });
        gtag("js", new Date());
        gtag("config", id, {
            page_location: pageLocation,
            page_title: page,
            page_referrer: referrer,
            allow_google_signals: false,
            allow_ad_personalization_signals: false
        });

        var s = document.createElement("script");
        s.async = true;
        s.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(id);
        document.head.appendChild(s);
    }

    // Withdrawal: stop storage, and remove the Google Analytics cookies that are already in the browser.
    function withdraw() {
        if (loaded) gtag("consent", "update", { analytics_storage: "denied" });
        try {
            document.cookie.split(";").forEach(function (part) {
                var name = part.split("=")[0].trim();
                if (/^_ga(_|$)/.test(name)) document.cookie = name + "=; Max-Age=0; Path=/";
            });
        } catch (e) { /* cookies blocked: nothing was stored */ }
    }

    function track(name, params) {
        if (!loaded || !Object.prototype.hasOwnProperty.call(EVENTS, name)) return;
        var clean = {};
        EVENTS[name].forEach(function (key) {
            var value = params ? params[key] : undefined;
            if (key === "game" && typeof value === "string" && /^[a-z]{2,20}$/.test(value)) clean.game = value;
            if (key === "answers" && typeof value === "number" && isFinite(value)) clean.answers = Math.max(0, Math.round(value));
        });
        gtag("event", name, clean);
    }

    // Scripts on the pages call this: gaTrack("game_start", { game: "odd" }). Absent when analytics is off.
    window.gaTrack = track;

    // The choice from the notice ("" until the visitor has chosen)
    function readChoice() {
        try {
            var m = document.cookie.match(/(?:^|;\s*)ga_consent=(granted|denied)/);
            return m ? m[1] : "";
        } catch (e) { return ""; }
    }

    function saveChoice(value) {
        try {
            document.cookie = COOKIE + "=" + value + "; Max-Age=" + YEAR + "; Path=/; SameSite=Lax" +
                (location.protocol === "https:" ? "; Secure" : "");
        } catch (e) { /* cookies blocked: the notice simply shows again next time */ }
    }

    // The notice. While it is open the game screen stops above it (base.html), so its buttons stay usable.
    var banner = document.getElementById("consent-banner");

    function setHeight() {
        document.documentElement.style.setProperty("--consent-h", (banner.offsetHeight + 16) + "px");
    }
    function showBanner() {
        if (!banner) return;
        banner.hidden = false;
        document.body.classList.add("consent-showing");
        setHeight();
    }
    function hideBanner() {
        if (!banner) return;
        banner.hidden = true;
        document.body.classList.remove("consent-showing");
    }
    window.addEventListener("resize", function () {
        if (banner && !banner.hidden) setHeight();
    });

    function choose(value) {
        saveChoice(value);
        hideBanner();
        if (value === "granted") allow();
        else withdraw();
    }

    var choice = readChoice();
    if (choice === "granted") allow();
    else if (choice !== "denied") showBanner();

    // Accept and Reject; "Cookie settings" in the footer reopens the notice at any time
    document.querySelectorAll("[data-consent]").forEach(function (button) {
        button.addEventListener("click", function () {
            choose(button.getAttribute("data-consent") === "granted" ? "granted" : "denied");
        });
    });
    document.querySelectorAll("[data-open-consent]").forEach(function (button) {
        button.addEventListener("click", showBanner);
    });

    // Page events declared in the markup: <section data-ga-event="sign_up_done">
    document.querySelectorAll("[data-ga-event]").forEach(function (el) {
        track(el.getAttribute("data-ga-event"));
    });

    // A sign-up form that has been started: the first time anyone types in it
    var form = document.querySelector("form[data-ga-start]");
    if (form) {
        var name = form.getAttribute("data-ga-start");
        form.addEventListener("input", function once() {
            form.removeEventListener("input", once);
            track(name);
        });
    }
})();
