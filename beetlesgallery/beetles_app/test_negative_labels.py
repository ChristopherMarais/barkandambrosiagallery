"""
Negative labels: every game answer writes down what it says a beetle is *not* (game_negatives, NegativeLabel), and the
game's name suggestions and their confidence (game.consensus) count them as evidence against a name.
"""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings

from beetlesgallery.beetles_app import game, game_negatives, game_tips
from beetlesgallery.beetles_app.models import GameAnswer, GameRound, NegativeLabel
from beetlesgallery.beetles_app.test_game import GameCase
from beetlesgallery.beetles_app.test_game_select_all import SelectCase


class NegativeCase(GameCase):
    def setUp(self):
        super().setUp()
        self.open = self.roi(self.t_ferr, validated=False)   # the unvalidated beetle under discussion
        self.known = [self.roi(self.t_affinis) for _ in range(3)]
        self.plat = self.roi(self.t_plat)
        self.players = [get_user_model().objects.create_user(f"n{i}", password="pw") for i in range(4)]

    def answer(self, player, mode, **fields):
        rnd = GameRound.objects.create(player=player, mode=mode, items=[])
        fields.setdefault("is_check", False)
        return GameAnswer.objects.create(round=rnd, player=player, mode=mode, index=0, **fields)

    def select(self, player, picks, rank="genus", flagged=(), taxon=None):
        tiles = [str(self.open.id)] + [str(k.id) for k in self.known]
        return self.answer(player, "select", roi=self.known[0], tiles=tiles, picks=list(picks), flagged=list(flagged),
                           grid_rank=rank, grid_group=game.lineage(taxon or self.t_affinis, rank))

    def odd(self, player, picks, rank="genus"):
        tiles = [str(self.open.id)] + [str(k.id) for k in self.known]
        return self.answer(player, "odd", roi=self.open, roi_b=self.open, tiles=tiles, picks=list(picks),
                           grid_rank=rank, grid_group=game.lineage(self.t_affinis, rank))

    def pair(self, player, partner, rung):
        return self.answer(player, "pair", roi=self.open, roi_b=partner, pair_answer=rung)

    def name(self, player, taxon, roi=None):
        return self.answer(player, "classify", roi=roi or self.open, subfamily=taxon.subfamily, tribe=taxon.tribe,
                           genus=taxon.genus, species=taxon.species)

    def rows(self, answer):
        return sorted((str(n.roi_id), n.rank, n.value, n.mode) for n in NegativeLabel.objects.filter(answer=answer))


class RecordedPerGameTests(NegativeCase):
    def test_find_them_all_records_not_the_group_for_every_untapped_beetle(self):
        ans = self.select(self.players[0], picks=[1, 2])   # left untapped: the open beetle and known[2]
        self.assertEqual(self.rows(ans), sorted([(str(self.open.id), "genus", "Xyleborus", "select"),
                                                 (str(self.known[2].id), "genus", "Xyleborus", "select")]))
        self.assertEqual(NegativeLabel.objects.get(answer=ans, roi=self.open).player_id, self.players[0].id)

    def test_find_them_all_leaves_out_flagged_photos_and_grids_with_nothing_tapped(self):
        ans = self.select(self.players[0], picks=[1, 2], flagged=[0])
        self.assertEqual([r[0] for r in self.rows(ans)], [str(self.known[2].id)])
        self.assertEqual(self.rows(self.select(self.players[1], picks=[])), [])

    def test_odd_one_out_records_not_the_rests_group_for_each_pick(self):
        ans = self.odd(self.players[0], picks=[0])
        self.assertEqual(self.rows(ans), [(str(self.open.id), "genus", "Xyleborus", "odd")])

    def test_odd_one_out_from_before_several_odd_ones_uses_its_one_pick(self):
        ans = self.odd(self.players[0], picks=[])   # roi is the pick
        self.assertEqual(self.rows(ans), [(str(self.open.id), "genus", "Xyleborus", "odd")])

    def test_similarity_records_not_the_partners_name_just_below_the_shared_rank(self):
        self.assertEqual(self.rows(self.pair(self.players[0], self.known[0], "tribe")),
                         [(str(self.open.id), "genus", "Xyleborus", "pair")])
        self.assertEqual(self.rows(self.pair(self.players[1], self.known[0], "genus")),
                         [(str(self.open.id), "species", "Xyleborus affinis", "pair")])
        self.assertEqual(self.rows(self.pair(self.players[2], self.plat, "different")),
                         [(str(self.open.id), "subfamily", "Platypodinae", "pair")])

    def test_similarity_says_nothing_when_sure_of_the_species_unsure_or_next_to_an_unvalidated_beetle(self):
        self.assertEqual(self.rows(self.pair(self.players[0], self.known[0], "species")), [])
        self.assertEqual(self.rows(self.pair(self.players[1], self.known[0], "unsure")), [])
        unsure = self.roi(self.t_affinis, validated=False)
        self.assertEqual(self.rows(self.pair(self.players[2], unsure, "different")), [])

    def test_naming_records_none_and_a_skip_says_nothing(self):
        self.assertEqual(self.rows(self.name(self.players[0], self.t_plat)), [])
        skipped = self.answer(self.players[2], "select", roi=self.known[0], skipped=True,
                              tiles=[str(self.open.id)], picks=[], grid_rank="genus",
                              grid_group=game.lineage(self.t_affinis, "genus"))
        self.assertEqual(self.rows(skipped), [])

    def test_recording_twice_adds_nothing(self):
        ans = self.select(self.players[0], picks=[1, 2])
        game_negatives.record(ans)
        self.assertEqual(NegativeLabel.objects.filter(answer=ans).count(), 2)

    def test_a_deleted_answer_takes_its_labels_with_it(self):
        ans = self.select(self.players[0], picks=[1, 2])
        ans.delete()
        self.assertFalse(NegativeLabel.objects.exists())


class PlayedTests(SelectCase):
    def test_a_find_them_all_answer_through_the_game_records_every_untapped_tile(self):
        rnd, item = self.grid("species")
        members = self.members(rnd, item)
        res = self.answer(rnd, item, picks=members)
        self.assertEqual(res.status_code, 200, res.content)
        it = rnd.items[item["index"]]
        untapped = {t for i, t in enumerate(it["tiles"]) if i not in members}
        rows = NegativeLabel.objects.filter(answer__round=rnd)
        self.assertEqual({str(r.roi_id) for r in rows}, untapped)
        self.assertEqual({(r.rank, r.value, r.mode) for r in rows}, {("species", it["group"]["species"], "select")})


@override_settings(GAME_PROPOSALS_NEED_LEVEL=False)
class SuggestionTests(NegativeCase):
    def entry(self):
        return game.consensus(roi_ids=[self.open.id])[0]

    def test_a_name_enough_players_ruled_out_is_never_the_suggestion(self):
        self.name(self.players[0], self.t_affinis)   # one name says Xyleborus
        for p in self.players[1:3]:                  # two say it is not a Xyleborus
            self.select(p, picks=[1, 2, 3])
        ranks = self.entry()["ranks"]
        self.assertIsNone(ranks["genus"])
        self.assertIsNone(ranks["species"])          # nor a name under it
        self.assertEqual(ranks["subfamily"]["value"], "Scolytinae")

    def test_the_runner_up_takes_over_when_the_leader_is_ruled_out(self):
        self.name(self.players[0], self.t_affinis)
        self.name(self.players[1], self.t_affinis)
        self.name(self.players[2], self.t_plat)
        self.assertEqual(self.entry()["ranks"]["subfamily"]["value"], "Scolytinae")
        for p in self.players[2:]:
            self.select(p, picks=[1, 2, 3], rank="subfamily")   # not Scolytinae
        with override_settings(GAME_SELECT_TAP_WEIGHT=1.0):
            ranks = self.entry()["ranks"]
        self.assertEqual(ranks["subfamily"]["ruled_out"], [])   # two names for it, two against: not outweighed
        for p in get_user_model().objects.bulk_create([get_user_model()(username=f"x{i}") for i in range(2)]):
            self.select(p, picks=[1, 2, 3], rank="subfamily")
        ranks = self.entry()["ranks"]
        self.assertEqual(ranks["subfamily"]["value"], "Platypodinae")
        self.assertEqual(ranks["genus"]["ruled_out"], ["Xyleborus"])   # a name under a ruled-out one is out too
        self.assertEqual(ranks["genus"]["value"], "Platypus")

    def test_one_stray_tap_does_not_hide_a_name_but_lowers_its_confidence(self):
        self.name(self.players[0], self.t_affinis)
        before = self.entry()["ranks"]["genus"]
        self.assertEqual(before["support"], 1.0)
        self.select(self.players[1], picks=[1, 2, 3])   # one player: not a Xyleborus
        after = self.entry()["ranks"]["genus"]
        self.assertEqual((after["value"], after["against"], after["ruled_out"]), ("Xyleborus", 1, []))
        # 0.5 for (no record yet) against 0.8 x 0.5 said not: 0.5 / (0.5 + 0.4)
        self.assertAlmostEqual(after["support"], 0.5 / 0.9)

    @override_settings(GAME_NOT_MIN_PLAYERS=3)
    def test_how_many_players_it_takes_is_a_setting(self):
        self.name(self.players[0], self.t_affinis)
        for p in self.players[1:3]:
            self.select(p, picks=[1, 2, 3])
        self.assertEqual(self.entry()["ranks"]["genus"]["value"], "Xyleborus")

    def test_a_validated_beetles_grid_labels_do_not_count_for_it(self):
        for p in self.players[1:3]:
            self.select(p, picks=[0, 1, 3])   # tile 2, known[1], left untapped: recorded, but it is validated
        self.assertEqual(NegativeLabel.objects.filter(roi=self.known[1]).count(), 2)
        self.assertEqual(game_negatives.against([self.known[1].id]), {})

    def test_only_the_voters_asked_for_count(self):
        self.name(self.players[0], self.t_affinis)
        for p in self.players[1:3]:
            self.select(p, picks=[1, 2, 3])
        [entry] = game.consensus(roi_ids=[self.open.id], voters=[self.players[0].id])
        self.assertEqual(entry["ranks"]["genus"]["value"], "Xyleborus")

    def test_the_curators_not_in_tips_read_the_recorded_labels(self):
        self.select(self.players[0], picks=[1, 2, 3])
        self.odd(self.players[1], picks=[0])
        tips = game_tips._not_tips([self.open.id], None)
        self.assertEqual([(t["rank"], t["value"], t["count"]) for t in tips[self.open.id]], [("genus", "Xyleborus", 2)])


class BackfillTests(NegativeCase):
    def test_the_command_writes_the_labels_of_earlier_answers_once(self):
        select = self.select(self.players[0], picks=[1, 2])
        pair = self.pair(self.players[1], self.plat, "different")
        NegativeLabel.objects.all().delete()   # as if answered before they were recorded
        out = StringIO()
        call_command("backfill_negative_labels", "--dry-run", stdout=out)
        self.assertFalse(NegativeLabel.objects.exists())
        self.assertIn("would say 3 negative labels", out.getvalue())
        call_command("backfill_negative_labels", stdout=StringIO())
        self.assertEqual(len(self.rows(select)), 2)
        self.assertEqual(len(self.rows(pair)), 1)
        out = StringIO()
        call_command("backfill_negative_labels", stdout=out)
        self.assertIn("0 new rows", out.getvalue())
