"""
The expertise tree in plain words (#539): telling apart is a triangle, the counts read "Naming: 7 of 10 correct" and
"covered 2 of 3 genera", a "What it takes" box states the expert rule with the live settings, the colour key has no
sentences, and the intro is short.
"""
import html
import re

from django.test import override_settings
from django.urls import reverse
from django.utils.html import strip_tags

from beetlesgallery.beetles_app import game_tuning
from beetlesgallery.beetles_app.game_trust import recompute_skills
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase
from beetlesgallery.beetles_app.testing import make_taxon


def text_of(page):
    """A page's words, tags and extra spaces gone, entities decoded."""
    return " ".join(html.unescape(strip_tags(page)).split())


class PlainWordsCase(ScoringCase):
    def setUp(self):
        super().setUp()
        game_tuning.forget()
        self.p = self.player("p")
        self.client.force_login(self.p)

    def page(self):
        recompute_skills(self.p)
        return self.client.get(reverse("game_expertise")).content.decode()

    def words(self, page):
        """What the page says below its title, without the styles and menus above."""
        return text_of(page[page.index('class="page-title"'):])

    def section(self, page, testid):
        """The text of the element with this data-testid, up to its closing tag."""
        found = re.search(rf'data-testid="{testid}"[^>]*>(.*?)</(section|div|p)>', page, re.S)
        self.assertIsNotNone(found, testid)
        return text_of(found.group(1))


class CountWordsTests(PlainWordsCase):
    def test_naming_counts_say_how_many_were_correct_and_how_many_members_are_covered(self):
        wrong = dict(AFFINIS, species="Xyleborus ferrugineus")
        for _ in range(7):
            self.answer(self.p, self.roi(self.t_affinis), AFFINIS)
        for _ in range(3):
            self.answer(self.p, self.roi(self.t_affinis), wrong)
        text = self.words(self.page())
        self.assertIn("Xyleborus genus 7/10 correct · 1 of 1 species", text)
        tree = text[text.index("Subfamilies"):]
        self.assertNotRegex(tree, r"\bnames \d|\bapart \d")   # no more "names 7/10"

    def test_it_says_how_many_are_needed_when_not_all_are(self):
        for genus in ("Xylosandrus", "Euwallacea", "Ambrosiodmus"):
            self.roi(make_taxon(subfamily="Scolytinae", tribe="Xyleborini", genus=genus, species="sp",
                                scientific_name=f"{genus} sp"))
        for _ in range(6):
            self.answer(self.p, self.roi(self.t_affinis), AFFINIS)
        self.assertIn("1 of 4 genera (3 needed)", self.words(self.page()))

    def test_telling_apart_counts_in_the_same_words(self):
        for _ in range(2):
            self.answer(self.p, self.roi(self.t_affinis), mode="pair", roi_b=self.roi(self.t_ferr), pair="genus")
        self.answer(self.p, self.roi(self.t_affinis), mode="pair", roi_b=self.roi(self.t_ferr), pair="species")
        with override_settings(GAME_REPORT_MIN_JUDGED=1):
            text = self.words(self.page())
        self.assertIn("Xyleborus genus 2/3 correct · 2 of 2 species", text)


class RuleBoxTests(PlainWordsCase):
    def test_what_it_takes_states_the_rule_for_both_kinds(self):
        box = self.section(self.page(), "expertise-rule")
        self.assertIn("What it takes", box)
        # Find Them All counts as naming since #543
        self.assertIn("Naming expert: names a taxon’s beetles in Naming and Find Them All.", box)
        self.assertIn("Distinction expert: tells them apart in Similarity and Odd One Out.", box)
        self.assertIn("Either one, in a taxon: at least 90% correct over 10 or more checked answers, covering 75% of "
                      "its members (a tribe’s genera, a genus’s species) with at least 5 answers each.", box)

    @override_settings(GAME_TRUST_MIN_ACCURACY=0.85, GAME_TRUST_MIN_JUDGED=20, GAME_TRUST_CHILDREN_SHARE=0.6,
                       GAME_TRUST_IMAGES_PER_SPECIES=3)
    def test_it_shows_the_live_settings(self):
        box = self.section(self.page(), "expertise-rule")
        self.assertIn("at least 85% correct over 20 or more checked answers, covering 60% of its members", box)
        self.assertIn("with at least 3 answers each", box)

    def test_each_kind_shows_its_own_gold_marker(self):
        page = self.page()
        box = page[page.index('data-testid="expertise-rule"'):page.index("</section>")]
        self.assertIn('<span class="tree-dot mark-expert" aria-hidden="true"></span>', box)
        self.assertIn('<span class="tree-tri mark-expert" aria-hidden="true"></span>', box)


class KeyAndIntroTests(PlainWordsCase):
    def test_the_colour_key_has_no_sentences(self):
        page = self.page()
        text = self.words(page)
        for gone in ("Gold: Distinction expert; it unlocks nothing.", "Gold and glowing", "unlocks nothing"):
            self.assertNotIn(gone, text)
        key = text_of(page[page.index('data-testid="expertise-legend"'):page.index('id="tree"')])
        self.assertIn("naming telling apart", key)   # the one-line summary (#618 exp-legend)
        self.assertNotIn("<details", page[page.index('data-testid="expertise-legend"'):page.index('id="tree"')])   # always shown (r7 D3)
        self.assertIn("Not yet: fewer than 5 answers Fair under 50%", key)   # words of the scale (#572)
        self.assertNotIn(".", key)   # labels only, no sentences
        self.assertIn('data-testid="legend-expert"><span class="tree-dot mark-expert"></span>'
                      '<span class="tree-tri mark-expert"></span>Expert</span>', page)
        self.assertIn('<span class="tree-tri mark-unknown"></span>Not yet: fewer than 5 answers', page)

    def test_the_intro_is_short_and_says_how_it_works(self):
        intro = self.section(self.page(), "expertise-intro")
        self.assertEqual(intro, "Your answers on beetles whose names curators have checked count toward every taxon "
                                "the beetle belongs to. Play beetles from a taxon to light it up; a focus helps.")

    def test_someone_elses_page_speaks_of_them(self):
        other = self.player("other")
        page = self.client.get(reverse("game_player_expertise", args=[other.id])).content.decode()
        self.assertTrue(self.section(page, "expertise-intro").startswith("Their answers"))
        self.assertNotIn("focus", self.section(page, "expertise-intro"))


class TriangleMarkerTests(PlainWordsCase):
    def test_a_distinction_expert_triangle_is_labelled_for_screen_readers(self):
        for _ in range(12):
            self.answer(self.p, self.roi(self.t_affinis), mode="pair", roi_b=self.roi(self.t_ferr), pair="genus")
        page = self.page()
        self.assertIn('<span class="tree-tri mark-expert" title="Distinction expert" role="img" '
                      'aria-label="Distinction expert"></span>', page)
        self.assertNotIn("tree-square", page)

    def test_other_triangles_are_titled_and_hidden_from_screen_readers(self):
        page = self.page()
        self.assertIn('<span class="tree-tri mark-unknown" title="Telling apart" aria-hidden="true"></span>', page)
        self.assertIn('<span class="tree-dot mark-unknown" title="Naming" aria-hidden="true"></span>', page)
