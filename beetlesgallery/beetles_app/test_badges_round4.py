"""
Badges, round 4 (#608): the hardest blue and the strange ones purple (badges only), a few new badges worked out from
the answers there are, and an event for each badge an answer earns, for the pop-up during play.
"""
import re
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.utils import timezone

from beetlesgallery.beetles_app import game_rewards
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, GameRound, ModelPrediction
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase

CSS = (Path(settings.BASE_DIR) / "beetlesgallery" / "static" / "css" / "input.css").read_text(encoding="utf-8")
NEW = ("fullhouse", "imposters", "splitter", "lumpsplit", "fungus", "machine", "lonewolf", "weekend", "leap", "fullmoon")


class ColourTests(SimpleTestCase):
    def test_the_hardest_are_blue_and_the_strange_ones_purple(self):
        tier = game_rewards.badge_tier
        for key in ("flawless", "expert5", "tenthousand", "streak365", "discovery", "lumpsplit", "machine"):
            self.assertEqual(tier(key), "blue", key)
        for key in ("nightowl", "earlybird", "twins", "lonewolf", "leap", "fullmoon"):
            self.assertEqual(tier(key), "purple", key)
        self.assertEqual(tier("king"), "excellent")   # Royalty keeps the top level's colour
        self.assertEqual(tier("fullhouse"), "great")

    def test_blue_and_purple_have_their_classes_outside_the_scale(self):
        scale = CSS[CSS.index("/* The one colour scale"):]
        for colour in ("blue", "purple"):
            for kind in ("scale-", "scale-fill-", "scale-chip-", "scale-soft-", "scale-card-"):
                self.assertIn(f".{kind}{colour} {{", CSS)
                self.assertNotIn(f".{kind}{colour} {{", scale)
        self.assertEqual(game_rewards.BADGE_HEX["blue"], "#3b82f6")
        self.assertEqual(game_rewards.BADGE_HEX["purple"], "#a855f7")

    def test_a_blue_badge_card(self):
        html = render_to_string("beetles/includes/game_badge.html",
                                {"b": {"key": "flawless", "name": "x", "how": "", "icon": "fi-rr-diamond", "earned": True, "tier": "blue"}})
        self.assertIn("scale-card-blue", html)
        self.assertIn("scale-blue", html)

    def test_the_new_badges_are_there_with_short_lines(self):
        for key in NEW:
            name, how, icon, _ = game_rewards.BADGES[key]
            self.assertLessEqual(len(name), 20)
            self.assertLessEqual(len(how), 60)
            self.assertTrue(icon.startswith("fi-rr-"))

    def test_the_moon(self):
        full = datetime(2024, 1, 25, 17, 54, tzinfo=dt_timezone.utc)   # a full moon
        self.assertLess(abs(game_rewards.moon_age(full) - game_rewards.SYNODIC_DAYS / 2), 0.6)   # mean motion: within hours
        new = datetime(2024, 1, 11, 11, 57, tzinfo=dt_timezone.utc)    # a new moon
        self.assertLess(min(game_rewards.moon_age(new), game_rewards.SYNODIC_DAYS - game_rewards.moon_age(new)), 0.6)


class NewBadgeTests(ScoringCase):
    def grid(self, mode, n, detail):
        rnd = GameRound.objects.create(player=self.user, mode=mode, items=[])
        roi = self.roi(self.t_affinis)
        ans = GameAnswer.objects.create(round=rnd, player=self.user, mode=mode, index=0, roi=roi, is_check=True,
                                        tiles=[str(roi.id)] * n, picks=[0])
        self.details = getattr(self, "details", []) + [(ans, detail)]

    def earned(self):
        """earned_badges, with the grids' points as given (set last: saving answers may score them afresh)."""
        for ans, detail in self.details:
            AnswerPoints.objects.update_or_create(answer=ans, defaults={"points": 1.0, "detail": detail})
        return game_rewards.earned_badges(self.user)

    def test_full_house(self):
        self.grid("select", 16, {"perfect": True})
        self.grid("select", 25, {"perfect": False})
        self.assertNotIn("fullhouse", self.earned())
        self.grid("select", 25, {"perfect": True})
        self.assertIn("fullhouse", self.earned())

    def test_imposter_hunter(self):
        for _ in range(49):
            self.grid("odd", 9, {"right": True, "tiles": ["right", "clear"]})
        self.grid("odd", 9, {"right": True, "tiles": ["right", "missed"]})   # one odd one left: doesn't count
        self.assertNotIn("imposters", self.earned())
        self.grid("odd", 9, {"right": True, "tiles": ["right", "clear"]})
        self.assertIn("imposters", self.earned())

    def test_fungus_farmer(self):
        for _ in range(25):
            self.answer(self.user, self.roi(self.t_affinis), AFFINIS)   # Xyleborus affinis: Xyleborini
        self.assertIn("fungus", game_rewards.earned_badges(self.user))

    def test_beat_the_machine(self):
        for i in range(5):
            roi = self.roi(self.t_affinis)
            ModelPrediction.objects.create(roi=roi, valid_species_id=self.t_ferr.valid_species_id, taxon=self.t_ferr,
                                           confidence=0.95 if i else 0.5, model_name="m", model_version="1")
            self.answer(self.user, roi, AFFINIS)
        self.assertNotIn("machine", game_rewards.earned_badges(self.user))   # once it was unsure
        roi = self.roi(self.t_affinis)
        ModelPrediction.objects.create(roi=roi, valid_species_id=self.t_ferr.valid_species_id, taxon=self.t_ferr,
                                       confidence=0.99, model_name="m", model_version="1")
        self.answer(self.user, roi, AFFINIS)
        self.assertIn("machine", game_rewards.earned_badges(self.user))

    def test_lone_wolf(self):
        wrong = dict(AFFINIS, species="ferrugineus")
        others = [self.player(f"p{i}") for i in range(5)]

        def beetle(n_wrong, n_right):
            roi = self.roi(self.t_affinis)
            for other in others[:n_wrong]:
                self.answer(other, roi, wrong)
            for other in others[n_wrong:n_wrong + n_right]:
                self.answer(other, roi, AFFINIS)
            self.answer(self.user, roi, AFFINIS)

        for _ in range(4):
            beetle(2, 0)
        beetle(2, 3)   # here most of them were right
        beetle(1, 0)   # one other player is not "most players"
        self.assertNotIn("lonewolf", game_rewards.earned_badges(self.user))
        beetle(3, 1)
        self.assertIn("lonewolf", game_rewards.earned_badges(self.user))

    def test_splitter_and_lumper(self):
        with mock.patch("beetlesgallery.beetles_app.game_trust.distinction_experts", return_value=[("species", "Xyleborus")]):
            have = game_rewards.earned_badges(self.user)
            self.assertIn("splitter", have)
            self.assertNotIn("lumpsplit", have)
            from beetlesgallery.beetles_app.models import PlayerSkill
            PlayerSkill.objects.create(player=self.user, rank="species", branch="Xyleborus", correct=10, judged=10,
                                       proven=True)
            self.assertIn("lumpsplit", game_rewards.earned_badges(self.user))

    def at(self, day, hour):
        return timezone.make_aware(datetime(day.year, day.month, day.day, hour, 30))

    def test_weekend_naturalist(self):
        saturday = date(2026, 10, 3)
        goal = game_rewards.daily_goal()
        for day in (saturday, saturday + timedelta(days=1)):
            for _ in range(goal):
                self.answer(self.user, self.roi(self.t_affinis), AFFINIS, when=self.at(day, 10))
        self.assertIn("weekend", game_rewards.earned_badges(self.user))

    def test_a_sunday_then_a_saturday_is_not_a_weekend(self):
        sunday = date(2026, 10, 4)
        for day in (sunday, sunday + timedelta(days=6)):
            for _ in range(game_rewards.daily_goal()):
                self.answer(self.user, self.roi(self.t_affinis), AFFINIS, when=self.at(day, 10))
        self.assertNotIn("weekend", game_rewards.earned_badges(self.user))

    def test_leap_beetle(self):
        self.answer(self.user, self.roi(self.t_affinis), AFFINIS, when=self.at(date(2028, 2, 28), 12))
        self.assertNotIn("leap", game_rewards.earned_badges(self.user))
        self.answer(self.user, self.roi(self.t_affinis), AFFINIS, when=self.at(date(2028, 2, 29), 12))
        self.assertIn("leap", game_rewards.earned_badges(self.user))

    def test_full_moon(self):
        full = timezone.localtime(datetime(2024, 1, 25, 17, 54, tzinfo=dt_timezone.utc)).date()
        self.answer(self.user, self.roi(self.t_affinis), AFFINIS, when=self.at(full, 12))   # by day
        self.answer(self.user, self.roi(self.t_affinis), AFFINIS, when=self.at(full - timedelta(days=7), 22))
        self.assertNotIn("fullmoon", game_rewards.earned_badges(self.user))
        self.answer(self.user, self.roi(self.t_affinis), AFFINIS, when=self.at(full, 22))
        self.assertIn("fullmoon", game_rewards.earned_badges(self.user))


class PlayEventTests(ScoringCase):
    DAY = date(2026, 9, 1)

    def at(self, hour, minute=0):
        return timezone.make_aware(datetime(self.DAY.year, self.DAY.month, self.DAY.day, hour, minute))

    def play(self, when, fields=AFFINIS):
        before = game_rewards.progress(self.user)
        self.answer(self.user, self.roi(self.t_affinis), fields, when=when)
        return [e for e in game_rewards.play_events(self.user, before) if e["kind"] == "badge"]

    def test_the_first_answer_pops_its_badge(self):
        [event] = [e for e in self.play(self.at(1)) if e["title"] == "First steps"]
        self.assertEqual(event, {"kind": "badge", "title": "First steps", "text": "Label your first beetle",
                                 "icon": "fi-rr-flag", "tier": "fair", "colour": "#ef4444"})

    def test_an_ordinary_answer_works_nothing_out(self):
        self.play(self.at(10))
        self.play(self.at(11))
        with mock.patch.object(game_rewards, "earned_badges", wraps=game_rewards.earned_badges) as working:
            self.assertEqual(self.play(self.at(12)), [])
        working.assert_not_called()

    def test_a_night_owl_pops_once_in_purple(self):
        self.play(self.at(0))   # First steps, and Night owl too
        self.DAY = date(2026, 9, 2)
        self.assertEqual(self.play(self.at(1)), [])   # already had it
        self.DAY = date(2026, 9, 3)
        events = self.play(self.at(5))
        self.assertEqual([(e["title"], e["tier"], e["colour"]) for e in events], [("Early bird", "purple", "#a855f7")])

    def test_badges_that_tell_accuracy_wait_for_the_recap(self):
        for key in ("species1", "flawless", "fullhouse", "lonewolf", "machine"):
            self.assertFalse(game_rewards.BADGES[key][3], key)
        self.assertNotIn("Species spotter", [e["title"] for e in self.play(self.at(10))])

    def test_the_late_level_check_leaves_badges_out(self):
        with mock.patch.object(game_rewards, "new_badges") as asked:
            game_rewards.play_events(self.user, game_rewards.progress(self.user), badges=False)
        asked.assert_not_called()


class IconTests(SimpleTestCase):
    def test_every_badge_icon_is_a_regular_rounded_uicon(self):
        for key, (_, _, icon, _) in game_rewards.BADGES.items():
            self.assertRegex(icon, re.compile(r"^fi-rr-[a-z0-9-]+$"), key)
