"""Accuracy standing, badges, sharing, review links, levels and safe concurrent scoring."""
from django.urls import reverse

from beetlesgallery.beetles_app import game_board, game_levels, game_rewards, game_scoring
from beetlesgallery.beetles_app.models import AnswerPoints, GameReport, PlayerScore
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase


class StandingTests(ScoringCase):
    def test_where_you_stand_among_players(self):
        for i, acc in enumerate([0.3, 0.5, 0.6, 0.7, 0.9]):
            PlayerScore.objects.create(player=self.player(f"p{i}"), accuracy=acc, judged=20)
        PlayerScore.objects.create(player=self.user, accuracy=0.95, judged=20)
        s = game_board.accuracy_standing(self.user)
        self.assertEqual(s["players"], 6)
        self.assertEqual(s["me"]["percentile"], 100)
        self.assertEqual((s["me"]["tier"], s["me"]["tier_name"]), ("excellent", "Excellent"))
        self.assertEqual(sum(b["count"] for b in s["bins"]), 6)
        self.assertAlmostEqual(s["average"], (0.3 + 0.5 + 0.6 + 0.7 + 0.9 + 0.95) / 6)

    def test_not_enough_answers_yet(self):
        s = game_board.accuracy_standing(self.user)
        self.assertIsNone(s["me"])
        self.assertEqual(s["needed"], 10)

    def test_the_home_shows_it_and_a_qr_code_to_share(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_home")).content.decode()
        self.assertIn('data-testid="accuracy-standing"', page)
        self.assertIn('data-testid="share-qr"', page)
        from django.conf import settings
        self.assertIn(settings.SITE_URL.rstrip("/") + "/game/", page)   # the public address, not this server's


class BadgeTests(ScoringCase):
    def test_there_are_many_badges_and_hard_ones(self):
        self.assertGreaterEqual(len(game_rewards.BADGES), 30)
        for key in ("streak365", "flawless", "genera50", "king", "tenthousand"):
            self.assertIn(key, game_rewards.BADGES)

    def test_a_comeback_and_a_flawless_run(self):
        roi = self.roi(self.t_affinis)
        self.answer(self.user, roi, AFFINIS, retry=True)
        for _ in range(20):
            self.answer(self.user, self.roi(self.t_affinis), AFFINIS)
        have = game_rewards.earned_badges(self.user)
        self.assertIn("comeback", have)
        self.assertIn("flawless", have)

    def test_the_profile_goes_back_to_the_game_home(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_profile", args=[self.user.id])).content.decode()
        self.assertIn(f'href="{reverse("game_home")}"', page)
        self.assertNotIn("&larr; Leaderboard", page)


class LevelTests(ScoringCase):
    def test_level_four_has_a_proper_name(self):
        self.assertEqual(game_levels.LEVELS[3][2], "Teneral")

    def test_scale_colours(self):
        from django.template.loader import render_to_string
        colours = {lvl: render_to_string("beetles/includes/game_level_badge.html", {"level": lvl}) for lvl in (1, 3, 5, 7, 9, 10)}
        for lvl, colour in ((1, "scale-chip-none"), (3, "scale-chip-fair"), (5, "scale-chip-decent"), (7, "scale-chip-good"),
                            (9, "scale-chip-great"), (10, "scale-chip-excellent")):
            self.assertIn(colour, colours[lvl])


class ReviewPageTests(ScoringCase):
    def test_links_go_to_the_annotation_page_with_the_roi_open(self):
        roi = self.roi(self.t_affinis)
        GameReport.objects.create(roi=roi, reporter=self.user, reason="bad_box")
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("game_review")).content.decode()
        self.assertIn(f'?image={roi.image_asset_id}&roi={roi.id}', page)
        self.assertIn('data-testid="to-annotation"', page)
        self.assertIn(f'href="{reverse("game_home")}"', page)
        self.assertGreaterEqual(page.count('data-testid="section"'), 3)

    def test_the_annotation_page_opens_the_roi_from_the_link(self):
        from django.conf import settings
        source = (settings.BASE_DIR / "beetlesgallery" / "templates" / "beetles" / "tool_annotate.html").read_text()
        self.assertIn("deepLink.get('roi')", source)


class ScoreRaceTests(ScoringCase):
    def test_new_answers_add_to_the_total_in_one_update(self):
        PlayerScore.objects.create(player=self.user, score=10.0)
        a = self.answer(self.user, self.roi(self.t_affinis), AFFINIS)
        game_scoring.score_new_answer(a)
        score = PlayerScore.objects.get(player=self.user)
        self.assertEqual((score.score, score.viewed), (10.0 + AnswerPoints.objects.get(answer=a).points, 1))

    def test_the_total_never_goes_below_zero(self):
        PlayerScore.objects.create(player=self.user, score=1.0)
        wrong = dict(AFFINIS, subfamily="Platypodinae", tribe="Platypodini", genus="Platypus", species="cylindrus")
        game_scoring.score_new_answer(self.answer(self.user, self.roi(self.t_affinis), wrong))
        self.assertEqual(PlayerScore.objects.get(player=self.user).score, 0.0)


class ToggleTests(ScoringCase):
    def test_the_game_types_are_identification_and_similarity(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        self.assertIn(">Naming<", page)
        self.assertIn(">Similarity<", page)
        self.assertNotIn(">Ties<", page)
        self.assertIn('data-reason="bad_image" role="menuitem">Bad photo<', page)


class BackgroundTests(ScoringCase):
    def test_other_players_are_rescored_on_the_worker_in_production(self):
        from unittest import mock

        from django.test import override_settings

        from beetlesgallery.beetles_app import game
        from beetlesgallery.beetles_app.models import GameAnswer, GameRound
        target = self.roi(self.t_affinis, validated=False)
        early = self.player("early")
        self.answer(early, target, AFFINIS, check=False)
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=0, roi=target, is_check=False, **AFFINIS)
        with override_settings(GAME_RECOMPUTE_IN_BACKGROUND=True), \
                mock.patch("beetlesgallery.beetles_app.tasks.recompute_game_players_task.apply_async") as queued, \
                self.captureOnCommitCallbacks(execute=True):
            game.finish_round(rnd)
        self.assertEqual(queued.call_args.kwargs["args"], [[early.id]])


class LayoutTests(ScoringCase):
    def test_checked_beetles_live_in_the_history_linked_from_the_game_home(self):
        self.client.force_login(self.user)
        home = self.client.get(reverse("game_home")).content.decode()
        self.assertIn('data-testid="history-link"', home)
        self.assertIn(reverse("game_history"), home)
        self.assertNotIn(reverse("game_checked"), self.client.get(reverse("game_leaderboard")).content.decode())
        self.assertRedirects(self.client.get(reverse("game_checked")), reverse("game_history") + "?tab=checked")

    def test_game_buttons_use_the_lighter_house_grey(self):
        from django.conf import settings
        root = settings.BASE_DIR / "beetlesgallery" / "templates" / "beetles"
        for name in ("game_home.html", "game_play.html"):
            source = (root / name).read_text()
            self.assertNotIn("text-white bg-gray-700", source, name)
            self.assertNotIn("bg-gray-700 hover:bg-gray-800", source, name)
