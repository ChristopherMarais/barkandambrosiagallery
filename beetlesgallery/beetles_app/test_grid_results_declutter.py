"""
A grid's photos after the answer and under Back (Odd One Out, Find Them All) carry one name each, at the grid's rank
(owner: the full lineage, dots and ID types cluttered the grid). A validated beetle's name stands alone; a name from
IBBI-AI or the players has how sure they are after it, in their colour. Every rank, both sources and their
confidences stay on the whole photo.
"""
from pathlib import Path

from django.test import SimpleTestCase

from beetlesgallery.beetles_app import game
from beetlesgallery.beetles_app.test_game_answer_review import GENUS_GROUP, ReviewCase

GAME_PLAY = Path(__file__).resolve().parent.parent / "templates" / "beetles" / "game_play.html"


class GridTileNamesPageTests(SimpleTestCase):
    def setUp(self):
        self.html = GAME_PLAY.read_text(encoding="utf-8")

    def test_a_grid_tile_takes_the_one_name_and_the_whole_photo_keeps_everything(self):
        self.assertIn("const at = !full && review && review.grid && review.grid.rank;\n    if (at) return rankLabel(b, at);",
                      self.html)
        # the whole photo still gets every rank and both sources
        self.assertIn('$("lb-names").replaceChildren(...(names ? [namesLabel(names, previous, true)] : []));', self.html)

    def test_the_label_has_no_validated_dot_rank_tags_or_id_types(self):
        body = self.html.split("function rankLabel(b, rank) {", 1)[1].split("\n  }\n", 1)[0]
        for clutter in ('sourceDot("truth")', "RANK_ABBR", '"idl"', "columnCell", '"hdr"'):
            self.assertNotIn(clutter, body)
        self.assertIn('const r = (b.ranks || []).find((x) => x.rank === rank) || {};', body)

    def test_only_an_unvalidated_name_shows_how_sure_and_whose_it_is(self):
        self.assertIn('if (!b.validated && r.sure != null && SURE_BY[r.source]) {', self.html)
        self.assertIn('const sure = node("span", "sure " + r.source, r.sure + "%");', self.html)
        self.assertIn(".rv-names.at-rank .sure.ai { color: #93c5fd; }", self.html)        # blue: IBBI-AI
        self.assertIn(".rv-names.at-rank .sure.players { color: #d8b4fe; }", self.html)   # purple: the players
        self.assertIn("if (!b.validated && SURE_BY[r.source]) line.prepend(sourceDot(r.source));", self.html)   # their dot

    def test_the_name_is_centred_and_sized_per_grid(self):
        self.assertIn(".rv-names.at-rank > span { display: block; text-align: center; overflow-wrap: break-word; }", self.html)
        for size in ("9", "16", "25"):
            self.assertIn(f'#game :is(#photos, #previous-photos)[data-size="{size}"] .cell > .rv-names.at-rank {{', self.html)


class GridReviewCarriesTheTileNameTests(ReviewCase):
    """What the tile reads from the review: the grid's rank, and each beetle's name, source and confidence there."""

    def test_odd_one_out(self):
        unchecked = self.roi(validated=False)
        self.predict(unchecked, self.t_affinis, 0.9)
        odd = self.roi(self.t_plat)
        tiles = [self.roi(self.t_affinis), odd, self.roi(self.t_ferr), unchecked]
        review = self.answer({"a": str(odd.id), "b": None, "check": True, "mode": "odd",
                              "tiles": [str(t.id) for t in tiles], "rank": "genus", "group": GENUS_GROUP}, {"pick": 1})
        self.assertEqual(review["grid"]["rank"], "genus")
        at = [next(r for r in b["ranks"] if r["rank"] == "genus") for b in review["beetles"]]
        self.assertEqual([r["name"] for r in at], ["Xyleborus", "Platypus", "Xyleborus", "Xyleborus"])
        self.assertTrue(review["beetles"][0]["validated"])
        self.assertEqual((review["beetles"][3]["validated"], at[3]["source"]), (False, "ai"))
        self.assertIsInstance(at[3]["sure"], int)
        # the whole photo still has every rank
        self.assertEqual([r["rank"] for r in review["beetles"][0]["ranks"]], list(game.RANKS))

    def test_find_them_all(self):
        members = [self.roi(self.t_affinis) for _ in range(2)]
        tiles = [*members, self.roi(self.t_plat)]
        review = self.answer({"a": str(members[0].id), "b": None, "check": True, "mode": "select",
                              "tiles": [str(t.id) for t in tiles], "rank": "genus",
                              "group": game.lineage(self.t_affinis, "genus")}, {"picks": [0, 1]})
        self.assertEqual(review["grid"]["rank"], "genus")
        names = [next(r for r in b["ranks"] if r["rank"] == "genus")["name"] for b in review["beetles"]]
        self.assertEqual(names, ["Xyleborus", "Xyleborus", "Platypus"])


class VoteTagTests(SimpleTestCase):
    """A pick nobody can check yet has no "vote" tag on its photo; one line under the result counts them."""

    def setUp(self):
        self.html = GAME_PLAY.read_text(encoding="utf-8")

    def test_no_vote_tag_on_the_photos(self):
        self.assertNotIn('["vote", "flat"]', self.html)

    def test_the_line_under_the_result_counts_the_votes(self):
        self.assertIn('" of your picks count as votes: no one knows these yet."', self.html)
        self.assertIn('"1 of your picks counts as a vote: no one knows this one yet."', self.html)
