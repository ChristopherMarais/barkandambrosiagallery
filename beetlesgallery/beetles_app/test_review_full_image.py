"""
The whole photo from a round's answers, as reviewed on a phone: the beetle's box is the specimen page's box (white
with a dark edge, so it shows on light and dark photos) with the rest of the photo dimmed, and a photo held down
(about half a second) opens the whole photo on the round's answers and on the review after an answer, while a tap
does what it did before.
"""
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import reverse

from beetlesgallery.beetles_app.test_game_answer_review import ReviewCase
from beetlesgallery.beetles_app.test_game_history_filters import HistoryFilterCase

BOX_RULE = ".roi-box { position: absolute; border: 2px solid rgb(255 255 255 / 0.9); border-radius: 0.375rem;"
LONG_PRESS = Path(__file__).resolve().parent.parent / "static" / "js" / "long_press.js"


class RoundReviewWholePhotoTests(HistoryFilterCase):
    def page(self):
        rnd = self.session(["classify"])
        res = self.client.get(reverse("game_round_review", args=[rnd.id]))
        self.assertEqual(res.status_code, 200)
        return res.content.decode()

    def test_the_box_is_the_specimen_page_s_box_not_plain_white(self):
        page = self.page()
        self.assertIn(BOX_RULE, page)
        self.assertIn('id="lightbox-box" class="roi-box pointer-events-none"', page)
        self.assertNotIn("border-2 border-white", page)

    def test_the_rest_of_the_photo_is_dimmed_so_the_beetle_stands_out(self):
        page = self.page()
        self.assertIn("#lightbox-box { box-shadow: 0 0 0 1px rgb(0 0 0 / 0.4), inset 0 0 0 1px rgb(0 0 0 / 0.4), "
                      "0 0 0 9999px rgb(0 0 0 / 0.35); }", page)
        self.assertIn('<div class="relative inline-block overflow-hidden">', page)   # the dimming stays on the photo

    def test_a_held_photo_opens_the_whole_photo_and_a_tap_still_does(self):
        page = self.page()
        self.assertIn("js/long_press.js", page)
        self.assertIn('btn.addEventListener("click", () => openLightbox(side.url, side.box));', page)
        self.assertIn('window.onLongPress($("items"), ".review-photo",', page)
        self.assertIn(".review-photo { -webkit-touch-callout: none;", page)


class SpecimenPageBoxTests(HistoryFilterCase):
    def test_the_specimen_page_still_draws_the_same_box(self):
        res = self.client.get(reverse("beetle_detail", args=[self.a.id]))
        self.assertEqual(res.status_code, 200)
        page = res.content.decode()
        self.assertIn(BOX_RULE, page)
        self.assertIn(".roi-box-current { cursor: default; border-color: rgb(17 24 39 / 0.9);", page)


class ReviewHoldTests(ReviewCase):
    def test_a_held_review_photo_opens_the_whole_photo(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        self.assertIn("js/long_press.js", page)
        self.assertIn('window.onLongPress(box, ".cell.has-names", (c) => { if (previous) openReviewPhoto(c); });', page)
        self.assertIn('else c.classList.toggle("names-off");', page)   # a tap still turns the names off and on


class LongPressScriptTests(SimpleTestCase):
    def test_half_a_second_cancelled_by_a_move_and_the_tap_after_it_dropped(self):
        js = LONG_PRESS.read_text(encoding="utf-8")
        self.assertIn("const HOLD_MS = 500", js)
        for event in ('"pointerdown"', '"pointermove"', '"pointerup"', '"pointercancel"', '"scroll"'):
            self.assertIn(event, js)
        self.assertIn('root.addEventListener("contextmenu"', js)
        self.assertIn('root.addEventListener("dragstart"', js)
        self.assertIn("e.stopImmediatePropagation();", js)
