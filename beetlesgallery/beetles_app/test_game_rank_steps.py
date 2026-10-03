"""
Rank steps: new players name only the subfamily at first; the tribe, genus and species open after a few beetles,
usually within the first session. Similarity's rungs follow the same steps. Level 3 and granted players have them all.
"""
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import game_levels
from beetlesgallery.beetles_app.models import GameAnswer, GamePreference, GameRound, PlayerScore
from beetlesgallery.beetles_app.test_game import GameCase


@override_settings(GAME_RANK_UNLOCK_ANSWERS={}, GAME_ROUND_SIZE=1)
class RankStepTests(GameCase):
    def answered(self, n):
        """n earlier answers for the player (right or wrong does not matter)."""
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        roi = self.roi(self.t_affinis, validated=False)
        GameAnswer.objects.bulk_create(GameAnswer(round=rnd, player=self.user, mode="classify", index=i, roi=roi,
                                                  subfamily="Scolytinae") for i in range(n))

    def test_the_steps(self):
        self.assertEqual(game_levels.rank_unlock(1, 0), {"rank": "subfamily", "next": {"rank": "tribe", "at": 5, "needed": 5}})
        self.assertEqual(game_levels.rank_unlock(1, 5)["rank"], "tribe")
        self.assertEqual(game_levels.rank_unlock(2, 20), {"rank": "genus", "next": {"rank": "species", "at": 30, "needed": 10}})
        self.assertEqual(game_levels.rank_unlock(2, 30), {"rank": "species", "next": None})

    def test_level_three_or_a_granted_unlock_opens_every_rank(self):
        self.assertEqual(game_levels.rank_unlock(3, 0), {"rank": "species", "next": None})
        self.assertEqual(game_levels.rank_unlock(1, 0, granted_any=True)["rank"], "species")
        GamePreference.objects.create(player=self.user, granted_perks=[game_levels.CHOOSE_GAME])
        self.assertEqual(game_levels.rank_for(self.user)["rank"], "species")

    def test_level_three_counts_from_points(self):
        PlayerScore.objects.create(player=self.user, score=200, rating=0.5)
        self.assertGreaterEqual(game_levels.for_player(self.user)["level"], 3)
        self.assertEqual(game_levels.rank_for(self.user)["rank"], "species")

    @override_settings(GAME_RANK_UNLOCK_ANSWERS={"tribe": 2})
    def test_the_steps_are_a_setting(self):
        self.assertEqual(game_levels.rank_unlock(1, 2)["rank"], "tribe")

    def test_a_deeper_name_than_is_open_is_refused(self):
        self.roi(self.t_affinis)
        rnd, item = self.play("classify")
        res = self.post("game_answer", {"index": item["index"], "subfamily": "Scolytinae", "tribe": "Xyleborini"}, rnd.id)
        self.assertEqual(res.status_code, 400)
        self.assertFalse(GameAnswer.objects.filter(round=rnd).exists())
        res = self.post("game_answer", {"index": item["index"], "subfamily": "Scolytinae"}, rnd.id)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["chip"]["rank"], "subfamily")
        self.assertEqual(res.json()["chip"]["rank_next"]["needed"], 4)

    def test_a_deeper_rung_than_is_open_is_refused(self):
        self.roi(self.t_affinis)
        self.roi(self.t_ferr)
        rnd, item = self.play("pair")
        res = self.post("game_answer", {"index": item["index"], "pair_answer": "tribe"}, rnd.id)
        self.assertEqual(res.status_code, 400)
        res = self.post("game_answer", {"index": item["index"], "pair_answer": "subfamily"}, rnd.id)
        self.assertEqual(res.status_code, 200, res.content)

    def test_the_next_rank_opens_with_a_toast_the_moment_it_is_reached(self):
        self.answered(4)
        self.roi(self.t_affinis)
        rnd, item = self.play("classify")
        self.assertEqual(self.post("game_start", {"mode": "classify"}).json()["chip"]["rank"], "subfamily")
        res = self.post("game_answer", {"index": item["index"], "subfamily": "Scolytinae"}, rnd.id).json()
        self.assertEqual(res["chip"]["rank"], "tribe")
        self.assertIn({"kind": "rank", "title": "Tribe unlocked", "text": "You can now name the tribe too."}, res["events"])

    def test_skips_do_not_count(self):
        self.answered(4)
        GameAnswer.objects.filter(player=self.user).update(skipped=True)
        self.assertEqual(game_levels.rank_for(self.user)["rank"], "subfamily")

    def test_the_pages_say_so(self):
        self.client.force_login(self.user)
        play = self.client.get(reverse("game_play", args=["classify"]))
        self.assertContains(play, 'data-testid="rank-lock"')
        self.assertContains(self.client.get(reverse("game_unlocks")), "The tribe opens after 5 more beetles.")
        self.assertContains(self.client.get(reverse("game_how")), "The tribe opens after 5 beetles, the genus after 15")
        self.answered(30)
        self.assertNotContains(self.client.get(reverse("game_unlocks")), 'data-testid="rank-next"')
