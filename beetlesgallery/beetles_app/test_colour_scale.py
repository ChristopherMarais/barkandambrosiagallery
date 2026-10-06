"""
One colour scale across the site (#572): grey for "not yet", then red, orange, yellow and green from worst to best,
for levels, the day streak, badges, accuracy, challenge and the expertise tree. Blue is IBBI-AI and purple the players,
never a rating; the confetti says who agreed in those colours, and a new level pops up in the middle of the screen.
"""
import re
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase, override_settings

from beetlesgallery.beetles_app import game_answer_review, game_board, game_rewards, game_scale, game_trust
from beetlesgallery.beetles_app.game_scale import STEPS, WORDS
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_answer_review import ReviewCase

BASE = Path(settings.BASE_DIR) / "beetlesgallery"
CSS = (BASE / "static" / "css" / "input.css").read_text(encoding="utf-8")
TEMPLATES = BASE / "templates"
PLAY = (TEMPLATES / "beetles" / "game_play.html").read_text(encoding="utf-8")
CONFETTI = (BASE / "static" / "js" / "beetle_confetti.js").read_text(encoding="utf-8")


def scale_block():
    """The scale's rules in input.css."""
    return CSS[CSS.index("/* The one colour scale"):]


class ScaleTests(SimpleTestCase):
    def test_the_steps_and_their_words_run_worst_to_best(self):
        self.assertEqual(STEPS, ("none", "fair", "decent", "good", "great", "excellent"))
        self.assertEqual([WORDS[s] for s in STEPS], ["Not yet", "Fair", "Decent", "Good", "Great", "Excellent"])

    def test_a_0_to_1_value(self):
        steps = [game_scale.value_step(v) for v in (None, 0.0, 0.29, 0.3, 0.5, 0.7, 0.84, 0.85, 1.0)]
        self.assertEqual(steps, ["none", "fair", "fair", "decent", "good", "great", "great", "excellent", "excellent"])

    def test_levels_each_have_their_own_colour_and_the_top_glows(self):
        self.assertEqual([game_scale.level_classes(n) for n in range(1, 11)],
                         [f"level-{n}" for n in range(1, 10)] + ["level-10 scale-glow"])   # #606
        self.assertEqual(game_scale.level_classes(14), "level-10 scale-glow")
        self.assertEqual(game_scale.level_classes(None), "level-1")

    def test_the_day_streak(self):
        self.assertEqual([game_scale.streak_step(d) for d in (0, 2, 3, 7, 14, 30, 100, 365)],
                         ["none", "none", "fair", "decent", "good", "great", "excellent", "excellent"])

    def test_the_feed_knows_the_same_streak_steps(self):
        js = re.search(r"const STREAK_STEPS = (\[.*?\]);", PLAY).group(1)
        self.assertEqual(js.replace('"', "'"), str([list(s) for s in game_scale.STREAK_STEPS]))

    def test_every_step_has_its_classes_and_the_scale_has_no_blue_or_purple(self):
        block = scale_block()
        for step in STEPS:
            for kind in ("scale-", "scale-fill-", "scale-chip-", "scale-card-"):
                self.assertIn(f".{kind}{step} {{", block)
        for step in ("fair", "decent", "good", "great"):
            self.assertIn(f".scale-soft-{step} {{", block)
        self.assertIn(".scale-glow {", block)
        for colour in ("blue", "purple", "indigo", "violet"):
            self.assertNotIn(colour, block)

    def test_the_house_style_says_what_the_colours_mean(self):
        style = CSS[CSS.index("/* The site's house style"):CSS.index("@layer components")]
        self.assertIn("red, orange, yellow, green", style)
        self.assertIn("Blue is IBBI-AI and purple the players' consensus", style)
        self.assertNotIn("rarity", style)


class NoRarityLeftTests(SimpleTestCase):
    WORDS = re.compile(r"rarity|\b(Common|Uncommon|Rare|Epic|Legendary|Mythic)\b|\"(uncommon|epic|legendary|mythic)\"")

    def test_no_game_rarity_in_templates_scripts_or_the_game_code(self):
        files = list(TEMPLATES.rglob("*.html")) + [BASE / "static" / "js" / "beetle_confetti.js"]
        files += [BASE / "beetles_app" / n for n in ("game_rewards.py", "game_board.py", "game_trust.py", "game_scale.py",
                                                       "game_answer_review.py")]
        files.append(BASE / "beetles_app" / "templatetags" / "beetle_tags.py")
        for path in files:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(self.WORDS.search(text), f"{path.name}: {self.WORDS.search(text)}")


class RenderedTests(SimpleTestCase):
    def test_level_badges(self):
        html = render_to_string("beetles/includes/game_level_badge.html", {"level": 4, "name": "Teneral"})
        self.assertIn("level-4", html)
        self.assertEqual(render_to_string("beetles/includes/game_level_badge.html", {"level": 10}).count("scale-glow"), 1)
        locked = render_to_string("beetles/includes/game_level_badge.html", {"level": 10, "locked": True})
        self.assertNotIn("scale-", locked)

    def test_the_streak_flame(self):
        html = render_to_string("beetles/includes/game_streak.html", {"days": 14, "alive": True})
        self.assertIn("scale-good", html)

    def test_badges_take_their_step(self):
        cards = {tier: render_to_string("beetles/includes/game_badge.html",
                                        {"b": {"key": "k", "name": "x", "how": "", "icon": "fi-rr-crown", "earned": True, "tier": tier}})
                 for tier in game_rewards.BADGE_TIERS}
        self.assertEqual(list(cards), ["fair", "decent", "good", "great", "excellent"])
        for tier, html in cards.items():
            self.assertIn(f"scale-card-{tier}", html)
            self.assertIn(f"scale-{tier}", html)
            self.assertEqual("scale-glow" in html, tier == "excellent")

    def test_accuracy_standing_shows_the_word_in_its_colour(self):
        standing = {"players": 3, "bins": [], "average": 0.6,
                    "me": {"accuracy": 0.8, "percentile": 80, "tier": "great", "tier_name": "Great", "bin": 8}}
        html = render_to_string("beetles/includes/game_accuracy.html", {"standing": standing})
        self.assertIn('scale-chip-great" data-testid="accuracy-tier">Great<', html)
        standing["me"].update(percentile=None, tier="none", tier_name="Not yet")
        self.assertNotIn("accuracy-tier", render_to_string("beetles/includes/game_accuracy.html", {"standing": standing}))

    def test_accuracy_tiers_by_percentile(self):
        self.assertEqual([(lo, word) for lo, _, word in game_board.ACCURACY_TIERS],
                         [(0, "Fair"), (25, "Decent"), (50, "Good"), (75, "Great"), (90, "Excellent")])

    @override_settings(GAME_TRUST_MIN_ACCURACY=0.8)
    def test_the_expertise_key_names_each_band(self):
        self.assertEqual(game_trust.expertise_legend(), [
            ("fair", "Fair", "under 50%"), ("decent", "Decent", "50–60%"), ("good", "Good", "60–70%"),
            ("great", "Great", "70%+")])
        node = SimpleNamespace(correct=81, judged=100, proven=False)
        self.assertEqual(game_trust.node_status(node, 5), "great")

    def test_the_tree_markers_use_the_scale_colours(self):
        page = (TEMPLATES / "beetles" / "game_expertise.html").read_text(encoding="utf-8")
        for status, colour in (("fair", "#ef4444"), ("decent", "#f97316"), ("good", "#facc15"), ("great", "#22c55e"),
                               ("expert", "#15803d")):
            self.assertIn(f".mark-{status} {{ background: {colour}; }}", page)
            if status != "expert":
                self.assertEqual(game_scale.HEX[status], colour)
        self.assertEqual(game_scale.HEX["excellent"], "#15803d")
        self.assertIn(".mark-unknown { background: #fff;", page)   # an empty grey marker


class LevelPopTests(SimpleTestCase):
    def test_a_new_level_event_carries_its_colour(self):
        before = {"level": 4, "perks": [], "rank": None, "goal_met": True, "total": 5}
        now = dict(before, level=5, level_name="Tunnel master", streak=1, goal=20)
        with mock.patch.object(game_rewards, "progress", return_value=now):
            event = game_rewards.play_events(SimpleNamespace(id=1), before)[0]
        self.assertEqual((event["kind"], event["level"], event["step"]), ("level", 5, "decent"))
        self.assertEqual((event["badge"], event["colour"]), ("level-5", "#f59e0b"))   # amber (#606)
        self.assertTrue(event["icon"].startswith("fi-rr-"))

    def test_the_pop_up_sits_in_the_middle_and_throws_the_levels_colour(self):
        self.assertIn('if (event.kind === "level") { levelPop(event); return; }', PLAY)
        rule = re.search(r"\.level-pop \{([^}]*)\}", PLAY).group(1)
        for part in ("position: fixed", "inset: 0", "align-items: center", "justify-content: center"):
            self.assertIn(part, rule)
        self.assertIn("max-width: min(22rem, 92vw)", PLAY)   # readable on a phone
        self.assertIn('window.beetleConfetti($("confetti"), "level", 1, event.colour)', PLAY)
        self.assertIn('if (accent) list.push(["accent"', CONFETTI)


class ConfettiColourTests(SimpleTestCase):
    def test_who_agreed_picks_the_colours(self):
        def palette(kind):
            return re.search(r"\n    " + kind + r": ([^\n]*),\n", CONFETTI).group(1)
        self.assertEqual(palette("validated"), "GREEN")
        self.assertEqual(palette("validated_agreed"), "[...GREEN, ...GREEN, ...BLUE, ...PURPLE]")
        self.assertEqual(palette("ai"), "[...GREY, ...BLUE, ...BLUE]")
        self.assertEqual(palette("players"), "[...GREY, ...PURPLE, ...PURPLE]")
        self.assertEqual(palette("ai_players"), "[...GREY, ...BLUE, ...PURPLE]")
        self.assertEqual(palette("expert"), "PURPLE")
        self.assertEqual(palette("pop"), "GREY")
        self.assertIn('GLOWS = { expert: ', CONFETTI)
        level = palette("level")
        self.assertIn("#ca8a04", level)   # gold
        self.assertIn("#5b3a1e", level)   # brown

    def celebrate(self, basis="agreement", earned=10.0, **facts):
        out = {"skipped": False, "held": False, "points": {"earned": earned, "basis": basis}}
        found = game_answer_review._celebrate(out, facts)
        return found and found["kind"]

    def test_the_kind_for_each_case(self):
        self.assertEqual(self.celebrate("truth", complete=True), "validated")
        self.assertEqual(self.celebrate("truth", complete=True, ai_agrees=True, players_agree=True), "validated_agreed")
        self.assertEqual(self.celebrate("truth", complete=True, ai_agrees=True), "validated")
        self.assertEqual(self.celebrate("truth", earned=30.0), "partial")
        self.assertEqual(self.celebrate("truth", earned=0.5), "pop")
        self.assertEqual(self.celebrate(ai_agrees=True), "ai")
        self.assertEqual(self.celebrate(players_agree=True), "players")
        self.assertEqual(self.celebrate(ai_agrees=True, players_agree=True), "ai_players")
        self.assertEqual(self.celebrate(players_agree=True, expert_agrees=True), "expert")
        self.assertEqual(self.celebrate(earned=0.3), "pop")
        self.assertEqual(self.celebrate(earned=0.0, ai_agrees=True), "ai")
        self.assertIsNone(self.celebrate(earned=0.0, players_agree=True))
        self.assertIsNone(self.celebrate("truth", earned=-2.0))

    def test_who_agreed_comes_from_the_points_on_an_open_beetle(self):
        def agreed(detail, ai=False):
            return game_answer_review._agreed(None, SimpleNamespace(detail=detail), "agreement", {"ai_agrees": ai})
        self.assertEqual(agreed({"agreement": {"genus": 0.6}}),
                         {"players_agree": True, "expert_agrees": False, "ai_agrees": False})
        expert = {"reference": {"species": {"name": "x", "source": "expert", "match": True}}}
        self.assertTrue(agreed(expert)["expert_agrees"])
        model = {"reference": {"species": {"name": "x", "source": "model", "match": True}}}
        self.assertTrue(agreed(model)["ai_agrees"])
        self.assertFalse(agreed({"reference": {"species": {"name": "x", "source": "expert", "match": False}}})["expert_agrees"])
        grid = {"votes": {"2": {"agreement": {"genus": 0.4}, "points": 1.0}}}   # an Odd One Out grid, pick by pick
        self.assertTrue(agreed(grid)["players_agree"])
        self.assertFalse(agreed({"agreement": {"genus": -0.3}})["players_agree"])


class ValidatedAndAgreedTests(ReviewCase):
    def test_a_right_name_that_ibbi_ai_and_the_players_also_gave(self):
        roi = self.roi(self.t_affinis)
        self.assertEqual(self.classify(roi, AFFINIS)["celebrate"]["kind"], "validated")
        roi = self.roi(self.t_affinis)
        self.other("p1", roi, AFFINIS)
        self.other("p2", roi, AFFINIS)
        self.predict(roi, self.t_affinis, 0.9)
        self.assertEqual(self.classify(roi, AFFINIS)["celebrate"]["kind"], "validated_agreed")

    def test_only_one_of_them_is_just_green(self):
        roi = self.roi(self.t_affinis)
        self.other("p1", roi, AFFINIS)
        self.assertEqual(self.classify(roi, AFFINIS)["celebrate"]["kind"], "validated")
