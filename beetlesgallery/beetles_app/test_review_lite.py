"""
A lighter review card (#569): the names on the photos in the annotation page's Save grey, big and over the whole photo,
with a coloured dot for who says so (green validated, blue IBBI-AI, purple the players, glowing purple when an expert
backs them) and the confidence on every line; under the photos a headline and at most one short line; one ring scheme
for every game (solid on what was chosen, fainter dashed on what was left); a reviewed photo opens whole from a click
or its own button (a tap turns its names off, #600), and its Flag never re-answers.
"""
from django.urls import reverse

from beetlesgallery.beetles_app import game_answer_review
from beetlesgallery.beetles_app.models import GameAnswer
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_answer_review import ReviewCase


def words(parts):
    return "".join(p if isinstance(p, str) else p.get("name", "") for p in parts)


class HeadlineTests(ReviewCase):
    def pair(self, a, b, said):
        return self.answer({"a": str(a.id), "b": str(b.id), "check": True, "mode": "pair"}, {"pair_answer": said})

    def test_a_right_similarity_says_the_rung_once(self):
        review = self.pair(self.roi(self.t_affinis), self.roi(self.t_ferr), "genus")
        self.assertRegex(review["headline"], r"^Correct · Same genus · \+[\d.]+$")

    def test_a_wrong_similarity_says_what_was_said_and_what_it_is_in_one_line(self):
        review = self.pair(self.roi(self.t_affinis), self.roi(self.t_ferr), "species")
        self.assertRegex(review["headline"], r"^Not quite · You said Same species · It's Same genus · −[\d.]+$")

    def test_no_points_word_and_no_pending_filler(self):
        review = self.classify(self.roi(self.t_affinis), AFFINIS)
        self.assertEqual(review["headline"], "Correct to species · +45")
        review = self.classify(self.roi(validated=False), AFFINIS)
        self.assertEqual(review["headline"], "Not checked yet")


class GridNoteTests(ReviewCase):
    def test_odd_one_out_names_the_odd_one_only(self):
        rest = [self.roi(self.t_affinis) for _ in range(3)]
        odd = self.roi(self.t_plat)
        item = {"a": str(odd.id), "b": None, "check": True, "mode": "odd", "rank": "subfamily",
                "tiles": [str(t.id) for t in [odd, *rest]], "group": {"subfamily": "Scolytinae"}}
        grid = self.answer(item, {"pick": 0})["grid"]
        self.assertEqual(words(grid["note"]), "Odd one: 1 · Platypodinae")
        self.assertNotIn("lines", grid)
        self.assertNotIn("lead", grid)


class SourceTests(ReviewCase):
    def test_the_players_name_says_whether_an_expert_backs_it(self):
        vote = {"value": "Xyleborus", "support": 0.8, "votes": 3}
        self.assertFalse(game_answer_review._likeliest("genus", vote, None)["expert"])
        backed = game_answer_review._likeliest("genus", dict(vote, trusted=True, trusted_votes=1), None)
        self.assertEqual((backed["source"], backed["sure"], backed["expert"]), ("players", 80, True))

    def test_every_rank_of_ibbi_ai_has_its_confidence(self):
        roi = self.roi(validated=False)
        self.predict(roi, self.t_affinis, 0.62, said={"subfamily": {"value": "Scolytinae", "confidence": 1.0}})
        beetle, = self.classify(roi, AFFINIS)["beetles"]
        self.assertEqual([(r["source"], r["sure"]) for r in beetle["ranks"]],
                         [("ai", 100), ("ai", 62), ("ai", 62), ("ai", 62)])

    def test_back_after_a_reload_knows_where_its_answer_is(self):
        self.classify(self.roi(self.t_affinis), AFFINIS)
        answer = GameAnswer.objects.get()
        res = self.client.get(reverse("game_past_review", args=[answer.round_id, 0]))
        self.assertEqual(res.json()["review"]["where"], {"round": str(answer.round_id), "index": 0})


class PageTests(ReviewCase):
    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=["mixed"])).content.decode()

    def test_the_names_are_light_grey_and_cover_the_photo(self):
        page = self.page()
        self.assertIn("background: rgba(107,114,128,.8)", page)   # gray-500, the .btn-primary Save
        self.assertIn(".rv-names { position: absolute; inset: 0;", page)
        self.assertNotIn("rgba(17,24,39,.8)", page)

    def test_a_dot_for_each_source_with_its_words(self):
        page = self.page()
        for rule in (".rv-dot.truth { background: #16a34a; }", ".rv-dot.ai { background: #2563eb; }",
                     ".rv-dot.players, .rv-dot.expert { background: #9333ea; }",
                     "@media (prefers-reduced-motion: reduce) { .rv-dot.expert { animation: none; } }"):
            self.assertIn(rule, page)
        self.assertIn('dot.setAttribute("aria-label", SOURCE_DOT[kind]);', page)
        self.assertIn("playersDot(r.expert)", page)

    def test_no_legend_and_no_line_per_tile(self):
        page = self.page()
        self.assertNotIn("Rings: green", page)
        self.assertNotIn("rv-tile", page)
        self.assertIn('node("p", "rv-line", ...line)', page)

    def test_one_ring_scheme_for_every_game(self):
        page = self.page()
        self.assertIn(":is(#photos, #previous-photos) .cell:is(.ring-missed, .ring-avoided, .ring-unknown)::after "
                      "{ border-style: dashed; border-width: 2px; opacity: 0.7; }", page)
        self.assertIn('if (chosen) return good === true ? "ring-right" : good === false ? "ring-wrong" : "ring-open";', page)
        self.assertIn('const VERDICT_RING = { right: "ring-right", partly: "ring-partly", wrong: "ring-wrong" };', page)
        for gone in ("sel-missed", "odd-one", "sel-vote"):
            self.assertNotIn(gone, page)

    def test_a_tap_opens_the_whole_photo_and_its_flag_never_reanswers(self):
        page = self.page()
        self.assertIn("if (im) openLightbox(im.url, im.box, i, null, previous.where);", page)
        self.assertIn('reviewPhotos($("previous-photos"));', page)
        self.assertIn("if (lightboxReview) {", page)

    def test_the_headline_dot_and_short_odd_question(self):
        page = self.page()
        self.assertIn('const dot = node("span", "rv-verdict " + (verdict || "none"));', page)
        self.assertIn('"Which one is a different " + rank + "?"', page)
