"""One game: a mixed feed of Name That Beetle and Family Ties, a choice of game from level 2, and focus in the feed."""
import json
from unittest import mock

from django.urls import reverse

from beetlesgallery.beetles_app import game, game_levels
from beetlesgallery.beetles_app.models import GameAnswer, GamePreference, GameRound, PlayerScore
from beetlesgallery.beetles_app.test_game import AFFINIS, GameCase


class MixedFeedCase(GameCase):
    def setUp(self):
        super().setUp()
        for taxon in (self.t_affinis, self.t_ferr, self.t_plat) * 4:
            self.roi(taxon)
            self.roi(taxon, validated=False)

    def level(self, score, rating=0.0):
        PlayerScore.objects.update_or_create(player=self.user, defaults={"score": score, "rating": rating})

    def modes(self, rnd):
        return [i["mode"] for i in rnd.items]

    def post(self, name, body):
        self.client.force_login(self.user)
        return self.client.post(reverse(name), json.dumps(body), content_type="application/json")


class MixTests(MixedFeedCase):
    def test_every_item_carries_its_game(self):
        rnd = game.start_round(self.user, "mixed", size=10)
        self.assertEqual(rnd.mode, "mixed")
        self.assertTrue(set(self.modes(rnd)) <= {"classify", "pair"})
        for it in rnd.items:
            self.assertEqual(bool(it.get("b")), it["mode"] == "pair")

    def test_beginners_get_mostly_family_ties_and_experts_mostly_naming(self):
        GamePreference.objects.create(player=self.user, granted_perks=["choose_game", "identification"])   # plays both
        asked = {}

        def fake(mode):
            def build(player, n, fresh_only=False):
                asked[mode] = n
                return [{"a": f"{mode}{i}", "b": "x" if mode == "pair" else None, "check": False} for i in range(n)]
            return build

        with mock.patch.object(game, "build_pair_items", fake("pair")), \
                mock.patch.object(game, "build_classify_items", fake("classify")):
            with mock.patch.object(game_levels, "pair_share", return_value=1.0):
                game.build_mixed_items(self.user, 6)
                self.assertEqual(asked, {"pair": 6})
            asked.clear()
            with mock.patch.object(game_levels, "pair_share", return_value=0.0):
                game.build_mixed_items(self.user, 6)
                self.assertEqual(asked, {"classify": 6})

    def test_answers_are_saved_with_the_game_of_their_item(self):
        self.client.force_login(self.user)
        data = self.post("game_start", {"mode": "mixed"}).json()
        item = data["item"]
        body = {"index": item["index"]}
        body.update(AFFINIS if item["mode"] == "classify" else {"pair_answer": "genus"})
        res = self.client.post(reverse("game_answer", args=[data["round"]]), json.dumps(body), content_type="application/json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(GameAnswer.objects.get().mode, item["mode"])

    def test_beetles_nobody_could_name_come_back_in_family_ties(self):
        other = self.make_user("other")
        stuck = self.roi(self.t_affinis, validated=False)
        rnd = GameRound.objects.create(player=other, mode="classify", items=[])
        GameAnswer.objects.create(round=rnd, player=other, mode="classify", index=0, roi=stuck, is_check=False,
                                  subfamily="Scolytinae", genus="Xyleborus")   # stopped at the genus
        self.assertEqual(list(game.stuck_rois(self.user, game.open_rois())), [stuck])
        named = self.roi(self.t_affinis, validated=False)
        GameAnswer.objects.create(round=rnd, player=other, mode="classify", index=1, roi=named, is_check=False, **AFFINIS)
        self.assertNotIn(named, game.stuck_rois(self.user, game.open_rois()))

    def make_user(self, name):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.create_user(name, password="pw")


class ChooseGameTests(MixedFeedCase):
    def test_level_one_plays_similarity_only(self):
        res = self.post("game_prefs", {"play_mode": "both"})
        self.assertEqual(res.status_code, 403)
        self.assertIn("level 2", res.json()["error"])
        self.assertEqual(game.play_mode(self.user), "pair")
        self.assertEqual(set(self.modes(game.start_round(self.user, "mixed", size=6))), {"pair"})

    def test_reaching_level_two_mid_game_brings_odd_one_out_in_at_once(self):
        from beetlesgallery.beetles_app import game_scoring
        self.level(49)
        data = self.post("game_start", {"mode": "mixed"}).json()
        self.assertFalse(data["prefs"]["choose_game"])
        body = {"index": data["item"]["index"], "pair_answer": "genus"}
        with mock.patch.object(game_scoring, "score_new_answer", side_effect=lambda record: self.level(60)), \
                mock.patch.object(game_levels, "pair_share", return_value=0.0), \
                mock.patch.object(game, "finish_round"):   # it would re-score from the real answers, undoing the 60
            res = self.client.post(reverse("game_answer", args=[data["round"]]), json.dumps(body),
                                   content_type="application/json").json()
        self.assertIn("level", [e["kind"] for e in res["events"]])
        self.assertTrue(res["prefs"]["choose_game"])                 # the toolbar unlocks straight away
        self.assertNotEqual(res["round"], data["round"])              # and a fresh batch, picked under the new rules
        self.assertIn("odd", self.modes(GameRound.objects.get(pk=res["round"])))

    def test_level_two_unlocks_odd_one_out_and_defaults_to_both(self):
        self.level(60)
        self.assertEqual(game.play_mode(self.user), "both")

    def test_from_level_two_a_player_can_pick_one_game(self):
        self.level(60)
        self.assertEqual(self.post("game_prefs", {"play_mode": "pair"}).status_code, 200)
        self.assertEqual(set(self.modes(game.start_round(self.user, "mixed", size=6))), {"pair"})
        self.post("game_prefs", {"play_mode": "odd"})
        self.assertEqual(set(self.modes(game.start_round(self.user, "mixed", size=3))), {"odd"})
        res = self.post("game_prefs", {"play_mode": "classify"})   # Identification is level 4 now
        self.assertEqual((res.status_code, res.json()["error"]), (403, "Naming unlocks at level 4."))

    def test_a_chosen_game_is_never_topped_up_with_the_other(self):
        self.level(60)
        self.post("game_prefs", {"play_mode": "pair"})
        with mock.patch.object(game, "build_pair_items", return_value=[]):   # no pairs at all: nothing, never Odd One Out (#604)
            self.assertIsNone(game.start_round(self.user, "mixed", size=6))
        for _ in range(5):
            self.assertEqual(set(self.modes(game.start_round(self.user, "mixed", size=10))), {"pair"})

    def test_the_choice_lapses_if_the_level_drops(self):
        GamePreference.objects.create(player=self.user, play_mode="classify")
        self.assertEqual(game.play_mode(self.user), "pair")   # back to Similarity only

    def test_start_tells_the_feed_what_is_unlocked(self):
        prefs = self.post("game_start", {"mode": "mixed"}).json()["prefs"]
        self.assertEqual((prefs["play_mode"], prefs["choose_game"], prefs["choose_game_level"]), ("pair", False, 2))
        self.assertEqual([(r["rank"], r["unlocked"], r["level"]) for r in prefs["focus_ranks"]],
                         [("subfamily", False, 3), ("tribe", False, 4), ("genus", False, 5)])

    def test_a_fresh_start_applies_a_new_choice_straight_away(self):
        # granted rather than a made-up score: finishing the round recomputes the score from real answers
        GamePreference.objects.create(player=self.user, granted_perks=["choose_game"])
        first = self.post("game_start", {"mode": "mixed"}).json()["round"]
        self.post("game_prefs", {"play_mode": "pair"})
        again = self.post("game_start", {"mode": "mixed", "fresh": True}).json()
        self.assertNotEqual(again["round"], first)
        self.assertEqual(again["item"]["mode"], "pair")


class FocusInFeedTests(MixedFeedCase):
    def test_focus_from_the_feed_needs_its_level(self):
        self.level(60)
        self.assertEqual(self.post("game_prefs", {"focus_rank": "subfamily", "focus_value": "Scolytinae"}).status_code, 403)
        self.level(200, 0.4)
        res = self.post("game_prefs", {"focus_rank": "subfamily", "focus_value": "scolytinae"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["prefs"]["focus"], {"rank": "subfamily", "value": "Scolytinae"})
        self.assertEqual(self.post("game_prefs", {"focus_rank": ""}).json()["prefs"]["focus"], None)

    def test_a_made_up_taxon_is_refused(self):
        self.level(200, 0.4)
        self.assertEqual(self.post("game_prefs", {"focus_rank": "subfamily", "focus_value": "Nope"}).status_code, 400)

    def test_the_play_screen_has_the_toggle_and_the_focus_button(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        for marker in ('data-play="both"', 'data-play="classify"', 'data-play="pair"', 'id="focus-btn"', 'id="focus-sheet"'):
            self.assertIn(marker, page)


class TopLevelTests(GameCase):
    def test_there_are_ten_levels_and_the_top_is_hard(self):
        self.assertEqual(len(game_levels.LEVELS), 10)
        points, rating, name, _ = game_levels.LEVELS[-1]
        self.assertEqual(name, "King of Bark and Ambrosia")
        self.assertGreaterEqual(points, 20000)
        self.assertGreaterEqual(rating, 0.9)


class BadgeTests(GameCase):
    def test_the_leaderboard_shows_level_badges_and_new_species_finders(self):
        from beetlesgallery.beetles_app.models import SpeciesDiscovery
        PlayerScore.objects.create(player=self.user, score=30000, rating=0.95, viewed=5)
        SpeciesDiscovery.objects.create(player=self.user, roi=self.roi(self.t_affinis), genus="Xyleborus", species="affinis")
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_leaderboard"), {"period": "all"}).content.decode()
        self.assertIn('data-testid="level-badge"', page)
        self.assertIn("fi-rr-crown", page)              # level 10
        self.assertIn('data-testid="finder-badge"', page)
