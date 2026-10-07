"""
The review card on a phone (#600): a tap on a reviewed photo turns its names off and on (no more press and hold), and
the whole photo opens from its own button; one source dot per photo, with no rim; both confidences where IBBI-AI and
the players say the same name; Similarity underlines the rank the two beetles share; the whole photo opened from a
review shows every rank under the photo.
"""
from django.urls import reverse

from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_answer_review import ReviewCase


class OneSourcePerPhotoTests(ReviewCase):
    def test_a_validated_beetle_has_the_truth_as_its_one_source(self):
        beetle, = self.classify(self.roi(self.t_affinis), AFFINIS)["beetles"]
        self.assertEqual((beetle["source"], beetle["expert"]), ("truth", False))

    def test_the_source_of_the_deepest_name_and_both_confidences_where_they_agree(self):
        roi = self.roi(self.t_affinis, validated=False)
        self.other("a", roi, AFFINIS)
        self.predict(roi, self.t_affinis, 0.64)
        beetle, = self.classify(roi, AFFINIS)["beetles"]
        self.assertEqual(beetle["source"], "players")
        species = beetle["ranks"][3]
        self.assertEqual((species["source"], species["sure"]), ("players", 100))
        self.assertEqual((species["columns"]["ai"]["sure"], species["columns"]["players"]["sure"]), (64, 100))
        self.assertEqual(species["dots"], ["ai", "players"])   # both name it: both dots (#615)

    def test_one_dot_when_they_name_different_beetles(self):
        roi = self.roi(self.t_affinis, validated=False)
        self.other("a", roi, AFFINIS)
        self.predict(roi, self.t_plat, 0.9)
        beetle, = self.classify(roi, AFFINIS)["beetles"]
        species = beetle["ranks"][3]
        self.assertEqual(species["dots"], ["players"])
        self.assertNotEqual(species["columns"]["ai"]["name"], species["name"])

    def test_ibbi_ai_alone_is_the_photo_s_source(self):
        roi = self.roi(self.t_affinis, validated=False)
        self.predict(roi, self.t_affinis, 0.52)
        beetle, = self.classify(roi, AFFINIS)["beetles"]
        self.assertEqual(beetle["source"], "ai")

    def test_nobody_no_source(self):
        beetle, = self.classify(self.roi(self.t_affinis, validated=False), AFFINIS)["beetles"]
        self.assertEqual(beetle["source"], "")


class SharedRankTests(ReviewCase):
    def pair(self, a, b, check=True):
        return self.answer({"a": str(a.id), "b": str(b.id), "check": check, "mode": "pair"}, {"pair_answer": "genus"})

    def test_a_validated_pair_shares_the_true_rung_s_rank(self):
        self.assertEqual(self.pair(self.roi(self.t_affinis), self.roi(self.t_ferr))["pair"]["shared"], "genus")

    def test_different_subfamilies_share_nothing(self):
        self.assertIsNone(self.pair(self.roi(self.t_affinis), self.roi(self.t_plat))["pair"]["shared"])

    def test_an_open_pair_shares_what_the_players_say(self):
        open_one, partner = self.roi(self.t_affinis, validated=False), self.roi(self.t_ferr)
        self.other("a", open_one, AFFINIS)
        self.predict(open_one, self.t_ferr, 0.85)   # IBBI-AI says same species; the players come first
        self.assertEqual(self.pair(open_one, partner, check=False)["pair"]["shared"], "genus")

    def test_ibbi_ai_when_the_players_say_nothing(self):
        open_one, partner = self.roi(self.t_affinis, validated=False), self.roi(self.t_ferr)
        self.predict(open_one, self.t_ferr, 0.85)
        self.assertEqual(self.pair(open_one, partner, check=False)["pair"]["shared"], "species")


class PageTests(ReviewCase):
    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=["mixed"])).content.decode()

    def test_a_tap_toggles_the_names_and_the_whole_photo_has_its_own_button(self):
        page = self.page()
        self.assertIn('else c.classList.toggle("names-off");', page)
        self.assertNotIn("PEEK_MS", page)
        self.assertIn('const whole = node("button", "zoom-chip rv-whole"', page)
        self.assertIn('whole.dataset.testid = "review-whole-photo";', page)
        self.assertIn("@media (hover: hover) and (pointer: fine) { .cell > .rv-whole { display: none; } }", page)

    def test_one_dot_without_a_rim(self):
        page = self.page()
        self.assertNotIn("box-shadow: 0 0 0 1px rgba(255,255,255,.85)", page)
        self.assertIn("const dots = name ? (r.dots || []).map(sourceDot) : [];", page)

    def test_both_confidences_and_the_shared_rank(self):
        page = self.page()
        self.assertIn("const cells = cols.map((k) => columnCell(k, (r.columns || {})[k], r.name));", page)
        self.assertIn(":is(.rv-names, .lb-names) .nm.shared { text-decoration: underline;", page)
        self.assertIn("function sharedRank(review)", page)

    def test_the_whole_photo_from_a_review_shows_its_names(self):
        page = self.page()
        self.assertIn('<div id="lb-names" class="hidden"></div>', page)
        self.assertIn("namesLabel(names, previous, true)", page)
