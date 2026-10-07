// Google Analytics (GA4). Loads only after the visitor clicks Accept in the cookie notice (Google Consent Mode v2:
// analytics storage is denied until then). The choice is kept in a first-party cookie for a year.
// base.html renders the script tag (with data-ga-id and data-ga-page) only when GA_MEASUREMENT_ID is set.
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

    var started = false;

    function allow() {
        gtag("consent", "update", { analytics_storage: "granted" });
        if (started) return;
        started = true;

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

    function track(name, params) {
        if (!started || !Object.prototype.hasOwnProperty.call(EVENTS, name)) return;
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

    // Choice from the cookie notice
    var choice = "";
    try {
        var m = document.cookie.match(/(?:^|;\s*)ga_consent=(granted|denied)/);
        choice = m ? m[1] : "";
    } catch (e) { choice = ""; }

    function saveChoice(value) {
        try {
            document.cookie = COOKIE + "=" + value + "; Max-Age=" + YEAR + "; Path=/; SameSite=Lax" +
                (location.protocol === "https:" ? "; Secure" : "");
        } catch (e) { /* cookies blocked: the notice simply shows again next time */ }
    }

    var banner = document.getElementById("consent-banner");
    if (choice === "granted") {
        allow();
    } else if (!choice && banner) {
        banner.hidden = false;
    }

    document.querySelectorAll("[data-consent]").forEach(function (button) {
        button.addEventListener("click", function () {
            var value = button.getAttribute("data-consent") === "granted" ? "granted" : "denied";
            saveChoice(value);
            if (banner) banner.hidden = true;
            if (value === "granted") allow();
        });
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
