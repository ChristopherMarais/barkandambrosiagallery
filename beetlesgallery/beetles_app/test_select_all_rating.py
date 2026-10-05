"""
Select all counts towards the reliability rating once per grid (#381, owner's choice): a perfect grid is one correct
judgement at its rank, anything else one wrong, like one Odd One Out pick. Taps never count one by one.
"""
from beetlesgallery.beetles_app import game, game_scoring
from beetlesgallery.beetles_app.models import GameAnswer, GameRound
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase


class SelectAllRatingTests(ScoringCase):
    def setUp(self):
        super().setUp()
        self.p = self.player("p")

    def grid(self, perfect):
        """A Select all answer as the game stores it: nine tiles, three taps, correct at its rank if perfect."""
        tiles = [self.roi(self.t_affinis) for _ in range(3)] + [self.roi(self.t_ferr) for _ in range(6)]
        rnd = GameRound.objects.create(player=self.p, mode="select", items=[])
        return GameAnswer.objects.create(
            round=rnd, player=self.p, mode="select", index=0, roi=tiles[0], is_check=True, picks=[0, 1, 2],
            tiles=[str(t.id) for t in tiles], grid_rank="species", grid_group=game.lineage(self.t_affinis, "species"),
            correct_species=perfect)

    def test_a_perfect_grid_is_one_correct_judgement(self):
        self.grid(True)
        _, accuracy, judged = game_scoring.ratings()[self.p.id]
        self.assertEqual((accuracy, judged), (1.0, 1))

    def test_any_other_grid_is_one_wrong_judgement(self):
        self.grid(False)
        self.answer(self.p, self.roi(self.t_affinis), AFFINIS)   # four correct ranks
        _, accuracy, judged = game_scoring.ratings()[self.p.id]
        self.assertEqual((judged, round(accuracy, 2)), (5, 0.8))

    def test_reliability_counts_it_at_the_grid_rank(self):
        self.grid(True)
        select = game.player_reliability([self.p.id])[self.p.id]["select"]
        self.assertEqual((select["species"]["ok"], select["species"]["n"], select["genus"]["n"]), (1, 1, 0))

    def test_a_grid_without_validated_members_does_not_count(self):
        self.grid(None)
        self.assertNotIn(self.p.id, game_scoring.ratings())
