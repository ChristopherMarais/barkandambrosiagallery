"""A photo's Flag in play brings up the reasons in a small menu right at it, without the whole photo (owner: "You don't
have to open up the image to full screen to flag it. Just bring up the flag menu."). The R key does the same while
answering. The whole photo keeps its own Flag (for the beetle's other photos), and a reason picked in either menu
sends the same report."""
import re
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils.html import escape

from beetlesgallery.beetles_app.game_views import FEED_REPORT_HINTS, FEED_REPORT_REASONS
from beetlesgallery.beetles_app.test_game import GameCase

PAGE = (Path(__file__).resolve().parent.parent / "templates" / "beetles" / "game_play.html").read_text(encoding="utf-8")


def js_function(name):
    """The body of ``function name(...) {...}`` in the page, up to the next function at the same depth."""
    start = PAGE.index(f"function {name}(")
    end = PAGE.find("\n  function ", start + 1)
    return PAGE[start:end if end != -1 else len(PAGE)]


def between(start, end):
    i = PAGE.index(start)
    return PAGE[i:PAGE.index(end, i)]


class FlagButtonTests(SimpleTestCase):
    def test_a_photos_flag_brings_up_the_small_menu_not_the_whole_photo(self):
        chip = between('report.id = "report-chip-" + i;', "cell.appendChild(report);")
        self.assertIn("if (flagChip === report) closeFlagMenu();", chip)   # its Flag again closes it
        self.assertIn("else openFlagMenu(i);", chip)
        self.assertNotIn("openLightbox", chip)
        self.assertNotIn('$("report-menu")', chip)
        opened = js_function("openFlagMenu")
        self.assertNotIn('$("lightbox")', opened)
        self.assertIn('$("flag-menu").classList.remove("hidden");', opened)

    def test_the_flag_says_it_opens_a_menu(self):
        chip = between('report.id = "report-chip-" + i;', "cell.appendChild(report);")
        self.assertIn('report.setAttribute("aria-haspopup", "menu");', chip)
        self.assertIn('report.setAttribute("aria-controls", "flag-menu");', chip)
        self.assertIn('report.setAttribute("aria-expanded", "false");', chip)
        self.assertIn('chip.setAttribute("aria-expanded", "true");', js_function("openFlagMenu"))
        self.assertIn('chip.setAttribute("aria-expanded", "false");', js_function("closeFlagMenu"))

    def test_the_flag_key_while_answering_brings_up_the_same_menu(self):
        keys = between('else if (pressed === "photo" || pressed === "report") {', 'else if (pressed === "light")')
        self.assertIn('if (pressed === "report" && phase === "answer" && openFlagMenu(at, true)) return;', keys)
        # on a review (its Flags step aside) or a flagged tile, the whole photo with its reasons, as before
        self.assertLess(keys.index("openFlagMenu(at, true)"), keys.index('if (MODE === "odd" || MODE === "select") openTile(at);'))
        self.assertIn('if (pressed === "report") $("report-menu").classList.remove("hidden");', keys)
        opened = js_function("openFlagMenu")
        self.assertIn('if (!chip || flagged.has(i)) return false;', opened)
        # from the keys, on to the first reason
        self.assertIn('if (keys) $("flag-menu").querySelector(".report-reason").focus({ preventScroll: true });', opened)

    def test_the_menu_has_its_own_keys(self):
        menu_keys = between("    if (flagChip) {", "    if (document.querySelector(\"[data-overlay-open]\")) return;")
        self.assertIn('if (is("report", key)) { e.preventDefault(); closeFlagMenu(); }', menu_keys)
        self.assertIn('else if (is("up", key) || is("down", key)) {', menu_keys)
        self.assertIn("reasons[to].focus({ preventScroll: true });", menu_keys)
        self.assertIn("return;", menu_keys)   # Enter and Space pick the reason in focus, never Submit
        self.assertNotIn('$("submit")', menu_keys)


class SameReportTests(SimpleTestCase):
    def test_it_reports_what_the_whole_photos_flag_would(self):
        opened = js_function("openFlagMenu")
        # photo i of the beetle as shown (the first of its photos), from the review on screen if there is one
        self.assertIn("lightboxReview = reviewShown();", opened)
        self.assertIn("lightboxImage = i;", opened)
        self.assertIn("galleryAt = 0;", opened)
        self.assertIn("lightboxReview = review || reviewShown();", js_function("openLightbox"))
        self.assertIn('function reviewShown() { return phase === "review" || phase === "next" ? (previous && previous.where) || null : null; }', PAGE)

    def test_one_handler_for_the_reasons_of_both_menus(self):
        self.assertEqual(PAGE.count('document.querySelectorAll(".report-reason").forEach('), 1)
        handler = between('document.querySelectorAll(".report-reason").forEach(', "\n  }));")
        self.assertIn("await api(root.dataset.reportUrl, { round: lightboxReview.round, index: lightboxReview.index, image: lightboxImage, photo: galleryAt, reason: btn.dataset.reason });", handler)
        self.assertIn("await api(root.dataset.reportUrl, { round: roundId, index: item.index, image: lightboxImage, photo: galleryAt, reason: btn.dataset.reason });", handler)
        self.assertIn("flagTile(lightboxImage);", handler)
        self.assertIn('send(Object.assign({ skipped: true, reported: true }', handler)
        self.assertIn('text: "Sent to the curators. Carry on with the rest."', handler)
        # the small menu goes as the whole photo does, and when it couldn't send
        self.assertEqual(handler.count("closeFlagMenu();"), 3)
        self.assertEqual(handler.count('$("lightbox").classList.add("hidden");'), 2)

    def test_the_whole_photo_keeps_its_own_flag(self):
        self.assertIn('$("report-cog").addEventListener("click", (e) => { e.stopPropagation(); $("report-menu").classList.toggle("hidden"); });', PAGE)
        self.assertIn('else if (is("report", key)) $("report-menu").classList.toggle("hidden");', PAGE)


class PlacementTests(SimpleTestCase):
    def test_above_its_flag_or_below_and_never_off_the_screen(self):
        place = js_function("placeFlagMenu")
        self.assertIn("const above = at.top - gap - menu.offsetHeight;", place)
        self.assertIn("above >= edge ? above : at.bottom + gap", place)   # below only when there is no room above
        self.assertIn("Math.max(edge, Math.min(at.left, w - edge - menu.offsetWidth))", place)
        self.assertIn("h - edge - menu.offsetHeight", place)
        self.assertIn("edge = 8", place)
        self.assertIn('window.addEventListener("resize", placeFlagMenu);', PAGE)
        self.assertIn('document.addEventListener("scroll", placeFlagMenu, true);', PAGE)

    def test_its_style_is_in_the_page_and_its_flag_stays_clear(self):
        styles = PAGE[PAGE.index("#game { position: fixed"):]
        styles = styles[:styles.index("</style>")]
        rule = styles[styles.index("#flag-menu {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("position: fixed;", rule)
        self.assertIn("max-width: calc(100vw - 1rem);", rule)
        # over the game (60), under the toasts (96) and the whole photo (100)
        z = int(re.search(r"z-index: (\d+);", rule).group(1))
        self.assertTrue(60 < z < 96, z)
        self.assertIn('#game[data-mode] #photos .cell > .report-chip[aria-expanded="true"] { opacity: 1; pointer-events: auto; }', styles)

    def test_it_looks_like_the_whole_photos_menu(self):
        def classes(el):
            return set(re.search(rf'<div id="{el}" class="([^"]+)"', PAGE).group(1).split())
        self.assertEqual(classes("flag-menu"), classes("report-menu") - {"absolute"})   # placed by its own style


class ClosingTests(SimpleTestCase):
    def test_a_tap_outside_only_closes_it(self):
        self.assertIn("eatClick = !!flagChip && !$(\"flag-menu\").contains(e.target) && !flagChip.contains(e.target);", PAGE)
        self.assertIn('document.addEventListener("keydown", () => { eatClick = false; }, true);', PAGE)
        eat = between('document.addEventListener("click", (e) => {\n    if (!eatClick) return;', "}, true);")
        self.assertIn("e.stopPropagation();", eat)
        self.assertIn("e.preventDefault();", eat)

    def test_escape_and_the_back_gesture_close_it(self):
        overlays = js_function("closeOverlays")
        self.assertIn('["previous"], ["flag-menu"]].filter(', overlays)   # counts as open: Esc never leaves the game then
        self.assertIn("closeFlagMenu();", overlays)

    def test_the_next_beetle_and_back_close_it(self):
        shown = js_function("showItem")
        self.assertLess(shown.index("closeFlagMenu();"), shown.index('holder.innerHTML = "";'))
        back = js_function("showPrevious")
        self.assertLess(back.index("closeFlagMenu();"), back.index('$("previous").classList.remove("hidden");'))
        # Submit or Skip (by any key) locks the answer in: the reasons go with it
        self.assertIn("if (busy) closeFlagMenu();", js_function("setPhase"))

    def test_focus_goes_back_to_its_flag(self):
        closed = js_function("closeFlagMenu")
        self.assertIn('if ($("flag-menu").contains(document.activeElement)) chip.focus({ preventScroll: true });', closed)
        self.assertIn('$("flag-menu").classList.add("hidden");', closed)


class FlagMenuPageTests(GameCase):
    def test_the_small_menu_has_every_reason_outside_the_whole_photo(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        lightbox = page[page.index('<div id="lightbox"'):page.index('id="lb-count"')]
        self.assertNotIn('id="flag-menu"', lightbox)
        menu = page[page.index('<div id="flag-menu"'):]
        menu = menu[:menu.index("</div>\n\n")]
        self.assertIn('role="menu" aria-label="Flag this photo" data-testid="flag-menu"', menu)
        self.assertIn(">Flag this photo</div>", menu)
        for value, label in FEED_REPORT_REASONS:
            self.assertIn(f'data-reason="{value}" role="menuitem">{escape(label)}', menu)
        for hint in FEED_REPORT_HINTS.values():
            self.assertIn(hint, menu)
        self.assertEqual(menu.count('class="report-reason '), len(FEED_REPORT_REASONS))
        # the whole photo's Flag and reasons are still there
        self.assertIn('id="report-cog" class="report-chip"', lightbox)
        self.assertIn('id="report-menu"', lightbox)
