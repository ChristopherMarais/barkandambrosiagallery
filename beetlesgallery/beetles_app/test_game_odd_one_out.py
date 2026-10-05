"""
Odd One Out (#369): four (or six) beetles, all but one share a name at one rank, and the player picks the odd one.
It opens at level 2, Identification moves to level 4, and players who had Identification keep it.
"""
import importlib
from types import SimpleNamespace
from unittest import mock

from django.apps import apps
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import game, game_feedback, game_levels, game_scoring
from beetlesgallery.beetles_app.models import (
    AnswerPoints, GameAnswer, GamePreference, GameReport, GameRound, ModelPrediction, PlayerScore,
)
from beetlesgallery.beetles_app.test_game import AFFINIS, GameCase


class OddCase(GameCase):
    def setUp(self):
        super().setUp()
        self.rois = {}
        for taxon in (self.t_affinis, self.t_ferr, self.t_plat):
            self.rois[taxon.species] = [self.roi(taxon) for _ in range(4)]
        self.client.force_login(self.user)

    def level(self, score, rating=0.0):
        PlayerScore.objects.update_or_create(player=self.user, defaults={"score": score, "rating": rating})

    def name_at(self, roi_id, rank):
        roi = game.Beetles.objects.select_related("taxon").get(id=roi_id)
        return game.lineage(roi.taxon, rank)[rank]

    def odd_round(self, rank="species"):
        """A round of Odd One Out at one rank, started through the API like the feed does."""
        self.level(60)
        with mock.patch.object(game, "_relation_order", return_value=[rank]):
            rnd, item = self.play("odd")
        return rnd, item

    def answer(self, rnd, item, **body):
        return self.post("game_answer", dict(body, index=item["index"]), rnd.id)


class LadderTests(OddCase):
    def test_similarity_first_then_odd_one_out_then_identification(self):
        self.assertEqual(game_levels.games(game_levels.describe(0, 0)["perks"]), ["pair"])
        self.assertEqual(game_levels.games(game_levels.describe(60, 0)["perks"]), ["pair", "odd"])
        self.assertEqual(game_levels.games(game_levels.describe(450, 0.55)["perks"]), ["pair", "odd", "classify"])
        self.assertEqual((game_levels.game_level("odd"), game_levels.game_level("classify")), (2, 4))

    def test_players_who_had_identification_keep_it_without_every_rank_opening(self):
        self.level(60)
        GamePreference.objects.create(player=self.user, kept_perks=["identification"])
        info = game_levels.for_player(self.user)
        self.assertIn("classify", game_levels.games(info["perks"]))
        self.assertNotIn("granted", info)   # a kept unlock is not a superuser's grant, which opens every rank

    def test_the_migration_keeps_identification_for_whoever_could_play_it(self):
        from django.contrib.auth import get_user_model
        users = get_user_model().objects
        veteran, newcomer, namer, granted = (users.create_user(n, password="pw") for n in ("vet", "new", "namer", "granted"))
        PlayerScore.objects.create(player=veteran, score=80)
        PlayerScore.objects.create(player=newcomer, score=20)
        rnd = GameRound.objects.create(player=namer, mode="classify", items=[])
        GameAnswer.objects.create(round=rnd, player=namer, mode="classify", index=0, roi=self.rois["affinis"][0], **AFFINIS)
        GamePreference.objects.create(player=granted, granted_perks=["choose_game"])
        importlib.import_module("beetlesgallery.beetles_app.migrations.0041_odd_one_out").keep_identification(apps, None)
        kept = dict(GamePreference.objects.values_list("player__username", "kept_perks"))
        self.assertEqual((kept["vet"], kept["namer"]), (["identification"], ["identification"]))
        self.assertNotIn("new", kept)
        self.assertEqual(GamePreference.objects.get(player=granted).granted_perks, ["choose_game", "identification"])

    def test_the_toolbar_learns_which_games_are_open(self):
        prefs = self.post("game_start", {"mode": "mixed"}).json()["prefs"]
        self.assertEqual([(g["key"], g["unlocked"], g["level"]) for g in prefs["games"]],
                         [("pair", True, 1), ("odd", False, 2), ("classify", False, 4)])
        res = self.post("game_prefs", {"play_mode": "odd"})
        self.assertEqual((res.status_code, res.json()["error"]), (403, "Odd One Out unlocks at level 2."))

    def test_from_level_two_the_mix_is_similarity_and_odd_one_out(self):
        self.level(60)
        modes = set()
        for _ in range(3):
            rnd = game.start_round(self.user, "mixed", size=8)
            modes |= {it["mode"] for it in rnd.items}
        self.assertNotIn("classify", modes)
        self.assertIn("odd", modes)


class BuildTests(OddCase):
    def check(self, item, tiles=4):
        self.assertEqual(item["mode"], "odd")
        self.assertEqual(len(item["tiles"]), tiles)
        self.assertEqual(len(set(item["tiles"])), tiles)
        rank, group = item["rank"], item["group"]
        names = {t: self.name_at(t, rank) for t in item["tiles"]}
        self.assertEqual([t for t, n in names.items() if n.lower() != group[rank].lower()], [item["a"]])
        self.assertTrue(game.Beetles.objects.get(id=item["a"]).bbox_is_validated)   # the odd one is always known
        photos = game.Beetles.objects.filter(id__in=item["tiles"]).values_list("image_asset_id", flat=True)
        self.assertEqual(len(set(photos)), tiles)   # no two from one photo

    def test_every_item_has_exactly_one_odd_one_at_its_rank(self):
        self.level(60)
        for rank in game.RANKS:
            with self.subTest(rank=rank), mock.patch.object(game, "_relation_order", return_value=[rank]):
                items = game.build_odd_items(self.user, 2)
                self.assertTrue(items)
                for item in items:
                    self.assertEqual(item["rank"], rank)
                    self.check(item)

    def test_higher_levels_get_six(self):
        for taxon in (self.t_affinis, self.t_ferr, self.t_plat):   # enough for a group of five whichever comes first
            for _ in range(2):
                self.roi(taxon)
        self.level(900, 0.65)   # level 5
        with mock.patch.object(game, "_relation_order", return_value=["subfamily"]):
            self.check(game.build_odd_items(self.user, 1)[0], tiles=6)

    def test_the_rank_never_goes_past_the_players_open_ranks(self):
        self.level(60)
        with override_settings(GAME_RANK_UNLOCK_ANSWERS={"tribe": 50, "genus": 50, "species": 50}):
            ranks = {it["rank"] for _ in range(3) for it in game.build_odd_items(self.user, 3)}
        self.assertEqual(ranks, {"subfamily"})

    def predict(self, roi, conf, taxon=None):
        taxon = taxon or self.t_affinis
        ModelPrediction.objects.create(roi=roi, valid_species_id=taxon.valid_species_id, taxon=taxon,
                                       confidence=conf, model_name="m", model_version="1")

    def test_every_grid_has_a_sure_and_an_unsure_ai_beetle(self):
        self.level(60)
        # only Xyleborus affinis has enough beetles to be the group; one other species each can be the odd one
        for roi in self.rois["ferrugineus"][1:] + self.rois["cylindrus"][1:]:
            roi.delete()
        sure, unsure = self.roi(self.t_affinis, validated=False), self.roi(self.t_affinis, validated=False)
        self.predict(sure, 0.95)
        self.predict(unsure, 0.3)
        for target in (0.2, 0.8):   # easy and hard rounds alike
            with self.subTest(target=target), mock.patch.object(game, "_relation_order", return_value=["species"]), \
                    mock.patch.object(game, "target_difficulty", return_value=target):
                item = game.build_odd_items(self.user, 1)[0]
                self.assertIn(str(sure.id), item["tiles"])
                self.assertIn(str(unsure.id), item["tiles"])
                validated = game.Beetles.objects.filter(id__in=item["tiles"], bbox_is_validated=True)
                self.assertEqual(validated.count(), 2)   # the odd one and one of the rest

    def test_without_both_kinds_of_ai_beetle_there_is_no_grid(self):
        self.level(60)
        self.predict(self.roi(self.t_affinis, validated=False), 0.95)   # sure ones only, nothing unsure
        with mock.patch.object(game, "_relation_order", return_value=["species"]):
            self.assertEqual(game.build_odd_items(self.user, 1), [])
        with override_settings(GAME_GRID_REQUIRE_AI=False), mock.patch.object(game, "_relation_order", return_value=["species"]):
            self.assertTrue(game.build_odd_items(self.user, 1))

    def test_an_odd_one_the_player_has_been_shown_is_never_used_again(self):
        rnd, item = self.odd_round()
        odd = rnd.items[item["index"]]["a"]
        self.answer(rnd, item, skipped=True)
        self.assertIn(odd, {str(i) for i in game.revealed_ids(self.user)})


@override_settings(GAME_POINTS_PARTICIPATION=0.0)
class AnswerTests(OddCase):
    def pick(self, rnd, item, odd):
        tiles = rnd.items[item["index"]]["tiles"]
        a = rnd.items[item["index"]]["a"]
        return tiles.index(a) if odd else next(i for i, t in enumerate(tiles) if t != a)

    def test_a_correct_pick_is_saved_scored_and_celebrated(self):
        rnd, item = self.odd_round("species")
        self.assertEqual((item["mode"], item["rank"], len(item["images"])), ("odd", "species", 4))
        res = self.answer(rnd, item, pick=self.pick(rnd, item, odd=True))
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        ans = GameAnswer.objects.get()
        self.assertEqual((ans.mode, ans.grid_rank, ans.is_check, ans.roi_id, ans.correct_species),
                         ("odd", "species", True, ans.roi_b_id, True))
        self.assertEqual(len(ans.tiles), 4)
        self.assertEqual(data["celebrate"], "validated")
        self.assertEqual(data["reveal"]["odd"], self.pick(rnd, item, odd=True))
        self.assertEqual(data["reveal"]["rank"], "species")
        self.assertGreater(ans.points.points, 0)

    def test_a_wrong_pick_costs_more_than_a_correct_one_earns_and_skip_earns_a_little(self):
        rnd, item = self.odd_round("species")
        self.answer(rnd, item, pick=self.pick(rnd, item, odd=False))
        wrong = GameAnswer.objects.get()
        self.assertEqual((wrong.is_check, wrong.correct_species), (True, False))
        worth = game_scoring.odd_base(wrong)
        self.assertAlmostEqual(wrong.points.points, -1.25 * worth)
        rnd2, item2 = self.odd_round("species")   # the same feed, carrying on
        self.answer(rnd2, item2, skipped=True)
        skipped = GameAnswer.objects.get(round=rnd2, index=item2["index"])
        self.assertEqual((skipped.points.points, skipped.points.basis), (0.25, AnswerPoints.Basis.UNSURE))

    def test_a_pick_must_be_one_of_the_beetles_shown(self):
        rnd, item = self.odd_round()
        for bad in (4, -1, True, "1", None):
            with self.subTest(pick=bad):
                self.assertEqual(self.answer(rnd, item, pick=bad).status_code, 400)
        self.assertFalse(GameAnswer.objects.exists())

    def test_any_photo_of_the_grid_can_be_reported_from_the_feed(self):
        rnd, item = self.odd_round()
        third = rnd.items[item["index"]]["tiles"][2]
        res = self.post("game_report_item", {"round": str(rnd.id), "index": item["index"], "image": 2, "reason": "bad_box"})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(str(GameReport.objects.get().roi_id), third)

    def test_a_species_round_gives_the_rest_away_too(self):
        rnd, item = self.odd_round("species")
        self.answer(rnd, item, skipped=True)
        tiles = set(rnd.items[item["index"]]["tiles"])
        self.assertTrue(tiles <= {str(i) for i in game.revealed_ids(self.user)})

    def test_other_ranks_keep_the_rest_scorable_and_the_review_names_them_only_that_far(self):
        rnd, item = self.odd_round("genus")
        self.answer(rnd, item, pick=self.pick(rnd, item, odd=False))
        tiles = set(rnd.items[item["index"]]["tiles"])
        self.assertEqual(len(tiles & {str(i) for i in game.revealed_ids(self.user)}), 2)   # the pick and the odd one
        game.finish_round(rnd)
        rest = [s for s in game_feedback.round_feedback(rnd)["items"][0]["sides"] if not (s["picked"] or s["odd"])]
        self.assertEqual(len(rest), 2)
        for side in rest:
            self.assertEqual((side["label"]["species"], bool(side["label"]["genus"])), ("", True))

    def test_the_round_review_shows_the_grid_and_the_odd_one(self):
        rnd, item = self.odd_round("species")
        self.answer(rnd, item, pick=self.pick(rnd, item, odd=False))
        game.finish_round(rnd)
        feedback = game_feedback.round_feedback(rnd)["items"][0]
        self.assertEqual(len(feedback["sides"]), 4)
        self.assertEqual(sum(s["odd"] for s in feedback["sides"]), 1)
        self.assertEqual(sum(s["picked"] for s in feedback["sides"]), 1)
        self.assertEqual(feedback["truth_odd"]["rank"], "species")
        self.assertEqual(feedback["losses"]["kind"], "odd")
        self.assertEqual(self.client.get(reverse("game_round_review", args=[rnd.id])).status_code, 200)


@override_settings(GAME_POINTS_PARTICIPATION=0.0)
class UnvalidatedPickTests(OddCase):
    def odd_answer(self, picked, group_taxon, odd_taxon, rank="species"):
        rnd = GameRound.objects.create(player=self.user, mode="odd", items=[])
        odd = self.roi(odd_taxon)
        return GameAnswer.objects.create(
            round=rnd, player=self.user, mode="odd", index=0, roi=picked, roi_b=odd, is_check=False,
            tiles=[str(picked.id), str(odd.id)], grid_rank=rank, grid_group=game.lineage(group_taxon, rank))

    def test_agreement_that_it_does_not_belong_earns_and_disagreement_never_costs(self):
        picked = self.roi(self.t_ferr, validated=False)
        ans = self.odd_answer(picked, self.t_affinis, self.t_plat)
        judges = SimpleNamespace(weight=lambda *a: 1.0, trust=None)
        agree = [(999, {"species": "Xyleborus ferrugineus"})]
        disagree = [(999, {"species": "Xyleborus affinis"})]
        points, detail = game_scoring.odd_consensus(ans, agree, judges, {})
        self.assertAlmostEqual(points, 0.6 * game_scoring.odd_base(ans) * 0.5)
        self.assertEqual(game_scoring.odd_consensus(ans, disagree, judges, {})[0], 0.0)

    def test_a_pick_on_a_beetle_validated_later_is_scored_against_the_truth(self):
        picked = self.roi(self.t_ferr, validated=False)
        ans = self.odd_answer(picked, self.t_affinis, self.t_plat)
        game_scoring.recompute([self.user.id])
        self.assertEqual(AnswerPoints.objects.get(answer=ans).basis, AnswerPoints.Basis.CONSENSUS)
        game.Beetles.objects.filter(id=picked.id).update(bbox_is_validated=True)
        game_scoring.recompute([self.user.id])
        ans.refresh_from_db()
        self.assertEqual((ans.validated_later, ans.correct_species), (True, True))
        self.assertEqual(AnswerPoints.objects.get(answer=ans).basis, AnswerPoints.Basis.TRUTH)

    def test_a_pick_is_not_a_name(self):
        picked = self.roi(self.t_ferr, validated=False)
        self.odd_answer(picked, self.t_affinis, self.t_plat)
        self.assertEqual(game.consensus(roi_ids=[picked.id]), [])
        self.assertNotIn(picked, game.peer_rois(self.make_other(), game.open_rois()))

    def make_other(self):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.create_user("other", password="pw")


class PageTests(OddCase):
    def test_the_play_page_has_the_game(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        for marker in ('data-play="odd"', 'data-testid="odd-panel"', 'id="odd-prompt"'):
            self.assertIn(marker, page)
        self.assertEqual(self.client.get(reverse("game_play", args=["odd"])).status_code, 200)

    def test_how_it_works_explains_it(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_how")).content.decode()
        self.assertIn('data-testid="how-odd"', page)
        self.assertIn("Odd One Out, and choosing your game, unlock at level 2", page)
