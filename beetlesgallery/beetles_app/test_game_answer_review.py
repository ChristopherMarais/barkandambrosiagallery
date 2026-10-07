"""
The review after every answer (#488): what the player said next to the truth, or, on a beetle nobody has validated
yet, next to what the other players and IBBI-AI say; points per rank and per tile that add up to the answer's points;
grid names never deeper than the grid's rank; nothing true after a skip; confetti that follows the points.

Each answer goes through the real game_answer view on a hand-made round item, so these tests don't depend on how the
round builders pick beetles.
"""
from django.contrib.auth import get_user_model
from django.test import override_settings

from beetlesgallery.beetles_app import game, game_answer_review, game_scoring
from beetlesgallery.beetles_app.models import AnswerPoints, Beetles, GameAnswer, GameRound, ModelPrediction
from beetlesgallery.beetles_app.test_game import AFFINIS, FERR, GameCase

PLAT = {"subfamily": "Platypodinae", "tribe": "Platypodini", "genus": "Platypus", "species": "cylindrus"}


class ReviewCase(GameCase):
    def answer(self, item, body):
        """Answer one hand-made round item through the API; returns the review card."""
        rnd = GameRound.objects.create(player=self.user, mode=item.get("mode", "classify"), items=[item])
        self.client.force_login(self.user)
        res = self.post("game_answer", dict(body, index=0), rnd.id)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()["review"]

    def classify(self, roi, body, check=None):
        return self.answer({"a": str(roi.id), "b": None, "check": roi.bbox_is_validated if check is None else check}, body)

    def points_of(self, mode="classify"):
        """The answer's AnswerPoints (the newest of this player's answers in that game)."""
        return AnswerPoints.objects.filter(answer__player=self.user, answer__mode=mode).latest("answer__answered_at")

    def earned(self, mode="classify"):
        row = self.points_of(mode)
        return row.points - row.detail.get("participation", 0.0)

    def other(self, name, roi, fields, mode="classify"):
        """Another player's answer on a beetle (what the review calls "players")."""
        player = get_user_model().objects.create_user(name, password="pw")
        rnd = GameRound.objects.create(player=player, mode=mode, items=[])
        return GameAnswer.objects.create(round=rnd, player=player, mode=mode, index=0, roi=roi, **fields)

    def predict(self, roi, taxon, confidence, said=None):
        ModelPrediction.objects.create(roi=roi, valid_species_id=taxon.valid_species_id, taxon=taxon,
                                       confidence=confidence, rank_confidence=said or {}, model_name="m",
                                       model_version="1")


class IdentificationTests(ReviewCase):
    def test_a_validated_beetle_shows_the_truth_and_the_points_rank_by_rank(self):
        roi = self.roi(self.t_affinis)
        Beetles.objects.filter(pk=roi.pk).update(label_source="taxonomist")
        review = self.classify(roi, AFFINIS)
        self.assertEqual((review["mode"], review["verdict"], review["points"]["basis"]), ("classify", "right", "truth"))
        self.assertEqual(review["classify"]["truth"], {"name": "Xyleborus affinis", "rank": "species", "tier": "Taxonomist ID"})
        ranks = review["classify"]["ranks"]
        self.assertEqual([c["state"] for c in ranks], ["right"] * 4)
        self.assertEqual([c["points"] for c in ranks], [3.0, 6.0, 12.0, 24.0])   # 1, 2, 4, 8 times the weight, 3
        self.assertEqual(ranks[3]["truth"], "Xyleborus affinis")
        self.assertEqual(review["points"]["earned"], 45.0)
        self.assertEqual(review["headline"], "Correct to species · +45")

    def test_a_wrong_species_after_a_correct_genus(self):
        review = self.classify(self.roi(self.t_affinis), FERR)
        self.assertEqual(review["verdict"], "wrong")   # anything claimed that isn't true is wrong (#530)
        species = review["classify"]["ranks"][3]
        self.assertEqual((species["yours"], species["truth"], species["state"], species["points"]),
                         ("Xyleborus ferrugineus", "Xyleborus affinis", "wrong", -56.0))   # 8 × 3 × 2⅓
        self.assertEqual(review["headline"], "Correct to genus · −35")
        self.assertAlmostEqual(sum(c["points"] for c in review["classify"]["ranks"]), self.earned(), places=1)

    def test_stopping_at_the_genus_and_getting_it_all_wrong(self):
        genus_only = self.classify(self.roi(self.t_affinis), dict(AFFINIS, species=""))
        self.assertEqual(genus_only["headline"], "Correct to genus · +21")
        self.assertEqual(genus_only["classify"]["ranks"][3]["state"], "stopped")
        wrong = self.classify(self.roi(self.t_affinis), PLAT)
        self.assertEqual((wrong["verdict"], wrong["headline"]), ("wrong", "Not quite · −105"))
        self.assertIsNone(wrong["celebrate"])

    def test_an_unvalidated_beetle_shows_what_the_other_players_and_ibbi_ai_say(self):
        roi = self.roi(self.t_affinis, validated=False)
        self.other("a", roi, FERR)
        self.predict(roi, self.t_affinis, 0.64, said={"genus": {"value": "Xyleborus", "confidence": 0.71}})
        review = self.classify(roi, AFFINIS)
        self.assertEqual((review["verdict"], review["points"]["basis"], review["classify"]["truth"]),
                         (None, "agreement", None))
        self.assertEqual(review["headline"], "Not checked yet")
        self.assertEqual(review["classify"]["players"], 1)
        genus, species = review["classify"]["ranks"][2:]
        self.assertEqual(genus["players"], {"name": "Xyleborus", "sure": 100, "agrees": True, "votes": 1, "experts": 0})
        self.assertEqual(species["players"]["name"], "Xyleborus ferrugineus")
        self.assertIs(species["players"]["agrees"], False)
        self.assertEqual(genus["ai"], {"name": "Xyleborus", "sure": 71, "agrees": True})
        self.assertEqual(species["ai"], {"name": "Xyleborus affinis", "sure": 64, "agrees": True})
        self.assertNotIn("truth", genus)

    def test_the_players_never_include_your_own_answers(self):
        roi = self.roi(self.t_affinis, validated=False)
        earlier = GameRound.objects.create(player=self.user, mode="classify", items=[])
        GameAnswer.objects.create(round=earlier, player=self.user, mode="classify", index=0, roi=roi, **PLAT)
        self.assertEqual(self.classify(roi, AFFINIS)["classify"]["players"], 0)   # the first besides yourself
        self.other("a", roi, AFFINIS)
        species = self.classify(roi, AFFINIS)["classify"]["ranks"][3]["players"]
        self.assertEqual((species["name"], species["votes"], species["sure"]), ("Xyleborus affinis", 1, 100))

    def test_a_skip_shows_no_truth(self):
        roi = self.roi(self.t_affinis)
        review = self.classify(roi, {"skipped": True})
        self.assertTrue(review["skipped"])
        self.assertNotIn("classify", review)
        self.assertNotIn("affinis", str(review).lower())
        self.assertEqual(review["headline"], "Skipped · −0.3")
        self.assertEqual(len(review["images"]), 1)

    def test_a_retry_is_marked(self):
        roi = self.roi(self.t_affinis)
        self.assertTrue(self.answer({"a": str(roi.id), "b": None, "check": True, "retry": True}, AFFINIS)["again"])

    def test_an_answer_held_while_its_name_is_checked_shows_no_truth(self):
        roi = self.roi(self.t_affinis)
        self.classify(roi, AFFINIS)
        answer = GameAnswer.objects.get(player=self.user)
        GameAnswer.objects.filter(pk=answer.pk).update(score_hold=True)
        answer.refresh_from_db()
        game_scoring.score_new_answer(answer)
        review = game_answer_review.past(answer.round, 0)
        self.assertTrue(review["held"])
        self.assertNotIn("classify", review)
        self.assertEqual(review["headline"], "Not counted while its name is checked")


class SimilarityTests(ReviewCase):
    def test_both_validated_names_a_and_b_in_the_order_shown_and_the_true_relation(self):
        a, b = self.roi(self.t_affinis), self.roi(self.t_ferr)
        review = self.answer({"a": str(a.id), "b": str(b.id), "check": True, "flip": True, "mode": "pair"},
                             {"pair_answer": "genus"})
        pair = review["pair"]
        self.assertEqual((pair["said"], pair["truth"], pair["state"], review["verdict"]),
                         ("Same genus", "Same genus", "right", "right"))
        self.assertEqual([(s["letter"], s["name"]) for s in pair["sides"]],
                         [("A", "Xyleborus ferrugineus"), ("B", "Xyleborus affinis")])   # flipped: B was shown first
        self.assertEqual(review["celebrate"]["kind"], "validated")

    def test_an_unvalidated_beetle_shows_its_partner_and_what_players_and_ibbi_ai_make_of_it(self):
        open_one, partner = self.roi(self.t_affinis, validated=False), self.roi(self.t_ferr)
        self.other("a", open_one, AFFINIS)
        self.predict(open_one, self.t_ferr, 0.85)
        review = self.answer({"a": str(open_one.id), "b": str(partner.id), "check": False, "mode": "pair"},
                             {"pair_answer": "genus"})
        self.assertEqual(review["points"]["basis"], "agreement")
        unknown, known = review["pair"]["sides"]   # not flipped: A is the open one, as shown
        self.assertEqual((known["letter"], known["validated"], known["name"]), ("B", True, "Xyleborus ferrugineus"))
        self.assertEqual((unknown["letter"], unknown["validated"]), ("A", False))
        self.assertNotIn("name", unknown)   # its own (unchecked) label is not shown as a name
        self.assertEqual((unknown["players"]["name"], unknown["players"]["rung"], unknown["players"]["agrees"]),
                         ("Xyleborus affinis", "Same genus", True))
        self.assertEqual((unknown["ai"]["name"], unknown["ai"]["sure"], unknown["ai"]["rung"], unknown["ai"]["agrees"]),
                         ("Xyleborus ferrugineus", 85, "Same species", False))

    def test_not_sure_is_a_skip(self):
        a, b = self.roi(self.t_affinis), self.roi(self.t_ferr)
        review = self.answer({"a": str(a.id), "b": str(b.id), "check": True, "mode": "pair"}, {"pair_answer": "unsure"})
        self.assertTrue(review["skipped"])
        self.assertNotIn("pair", review)


GENUS_GROUP = {"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus"}


class OddOneOutTests(ReviewCase):
    def setUp(self):
        super().setUp()
        self.rest = [self.roi(self.t_affinis), self.roi(self.t_ferr), self.roi(self.t_affinis)]
        self.odd = self.roi(self.t_plat)

    def grid(self, tiles, body):
        item = {"a": str(self.odd.id), "b": None, "check": True, "mode": "odd", "tiles": [str(t.id) for t in tiles],
                "rank": "genus", "group": GENUS_GROUP}
        return self.answer(item, body)

    def test_the_odd_one_picked(self):
        tiles = [self.rest[0], self.odd, self.rest[1], self.rest[2]]
        review = self.grid(tiles, {"pick": 1})
        grid = review["grid"]
        self.assertEqual((review["verdict"], grid["odd"], grid["pick"], grid["odd_name"]), ("right", 1, 1, "Platypus"))
        self.assertEqual(grid["tiles"][1]["state"], "right")
        self.assertAlmostEqual(grid["tiles"][1]["points"], self.earned("odd"), places=1)
        self.assertEqual([t["name"] for t in grid["tiles"]], ["Xyleborus", "Platypus", "Xyleborus", "Xyleborus"])
        self.assertEqual(review["celebrate"]["kind"], "validated")
        self.assertEqual(len(review["images"]), 4)

    def test_a_wrong_pick_and_names_no_deeper_than_the_grid(self):
        tiles = [self.rest[0], self.odd, self.rest[1], self.rest[2]]
        review = self.grid(tiles, {"pick": 2})
        grid = review["grid"]
        self.assertEqual((review["verdict"], grid["tiles"][2]["state"], grid["tiles"][1]["state"]), ("wrong", "wrong", "odd"))
        self.assertLess(grid["tiles"][2]["points"], 0)
        self.assertTrue(review["headline"].startswith("Not quite · −"))
        self.assertIsNone(review["celebrate"])
        self.assertNotIn("ferrugineus", str(grid))   # a genus round names no species

    def test_unvalidated_beetles_show_what_ibbi_ai_and_players_say_about_them(self):
        inside, outside = self.roi(validated=False), self.roi(validated=False)
        self.predict(inside, self.t_affinis, 0.95)    # IBBI-AI: one of the group (a Xyleborus)
        self.predict(outside, self.t_plat, 0.8)       # IBBI-AI: not one of them
        tiles = [self.rest[0], inside, self.odd, outside]
        review = self.grid(tiles, {"pick": 1})        # picks the one IBBI-AI puts in the group
        cells = review["grid"]["tiles"]
        self.assertEqual((review["verdict"], review["points"]["basis"], cells[1]["state"]), (None, "agreement", "pick"))
        self.assertEqual((cells[1]["ai"]["name"], cells[1]["ai"]["sure"], cells[1]["ai"]["in"], cells[1]["pays"]),
                         ("Xyleborus", 95, True, False))
        self.assertEqual((cells[3]["ai"]["in"], cells[3]["pays"]), (False, True))   # picking that one would pay
        self.assertEqual((cells[1]["validated"], cells[1]["name"]), (False, ""))
        self.assertEqual(review["headline"], "Not checked yet")
        self.assertIsNone(review["celebrate"])

    def test_a_skip_gives_nothing_away(self):
        review = self.grid([self.rest[0], self.odd, self.rest[1], self.rest[2]], {"skipped": True})
        self.assertNotIn("grid", review)
        self.assertNotIn("Platypus", str(review))
        self.assertEqual(review["headline"], "Skipped · +0.3")


class SelectAllTests(ReviewCase):
    def setUp(self):
        super().setUp()
        self.members = [self.roi(self.t_affinis) for _ in range(3)]
        self.others = [self.roi(self.t_ferr), self.roi(self.t_ferr), self.roi(self.t_plat)]

    def grid(self, tiles, picks, rank="species"):
        group = game.lineage(self.t_affinis, rank)
        item = {"a": str(self.members[0].id), "b": None, "check": True, "mode": "select",
                "tiles": [str(t.id) for t in tiles], "rank": rank, "group": group}
        return self.answer(item, {"picks": picks})

    def test_every_tile_with_its_state_and_points_adding_up_to_the_answer(self):
        tap = self.roi(validated=False)
        self.predict(tap, self.t_affinis, 0.95)
        tiles = [*self.members, *self.others, tap]
        review = self.grid(tiles, [0, 1, 3, 6])   # two members, one non-member, one beetle nobody has checked
        grid = review["grid"]
        self.assertEqual([t["state"] for t in grid["tiles"]], ["right", "right", "missed", "wrong", "clear", "clear", "vote"])
        self.assertEqual((grid["right"], grid["members"], grid["wrong"], review["verdict"]), (2, 3, 1, "wrong"))
        # each tile is rounded to 2 places, so the sum may be a few hundredths off
        self.assertAlmostEqual(sum(t["points"] or 0 for t in grid["tiles"]), self.earned("select"), delta=0.02)
        self.assertGreater(grid["tiles"][0]["points"], 0)
        self.assertAlmostEqual(grid["tiles"][3]["points"], -game_scoring.wrong_cost() * grid["tiles"][0]["points"],
                               delta=0.01)
        self.assertEqual((grid["tiles"][2]["points"], grid["tiles"][6]["points"]), (0.0, None))
        vote = grid["tiles"][6]
        self.assertEqual((vote["validated"], vote["ai"]["in"], vote["pays"]), (False, True, True))
        self.assertEqual(review["headline"], "Found 2 of 3 · 1 wrong · −1")
        self.assertIsNone(review["celebrate"])   # the wrong tap cost more than two members earned (#530)

    def test_a_perfect_grid(self):
        review = self.grid([*self.members, *self.others], [0, 1, 2])
        self.assertEqual((review["verdict"], review["celebrate"]["kind"]), ("right", "validated"))
        self.assertEqual(review["headline"], "Found 3 of 3 · +8.8")   # 1.25 × 7, a grid from before the ladder

    def test_names_stop_at_the_grids_rank(self):
        review = self.grid([*self.members, *self.others], [0, 1, 2], rank="genus")
        names = [t["name"] for t in review["grid"]["tiles"]]
        self.assertEqual(names, ["Xyleborus"] * 5 + ["Platypus"])
        self.assertEqual(review["grid"]["target"], "Xyleborus")


class ConfettiTests(ReviewCase):
    def test_the_size_follows_the_points_on_a_log_scale(self):
        size = game_answer_review.confetti_size
        self.assertEqual(size(45), 1.0)                       # a fully correct species: full size
        self.assertEqual(size(0), 0.2)
        self.assertGreaterEqual(size(0.5), 0.2)
        self.assertLess(size(3), size(12.6))
        self.assertLess(size(12.6), size(45))
        self.assertEqual(size(500), 1.0)
        with override_settings(GAME_CONFETTI_FULL_POINTS=10):
            self.assertEqual(size(10), 1.0)

    def test_more_points_more_confetti_and_none_without_points(self):
        full = self.classify(self.roi(self.t_affinis), AFFINIS)["celebrate"]
        part = self.classify(self.roi(self.t_affinis), dict(AFFINIS, species=""))["celebrate"]   # stopped at the genus
        self.assertEqual((full["kind"], part["kind"]), ("validated", "partial"))
        self.assertGreater(full["size"], part["size"])
        self.assertIsNone(self.classify(self.roi(self.t_affinis, validated=False), AFFINIS)["celebrate"])

    def test_agreeing_with_a_sure_ibbi_ai_gets_the_smallest_burst(self):
        roi = self.roi(self.t_affinis, validated=False)
        self.predict(roi, self.t_affinis, 0.8)
        self.assertEqual(self.classify(roi, AFFINIS)["celebrate"], {"kind": "ai", "size": 0.2, "colour": "blue"})   # #572, #615
        unsure = self.roi(self.t_affinis, validated=False)
        self.predict(unsure, self.t_affinis, 0.3)
        self.assertIsNone(self.classify(unsure, AFFINIS)["celebrate"])

    def test_points_by_agreement_get_paper_confetti_sized_by_them(self):
        roi = self.roi(self.t_affinis, validated=False)
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[{"a": str(roi.id), "b": None, "check": False}])
        answer = GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=0, roi=roi, **AFFINIS)
        AnswerPoints.objects.create(answer=answer, points=9.5, basis=AnswerPoints.Basis.CONSENSUS,
                                    detail={"agreement": {"species": 0.8}, "participation": 0.5})
        review = game_answer_review.past(rnd, 0)
        self.assertEqual(review["headline"], "Not checked yet · +9 so far")
        self.assertEqual(review["celebrate"], {"kind": "players", "size": game_answer_review.confetti_size(9), "colour": "purple"})
