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
        self.assertLess(actions.index('id="skip"'), actions.index('class="gap"'))
        self.assertLess(actions.index('class="gap"'), actions.index('id="back"'))   # Back sits beside Next
        self.assertIn("<kbd class=\"kbd\">Del</kbd>", page)
        self.assertNotIn("Not sure</strong>", page)

    def test_keys_for_both_hands_and_no_number_keys(self):
        page = self.page()
        for keys in ('up: ["ArrowUp", "w"]', 'down: ["ArrowDown", "s"]', 'next: ["Enter", " "]', 'skip: ["Delete", "End", "x"]',
                     'back: ["Backspace", "z"]', 'photo: ["Home", "q", "v"]', 'light: ["PageUp", "e", "l"]'):
            self.assertIn(keys, page)
        self.assertNotIn("/^[1-9]$/.test(key)", page)
        self.assertNotIn('<kbd class="kbd">{{ forloop.counter }}</kbd>', page)
        self.assertIn("if (!closeOverlays()) $(\"exit\").click();", page)   # Esc with nothing open leaves

    def test_back_shows_the_last_beetle_first_then_leaves(self):
        page = self.page()
        self.assertIn('data-testid="previous"', page)
        self.assertIn("if (previous || previousUrl) { showPrevious(); return; }", page)

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
        self.assertIn('id="exit" class="inline-flex items-center"', page)
        self.assertIn('#game[data-mode="pair"] #photos .cell:nth-child(1) .ab-tag { top: 0.375rem; right: 0.375rem; }', page)
        self.assertIn("ctx.roundRect", page)

    def test_unlocks_get_a_walkthrough(self):
        self.assertIn("function unlocked(list)", TOUR)
        self.assertIn('key: "light"', self.page())
