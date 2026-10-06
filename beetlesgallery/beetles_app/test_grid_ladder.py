"""
The grid games' ladder (#489): up a step after two good grids in a row, down one after a poor grid, never below 1 or
above 12, never past the ranks the player has open; skips and flagged grids count for neither.
"""
from django.test import override_settings

from beetlesgallery.beetles_app import game, game_grid_ladder as ladder
from beetlesgallery.beetles_app.models import GameAnswer, GameRound, GridStep
from beetlesgallery.beetles_app.test_grid_builders import GridCase, at


class LadderCase(GridCase):
    def odd(self, right=True, **fields):
        """An Odd One Out answer at species: the odd one picked (right), or a validated beetle of the rest."""
        odd, rest = self.known["ferrugineus"][0], self.known["affinis"][:3]
        rnd = GameRound.objects.create(player=self.user, mode="odd", items=[])
        values = dict(round=rnd, player=self.user, mode="odd", index=0, roi=odd if right else rest[0], roi_b=odd,
                      tiles=[str(b.id) for b in (*rest, odd)], grid_rank="species",
                      grid_group=game.lineage(self.taxa["affinis"], "species"), correct_species=right)
        values.update(fields)
        return GameAnswer.objects.create(**values)

    def select(self, picks, flagged=()):
        """A Select all answer: tiles 0-2 are Xyleborus affinis (the group), 3-8 other species."""
        tiles = self.known["affinis"][:3] + self.known["ferrugineus"][:3] + self.known["cylindrus"][:3]
        rnd = GameRound.objects.create(player=self.user, mode="select", items=[])
        return GameAnswer.objects.create(
            round=rnd, player=self.user, mode="select", index=0, roi=tiles[0], is_check=True, picks=list(picks),
            flagged=list(flagged), tiles=[str(t.id) for t in tiles], grid_rank="species",
            grid_group=game.lineage(self.taxa["affinis"], "species"))

    def step(self, key="odd"):
        return ladder.current(self.user, key)


class OutcomeTests(LadderCase):
    def test_odd_one_out(self):
        self.assertEqual(ladder.outcome(self.odd(right=True)), ladder.GOOD)
        self.assertEqual(ladder.outcome(self.odd(right=False)), ladder.POOR)
        # a pick on a beetle nobody has validated, a skip, a grid ended by flags: neither
        self.assertIsNone(ladder.outcome(self.odd(right=False, correct_species=None, roi=self.unknown["affinis"][0])))
        self.assertIsNone(ladder.outcome(self.odd(skipped=True, correct_species=None)))
        self.assertIsNone(ladder.outcome(self.odd(skipped=True, score_hold=True, flagged=[0, 1], correct_species=None)))

    def test_select_all(self):
        self.assertEqual(ladder.outcome(self.select([0, 1, 2])), ladder.GOOD)          # all found, nothing wrong
        self.assertIsNone(ladder.outcome(self.select([0, 1])))                         # two of three: under 75%
        self.assertIsNone(ladder.outcome(self.select([0, 1, 2, 3])))                   # all found, one wrong
        self.assertEqual(ladder.outcome(self.select([0, 3, 4])), ladder.POOR)          # more wrong than right
        self.assertEqual(ladder.outcome(self.select([3])), ladder.POOR)                # nothing right
        self.assertEqual(ladder.outcome(self.select([0, 1], flagged=[2])), ladder.GOOD)   # a flagged member isn't missed
        with override_settings(GAME_GRID_GOOD_SHARE=0.6):
            self.assertEqual(ladder.outcome(self.select([0, 1])), ladder.GOOD)


class StepTests(LadderCase):
    def test_up_after_two_good_grids_in_a_row_and_down_after_a_poor_one(self):
        self.assertEqual(self.step(), 1)
        ladder.update(self.odd())
        self.assertEqual(self.step(), 1)
        ladder.update(self.odd())
        self.assertEqual(self.step(), 2)
        ladder.update(self.odd(right=False))
        self.assertEqual(self.step(), 1)
        ladder.update(self.odd(right=False))
        self.assertEqual(self.step(), 1)   # never below 1

    def test_a_poor_grid_breaks_the_run_and_neither_leaves_it(self):
        at(self.user, "odd", 5)
        ladder.update(self.odd())
        ladder.update(self.odd(skipped=True, correct_species=None))   # neither: the run goes on
        ladder.update(self.odd())
        self.assertEqual(self.step(), 6)
        ladder.update(self.odd())
        ladder.update(self.odd(right=False))
        ladder.update(self.odd())
        self.assertEqual(self.step(), 5)   # one good since the poor one

    def test_never_above_twelve(self):
        at(self.user, "odd", 12)
        for _ in range(4):
            ladder.update(self.odd())
        self.assertEqual(self.step(), 12)

    @override_settings(GAME_RANK_UNLOCK_ANSWERS={"tribe": 50, "genus": 50, "species": 50})
    def test_never_past_the_open_ranks(self):
        self.level(60)   # level 2, nothing answered: only the subfamily is open
        at(self.user, "odd", 3)
        for _ in range(4):
            ladder.update(self.odd())
        self.assertEqual(self.step(), 3)   # 16 beetles at subfamily
        plan = ladder.plan(self.user, "odd", "subfamily")
        self.assertEqual((plan["size"], plan["rank"]), (16, "subfamily"))
        at(self.user, "odd", 12)
        self.assertEqual(ladder.plan(self.user, "odd", "tribe")["rank"], "tribe")   # capped at the open rank

    def test_each_answer_moves_it_once(self):
        answer = self.odd()
        for _ in range(3):
            ladder.update(answer)
        ladder.update(self.odd())
        self.assertEqual(self.step(), 2)
        self.assertEqual(GridStep.objects.get(player=self.user, game="odd").good_run, 0)

    def test_each_game_has_its_own_step(self):
        ladder.update(self.odd())
        ladder.update(self.odd())
        self.assertEqual((self.step("odd"), self.step("select")), (2, 1))

    @override_settings(GAME_GRID_UP_AFTER=1, GAME_GRID_START_STEP=4)
    def test_the_settings_apply(self):
        self.assertEqual(self.step(), 4)
        ladder.update(self.odd())
        self.assertEqual(self.step(), 5)

    def test_the_steps(self):
        self.assertEqual(len(ladder.LADDER), 12)
        self.assertEqual(ladder.LADDER[:4], [(4, "subfamily"), (9, "subfamily"), (16, "subfamily"), (4, "tribe")])
        self.assertEqual(ladder.LADDER[-1], (16, "species"))


class FeedTests(GridCase):
    """Through the feed: the answer moves the step, and the batch's next grid is built at the new step."""

    def test_a_poor_grid_makes_the_next_grid_smaller_straight_away(self):
        at(self.user, "odd", ladder.step_for(16, "genus"))
        with override_settings(GAME_ROUND_SIZE=3):   # a short batch, leaving beetles for a fresh grid of nine
            res = self.post("game_start", {"mode": "odd"})
        rnd, item = GameRound.objects.get(id=res.json()["round"]), res.json()["item"]
        self.assertEqual((item["size"], item["rank"], item["step"]), (16, "genus", 9))
        grid = rnd.items[item["index"]]
        wrong = next(i for i, t in enumerate(grid["tiles"]) if t != grid["a"]
                     and game.Beetles.objects.get(id=t).bbox_is_validated)
        data = self.post("game_answer", {"index": item["index"], "pick": wrong}, rnd.id).json()
        self.assertEqual(ladder.current(self.user, "odd"), 8)
        self.assertEqual((data["item"]["size"], data["item"]["rank"], data["item"]["step"]), (9, "genus", 8))
        self.assertEqual(GameAnswer.objects.get().grid_step, 9)
