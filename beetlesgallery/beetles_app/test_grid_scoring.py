"""
Points by grid size (#489): bigger grids pay more, gains and losses alike, while a blind guess in Odd One Out and tapping
everything in Select all still lose at every size. And every selection is evidence about a beetle's name: in Odd One
Out the beetles left with the rest when the odd one was found count as weak votes for the group.
"""
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import game, game_scoring, game_tuning
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, GameRound, GameTuning
from beetlesgallery.beetles_app.test_grid_builders import GridCase


@override_settings(GAME_POINTS_PARTICIPATION=0.0)
class SizeTests(GridCase):
    def odd(self, size, right=True):
        rest = self.known["affinis"][: size - 1]
        odd = self.known["cylindrus"][0]   # another subfamily: Similarity's 1 point
        rnd = GameRound.objects.create(player=self.user, mode="odd", items=[])
        return GameAnswer.objects.create(
            round=rnd, player=self.user, mode="odd", index=0, roi=odd if right else rest[0], roi_b=odd, is_check=True,
            tiles=[str(b.id) for b in (*rest, odd)], grid_rank="species", grid_step=7,
            grid_group=game.lineage(self.taxa["affinis"], "species"), correct_species=right)

    def select(self, members, others):
        tiles = self.known["affinis"][:members] + self.known["typographus"][:others]
        rnd = GameRound.objects.create(player=self.user, mode="select", items=[])
        return GameAnswer.objects.create(
            round=rnd, player=self.user, mode="select", index=0, roi=tiles[0], is_check=True,
            picks=list(range(members)), tiles=[str(t.id) for t in tiles], grid_rank="genus", grid_step=8,
            grid_group=game.lineage(self.taxa["affinis"], "genus"))

    def points(self, answer):
        game_scoring.score_new_answer(answer)
        return AnswerPoints.objects.get(answer=answer)

    def test_odd_one_out_pays_by_size(self):
        right = {n: self.points(self.odd(n)).points for n in (4, 9, 16)}
        self.assertEqual(right, {4: 1.5, 9: 2.25, 16: 3.0})
        wrong = self.points(self.odd(16, right=False))
        self.assertAlmostEqual(wrong.points, -3.0 * game_scoring.wrong_cost(), places=2)
        self.assertEqual((wrong.detail["size"], wrong.detail["step"], wrong.detail["worth"]), (16, 7, 3.0))

    def test_select_all_pays_by_size_and_tells_the_review_each_beetles_share(self):
        perfect = {n: self.points(self.select(m, n - m)) for n, m in ((4, 1), (9, 3), (16, 6))}
        worth = 1.25 * game_scoring.PAIR_POINTS[1]   # genus grid: Similarity's "same tribe"
        self.assertEqual({n: p.points for n, p in perfect.items()}, {4: worth, 9: worth * 1.5, 16: worth * 2})
        detail = perfect[16].detail
        self.assertEqual((detail["size"], detail["step"], detail["members"], detail["share"]), (16, 8, 6, round(worth * 2 / 6, 3)))

    def test_a_tuned_size_factor_applies(self):
        GameTuning.objects.create(key="GAME_GRID_SIZE_FACTOR", value={"4": 1.0, "9": 3.0, "16": 4.0})
        game_tuning.forget()
        self.addCleanup(game_tuning.forget)
        self.assertEqual(self.points(self.odd(9)).points, 4.5)

    def test_an_old_grid_of_six_counts_as_four(self):
        self.assertEqual(game_scoring.size_factor(6), 1.0)
        self.assertEqual(game_scoring.size_factor(16), 2.0)


class BalanceTests(GridCase):
    def setUp(self):
        super().setUp()
        game_tuning.forget()
        self.addCleanup(game_tuning.forget)

    def test_blind_guessing_loses_at_every_size(self):
        for size in (4, 9, 16):
            with self.subTest(size=size):
                self.assertLess(game_tuning.odd_guess(size), 0)
                self.assertLess(game_tuning.select_tap_all(size), 0)
        self.assertTrue(all(ok for _, ok, _ in game_tuning.checks()), game_tuning.checks())

    def test_the_grids_the_builder_makes_are_the_ones_checked(self):
        # Select all: tapping everything loses on every composition the builder can choose
        for size in (4, 9, 16):
            for ai in range(game.SELECT_AI[size][1] + 1):
                for _ in range(10):
                    mix = game.select_mix(size, 16, ai, 16, ai)
                    if mix:
                        m, a, k = mix
                        self.assertGreaterEqual(k, m)
                        self.assertEqual(m + a + k, size)

    def test_tunings_that_reward_guessing_or_staying_small_are_flagged(self):
        GameTuning.objects.create(key="GAME_GRID_SIZE_FACTOR", value={"4": 2.0, "9": 1.5, "16": 1.0})
        game_tuning.forget()
        failing = [rule for rule, ok, _ in game_tuning.checks() if not ok]
        self.assertIn("Bigger grids are worth at least as much", failing)
        GameTuning.objects.all().delete()
        GameTuning.objects.create(key="GAME_POINTS_CONFIDENCE", value=0.5)   # a wrong tap costs only what a right one earns
        game_tuning.forget()
        self.assertIn("Tapping everything in Find Them All loses", [r for r, ok, _ in game_tuning.checks() if not ok])

    def test_the_scoring_page_has_the_grid_games(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("game_scoring")).content.decode()
        for key in ("GAME_GRID_SIZE_FACTOR", "GAME_GRID_UP_AFTER", "GAME_GRID_GOOD_SHARE", "GAME_GRID_START_STEP"):
            self.assertIn(f'data-testid="tunable-{key}"', page)
        self.assertIn('name="GAME_GRID_SIZE_FACTOR.16"', page)
        self.assertIn("16 beetles", page)
        self.assertIn('data-testid="scoring-grids"', page)


class OddVoteTests(GridCase):
    """A solved Odd One Out grid says the unvalidated beetles left with the rest are in the group."""

    def setUp(self):
        super().setUp()
        self.open = self.unknown["affinis"][0]
        self.players = [get_user_model().objects.create_user(f"p{i}", password="pw") for i in range(2)]

    def odd(self, player, right=True, flagged=()):
        rest = [self.open, *self.known["affinis"][:2]]
        odd = self.known["cylindrus"][0]
        rnd = GameRound.objects.create(player=player, mode="odd", items=[])
        return GameAnswer.objects.create(
            round=rnd, player=player, mode="odd", index=0, roi=odd if right else rest[1], roi_b=odd,
            tiles=[str(b.id) for b in (*rest, odd)], grid_rank="genus", flagged=list(flagged),
            grid_group=game.lineage(self.taxa["affinis"], "genus"))

    def test_the_rest_of_a_solved_grid_are_weak_votes_for_the_group(self):
        self.odd(self.players[0])
        votes = game.tap_votes([self.open.id])
        self.assertEqual([(r, p) for r, p, _ in votes], [(self.open.id, self.players[0].id)])
        self.assertEqual((dict(votes[0][2]), votes[0][2].weight),
                         ({"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus"}, 0.8))
        entry = game.consensus(roi_ids=[self.open.id])[0]   # it feeds the label confidence...
        self.assertEqual(entry["ranks"]["genus"]["value"], "Xyleborus")
        self.assertFalse(entry["ranks"]["genus"].get("trusted"))
        self.assertIn(self.players[0].id, [p for p, _ in game_scoring.votes_on([self.open.id])[self.open.id]])   # ...and scoring

    def test_a_missed_odd_one_or_a_flagged_photo_says_nothing(self):
        self.odd(self.players[0], right=False)
        self.odd(self.players[1], flagged=[0])
        self.assertEqual(game.tap_votes([self.open.id]), [])

    def test_a_pick_on_an_unvalidated_beetle_says_it_is_not_in_the_group(self):
        rnd = GameRound.objects.create(player=self.players[0], mode="odd", items=[])
        ans = GameAnswer.objects.create(
            round=rnd, player=self.players[0], mode="odd", index=0, roi=self.open, roi_b=self.known["cylindrus"][0],
            tiles=[str(self.open.id)], grid_rank="genus", grid_group=game.lineage(self.taxa["cylindrus"], "genus"))
        self.assertEqual(game.grid_exclusions(ans), [(self.open.id, "genus", "Platypus")])
