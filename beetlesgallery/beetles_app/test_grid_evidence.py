"""Grid games as evidence about beetles' names: a Select all tap counts a little less than a name, and what a grid
says a beetle is *not* reaches the curators' "not in" tips."""
from django.contrib.auth import get_user_model
from django.test import override_settings

from beetlesgallery.beetles_app import game, game_scoring, game_tips
from beetlesgallery.beetles_app.models import GameAnswer, GameRound
from beetlesgallery.beetles_app.test_game import GameCase


class GridEvidenceTests(GameCase):
    def setUp(self):
        super().setUp()
        self.open = self.roi(self.t_ferr, validated=False)   # unvalidated beetle under discussion
        self.known = [self.roi(self.t_affinis) for _ in range(3)]
        self.players = [get_user_model().objects.create_user(f"p{i}", password="pw") for i in range(3)]

    def answer(self, player, mode, **fields):
        rnd = GameRound.objects.create(player=player, mode=mode, items=[])
        return GameAnswer.objects.create(round=rnd, player=player, mode=mode, index=0, is_check=False, **fields)

    def tap(self, player, tapped=True, rank="genus"):
        tiles = [str(self.open.id)] + [str(k.id) for k in self.known]
        return self.answer(player, "select", roi=self.known[0], tiles=tiles, picks=[0] if tapped else [1],
                           grid_rank=rank, grid_group=game.lineage(self.t_affinis, rank))

    def name(self, player, taxon):
        return self.answer(player, "classify", roi=self.open, subfamily=taxon.subfamily, tribe=taxon.tribe,
                           genus=taxon.genus, species=taxon.species)

    def test_a_tap_is_a_vote_for_the_grids_group_worth_a_little_less_than_a_name(self):
        self.tap(self.players[0])
        votes = game.tap_votes([self.open.id])
        self.assertEqual(len(votes), 1)
        self.assertEqual(votes[0][2], {"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus"})
        self.assertEqual(votes[0][2].weight, 0.8)
        entry = game.consensus(roi_ids=[self.open.id])[0]
        self.assertEqual(entry["ranks"]["genus"]["value"], "Xyleborus")
        self.assertFalse(entry["ranks"]["genus"].get("trusted"))
        self.assertIn((self.players[0].id,), [(p,) for p, _ in game_scoring.votes_on([self.open.id])[self.open.id]])

    def test_a_name_outweighs_a_tap(self):
        self.tap(self.players[0])                       # says Xyleborus (0.8)
        other = self.name(self.players[1], self.t_plat)   # says Platypus (1)
        entry = game.consensus(roi_ids=[self.open.id])[0]
        self.assertEqual(entry["ranks"]["subfamily"]["value"].lower(), self.t_plat.subfamily.lower())
        self.assertTrue(other)
        with override_settings(GAME_SELECT_TAP_WEIGHT=1.5):   # (a tuning above 1 would make taps win)
            entry = game.consensus(roi_ids=[self.open.id])[0]
            self.assertEqual(entry["ranks"]["subfamily"]["value"], "Scolytinae")

    def test_untapped_and_odd_picks_say_what_a_beetle_is_not(self):
        self.tap(self.players[0], tapped=False)
        self.answer(self.players[1], "odd", roi=self.open, tiles=[str(self.open.id)] + [str(k.id) for k in self.known],
                    grid_rank="genus", grid_group=game.lineage(self.t_affinis, "genus"))
        self.assertEqual(game.grid_exclusions(GameAnswer.objects.get(mode="odd")), [(self.open.id, "genus", "Xyleborus")])
        with override_settings(GAME_TIP_MIN_NOT_VOTES=2):
            tips = game_tips._not_tips([self.open.id], None)
        self.assertEqual([(t["rank"], t["value"], t["count"]) for t in tips[self.open.id]], [("genus", "Xyleborus", 2)])

    def test_a_grid_with_nothing_tapped_says_nothing(self):
        ans = self.tap(self.players[0])
        ans.picks = []
        ans.save()
        self.assertEqual(game.grid_exclusions(ans), [])
        self.assertEqual(game.tap_votes([self.open.id]), [])
