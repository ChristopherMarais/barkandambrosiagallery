"""
Negative labels: what each game says a beetle is *not* (game.ruled_out, game.rules_out), and how that lowers the
players' confidence in a name (game.consensus ``support``) everywhere it is read: the review card, the grids' review,
the proposal queue and the curators' "not in" tips.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from beetlesgallery.beetles_app import game, game_answer_review, game_queue, game_scoring, game_tips
from beetlesgallery.beetles_app.models import GameAnswer, GameRound
from beetlesgallery.beetles_app.test_game import SMALL_TRUST, GameCase, TrustCase

SCOL = {"subfamily": "Scolytinae"}
XYLEBORINI = {"subfamily": "Scolytinae", "tribe": "Xyleborini"}
XYLEBORUS = dict(XYLEBORINI, genus="Xyleborus")
AFFINIS = dict(XYLEBORUS, species="Xyleborus affinis")
PLATYPUS = {"subfamily": "Platypodinae", "tribe": "Platypodini", "genus": "Platypus"}


class RulesOutTests(SimpleTestCase):
    """One rule for what rules a name out, whichever game the claim came from."""

    def test_a_negative_label_rules_out_its_name_and_every_name_under_it(self):
        not_scolytinae = game.Ruled(SCOL, "subfamily")
        for name in (SCOL, XYLEBORINI, XYLEBORUS, AFFINIS):
            self.assertTrue(game.rules_out(not_scolytinae, name), name)
        self.assertFalse(game.rules_out(not_scolytinae, PLATYPUS))

    def test_not_a_genus_says_nothing_about_the_tribe_above_it_or_other_genera(self):
        not_xyleborus = game.Ruled(XYLEBORUS, "genus")
        self.assertTrue(game.rules_out(not_xyleborus, AFFINIS))
        self.assertFalse(game.rules_out(not_xyleborus, XYLEBORINI))
        self.assertFalse(game.rules_out(not_xyleborus, dict(XYLEBORINI, genus="Ambrosiodmus")))

    def test_a_name_rules_out_other_names_at_its_ranks_and_below_but_not_past_where_it_stops(self):
        self.assertTrue(game.rules_out({"subfamily": "Platypodinae"}, XYLEBORUS))
        self.assertTrue(game.rules_out(dict(XYLEBORUS, species="Xyleborus ferrugineus"), AFFINIS))
        self.assertFalse(game.rules_out(XYLEBORUS, AFFINIS))     # "Xyleborus": silent on which Xyleborus
        self.assertFalse(game.rules_out(AFFINIS, XYLEBORUS))     # agrees
        self.assertFalse(game.rules_out({"subfamily": "SCOLYTINAE"}, XYLEBORUS))


class NegativeCase(GameCase):
    """One beetle nobody has validated (self.open) and the answers that speak about it."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.open = self.roi(self.t_ferr, validated=False)
        self.known = [self.roi(self.t_affinis) for _ in range(3)]
        self.players = [get_user_model().objects.create_user(f"p{i}", password="pw") for i in range(5)]

    def answer(self, player, mode, **fields):
        rnd = GameRound.objects.create(player=player, mode=mode, items=[])
        fields.setdefault("is_check", False)
        return GameAnswer.objects.create(round=rnd, player=player, mode=mode, index=0, **fields)

    def name(self, player, names):
        """A Naming answer on the open beetle; ``names`` as {rank: value}, species "Genus species"."""
        species = names.get("species", "").partition(" ")[2]
        return self.answer(player, "classify", roi=self.open, subfamily=names.get("subfamily", ""),
                           tribe=names.get("tribe", ""), genus=names.get("genus", ""), species=species)

    def pair(self, player, rung, partner=None):
        return self.answer(player, "pair", roi=self.open, roi_b=partner or self.known[0], pair_answer=rung)

    def tiles(self):
        return [str(self.open.id)] + [str(k.id) for k in self.known]

    def select(self, player, picks=(1, 2, 3), rank="genus", taxon=None, flagged=()):
        """Find Them All: the open beetle (place 0) and three validated affinis; by default only those are tapped."""
        return self.answer(player, "select", roi=self.known[0], tiles=self.tiles(), picks=list(picks),
                           flagged=list(flagged), grid_rank=rank, grid_group=game.lineage(taxon or self.t_affinis, rank))

    def odd(self, player, picks=(0,), rank="genus"):
        """Odd One Out: the rest are affinis; by default the open beetle is picked as the odd one."""
        return self.answer(player, "odd", roi=self.open, roi_b=self.known[0], tiles=self.tiles(), picks=list(picks),
                           grid_rank=rank, grid_group=game.lineage(self.t_affinis, rank))

    def ruled(self, voters=None):
        return [(roi, pid, c.rank, c.value, c.weight) for roi, pid, c in game.ruled_out([self.open.id], voters)]

    def entry(self, voters=None):
        found = [e for e in game.consensus(roi_ids=[self.open.id], voters=voters) if e["roi"].id == self.open.id]
        return found[0] if found else None

    def support(self, rank, voters=None):
        return round(self.entry(voters)["ranks"][rank]["support"], 4)


class EachGameTests(NegativeCase):
    """game.ruled_out: every game's negative labels from one place."""

    def test_find_them_all_a_beetle_left_untapped_is_not_the_grids_group(self):
        p = self.players[0]
        self.select(p)
        self.assertEqual(self.ruled(), [(self.open.id, p.id, "genus", "Xyleborus", 0.8)])
        [(_, _, claim)] = game.ruled_out([self.open.id])
        self.assertEqual(dict(claim), XYLEBORUS)   # held with the names above it

    def test_find_them_all_select_all_of_a_subfamily(self):
        p = self.players[0]
        self.select(p, rank="subfamily", taxon=self.t_plat, picks=[])   # tapped nothing: says nothing
        self.assertEqual(self.ruled(), [])
        self.select(p, rank="subfamily", taxon=self.t_plat, picks=[1])
        self.assertEqual(self.ruled(), [(self.open.id, p.id, "subfamily", "Platypodinae", 0.8)])

    def test_find_them_all_a_flagged_photo_or_a_validated_beetle_says_nothing(self):
        self.select(self.players[0], picks=[1], flagged=[0])
        self.select(self.players[1], picks=[0])   # the validated ones left untapped are scored, not ruled out
        self.assertEqual(self.ruled(), [])
        self.assertEqual(game.ruled_out([k.id for k in self.known]), [])

    def test_odd_one_out_a_beetle_picked_is_not_the_rests_group(self):
        p, q = self.players[:2]
        self.odd(p)
        legacy = self.odd(q, picks=[])   # one pick, from before several odd ones: the answer's own beetle
        self.assertEqual(legacy.roi_id, self.open.id)
        self.assertEqual(sorted(self.ruled(), key=lambda r: r[1]),
                         [(self.open.id, p.id, "genus", "Xyleborus", 0.8), (self.open.id, q.id, "genus", "Xyleborus", 0.8)])

    def test_odd_one_out_the_rest_are_not_ruled_out(self):
        self.odd(self.players[0], picks=[1])   # a validated one picked: the open beetle was left with the rest
        self.assertEqual(self.ruled(), [])

    def test_similarity_rules_out_the_partners_name_one_rank_below_the_shared_one(self):
        expected = {"different": ("subfamily", "Scolytinae"), "subfamily": ("tribe", "Xyleborini"),
                    "tribe": ("genus", "Xyleborus"), "genus": ("species", "Xyleborus affinis")}
        for i, (rung, (rank, value)) in enumerate(expected.items()):
            with self.subTest(rung=rung):
                GameAnswer.objects.all().delete()
                self.pair(self.players[i], rung)
                self.assertEqual(self.ruled(), [(self.open.id, self.players[i].id, rank, value, 1.0)])

    def test_similarity_same_species_not_sure_or_an_unvalidated_partner_rule_nothing_out(self):
        self.pair(self.players[0], "species")
        self.pair(self.players[1], "unsure")
        self.pair(self.players[2], "tribe", partner=self.roi(self.t_affinis, validated=False))
        self.assertEqual(self.ruled(), [])

    def test_naming_lists_nothing_its_not_is_the_name_itself(self):
        self.name(self.players[0], PLATYPUS)
        self.assertEqual(self.ruled(), [])
        [(_, labels)] = game.evidence([self.open.id])[self.open.id]["votes"]
        self.assertTrue(game.rules_out(labels, XYLEBORUS))   # counted from the name, once

    def test_only_the_voters_count(self):
        self.select(self.players[0])
        self.pair(self.players[1], "different")
        self.assertEqual([r[1] for r in self.ruled(voters={self.players[1].id})], [self.players[1].id])


class ConfidenceTests(NegativeCase):
    """consensus ``support``: the weight for the name over the weight for and against it."""

    def test_names_alone_give_the_same_share_as_before(self):
        self.name(self.players[0], AFFINIS)
        self.name(self.players[1], AFFINIS)
        self.name(self.players[2], dict(XYLEBORUS, species="Xyleborus ferrugineus"))
        entry = self.entry()
        self.assertEqual((entry["ranks"]["species"]["value"], entry["ranks"]["species"]["votes"]), ("Xyleborus affinis", 2))
        self.assertEqual(self.support("species"), round(2 / 3, 4))
        self.assertEqual(entry["ranks"]["species"]["against"], 1)
        self.assertEqual(self.support("genus"), 1.0)

    def test_support_drops_when_a_player_rules_the_name_out_and_every_name_under_it(self):
        self.name(self.players[0], AFFINIS)
        self.assertEqual(self.support("subfamily"), 1.0)
        self.pair(self.players[1], "different")   # next to a validated affinis: not Scolytinae
        entry = self.entry()
        for rank in game.RANKS:   # both count 0.5 (no checks yet): 0.5 / (0.5 + 0.5)
            self.assertEqual(self.support(rank), 0.5, rank)
            self.assertEqual((entry["ranks"][rank]["votes"], entry["ranks"][rank]["against"]), (1, 1))
        self.assertEqual(entry["ranks"]["species"]["value"], "Xyleborus affinis")

    def test_a_negative_says_nothing_above_its_rank(self):
        self.name(self.players[0], AFFINIS)
        self.select(self.players[1])   # not genus Xyleborus
        self.assertEqual((self.support("subfamily"), self.support("tribe")), (1.0, 1.0))
        self.assertLess(self.support("genus"), 1.0)
        self.assertLess(self.support("species"), 1.0)

    def test_a_grid_negative_counts_like_a_tap(self):
        self.name(self.players[0], XYLEBORUS)
        self.select(self.players[1])
        self.assertEqual(self.support("genus"), round(0.5 / (0.5 + 0.8 * 0.5), 4))
        with override_settings(GAME_SELECT_TAP_WEIGHT=0.5):
            self.assertEqual(self.support("genus"), round(0.5 / (0.5 + 0.5 * 0.5), 4))
        GameAnswer.objects.filter(mode="select").delete()
        self.odd(self.players[1])
        self.assertEqual(self.support("genus"), round(0.5 / (0.5 + 0.8 * 0.5), 4))

    def test_a_reliable_players_negative_weighs_more(self):
        good, poor = self.players[1], self.players[2]
        for player, right in ((good, True), (poor, False)):
            for _ in range(8):   # weight at genus: (right + 1) / (judged + 2)
                self.answer(player, "classify", roi=self.known[0], is_check=True, genus="Xyleborus",
                            correct_genus=right)
        self.name(self.players[0], XYLEBORUS)
        self.select(poor)
        self.assertEqual(self.support("genus"), round(0.5 / (0.5 + 0.8 * 0.1), 4))
        GameAnswer.objects.filter(mode="select").delete()
        self.select(good)
        self.assertEqual(self.support("genus"), round(0.5 / (0.5 + 0.8 * 0.9), 4))

    def test_a_name_for_another_branch_counts_against_the_names_under_this_one(self):
        self.name(self.players[0], AFFINIS)
        self.name(self.players[1], SCOL)   # same branch, stops at subfamily: silent below it
        self.assertEqual(self.support("tribe"), 1.0)
        self.name(self.players[2], {"subfamily": "Platypodinae"})   # another subfamily: not Xyleborini, Xyleborus...
        self.assertEqual(self.support("subfamily"), round(1 / 1.5, 4))
        for rank in ("tribe", "genus", "species"):
            self.assertEqual(self.support(rank), 0.5, rank)

    def test_the_name_and_its_votes_stay_even_when_ruled_out_more_than_named(self):
        self.name(self.players[0], XYLEBORUS)
        for p in self.players[1:4]:
            self.select(p)
        genus = self.entry()["ranks"]["genus"]
        self.assertEqual((genus["value"], genus["votes"], genus["against"]), ("Xyleborus", 1, 3))
        self.assertEqual(round(genus["support"], 4), round(0.5 / (0.5 + 3 * 0.4), 4))

    def test_a_negative_never_adds_answers_players_or_a_proposal(self):
        self.select(self.players[1])
        self.assertIsNone(self.entry())   # only ruled out: nothing to propose
        self.assertEqual(len(game.evidence([self.open.id])[self.open.id]["ruled"]), 1)
        self.name(self.players[0], XYLEBORUS)
        entry = self.entry()
        self.assertEqual((entry["answers"], entry["players"], len(entry["ruled"])), (1, 1, 1))

    def test_scoring_still_reads_the_names_only(self):
        self.name(self.players[0], XYLEBORUS)
        self.select(self.players[1])
        self.assertEqual([p for p, _ in game_scoring.votes_on([self.open.id])[self.open.id]], [self.players[0].id])


@override_settings(**SMALL_TRUST)
class TrustedVerdictTests(TrustCase):
    def test_negatives_never_change_what_the_experts_back(self):
        open_ = self.roi(validated=False)
        self.prove(self.user, self.t_affinis)
        self.label(self.user, open_, self.t_ferr)
        before = game.consensus(roi_ids=[open_.id])[0]
        self.assertTrue(before["trusted_rank"])
        tiles = [str(open_.id)] + [str(self.roi(self.t_affinis).id) for _ in range(3)]
        for i in range(3):
            player = get_user_model().objects.create_user(f"n{i}", password="pw")
            rnd = GameRound.objects.create(player=player, mode="select", items=[])
            GameAnswer.objects.create(round=rnd, player=player, mode="select", index=0, roi_id=tiles[1], tiles=tiles,
                                      picks=[1, 2, 3], grid_rank="genus", grid_group=game.lineage(self.t_affinis, "genus"))
        after = game.consensus(roi_ids=[open_.id])[0]
        self.assertEqual(after["trusted_rank"], before["trusted_rank"])
        for rank in game.RANKS:
            b, a = before["ranks"][rank], after["ranks"][rank]
            self.assertEqual((a["value"], a["votes"], a["trusted"]), (b["value"], b["votes"], b["trusted"]), rank)
        self.assertLess(after["ranks"]["genus"]["support"], before["ranks"]["genus"]["support"])


@override_settings(GAME_PROPOSALS_NEED_LEVEL=False)
class ReadersTests(NegativeCase):
    """The review card, the grids' review, the proposal queue and the tips all read the same evidence."""

    def test_the_review_card_shows_the_lower_confidence_without_the_players_own_answer(self):
        self.name(self.players[0], AFFINIS)
        self.select(self.players[1])
        self.select(self.players[2])
        vote = game_answer_review._said([self.open.id], self.players[3].id)[self.open.id]["ranks"]["genus"]
        self.assertEqual(round(vote["support"], 4), round(0.5 / (0.5 + 2 * 0.4), 4))
        self.assertEqual(game_answer_review._likeliest("genus", vote, None)["columns"]["players"]["sure"], 38)
        mine = game_answer_review._said([self.open.id], self.players[1].id)[self.open.id]["ranks"]["genus"]
        self.assertEqual(round(mine["support"], 4), round(0.5 / (0.5 + 0.4), 4))   # their own grid left out

    def test_the_grids_review_is_no_longer_sure_the_beetle_is_in_the_group(self):
        self.name(self.players[0], XYLEBORUS)

        def view():
            cache.clear()
            opinions = game_answer_review.Opinions(self.players[3].id, [self.open.id])
            return game_answer_review._grid_views([self.open], opinions, "genus", "Xyleborus")[0]

        self.assertTrue(view()["in"])
        self.select(self.players[1])
        self.select(self.players[2])
        seen = view()
        self.assertEqual(seen["players"]["sure"], 38)
        self.assertIsNone(seen["in"])

    def test_the_queue_no_longer_counts_a_name_players_ruled_out_as_agreed(self):
        for p in self.players[:3]:
            self.name(p, AFFINIS)
        image = str(self.open.image_asset_id)
        self.assertEqual((game_queue._build()[image]["agreed_rank"], game_queue._build()[image]["support"]),
                         ("species", 1.0))
        self.pair(self.players[3], "different")
        self.pair(self.players[4], "different")
        conf = game_queue._build()[image]
        self.assertEqual((conf["agreed_rank"], conf["support"]), ("", 0.6))   # 1.5 / (1.5 + 1)

    def test_a_name_for_something_else_is_one_more_player_against_in_the_not_in_tip(self):
        self.select(self.players[0])
        self.assertEqual(game_tips.tips([self.open.id]), {})   # one player: too few
        self.name(self.players[1], PLATYPUS)
        [tip] = [t for t in game_tips.tips([self.open.id])[self.open.id] if t["kind"] == "not"]
        self.assertEqual((tip["rank"], tip["value"], tip["count"], tip["against"]), ("genus", "Xyleborus", 2, 0))

    def test_a_name_for_it_still_cancels_the_not_in_tip(self):
        self.select(self.players[0])
        self.select(self.players[1])
        self.assertEqual([t["kind"] for t in game_tips.tips([self.open.id])[self.open.id]], ["not"])
        self.name(self.players[2], XYLEBORUS)   # 2 of 3 is under 75%
        self.assertNotIn("not", [t["kind"] for t in game_tips.tips([self.open.id]).get(self.open.id, [])])

    def test_a_name_in_the_same_branch_that_stops_higher_says_nothing_either_way(self):
        self.select(self.players[0])
        self.name(self.players[1], XYLEBORINI)
        self.assertNotIn("not", [t["kind"] for t in game_tips.tips([self.open.id]).get(self.open.id, [])])

    def test_names_alone_never_make_a_not_in_tip(self):
        for p in self.players[:3]:
            self.name(p, PLATYPUS)
        self.name(self.players[3], XYLEBORUS)
        self.assertNotIn("not", [t["kind"] for t in game_tips.tips([self.open.id]).get(self.open.id, [])])
