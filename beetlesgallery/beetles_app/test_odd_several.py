"""
Odd One Out with several odd ones (#540): the ladder hides more odd ones as the grid grows (4·1, 9·1, 16·1, 9·2, 16·2,
16·3 at each rank), the player picks exactly that many, each pick is a claim scored like Find Them All's taps (#530),
and the review, the ladder, the votes and the round review read every pick and every odd one.
"""
import importlib
from pathlib import Path

from django.conf import settings
from django.test import override_settings

from beetlesgallery.beetles_app import game, game_answer_review, game_grid_ladder as ladder, game_scoring, game_tuning
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, GameRound, GridStep
from beetlesgallery.beetles_app.test_grid_builders import GridCase, at


class LadderShapeTests(GridCase):
    def test_odd_one_out_has_24_steps_and_select_all_keeps_12(self):
        self.assertEqual(len(ladder.steps("odd")), 24)
        self.assertEqual(ladder.steps("odd")[:7], [
            (4, "subfamily", 1), (9, "subfamily", 1), (16, "subfamily", 1), (9, "subfamily", 2),
            (16, "subfamily", 2), (16, "subfamily", 3), (4, "tribe", 1)])
        self.assertEqual(ladder.steps("odd")[-1], (16, "species", 3))
        self.assertEqual(len(ladder.steps("select")), 12)
        self.assertEqual(ladder.step_for(9, "species", game_key="select"), 11)
        self.assertEqual((ladder.step_for(16, "genus"), ladder.step_for(16, "genus", 3)), (15, 18))
        self.assertEqual([ladder.most_odds(n) for n in (4, 9, 16)], [1, 2, 3])

    def test_the_plan_says_how_many_odd_ones(self):
        at(self.user, "odd", ladder.step_for(9, "tribe", 2))
        plan = ladder.plan(self.user, "odd", "species")
        self.assertEqual((plan["size"], plan["rank"], plan["odds"]), (9, "tribe", 2))
        at(self.user, "select", 12)
        self.assertEqual(ladder.plan(self.user, "select", "species")["odds"], 1)

    def test_a_step_past_the_top_is_read_as_the_top(self):
        at(self.user, "select", 20)   # never saved, but a stale row must not break the feed
        self.assertEqual(ladder.current(self.user, "select"), 12)

    def test_saved_steps_move_to_the_same_grid_on_the_new_ladder(self):
        migration = importlib.import_module("beetlesgallery.beetles_app.migrations.0051_odd_ladder_steps")
        old = [(size, rank) for rank in game.RANKS for size in game.GRID_SIZES]
        for step, (size, rank) in enumerate(old, start=1):
            new = migration.old_to_new(step)
            self.assertEqual(ladder.steps("odd")[new - 1], (size, rank, 1))
            self.assertEqual(migration.new_to_old(new), step)
        self.assertEqual(migration.new_to_old(ladder.step_for(16, "genus", 3)), 9)   # back to 16 at genus


class BuilderTests(GridCase):
    def test_grids_hide_the_steps_number_of_odd_ones_on_their_own_photos(self):
        for size, odds in ((9, 2), (16, 2), (16, 3)):
            with self.subTest(size=size, odds=odds):
                at(self.user, "odd", ladder.step_for(size, "genus", odds))
                item = game.build_odd_items(self.user, 1)[0]
                self.assertEqual((item["size"], len(item["odds"]), item["a"]), (size, odds, item["odds"][0]))
                group = item["group"]["genus"]
                found = game.Beetles.objects.select_related("taxon").filter(id__in=item["tiles"])
                outside = {str(b.id) for b in found if b.taxon.genus != group}
                self.assertEqual(outside, set(item["odds"]))
                self.assertTrue(all(b.bbox_is_validated for b in found if str(b.id) in outside))
                self.assertEqual(len({b.image_asset_id for b in found}), size)

    def test_short_of_odd_ones_it_hides_fewer_before_it_shrinks(self):
        # Xyleborus affinis, and a single validated beetle of any other species to be odd
        for species, beetles in self.known.items():
            if species not in ("affinis", "ferrugineus"):
                for roi in beetles:
                    roi.delete()
        for roi in self.known["ferrugineus"][1:]:
            roi.delete()
        at(self.user, "odd", ladder.step_for(16, "species", 3))
        item = game.build_odd_items(self.user, 1)[0]
        self.assertEqual((item["size"], len(item["odds"])), (16, 1))


class FeedTests(GridCase):
    def start(self, size=9, odds=2, rank="species"):
        at(self.user, "odd", ladder.step_for(size, rank, odds))
        res = self.post("game_start", {"mode": "odd"})
        self.assertEqual(res.status_code, 200, res.content)
        self.rnd, self.item = GameRound.objects.get(id=res.json()["round"]), res.json()["item"]
        self.grid = self.rnd.items[self.item["index"]]
        self.odd_places = [self.grid["tiles"].index(t) for t in self.grid["odds"]]
        self.rest = [i for i in range(len(self.grid["tiles"])) if i not in self.odd_places]
        return self.item

    def answer(self, **body):
        return self.post("game_answer", dict(body, index=self.item["index"]), self.rnd.id)

    def test_the_feed_says_how_many_to_find_but_not_which(self):
        item = self.start()
        self.assertEqual(item["odds"], 2)
        self.assertNotIn(self.grid["odds"][1], str(item))

    def test_exactly_that_many_picks(self):
        self.start()
        for body in ({"picks": [self.odd_places[0]]}, {"pick": self.odd_places[0]},
                     {"picks": [self.odd_places[0]] * 2}, {"picks": [*self.odd_places, self.rest[0]]}):
            with self.subTest(body=body):
                res = self.answer(**body)
                self.assertEqual(res.status_code, 400)
                self.assertEqual(res.json()["error"], "Please pick 2 beetles.")
        self.assertFalse(GameAnswer.objects.exists())

    def test_one_odd_one_still_takes_a_lone_pick(self):
        self.start(size=9, odds=1)
        self.assertEqual(self.item["odds"], 1)
        self.assertEqual(self.answer(pick=self.odd_places[0]).status_code, 200)
        answer = GameAnswer.objects.get()
        self.assertEqual((answer.picks, answer.correct_species, answer.roi_id == answer.roi_b_id),
                         (self.odd_places, True, True))

    @override_settings(GAME_POINTS_PARTICIPATION=0.0)
    def test_both_found(self):
        self.start()
        data = self.answer(picks=self.odd_places).json()
        answer = GameAnswer.objects.get()
        self.assertEqual((answer.picks, answer.is_check, answer.correct_species), (sorted(self.odd_places), True, True))
        points = AnswerPoints.objects.get(answer=answer)
        self.assertEqual(points.basis, AnswerPoints.Basis.TRUTH)
        self.assertAlmostEqual(points.points, points.detail["worth"], places=2)
        review = data["review"]
        self.assertEqual((review["verdict"], review["grid"]["found"], review["grid"]["count"]), ("right", 2, 2))
        self.assertTrue(review["headline"].startswith("Found 2 of 2 · +"))
        self.assertEqual(sorted(review["grid"]["odds"]), sorted(self.odd_places))
        self.assertEqual(len(review["grid"]["odd_names"]), 2)
        self.assertEqual({review["grid"]["tiles"][i]["state"] for i in self.odd_places}, {"right"})
        self.assertEqual(review["celebrate"]["kind"], "validated")

    @override_settings(GAME_POINTS_PARTICIPATION=0.0)
    def test_one_found_one_wrong_reads_tile_by_tile(self):
        self.start()
        data = self.answer(picks=[self.odd_places[0], self.rest[0]]).json()
        answer = GameAnswer.objects.get()
        self.assertEqual((answer.is_check, answer.correct_species), (True, False))
        points = AnswerPoints.objects.get(answer=answer)
        share = points.detail["worth"] / 2
        self.assertAlmostEqual(points.points, share * (1 - game_scoring.wrong_cost()), places=2)
        self.assertLess(points.points, 0)   # a wrong claim costs more than a right one earns
        grid = data["review"]["grid"]
        tiles = grid["tiles"]
        self.assertEqual((tiles[self.odd_places[0]]["state"], tiles[self.rest[0]]["state"], tiles[self.odd_places[1]]["state"]),
                         ("right", "wrong", "odd"))
        self.assertAlmostEqual(tiles[self.odd_places[0]]["points"], share, places=2)
        self.assertAlmostEqual(tiles[self.rest[0]]["points"], -share * game_scoring.wrong_cost(), places=1)
        self.assertEqual(data["review"]["verdict"], "wrong")
        self.assertTrue(data["review"]["headline"].startswith("Found 1 of 2 · 1 wrong · −"))
        self.assertEqual(ladder.outcome(answer), ladder.POOR)

    def test_a_flagged_odd_one_ends_the_grid(self):
        self.start()
        place = self.odd_places[1]
        self.post("game_report_item", {"round": str(self.rnd.id), "index": self.item["index"], "image": place,
                                       "reason": "bad_image"})
        self.assertEqual(self.answer(picks=[self.odd_places[0], self.rest[0]], flagged=[place]).status_code, 200)
        self.assertEqual(GameAnswer.objects.get().skipped, True)

    def test_the_round_review_marks_every_odd_one_and_every_pick(self):
        self.start()
        self.answer(picks=[self.odd_places[0], self.rest[0]])
        data = game.GameRound.objects.get(id=self.rnd.id)
        from beetlesgallery.beetles_app import game_feedback
        item = game_feedback.round_feedback(data)["items"][0]
        sides = item["sides"]
        self.assertEqual([i for i, s in enumerate(sides) if s["odd"]], sorted(self.odd_places))
        self.assertEqual([i for i, s in enumerate(sides) if s["picked"]], sorted([self.odd_places[0], self.rest[0]]))
        self.assertEqual((item["answer"], item["truth_odd"]["count"]), ("2 beetles", 2))


@override_settings(GAME_POINTS_PARTICIPATION=0.0)
class ScoringTests(GridCase):
    """Rest: Xyleborus affinis; odd ones from another subfamily (Platypodinae), worth Similarity's 1 point each."""

    def answer(self, picks, odd=None, extra=(), **fields):
        rest = self.known["affinis"][: 9 - 2 - len(extra)]
        odds = odd or [self.known["cylindrus"][0], self.known["parallelus"][0]]
        tiles = [*rest, *extra, *odds]
        rnd = GameRound.objects.create(player=self.user, mode="odd", items=[])
        values = dict(round=rnd, player=self.user, mode="odd", index=0, roi=tiles[picks[0]], roi_b=odds[0],
                      is_check=True, picks=sorted(picks), tiles=[str(t.id) for t in tiles], grid_rank="species",
                      grid_step=ladder.step_for(9, "species", 2), grid_group=game.lineage(self.taxa["affinis"], "species"))
        values.update(fields)
        return GameAnswer.objects.create(**values)

    def points(self, answer):
        game_scoring.score_new_answer(answer)
        return AnswerPoints.objects.get(answer=answer)

    def test_the_grid_is_worth_one_odd_ones_and_split_over_them(self):
        worth = 1.5 * game_scoring.PAIR_POINTS[-1] * 1.5   # the odd one weight, another subfamily, nine beetles
        right = self.points(self.answer([7, 8]))
        self.assertAlmostEqual(right.points, worth)
        self.assertEqual((right.detail["share"], right.detail["odds"], right.detail["found"]), (round(worth / 2, 3), 2, 2))
        half = self.points(self.answer([7, 0], correct_species=False))
        self.assertAlmostEqual(half.points, worth / 2 * (1 - game_scoring.wrong_cost()))
        none = self.points(self.answer([0, 1], correct_species=False))
        self.assertAlmostEqual(none.points, -worth * game_scoring.wrong_cost())

    def test_closer_relatives_are_worth_more(self):
        near = self.points(self.answer([7, 8], odd=[self.known["ferrugineus"][0], self.known["ferrugineus"][1]]))
        self.assertAlmostEqual(near.points, 1.5 * game_scoring.PAIR_POINTS[2] * 1.5)   # same genus

    def test_one_pick_scores_as_before(self):
        odd = self.known["cylindrus"][0]
        tiles = [*self.known["affinis"][:3], odd]
        rnd = GameRound.objects.create(player=self.user, mode="odd", items=[])
        common = dict(round=rnd, player=self.user, mode="odd", roi_b=odd, is_check=True, tiles=[str(t.id) for t in tiles],
                      grid_rank="species", grid_step=1, grid_group=game.lineage(self.taxa["affinis"], "species"))
        for i, (roi, place) in enumerate(((odd, 3), (tiles[0], 0))):
            old = GameAnswer.objects.create(index=2 * i, roi=roi, correct_species=roi == odd, **common)
            new = GameAnswer.objects.create(index=2 * i + 1, roi=roi, picks=[place], correct_species=roi == odd, **common)
            self.assertAlmostEqual(self.points(old).points, self.points(new).points)

    def test_a_pick_nobody_has_validated_is_scored_by_agreement(self):
        unknown = self.unknown["cylindrus"][0]
        answer = self.answer([6, 7], extra=[unknown], is_check=False, correct_species=None)
        row = self.points(answer)
        self.assertEqual(row.basis, AnswerPoints.Basis.CONSENSUS)
        self.assertAlmostEqual(row.points, row.detail["share"])   # the validated one found; nobody has said yet
        self.assertIn("6", row.detail["votes"])
        self.assertIsNone(ladder.outcome(answer))
        # validated later: the truth takes over
        game.Beetles.objects.filter(id=unknown.id).update(bbox_is_validated=True)
        game_scoring.sync_late_truth()
        answer.refresh_from_db()
        self.assertEqual((answer.validated_later, answer.correct_species), (True, True))
        game_scoring.recompute([self.user.id])
        self.assertEqual(AnswerPoints.objects.get(answer=answer).basis, AnswerPoints.Basis.TRUTH)

    def test_the_ladder_reads_every_pick(self):
        self.assertEqual(ladder.outcome(self.answer([7, 8])), ladder.GOOD)
        self.assertEqual(ladder.outcome(self.answer([7, 0])), ladder.POOR)
        self.assertEqual(ladder.outcome(self.answer([0, 1])), ladder.POOR)


class BalanceTests(GridCase):
    def setUp(self):
        super().setUp()
        game_tuning.forget()
        self.addCleanup(game_tuning.forget)

    def test_blind_picking_loses_at_every_step(self):
        for size, odds in ladder.ODD_SHAPES:
            with self.subTest(size=size, odds=odds):
                self.assertLess(game_tuning.odd_guess(size, odds), 0)
        check = next(c for c in game_tuning.checks() if c[0].startswith("A blind guess in"))
        self.assertTrue(check[1], check)
        self.assertIn("16 beetles, 3 odd", check[2])

    def test_the_grids_the_builder_makes_are_the_ones_checked(self):
        """However many AI beetles a grid holds, blind picks lose: wrong picks always outweigh the odd ones."""
        k = game_scoring.wrong_cost()
        for size, odds in ladder.ODD_SHAPES:
            for level in (1, 5, 10):
                ai = min(size - odds - 1, max(2, game.odd_open_count(level, size, odds)))
                with self.subTest(size=size, odds=odds, ai=ai):
                    self.assertLess(odds - k * (size - odds - ai), 0)


class VoteTests(GridCase):
    def answer(self, picks, right):
        unknown = self.unknown["affinis"][0]
        tiles = [*self.known["affinis"][:6], unknown, self.known["cylindrus"][0], self.known["parallelus"][0]]
        rnd = GameRound.objects.create(player=self.user, mode="odd", items=[])
        return GameAnswer.objects.create(
            round=rnd, player=self.user, mode="odd", index=0, roi=tiles[picks[0]], roi_b=tiles[7], is_check=True,
            picks=picks, tiles=[str(t.id) for t in tiles], grid_rank="genus", correct_genus=right,
            grid_group=game.lineage(self.taxa["affinis"], "genus")), unknown

    def test_the_rest_count_as_votes_only_when_every_odd_one_was_found(self):
        _, unknown = self.answer([7, 8], True)
        votes = game.tap_votes([unknown.id])
        self.assertEqual([(r, dict(v)) for r, _, v in votes], [(unknown.id, {"subfamily": "Scolytinae",
                                                                              "tribe": "Xyleborini",
                                                                              "genus": "Xyleborus"})])
        GameAnswer.objects.all().delete()
        self.answer([0, 7], False)
        self.assertEqual(game.tap_votes([unknown.id]), [])

    def test_a_pick_says_its_beetle_is_not_of_the_group(self):
        answer, unknown = self.answer([6, 7], None)
        self.assertEqual(game.grid_exclusions(answer), [(unknown.id, "genus", "Xyleborus")])


class PageTests(GridCase):
    def test_the_page_picks_up_to_that_many_and_sends_them_all(self):
        html = Path(settings.BASE_DIR, "beetlesgallery", "templates", "beetles", "game_play.html").read_text(encoding="utf-8")
        self.assertIn('oddWant = MODE === "odd" ? item.odds || 1 : 1;', html)
        self.assertIn('oddPrompt(item.rank, oddWant)', html)   # "Find the 2 that don't share the same ..." (#538)
        self.assertIn("else if (oddPicks.size < oddWant) oddPicks.add(i);", html)
        self.assertIn('MODE === "odd" ? oddPicks.size === oddWant', html)
        self.assertIn("picks: Array.from(oddPicks).sort((a, b) => a - b)", html)
        self.assertIn('several ? "the odd ones" : "the odd one"', html)   # the card's lead says "The odd ones" (#541)

    def test_review_card_keeps_its_shape_for_one_odd_one(self):
        """The card's per-tile fields stay, and the new lists are added (#541 reworks how the names show)."""
        self.assertTrue(callable(game_answer_review.review))
        at(self.user, "odd", 1)
        res = self.post("game_start", {"mode": "odd"})
        rnd, item = GameRound.objects.get(id=res.json()["round"]), res.json()["item"]
        grid = rnd.items[item["index"]]
        data = self.post("game_answer", {"index": item["index"], "pick": grid["tiles"].index(grid["a"])}, rnd.id).json()
        card = data["review"]["grid"]
        self.assertEqual(card["odd"], card["pick"])
        self.assertEqual((card["odds"], card["picks"], card["count"]), ([card["odd"]], [card["pick"]], 1))
        self.assertEqual(set(card["tiles"][card["pick"]]), {"validated", "name", "state", "points"})
        self.assertEqual(GridStep.objects.get(player=self.user, game="odd").step, 1)
