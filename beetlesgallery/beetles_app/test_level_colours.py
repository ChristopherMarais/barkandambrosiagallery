"""
Level colours (#606): every level its own light, hazy colour, grey at 1 then red through orange, yellow and green;
only level 10 is filled, solid green, and glows. Badges, the sidebar, the levels table and the level-up card follow it.
"""
import re
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase

from beetlesgallery.beetles_app import game_rewards, game_scale

CSS = (Path(settings.BASE_DIR) / "beetlesgallery" / "static" / "css" / "input.css").read_text(encoding="utf-8")
SHADES = {1: "gray", 2: "red", 3: "red", 4: "orange", 5: "amber", 6: "yellow", 7: "lime", 8: "green", 9: "green"}


def rule(level):
    return re.search(r"\n\.level-" + str(level) + r" \{([^}]*)\}", CSS).group(1)


class LevelColourTests(SimpleTestCase):
    def test_levels_one_to_nine_are_hazy_each_in_its_own_colour(self):
        rules = [rule(n) for n in range(1, 10)]
        self.assertEqual(len(set(rules)), 9)   # no two alike
        for n, text in zip(range(1, 10), rules):
            with self.subTest(level=n):
                self.assertIn(f"var(--color-{SHADES[n]}-", text)
                self.assertIn("box-shadow: inset 0 0 0 1px", text)   # a light tint with a thin ring, never filled
                self.assertRegex(text, r"background-color: [^;]*-(100|200)\)")
                self.assertRegex(text, r"color: [^;]*-(700|800|900)\)")   # dark text: readable on the tint
        self.assertIn("var(--color-orange-", rule(3))   # red-orange sits between red and orange

    def test_only_level_ten_is_filled(self):
        self.assertEqual(rule(10).strip(), "background-color: var(--color-green-700); color: #fff;")
        self.assertEqual(game_scale.level_classes(10), "level-10 scale-glow")
        self.assertEqual(sum("scale-glow" in game_scale.level_classes(n) for n in range(1, 11)), 1)

    def test_each_level_has_a_colour_for_the_confetti(self):
        colours = [game_scale.level_hex(n) for n in range(1, 11)]
        self.assertEqual(len(set(colours)), 10)
        self.assertTrue(all(re.fullmatch(r"#[0-9a-f]{6}", c) for c in colours))
        self.assertEqual(game_scale.level_hex(99), game_scale.level_hex(10))

    def test_the_steps_still_climb_with_the_levels(self):
        steps = [game_scale.STEPS.index(game_scale.level_step(n)) for n in range(1, 11)]
        self.assertEqual(steps, sorted(steps))
        self.assertEqual((game_scale.level_step(1), game_scale.level_step(10)), ("none", "excellent"))

    def test_badges_take_the_levels_colour(self):
        for n in range(1, 11):
            html = render_to_string("beetles/includes/game_level_badge.html", {"level": n})
            self.assertIn(f"level-{n}", html)
            self.assertEqual("scale-glow" in html, n == 10)

    def test_the_level_up_card_takes_the_levels_colour(self):
        before = {"level": 6, "perks": [], "rank": None, "goal_met": True, "total": 5}
        now = dict(before, level=7, level_name="x", streak=1, goal=20)
        with mock.patch.object(game_rewards, "progress", return_value=now):
            event = game_rewards.play_events(SimpleNamespace(id=1), before, badges=False)[0]
        self.assertEqual((event["badge"], event["colour"]), ("level-7", "#84cc16"))
