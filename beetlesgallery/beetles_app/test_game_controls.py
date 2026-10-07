"""Game controls: keys for both hands, Skip away from Next with Back beside it, the last beetle on Back, a guard
against button mashing, zoom and Lighting on the whole photo, A/B letters at the facing corners, a clear Exit."""
from pathlib import Path

from django.conf import settings
from django.urls import reverse

from beetlesgallery.beetles_app.test_game import GameCase

TOUR = (Path(settings.BASE_DIR) / "beetlesgallery" / "static" / "js" / "game_tour.js").read_text()


class GameControlsTests(GameCase):
    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=["mixed"])).content.decode()

    def test_skip_back_and_next(self):
        page = self.page()
        actions = page[page.index('<div id="actions">'):page.index('id="submit"')]
        self.assertLess(actions.index('id="skip"'), actions.index('id="back"'))   # Skip, then Back, then Next
        self.assertIn('<kbd class="kbd" title="Backspace">&#9003;</kbd>', page)   # Skip: Backspace (r7)
        self.assertNotIn("Not sure</strong>", page)
        # equal gaps, 1fr/1fr/2fr (#play-buttons: three different widths and an uneven gap before)
        self.assertIn("grid-template-columns: 1fr 1fr 2fr", page)

    def test_keys_for_both_hands_and_no_number_keys(self):
        page = self.page()
        for keys in ('up: ["ArrowUp", "w"]', 'down: ["ArrowDown", "s"]', 'next: ["Enter", " "]', 'skip: ["Backspace", "Delete", "End", "x"]',
                     'back: ["Tab", "z"]', 'photo: ["Home", "q", "v"]', 'light: ["PageUp", "e", "l"]'):
            self.assertIn(keys, page)
        self.assertNotIn("/^[1-9]$/.test(key)", page)
        self.assertNotIn('<kbd class="kbd">{{ forloop.counter }}</kbd>', page)
        self.assertIn("if (!closeOverlays()) $(\"exit\").click();", page)   # Esc with nothing open leaves

    def test_back_shows_the_last_beetle_and_the_back_gesture_closes_it(self):
        page = self.page()
        self.assertIn('data-testid="previous"', page)
        self.assertIn('$("back").addEventListener("click", showPrevious);', page)
        # the phone's back gesture closes it, or else shows the recap (#578; test_game_leave_recap)
        self.assertIn("if (closeOverlays()) return;   // your last beetle's review", page)

    def test_button_mashing_is_ignored(self):
        page = self.page()
        self.assertIn("performance.now() < readyAt", page)
        self.assertIn("e.repeat", page)

    def test_whole_photo_zoom_and_lighting(self):
        page = self.page()
        self.assertIn('makeZoomable($("lb-frame"), $("lb-zoom")', page)
        self.assertIn('data-testid="lightbox-lighting"', page)
        self.assertIn("<span>Lighting</span>", page)
        self.assertNotIn("<span>Light</span>", page)

    def test_layout_details(self):
        page = self.page()
        self.assertIn('id="exit" class="inline-flex items-center justify-center"', page)
        self.assertIn('#game[data-mode="pair"] #photos .cell:nth-child(1) .ab-tag { top: 0.375rem; right: 0.375rem; }', page)
        self.assertIn("ctx.roundRect", page)

    def test_unlocks_get_a_walkthrough(self):
        self.assertIn("function unlocked(list)", TOUR)
        self.assertIn('key: "light"', self.page())
