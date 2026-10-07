"""
The review card's IDs (#615): a validated name is "Validated ID" (never "Expert ID"); every taxon level has its own
dots (green validated, blue AI ID, purple Player ID, glowing purple when a proven expert backs the players), with its
ID type after the name; the AI and Player confidences sit in two columns, each only where that source exists; and the
confetti takes the colour of the ID type that gave the points.
"""
from django.urls import reverse

from beetlesgallery.beetles_app import game_answer_review
from beetlesgallery.beetles_app.models import Beetles
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_answer_review import ReviewCase

RANK_COUNT = 4


class WordingTests(ReviewCase):
    def test_a_validated_name_is_validated_id_whichever_tier_it_has(self):
        roi = self.roi(self.t_affinis)
        Beetles.objects.filter(pk=roi.pk).update(label_source="expert")
        beetle, = self.classify(roi, AFFINIS)["beetles"]
        self.assertEqual(beetle["tier"], "Expert ID")   # the stored tier, kept in the data ...
        self.assertEqual([r["label"] for r in beetle["ranks"]], ["Validated ID"] * RANK_COUNT)   # ... not on the card

    def test_the_page_words_for_the_dots_are_the_ids(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        self.assertIn('const SOURCE_DOT = { truth: "Validated ID", ai: "AI ID", players: "Player ID", '
                      'expert: "Player ID, backed by an expert" };', page)
        self.assertIn('const ID_COLUMN = { ai: "AI ID", players: "Player ID" };', page)


class DotsPerTaxonLevelTests(ReviewCase):
    def test_a_validated_beetle_has_a_green_dot_on_every_level(self):
        beetle, = self.classify(self.roi(self.t_affinis), AFFINIS)["beetles"]
        self.assertEqual([r["dots"] for r in beetle["ranks"]], [["truth"]] * RANK_COUNT)

    def test_ibbi_ai_alone_is_a_blue_dot_labelled_ai_id_with_only_its_column(self):
        roi = self.roi(self.t_affinis, validated=False)
        self.predict(roi, self.t_affinis, 0.64)
        beetle, = self.classify(roi, AFFINIS)["beetles"]
        species = beetle["ranks"][3]
        self.assertEqual((species["dots"], species["label"]), (["ai"], "AI ID"))
        self.assertEqual(beetle["columns"], ["ai"])
        self.assertIsNone(species["columns"]["players"])
        self.assertEqual(species["columns"]["ai"]["sure"], 64)

    def test_players_alone_is_a_purple_dot_labelled_player_id_with_only_its_column(self):
        roi = self.roi(self.t_affinis, validated=False)
        self.other("a", roi, AFFINIS)
        beetle, = self.classify(roi, AFFINIS)["beetles"]
        species = beetle["ranks"][3]
        self.assertEqual((species["dots"], species["label"]), (["players"], "Player ID"))
        self.assertEqual(beetle["columns"], ["players"])
        self.assertIsNone(species["columns"]["ai"])

    def test_both_agreeing_shows_both_dots_and_both_columns(self):
        roi = self.roi(self.t_affinis, validated=False)
        self.other("a", roi, AFFINIS)
        self.predict(roi, self.t_affinis, 0.64)
        beetle, = self.classify(roi, AFFINIS)["beetles"]
        species = beetle["ranks"][3]
        self.assertEqual((species["dots"], species["label"]), (["ai", "players"], "AI ID · Player ID"))
        self.assertEqual(beetle["columns"], ["ai", "players"])
        self.assertEqual((species["columns"]["ai"]["sure"], species["columns"]["players"]["sure"]), (64, 100))

    def test_each_level_has_its_own_dots(self):
        # the players say Xyleborini and IBBI-AI Platypodini at the tribe; both say the species
        roi = self.roi(self.t_affinis, validated=False)
        self.other("a", roi, AFFINIS)
        self.predict(roi, self.t_affinis, 0.64, said={"tribe": {"value": "Platypodini", "confidence": 0.9}})
        beetle, = self.classify(roi, AFFINIS)["beetles"]
        tribe, species = beetle["ranks"][1], beetle["ranks"][3]
        self.assertEqual(tribe["dots"], ["players"])
        self.assertEqual(tribe["columns"]["ai"]["name"], "Platypodini")   # the other source names something else
        self.assertEqual(species["dots"], ["ai", "players"])

    def test_a_name_nobody_gives_has_no_dots_or_labels(self):
        beetle, = self.classify(self.roi(validated=False), AFFINIS)["beetles"]
        self.assertEqual((beetle["source"], beetle["columns"]), ("", []))
        self.assertTrue(all(r["dots"] == [] and r["label"] == "" and r["name"] == "" for r in beetle["ranks"]))

    def test_a_proven_expert_behind_the_players_glows_purple(self):
        vote = {"value": "Xyleborus", "support": 0.8, "votes": 3, "trusted": True, "trusted_votes": 2}
        tip = {"value": "Xyleborus", "confidence": 0.7}
        found = game_answer_review._likeliest("genus", vote, tip)
        self.assertEqual((found["dots"], found["label"], found["expert"]), (["ai", "expert"], "AI ID · Player ID", True))
        self.assertEqual(found["columns"]["players"], {"name": "Xyleborus", "sure": 80, "votes": 3, "experts": 2})
        self.assertEqual(game_answer_review._likeliest("genus", vote, None)["dots"], ["expert"])
        self.assertEqual(game_answer_review._likeliest("genus", dict(vote, trusted=False), None)["dots"], ["players"])


class ColumnTests(ReviewCase):
    def test_the_two_columns_carry_the_counts_and_the_confidences(self):
        roi = self.roi(self.t_affinis, validated=False)
        self.other("a", roi, AFFINIS)
        self.other("b", roi, AFFINIS)
        self.predict(roi, self.t_affinis, 0.64)
        beetle, = self.classify(roi, AFFINIS)["beetles"]
        players = beetle["ranks"][3]["columns"]["players"]
        self.assertEqual((players["votes"], players["sure"], players["experts"]), (2, 100, 0))

    def test_a_validated_beetle_has_no_columns(self):
        beetle, = self.classify(self.roi(self.t_affinis), AFFINIS)["beetles"]
        self.assertEqual(beetle["columns"], [])
        self.assertTrue(all(r["columns"] == {"ai": None, "players": None} for r in beetle["ranks"]))


class ConfettiColourTests(ReviewCase):
    def celebrate(self, basis="agreement", earned=10.0, **facts):
        out = {"skipped": False, "held": False, "points": {"earned": earned, "basis": basis}}
        return game_answer_review._celebrate(out, facts)

    def test_green_for_validated_whatever_else_agreed(self):
        for facts in ({"complete": True}, {"complete": True, "ai_agrees": True, "players_agree": True}):
            self.assertEqual(self.celebrate("truth", **facts)["colour"], "green")
        self.assertEqual(self.celebrate("truth", earned=30.0)["colour"], "green")   # partly correct
        self.assertEqual(self.celebrate("truth", earned=0.5)["colour"], "green")    # a small pop

    def test_blue_for_ai_id(self):
        self.assertEqual((self.celebrate(ai_agrees=True)["kind"], self.celebrate(ai_agrees=True)["colour"]), ("ai", "blue"))
        self.assertEqual(self.celebrate(earned=0.0, ai_agrees=True)["colour"], "blue")

    def test_purple_for_player_id_and_the_expert_glow(self):
        self.assertEqual(self.celebrate(players_agree=True)["colour"], "purple")
        self.assertEqual(self.celebrate(ai_agrees=True, players_agree=True)["colour"], "purple")
        expert = self.celebrate(players_agree=True, expert_agrees=True)
        self.assertEqual((expert["kind"], expert["colour"]), ("expert", "purple"))
        self.assertEqual(self.celebrate(earned=0.3)["colour"], "purple")   # points from the players' vote, very few

    def test_the_burst_sizes_are_unchanged(self):
        self.assertEqual(self.celebrate("truth", complete=True)["size"], game_answer_review.confetti_size(10.0))
        self.assertEqual(self.celebrate(earned=0.3)["size"], game_answer_review.confetti_size(0.3))

    def test_a_validated_right_answer_is_green_on_the_card(self):
        review = self.classify(self.roi(self.t_affinis), AFFINIS)
        self.assertEqual((review["celebrate"]["kind"], review["celebrate"]["colour"]), ("validated", "green"))

    def test_an_ibbi_ai_agreement_on_an_open_beetle_is_blue_on_the_card(self):
        roi = self.roi(self.t_affinis, validated=False)
        self.predict(roi, self.t_affinis, 0.9)
        review = self.classify(roi, AFFINIS)
        self.assertEqual((review["celebrate"]["kind"], review["celebrate"]["colour"]), ("ai", "blue"))

    def test_no_confetti_for_a_skip(self):
        review = self.classify(self.roi(self.t_affinis), {"skipped": True})
        self.assertIsNone(review["celebrate"])


class SeenBeforeTests(ReviewCase):
    def pair(self, a, b):
        return self.answer({"a": str(a.id), "b": str(b.id), "check": True, "mode": "pair"}, {"pair_answer": "genus"})

    def test_a_photo_seen_before_is_marked_on_the_photo_not_the_headline(self):
        roi = self.roi(self.t_affinis)
        self.classify(roi, AFFINIS)
        review = self.classify(roi, AFFINIS)
        self.assertEqual(review["seen"], [True])
        self.assertNotIn("Seen before", review["headline"])

    def test_a_pair_marks_only_the_photo_seen_before(self):
        a, b = self.roi(self.t_affinis), self.roi(self.t_ferr)
        self.classify(a, AFFINIS)
        self.assertEqual(self.pair(a, b)["seen"], [True, False])

    def test_a_new_photo_is_not_marked(self):
        self.assertEqual(self.classify(self.roi(self.t_affinis), AFFINIS)["seen"], [False])

    def test_the_page_has_no_headline_pill(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        self.assertNotIn('node("span", "rv-pill", "Seen before")', page)
        self.assertIn('if (review.seen && review.seen[i]) c.appendChild(seenMark("photo-seen"));', page)


class NoNameYetTests(ReviewCase):
    def test_a_beetle_nobody_has_named_shows_not_named_yet(self):
        beetle, = self.classify(self.roi(validated=False), AFFINIS)["beetles"]
        self.assertFalse(beetle["validated"])
        self.assertEqual(beetle["source"], "")
