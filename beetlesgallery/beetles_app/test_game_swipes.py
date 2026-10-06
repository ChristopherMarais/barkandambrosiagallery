"""
Swipes on a phone, only where they are obvious (#603): the whole photo (left and right for the specimen's other
photos, down to close, never while zoomed), the review card (left is Next) and Back's card (left returns). Never while
answering; one finger only; far or quick enough and mostly in one direction; a short hint the first time; the buttons
stay as they are.
"""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

PAGE = (Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles" / "game_play.html").read_text(encoding="utf-8")


def js_function(name):
    start = PAGE.index(f"function {name}(")
    return PAGE[start:PAGE.index("\n  }\n", start)]


class SwipeTests(SimpleTestCase):
    def test_one_finger_far_or_quick_enough_and_mostly_one_way(self):
        swipe = js_function("onSwipe")
        self.assertIn('if (e.pointerType !== "touch") return;', swipe)
        self.assertIn("if (fingers.size > 1) { start = null; return; }", swipe)   # a pinch is never a swipe
        self.assertIn("Math.abs(dx) > 1.5 * Math.abs(dy) && (Math.abs(dx) >= SWIPE_MIN || quick(Math.abs(dx)))", swipe)
        self.assertIn("d / dt >= SWIPE_SPEED", swipe)
        self.assertIn("e.clientX < SWIPE_EDGE", swipe)   # the phone's own back gesture
        self.assertIn('document.addEventListener("click", eat, true);', swipe)   # never ends in a tap

    def test_the_whole_photo_never_while_zoomed(self):
        self.assertIn('onSwipe($("lightbox"), () => !$("lightbox").classList.contains("hidden") && !lbZoom.zoomed()', PAGE)
        self.assertIn('stepGallery(dir === "left" ? 1 : -1);', PAGE)
        self.assertIn('if (dir === "down") { closeLightbox(); return; }', PAGE)
        self.assertIn("return { zoomed: () => z > 1, reset:", PAGE)

    def test_the_review_card_only_on_the_review(self):
        self.assertIn('onSwipe($("stage"), (e) => phase === "review"', PAGE)
        self.assertIn('if (dir !== "left" || $("submit").disabled || performance.now() < readyAt) return false;', PAGE)
        self.assertIn('onSwipe($("previous"), (e) => !$("previous").classList.contains("hidden")', PAGE)

    def test_a_slide_is_never_a_tap(self):
        self.assertIn("> TAP_SLOP) {\n        moved = true;", PAGE)

    def test_a_first_time_hint_on_a_phone_and_reduced_motion(self):
        hint = js_function("swipeHint")
        self.assertIn("if (!FINGERS) return;", hint)
        self.assertIn('localStorage.getItem("game-swipe-hint-" + key)', hint)
        self.assertIn('swipeHint("next", $("photo-area"), "Swipe left for the next beetle");', PAGE)
        self.assertIn("@media (prefers-reduced-motion: reduce) { .swipe-hint { transition: none;", PAGE)
