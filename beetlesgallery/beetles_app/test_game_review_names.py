"""
The review after every answer names every beetle shown at all four ranks (#541): the true names of a validated beetle,
or the likeliest name of one nobody has validated with who says so and how sure. A grid's card has a plain line for
every beetle (what it is next to the group, what the player did, correct or not, or agreeing with IBBI-AI and the
players), and since every grid beetle is named, they all count as shown to that player (game.reveals).
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

    def test_every_beetle_has_a_line_that_names_the_group(self):
        review = self.grid([self.rest[0], self.odd, self.rest[1], self.rest[2]], {"pick": 2})
        grid = review["grid"]
        self.assertEqual(text(grid["lead"]), "Group: Xyleborus. The odd one: 2 (Platypus).")
        lines = [text(line) for line in grid["lines"]]
        self.assertEqual([line["tile"] for line in grid["lines"]], [1, 2, 3, 4])
        self.assertEqual(lines[0], "Xyleborus. Not picked — correct")
        self.assertEqual(lines[1], "Platypus: not Xyleborus, the odd one. Not picked — missed")
        self.assertRegex(lines[2], r"^Xyleborus\. Your pick: not Xyleborus — not correct · −[\d.]+ points$")
        for line in lines:
            self.assertNotIn("the rest", line)
            self.assertNotIn("in it", line)
        self.assertEqual([(t["belongs"], t["chosen"]) for t in grid["tiles"]],
                         [(True, False), (False, False), (True, True), (True, False)])
        # the photos carry every name, past the grid's rank
        self.assertEqual(review["beetles"][2]["ranks"][3]["name"], "Xyleborus ferrugineus")

    def test_the_right_pick(self):
        grid = self.grid([self.rest[0], self.odd, self.rest[1], self.rest[2]], {"pick": 1})["grid"]
        self.assertRegex(text(grid["lines"][1]),
                         r"^Platypus: not Xyleborus, the odd one\. Your pick: not Xyleborus — correct · \+[\d.]+ points$")
        self.assertEqual(grid["lines"][1]["parts"][-2], {"say": "correct", "tone": "good"})

    def test_a_beetle_nobody_has_checked(self):
        inside, outside = self.roi(validated=False), self.roi(validated=False)
        self.predict(inside, self.t_affinis, 0.95)   # IBBI-AI: a Xyleborus
        self.predict(outside, self.t_plat, 0.8)      # IBBI-AI: a Platypus
        self.other("a", inside, AFFINIS)
        grid = self.grid([self.rest[0], inside, self.odd, outside], {"pick": 1})["grid"]
        self.assertEqual(text(grid["lines"][1]),
                         "Not checked yet: IBBI-AI says Xyleborus (95%), 1 player says Xyleborus (100%). "
                         "Your pick: not Xyleborus — doesn't agree with IBBI-AI or the players"
                         " · may cost points once it is checked")
        self.assertEqual(text(grid["lines"][3]), "Not checked yet: IBBI-AI says Platypus (80%). Not picked.")
        self.assertIsNone(grid["tiles"][1]["belongs"])

    def test_a_flagged_photo_is_explained_too(self):
        self.grid([self.rest[0], self.odd, self.rest[1], self.rest[2]], {"pick": 1})
        answer = GameAnswer.objects.get()
        GameAnswer.objects.filter(pk=answer.pk).update(flagged=[3])   # as if the photo had been flagged in play
        review = game_answer_review.past(answer.round, 0)
        self.assertEqual(text(review["grid"]["lines"][3]), "You flagged it: out of this grid.")
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

    def test_each_beetle_belongs_or_not_was_selected_or_not_and_its_points(self):
        tap = self.roi(validated=False)
        self.predict(tap, self.t_affinis, 0.9)
        ferr = self.roi(self.t_ferr)   # another Xyleborus
        review = self.grid([*self.members, *self.others, ferr, tap], [0, 2, 4])
        lines = [text(line) for line in review["grid"]["lines"]]
        self.assertEqual(text(review["grid"]["lead"]), "Select every Xyleborus.")
        self.assertRegex(lines[0], r"^Xyleborus: belongs to Xyleborus\. You selected it — correct · \+[\d.]+ points$")
        self.assertEqual(lines[1], "Xyleborus: belongs to Xyleborus. You left it out — missed")
        self.assertRegex(lines[2],
                         r"^Platypus: doesn't belong to Xyleborus\. You selected it — not correct · −[\d.]+ points$")
        self.assertEqual(lines[3], "Xyleborus: belongs to Xyleborus. You left it out — missed")
        self.assertEqual(lines[4], "Not checked yet: IBBI-AI says Xyleborus (90%). "
                                   "You selected it — agrees with IBBI-AI · points once it is checked")

    def test_leaving_out_one_that_doesnt_belong_is_correct(self):
        review = self.grid([*self.members, *self.others], [0, 1])
        self.assertEqual(text(review["grid"]["lines"][2]), "Platypus: doesn't belong to Xyleborus. Not selected — correct")

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

    def test_a_hover_or_a_tap_clears_them_off_the_photo(self):
        page = self.page()
        self.assertIn("@media (hover: hover) { .cell:hover .rv-names { opacity: 0; } }", page)
        self.assertIn(".cell.names-off .rv-names { opacity: 0; }", page)
        self.assertIn('c.classList.toggle("names-off");', page)
        self.assertIn('tapForNames($("photos"));', page)

    def test_the_rings_are_drawn_on_top_of_the_photos(self):
        page = self.page()
        self.assertIn("#photos .cell::after, #previous-photos .cell::after { content: \"\"; position: absolute; inset: 0; z-index: 4;", page)
        self.assertIn("border: 3px solid var(--ring, transparent)", page)
        self.assertNotIn("outline-offset: -3px", page)
        self.assertIn(":is(#photos, #previous-photos) .cell.sel-missed::after { border-style: dashed; }", page)

    def test_beetles_that_read_the_same_share_a_line(self):
        self.assertIn('same.get(key).tiles.push(l.tile);', self.page())
