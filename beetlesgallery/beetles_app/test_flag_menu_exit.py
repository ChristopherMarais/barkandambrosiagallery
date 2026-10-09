"""
Owner: a photo's Flag brings up the reasons right next to it, without opening the whole photo first; one menu for every
Flag (the photos in play, the grid tiles, the whole photo, and the keys PageDown / R). The recap's way out says "Exit".
"""
from django.urls import reverse

from beetlesgallery.beetles_app.game_views import FEED_REPORT_REASONS
from beetlesgallery.beetles_app.test_game import GameCase


class FlagMenuCase(GameCase):
    def page(self, mode="mixed"):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=[mode])).content.decode()

    @staticmethod
    def between(page, start, end):
        at = page.index(start)
        return page[at:page.index(end, at)]


class FlagMenuTests(FlagMenuCase):
    def test_one_menu_of_reasons_outside_the_whole_photo(self):
        page = self.page()
        lightbox = self.between(page, '<div id="lightbox"', '<div id="report-menu"')
        self.assertNotIn('id="report-menu"', lightbox)   # no longer inside the whole photo
        menu = self.between(page, '<div id="report-menu"', "</div>\n")
        self.assertIn('class="hidden fixed ', menu)      # placed on the screen, next to the Flag tapped
        for value, label in FEED_REPORT_REASONS:
            self.assertIn(f'data-reason="{value}"', page)
        self.assertIn("#report-menu { left: 0; top: 0; z-index: 110;", page)   # over the whole photo (z-100) too
        self.assertIn('data-report-url="' + reverse("game_report_item") + '"', page)   # the same endpoint

    def test_a_photos_flag_opens_the_menu_and_not_the_whole_photo(self):
        page = self.page()
        chips = self.between(page, 'report.className = "report-chip";', "cell.appendChild(report);")
        self.assertIn("openReportMenu(report, { image: i, photo: 0, review: reviewWhere() }, true);", chips)
        self.assertNotIn("openLightbox", chips)

    def test_the_menu_stays_inside_the_screen(self):
        place = self.between(self.page(), "function placeReportMenu()", "\n  }\n")
        self.assertIn("reportAnchor.getBoundingClientRect()", place)
        self.assertIn("window.innerHeight - h - edge", place)
        self.assertIn("window.innerWidth - w - edge", place)

    def test_the_report_is_sent_as_before_for_the_photo_the_menu_was_opened_for(self):
        page = self.page()
        send = self.between(page, 'document.querySelectorAll(".report-reason")', "\n  }));")
        self.assertIn("image: t.image, photo: t.photo, reason: btn.dataset.reason", send)
        self.assertIn("round: t.review.round, index: t.review.index", send)   # an answered beetle (the review)
        self.assertIn("flagTile(t.image);", send)                            # a grid carries on without it
        self.assertIn("send(Object.assign({ skipped: true, reported: true }", send)

    def test_the_whole_photos_flag_and_the_keys_use_the_same_menu(self):
        page = self.page()
        self.assertIn('$("report-cog").addEventListener("click", (e) => { e.stopPropagation(); openLightboxReport(true); });', page)
        self.assertIn("{ image: lightboxImage, photo: galleryAt, review: lightboxReview }", page)
        self.assertIn('else if (is("report", key)) openLightboxReport(true);', page)
        self.assertIn('const chip = pressed === "report" ? $("report-chip-" + at) : null;', page)
        self.assertIn('openReportMenu(chip, { image: at, photo: 0, review: reviewWhere() }, true);', page)
        self.assertIn('if (pressed === "report") openLightboxReport(false);', page)   # its Flag hidden: the whole photo

    def test_esc_or_a_tap_elsewhere_closes_it(self):
        page = self.page()
        self.assertIn('if (!$("report-menu").classList.contains("hidden")) { closeReportMenu(); return; }', page)
        tap = self.between(page, 'document.addEventListener("pointerdown", (e) => {\n    if ($("report-menu")', "}, true);")
        self.assertIn("closeReportMenu();", tap)
        self.assertIn('$("report-menu").contains(e.target)', tap)

    def test_every_mode_has_the_flags(self):
        for mode in ("mixed", "classify", "pair", "odd", "select"):
            with self.subTest(mode=mode):
                page = self.page(mode)
                self.assertIn('id="report-menu"', page)
                self.assertIn("function openReportMenu(anchor, target, toggle)", page)


class RecapExitTests(FlagMenuCase):
    def test_the_recaps_way_home_says_exit(self):
        page = self.page()
        self.assertIn(f'<a href="{reverse("game_home")}" class="px-6 py-3.5 text-base font-medium text-gray-700 border '
                      'border-gray-300 rounded-xl" data-testid="recap-home">Exit</a>', page)
        recap = self.between(page, '<div id="recap"', 'data-testid="discussions-link"')
        self.assertNotIn(" home</a>", recap)
