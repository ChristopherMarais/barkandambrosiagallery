"""
The game page follows the site's changes (#604, #606, #608): a start with nothing in the chosen game still shows the
game choices, a level-up's badge falls back to level 1's colours, and a badge pop-up sits on a card in its own colours.
"""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

PAGE = (Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles" / "game_play.html").read_text(encoding="utf-8")


def js_function(name):
    start = PAGE.index(f"function {name}(")
    return PAGE[start:PAGE.index("\n  }\n", start)]


class GamePageSiteChangesTests(SimpleTestCase):
    def test_an_error_keeps_its_json(self):
        self.assertIn("err.data = data;", js_function("api"))

    def test_nothing_to_play_still_shows_the_game_choices(self):
        start = js_function("startFeed")
        self.assertIn("if (e.data && e.data.prefs) renderPrefs(e.data.prefs);", start)
        self.assertLess(start.index("renderPrefs(e.data.prefs)"), start.index("showError(e.message)"))

    def test_level_pop_falls_back_to_level_one(self):
        self.assertIn('event.badge || "level-1"', js_function("levelPop"))
        self.assertNotIn("scale-chip-none", js_function("levelPop"))

    def test_badge_pop_card_in_its_colours(self):
        badge = js_function("badgePop")
        self.assertIn('"scale-card-" + event.tier', badge)
        self.assertIn('event.colour || "#6b7280"', badge)   # a tier without a class still gets its colour
        self.assertIn('card.className = "level-pop-card" + (cardClass ? " " + cardClass : "");', js_function("showPop"))
