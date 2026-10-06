"""
A badge earned during play pops up like a new level (#608): a card in the middle with the badge's icon, name and how
it was earned, confetti in its colour (none with reduced motion); several wait their turn, one after another.
The events come from game_rewards.play_events ({"kind": "badge", "title", "text", "icon", "tier", "colour"}).
"""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

PAGE = (Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles" / "game_play.html").read_text(encoding="utf-8")


def js_function(name):
    start = PAGE.index(f"function {name}(")
    return PAGE[start:PAGE.index("\n  }\n", start)]


class BadgePopupTests(SimpleTestCase):
    def test_a_badge_event_pops_up(self):
        self.assertIn('if (event.kind === "badge") { badgePop(event); return; }', js_function("toast"))
        badge = js_function("badgePop")
        self.assertIn('"badge-pop"', badge)
        self.assertIn('"scale-chip-" + event.tier', badge)   # a step of the site's scale
        self.assertIn("event.colour", badge)                 # or a colour of its own (blue, purple)

    def test_one_after_another(self):
        self.assertIn("queuePop(() => showPop(", js_function("badgePop"))
        self.assertIn("queuePop(() => showPop(", js_function("levelPop"))   # a level waits its turn too
        self.assertIn("setTimeout(nextPop, POP_MS + 450);", js_function("nextPop"))

    def test_confetti_in_its_colour_but_not_with_reduced_motion(self):
        self.assertIn('if (!reduceMotion && window.beetleConfetti) window.beetleConfetti($("confetti"), "level", 1, event.colour);',
                      js_function("showPop"))
