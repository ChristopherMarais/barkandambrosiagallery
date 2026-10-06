"""
The review after every answer names every beetle shown at all four ranks (#541): the true names of a validated beetle,
or the likeliest name of one nobody has validated with who says so and how sure. A grid's card has at most one short
line (#569): the odd ones, or the beetles missed. Since every grid beetle is named, they all count as shown to that
player (game.reveals).
The rings round grid photos are drawn on top of the photo.
"""
from django.urls import reverse

from beetlesgallery.beetles_app import game, game_answer_review
from beetlesgallery.beetles_app.models import Beetles, GameAnswer
from beetlesgallery.beetles_app.test_game import AFFINIS, FERR
from beetlesgallery.beetles_app.test_game_answer_review import GENUS_GROUP, ReviewCase

TRUE_AFFINIS = ["Scolytinae", "Xyleborini", "Xyleborus", "Xyleborus affinis"]


def text(line):
    """A card line as the player reads it."""
    parts = line["parts"] if isinstance(line, dict) else line
    return "".join(p if isinstance(p, str) else p.get("say") or p.get("name") for p in parts)


class NamesOnEveryPhotoTests(ReviewCase):
    def test_a_validated_beetle_shows_its_true_names_at_every_rank(self):
        roi = self.roi(self.t_affinis)
        Beetles.objects.filter(pk=roi.pk).update(label_source="taxonomist")
        beetle, = self.classify(roi, FERR)["beetles"]
        self.assertEqual((beetle["validated"], beetle["tier"]), (True, "Taxonomist ID"))
        self.assertEqual([r["name"] for r in beetle["ranks"]], TRUE_AFFINIS)
        self.assertEqual({r["source"] for r in beetle["ranks"]}, {"truth"})

    def test_an_unvalidated_beetle_shows_the_likeliest_name_with_who_says_so(self):
        roi = self.roi(self.t_affinis, validated=False)
        self.other("a", roi, FERR)
        self.predict(roi, self.t_affinis, 0.64, said={"tribe": {"value": "Xyleborini", "confidence": 0.9}})
        beetle, = self.classify(roi, AFFINIS)["beetles"]
        self.assertFalse(beetle["validated"])
        species = beetle["ranks"][3]
        self.assertEqual((species["name"], species["source"], species["sure"], species["votes"]),
                         ("Xyleborus ferrugineus", "players", 100, 1))   # the other player, surer than IBBI-AI
        ranks = {r["rank"]: r for r in beetle["ranks"]}
        self.assertEqual((ranks["tribe"]["source"], ranks["tribe"]["sure"]), ("players", 100))   # a tie: the players

    def test_ibbi_ai_alone_and_nobody_at_all(self):
        roi = self.roi(self.t_affinis, validated=False)
        self.predict(roi, self.t_affinis, 0.52)
        beetle, = self.classify(roi, AFFINIS)["beetles"]
        self.assertEqual([(r["name"], r["source"], r["sure"]) for r in beetle["ranks"]],
                         [(n, "ai", 52) for n in TRUE_AFFINIS])
        nobody, = self.classify(self.roi(self.t_affinis, validated=False), AFFINIS)["beetles"]
        self.assertEqual({(r["name"], r["source"]) for r in nobody["ranks"]}, {("", "")})

    def test_both_beetles_of_a_pair_in_the_order_shown(self):
        a, b = self.roi(self.t_affinis), self.roi(self.t_plat)
        review = self.answer({"a": str(a.id), "b": str(b.id), "check": True, "flip": True, "mode": "pair"},
                             {"pair_answer": "genus"})
        self.assertEqual([x["ranks"][2]["name"] for x in review["beetles"]], ["Platypus", "Xyleborus"])

    def test_no_names_after_a_skip(self):
        self.assertNotIn("beetles", self.classify(self.roi(self.t_affinis), {"skipped": True}))


class OddOneOutLinesTests(ReviewCase):
    def setUp(self):
        super().setUp()
        self.rest = [self.roi(self.t_affinis), self.roi(self.t_ferr), self.roi(self.t_affinis)]
        self.odd = self.roi(self.t_plat)

    def grid(self, tiles, body):
        item = {"a": str(self.odd.id), "b": None, "check": True, "mode": "odd", "tiles": [str(t.id) for t in tiles],
                "rank": "genus", "group": GENUS_GROUP}
        return self.answer(item, body)

    def test_the_card_names_the_odd_one_and_nothing_more(self):
        review = self.grid([self.rest[0], self.odd, self.rest[1], self.rest[2]], {"pick": 2})
        grid = review["grid"]
        self.assertEqual(text(grid["note"]), "Odd one: 2 · Platypus")   # one short line (#569)
        self.assertNotIn("lines", grid)
        self.assertNotIn("lead", grid)
        self.assertEqual([(t["belongs"], t["chosen"]) for t in grid["tiles"]],
                         [(True, False), (False, False), (True, True), (True, False)])
        # the photos carry every name, past the grid's rank
        self.assertEqual(review["beetles"][2]["ranks"][3]["name"], "Xyleborus ferrugineus")

    def test_the_right_pick_reads_the_same(self):
        grid = self.grid([self.rest[0], self.odd, self.rest[1], self.rest[2]], {"pick": 1})["grid"]
        self.assertEqual(text(grid["note"]), "Odd one: 2 · Platypus")

    def test_beetles_nobody_has_checked_are_left_out_of_the_line(self):
        inside, outside = self.roi(validated=False), self.roi(validated=False)
        self.predict(inside, self.t_affinis, 0.95)
        self.predict(outside, self.t_plat, 0.8)   # IBBI-AI calls it odd, but that is not known
        grid = self.grid([self.rest[0], inside, self.odd, outside], {"pick": 1})["grid"]
        self.assertEqual(text(grid["note"]), "Odd one: 3 · Platypus")
        self.assertIsNone(grid["tiles"][1]["belongs"])

    def test_a_flagged_photo_is_left_out(self):
        self.grid([self.rest[0], self.odd, self.rest[1], self.rest[2]], {"pick": 1})
        answer = GameAnswer.objects.get()
        GameAnswer.objects.filter(pk=answer.pk).update(flagged=[3])   # as if the photo had been flagged in play
        review = game_answer_review.past(answer.round, 0)
        self.assertEqual(review["grid"]["tiles"][3]["state"], "flagged")
        self.assertEqual(text(review["grid"]["note"]), "Odd one: 2 · Platypus")
        self.assertIsNone(review["beetles"][3])


class FindThemAllLinesTests(ReviewCase):
    def setUp(self):
        super().setUp()
        self.members = [self.roi(self.t_affinis) for _ in range(2)]
        self.others = [self.roi(self.t_plat)]

    def grid(self, tiles, picks, rank="genus", skipped=False):
        item = {"a": str(self.members[0].id), "b": None, "check": True, "mode": "select",
                "tiles": [str(t.id) for t in tiles], "rank": rank, "group": game.lineage(self.t_affinis, rank)}
        return self.answer(item, {"skipped": True} if skipped else {"picks": picks})

    def test_the_card_names_the_beetles_missed(self):
        tap = self.roi(validated=False)
        self.predict(tap, self.t_affinis, 0.9)
        ferr = self.roi(self.t_ferr)   # another Xyleborus
        review = self.grid([*self.members, *self.others, ferr, tap], [0, 2, 4])
        self.assertEqual(text(review["grid"]["note"]), "Missed 2, 4 · Xyleborus")   # #569
        self.assertRegex(review["headline"], r"^Found 1 of 3 · 1 wrong · ")
        tiles = review["grid"]["tiles"]
        self.assertEqual([(t["belongs"], t["chosen"]) for t in tiles],
                         [(True, True), (True, False), (False, True), (True, False), (None, True)])

    def test_nothing_to_say_when_none_was_missed(self):
        review = self.grid([*self.members, *self.others], [0, 1])
        self.assertEqual(review["grid"]["note"], [])

    def test_every_beetle_of_an_answered_grid_counts_as_shown_and_waits(self):
        tiles = [*self.members, *self.others, self.roi(self.t_ferr)]
        self.grid(tiles, [0])
        ids = {t.id for t in tiles}
        self.assertTrue(ids <= game.revealed_ids(self.user))
        self.assertTrue(ids <= game.held_back_ids(self.user))
        self.assertEqual(game.reveals(self.user)[tiles[3].id]["modes"], {"select"})

    def test_a_skipped_grid_below_species_gives_nothing_away(self):
        tiles = [*self.members, *self.others, self.roi(self.t_ferr)]
        self.grid(tiles, [], skipped=True)
        self.assertEqual({t.id for t in tiles[1:]} & game.revealed_ids(self.user), set())


class GamePageTests(ReviewCase):
    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=["mixed"])).content.decode()

    def test_names_go_on_every_photo_live_and_under_back(self):
        page = self.page()
        self.assertIn('nameTiles($("photos").querySelectorAll(".cell"), review);', page)
        self.assertIn("nameTiles(cells, previous);", page)
        self.assertIn('box.dataset.testid = "beetle-names";', page)
        self.assertIn('name = genus[0] + ". " + name.slice(genus.length + 1);', page)   # X. affinis

    def test_a_hover_or_a_long_press_clears_them_off_the_photo(self):
        page = self.page()
        self.assertIn("@media (hover: hover) { .cell:hover .rv-names { opacity: 0; } }", page)
        self.assertIn(".cell.names-off .rv-names { opacity: 0; }", page)
        self.assertIn('c.classList.add("names-off"); }, PEEK_MS);', page)   # press and hold (#569)
        self.assertIn('reviewPhotos($("photos"));', page)

    def test_the_rings_are_drawn_on_top_of_the_photos(self):
        page = self.page()
        self.assertIn("#photos .cell::after, #previous-photos .cell::after { content: \"\"; position: absolute; inset: 0; z-index: 4;", page)
        self.assertIn("border: 3px solid var(--ring, transparent)", page)
        self.assertNotIn("outline-offset: -3px", page)
        self.assertIn(":is(#photos, #previous-photos) .cell:is(.ring-missed, .ring-avoided, .ring-unknown)::after { border-style: dashed;", page)
