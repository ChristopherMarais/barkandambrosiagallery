"""Beetles got wrong come back in a later sitting, in an easier game first, then in the game they were missed in (#490)."""
import json
from datetime import timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game, game_relearn, game_scoring as scoring
from beetlesgallery.beetles_app.models import GameAnswer, GamePreference, GameRound
from beetlesgallery.beetles_app.test_game import AFFINIS, FERR
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase

AGO = timedelta(hours=3)   # a sitting ago


class RelearnCase(ScoringCase):
    def setUp(self):
        super().setUp()
        self.beetles = {t: [self.roi(t) for _ in range(4)] for t in (self.t_affinis, self.t_ferr, self.t_plat)}
        self.x = self.beetles[self.t_affinis][0]

    def unlock_all(self):
        GamePreference.objects.update_or_create(player=self.user, defaults={"granted_perks": ["all"]})

    def grid(self, mode, tiles, rank, group, roi, roi_b=None, picks=(), retry=False, when=None):
        """A scored grid answer, saved the way game_answer saves it."""
        rnd = GameRound.objects.create(player=self.user, mode=mode, items=[])
        ans = GameAnswer(round=rnd, player=self.user, mode=mode, index=0, roi=roi, roi_b=roi_b, is_check=True,
                         is_retry=retry, tiles=[str(t.id) for t in tiles], grid_rank=rank, grid_group=group,
                         picks=list(picks))
        if mode == "odd":
            setattr(ans, f"correct_{rank}", game.score_odd(roi.taxon, rank, group)[rank])
        else:
            setattr(ans, f"correct_{rank}", game.score_select(tiles, picks, rank, group)["perfect"])
        ans.save()
        if when:
            GameAnswer.objects.filter(pk=ans.pk).update(answered_at=when)
        return ans

    def miss(self, roi=None, when=None, retry=False):
        """An Identification answer that got ``roi`` (affinis) wrong at the species."""
        return self.answer(self.user, roi or self.x, FERR, retry=retry, when=when or timezone.now() - AGO)


class WhenTests(RelearnCase):
    def test_a_mistake_waits_for_a_later_sitting(self):
        self.miss(when=timezone.now() - timedelta(minutes=20))
        self.answer(self.user, self.roi(self.t_plat), {"subfamily": "Platypodinae"},
                    when=timezone.now() - timedelta(minutes=2))
        self.assertEqual(game_relearn.due(self.user), {})   # still the same sitting

    def test_after_a_break_it_is_due(self):
        self.miss(when=timezone.now() - timedelta(hours=2))
        self.answer(self.user, self.roi(self.t_plat), {"subfamily": "Platypodinae"},
                    when=timezone.now() - timedelta(minutes=2))   # a new sitting began after the break
        self.assertEqual(list(game_relearn.due(self.user)), [self.x.id])

    @override_settings(GAME_SESSION_GAP_MINUTES=5)
    def test_the_break_is_a_setting(self):
        self.miss(when=timezone.now() - timedelta(minutes=10))
        self.assertEqual(list(game_relearn.due(self.user)), [self.x.id])

    def test_a_right_answer_in_the_game_it_was_missed_in_ends_it(self):
        self.miss(when=timezone.now() - timedelta(days=2))
        self.answer(self.user, self.x, AFFINIS, retry=True, when=timezone.now() - timedelta(days=1))
        self.assertEqual(game_relearn.due(self.user), {})

    @override_settings(GAME_RETRY_MAX=2)
    def test_at_most_a_few_tries(self):
        self.miss(when=timezone.now() - timedelta(days=3))
        self.miss(when=timezone.now() - timedelta(days=2), retry=True)
        self.assertIn(self.x.id, game_relearn.due(self.user))
        self.miss(when=timezone.now() - timedelta(days=1), retry=True)
        self.assertEqual(game_relearn.due(self.user), {})

    def test_a_beetle_no_longer_validated_does_not_come_back(self):
        self.miss()
        type(self.x).objects.filter(pk=self.x.pk).update(bbox_is_validated=False)
        self.assertEqual(game_relearn.due(self.user), {})


class EveryGameTests(RelearnCase):
    def test_a_wrong_similarity_answer(self):
        partner = self.beetles[self.t_ferr][0]   # same genus
        self.answer(self.user, self.x, mode="pair", roi_b=partner, pair="tribe", when=timezone.now() - AGO)
        mistake = game_relearn.due(self.user)[self.x.id]
        self.assertEqual((mistake["origin"], mistake["relation"]), ("pair", "genus"))

    def test_odd_one_out_brings_back_the_odd_one_missed_and_the_beetle_wrongly_picked(self):
        ferr = self.beetles[self.t_ferr][:3]
        group = game.lineage(self.t_ferr, "species")
        self.grid("odd", [self.x, *ferr], "species", group, roi=ferr[0], roi_b=self.x, when=timezone.now() - AGO)
        due = game_relearn.due(self.user)
        self.assertEqual(set(due), {self.x.id, ferr[0].id})
        self.assertEqual({m["origin"] for m in due.values()}, {"odd"})

    def test_select_all_brings_back_a_wrong_tap_and_a_missed_member(self):
        members, others = self.beetles[self.t_affinis][:2], self.beetles[self.t_plat][:2]
        group = game.lineage(self.t_affinis, "subfamily")
        tiles = [*members, *others]
        # tapped the first member (right) and the first non-member (wrong); missed the second member
        self.grid("select", tiles, "subfamily", group, roi=members[0], picks=[0, 2], when=timezone.now() - AGO)
        self.assertEqual(set(game_relearn.due(self.user)), {members[1].id, others[0].id})

    def test_a_perfect_grid_is_no_mistake(self):
        members, others = self.beetles[self.t_affinis][:2], self.beetles[self.t_plat][:2]
        group = game.lineage(self.t_affinis, "subfamily")
        self.grid("select", [*members, *others], "subfamily", group, roi=members[0], picks=[0, 1],
                  when=timezone.now() - AGO)
        self.assertEqual(game_relearn.due(self.user), {})


class WhichGameTests(RelearnCase):
    ALL = ["pair", "odd", "select", "classify"]

    def test_first_an_easier_game_then_the_one_it_was_missed_in(self):
        self.miss(when=timezone.now() - timedelta(days=2))
        mistake = game_relearn.due(self.user)[self.x.id]
        self.assertEqual(game_relearn.game_for(mistake, self.ALL), "select")
        # right in Select all: next time it is Identification again
        group = game.lineage(self.t_affinis, "species")
        tiles = [self.x, *self.beetles[self.t_plat][:3]]
        self.grid("select", tiles, "species", group, roi=self.x, picks=[0], retry=True,
                  when=timezone.now() - timedelta(days=1))
        mistake = game_relearn.due(self.user)[self.x.id]
        self.assertEqual((mistake["stage"], game_relearn.game_for(mistake, self.ALL)), ("origin", "classify"))
        # right there too: learned
        self.answer(self.user, self.x, AFFINIS, retry=True, when=timezone.now() - timedelta(hours=1))
        self.assertEqual(game_relearn.due(self.user), {})

    def test_wrong_again_in_the_easier_game_stays_there(self):
        self.miss(when=timezone.now() - timedelta(days=2))
        self.answer(self.user, self.x, mode="pair", roi_b=self.beetles[self.t_ferr][0], pair="species", retry=True,
                    when=timezone.now() - timedelta(days=1))
        mistake = game_relearn.due(self.user)[self.x.id]
        self.assertEqual((mistake["origin"], mistake["stage"], mistake["tries"]), ("classify", "easier", 1))

    def test_only_the_games_the_player_has(self):
        mistake = {"origin": "classify", "stage": "easier"}
        self.assertEqual(game_relearn.game_for(mistake, ["pair", "odd", "classify"]), "odd")
        self.assertEqual(game_relearn.game_for(mistake, ["classify"]), "classify")

    def test_a_mistake_in_the_easiest_game_comes_back_in_it(self):
        self.assertEqual(game_relearn.game_for({"origin": "pair", "stage": "easier"}, self.ALL), "pair")


class FeedTests(RelearnCase):
    def retries(self, rnd):
        return [i for i in rnd.items if i.get("retry")]

    def test_a_new_player_with_only_similarity_gets_similarity_retries(self):
        self.answer(self.user, self.x, mode="pair", roi_b=self.beetles[self.t_ferr][0], pair="tribe",
                    when=timezone.now() - AGO)
        for t in (self.t_affinis, self.t_ferr, self.t_plat):
            for _ in range(3):
                self.roi(t, validated=False)
        rnd = game.start_round(self.user, "mixed", size=6)
        [retry] = self.retries(rnd)
        self.assertEqual((retry["mode"], retry["a"], retry["check"]), ("pair", str(self.x.id), True))
        partner = type(self.x).objects.get(id=retry["b"])
        self.assertEqual(partner.taxon.genus, "Xyleborus")   # the same kind of partner it was missed with
        self.assertNotEqual(partner.taxon.species, "affinis")

    @override_settings(GAME_RETRY_PER_BATCH=2)
    def test_at_most_a_few_per_batch_spread_through_it(self):
        self.unlock_all()
        GamePreference.objects.filter(player=self.user).update(play_mode="classify")
        for roi in self.beetles[self.t_affinis]:
            self.miss(roi)
        for _ in range(8):
            self.roi(self.t_plat)
        rnd = game.start_round(self.user, "mixed", size=10)
        places = [i for i, it in enumerate(rnd.items) if it.get("retry")]
        self.assertEqual(len(places), 2)
        self.assertGreater(places[1] - places[0], 1)
        self.assertTrue(all(rnd.items[p]["mode"] == "classify" for p in places))   # the one game they chose

    def test_a_retry_takes_the_place_of_an_item_showing_the_same_beetle(self):
        x = str(self.x.id)
        batch = [{"a": "open", "b": x, "check": False}, {"a": "c1", "b": None, "check": True},
                 {"a": "c2", "b": None, "check": True}]
        out = game_relearn._swap_in(batch, [{"a": x, "b": None, "check": True, "retry": True}])
        self.assertEqual([it["a"] for it in out], ["c1", "c2", x])   # never one beetle twice in a batch
        out = game_relearn._swap_in(batch[1:], [{"a": x, "b": None, "check": True, "retry": True}])
        self.assertEqual(len(out), 2)   # else in place of a scored item

    def test_identification_mistakes_come_back_in_an_easier_game(self):
        self.unlock_all()
        self.miss()
        item = game_relearn.retry_items(self.user, "mixed", 5)[0]
        self.assertEqual((item["mode"], item["retry"], item["a"]), ("select", True, str(self.x.id)))
        self.assertEqual(len(item["tiles"]), 4)
        self.assertIn(str(self.x.id), item["tiles"])
        self.assertEqual(item["group"]["species"], "Xyleborus affinis")   # "tap every Xyleborus affinis"

    def test_an_odd_one_out_retry_is_built_around_the_beetle(self):
        self.unlock_all()
        self.grid("odd", [self.x, *self.beetles[self.t_ferr][:3]], "species", game.lineage(self.t_ferr, "species"),
                  roi=self.beetles[self.t_ferr][0], roi_b=self.x, when=timezone.now() - AGO)
        GamePreference.objects.filter(player=self.user).update(play_mode="odd")
        items = {i["a"]: i for i in game_relearn.retry_items(self.user, "mixed", 5)}
        item = items[str(self.x.id)]
        self.assertEqual((item["mode"], item["rank"], len(item["tiles"])), ("odd", "species", 4))
        self.assertNotEqual(item["group"]["species"], "Xyleborus affinis")   # the beetle is the one that doesn't belong

    def test_the_player_sees_it_marked(self):
        from beetlesgallery.beetles_app.game_views import _item_payload

        self.unlock_all()
        self.miss()
        rnd = GameRound.objects.create(player=self.user, mode="mixed", items=game_relearn.retry_items(self.user, "mixed", 1))
        self.assertTrue(_item_payload(rnd, 0)["again"])


class ScoringTests(RelearnCase):
    """Retries earn the retry share of the points, in every game, and stay out of the ratings."""

    def play_retry(self, item, body):
        rnd = GameRound.objects.create(player=self.user, mode="mixed", items=[item])
        self.client.force_login(self.user)
        res = self.client.post(reverse("game_answer", args=[rnd.id]), json.dumps(dict(body, index=0)),
                               content_type="application/json")
        self.assertEqual(res.status_code, 200, res.content)
        return GameAnswer.objects.get(round=rnd)

    def test_an_odd_one_out_retry_earns_half(self):
        self.unlock_all()
        self.grid("odd", [self.x, *self.beetles[self.t_ferr][:3]], "species", game.lineage(self.t_ferr, "species"),
                  roi=self.beetles[self.t_ferr][0], roi_b=self.x, when=timezone.now() - AGO)
        GamePreference.objects.filter(player=self.user).update(play_mode="odd")
        item = next(i for i in game_relearn.retry_items(self.user, "mixed", 5) if i["a"] == str(self.x.id))
        ans = self.play_retry(item, {"pick": item["tiles"].index(str(self.x.id))})
        self.assertTrue(ans.is_retry)
        points = self.points(ans)
        self.assertAlmostEqual(points.points, scoring.odd_base(ans) * 0.5)
        self.assertTrue(points.detail["retry"])

    def test_a_select_all_retry_earns_half_and_stays_out_of_the_ratings(self):
        self.unlock_all()
        self.miss()
        item = game_relearn.retry_items(self.user, "mixed", 5)[0]
        members = [i for i, t in enumerate(item["tiles"])
                   if type(self.x).objects.get(id=t).taxon.species == "affinis"]
        ans = self.play_retry(item, {"picks": members})
        self.assertTrue(ans.is_retry)
        full = scoring.select_truth(ans)[0]
        self.assertAlmostEqual(self.points(ans).points, full * 0.5)
        # only the first answer on the beetle counts towards the rating: the miss, not the retry
        rating = scoring.ratings()[self.user.id]
        self.assertEqual(rating[1], 0.75)   # the miss: subfamily, tribe, genus right, species wrong
