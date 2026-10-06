"""
Two kinds of expert (#498). An Identification expert names a taxon's beetles: proven on checked beetles across most
of its members, their names can go into the database without review, and their dot on the expertise tree glows gold.
A Distinction expert tells the taxon's beetles apart just as reliably (the same rule on Similarity and Odd One Out
answers; Select all counts as naming since #543), may not know their names, and unlocks nothing: a plain dark-gold
triangle. Also: a photo must show
a good part of the beetle, so one of just a leg is reported, not named.
"""
import html
import re
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils.html import strip_tags

from beetlesgallery.beetles_app import game_tuning, game_views
from beetlesgallery.beetles_app.game import consensus
from beetlesgallery.beetles_app.game_trust import (
    TrustContext, apart_counts, auto_apply_expert_labels, distinction_experts, expertise_tree, recompute_skills,
)
from beetlesgallery.beetles_app.models import GameAnswer, GameRound, PlayerScore, PlayerSkill
from beetlesgallery.beetles_app.test_game import AFFINIS, FERR
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase
from beetlesgallery.beetles_app.testing import make_taxon

XYLEBORUS = {"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus"}


def text_of(page):
    """A page's words, tags and extra spaces gone, entities decoded."""
    return " ".join(html.unescape(strip_tags(page)).split())


class ExpertCase(ScoringCase):
    def setUp(self):
        super().setUp()
        game_tuning.forget()   # the default rules: 5 images, 75% of the members, 90% right over at least 10 answers
        self.p = self.player("p")

    def tell_apart(self, player, times, a=None, b=None, pair="genus"):
        """Similarity answers on two checked beetles (Xyleborus affinis and X. ferrugineus unless given)."""
        for _ in range(times):
            self.answer(player, self.roi(a or self.t_affinis), mode="pair", roi_b=self.roi(b or self.t_ferr), pair=pair)


class DistinctionExpertTests(ExpertCase):
    def test_telling_the_species_of_a_genus_apart_makes_a_distinction_expert_all_the_way_up(self):
        self.tell_apart(self.p, 12)
        self.assertEqual(distinction_experts(self.p), [
            ("genus", "Xyleborini"), ("species", "Xyleborus"), ("subfamily", ""), ("tribe", "Scolytinae")])

    def test_it_follows_the_identification_rule_ninety_percent_over_at_least_ten_answers(self):
        self.tell_apart(self.p, 9)
        self.assertNotIn(("species", "Xyleborus"), distinction_experts(self.p))   # too few answers, all right
        self.tell_apart(self.p, 1, pair="species")                                # wrong: "same species"
        self.assertIn(("species", "Xyleborus"), distinction_experts(self.p))       # 9 of 10
        self.tell_apart(self.p, 1, pair="species")
        self.assertNotIn(("species", "Xyleborus"), distinction_experts(self.p))   # 9 of 11: under 90%

    def test_it_needs_most_members_covered(self):
        self.roi(self.t_ferr)   # Xyleborus has a second species with a checked image, never shown to the player
        self.tell_apart(self.p, 12, b=self.t_affinis, pair="species")   # only ever affinis against affinis, rightly
        experts = distinction_experts(self.p)
        self.assertNotIn(("species", "Xyleborus"), experts)   # one of its two species covered
        self.assertIn(("genus", "Xyleborini"), experts)        # its only genus, Xyleborus, is covered

    def test_a_similarity_answer_covers_both_beetles_members(self):
        self.tell_apart(self.p, 1)
        counts = apart_counts(self.p)
        self.assertEqual(dict(counts[("species", "xyleborus")][3]),
                         {"xyleborus affinis": 1, "xyleborus ferrugineus": 1})
        self.assertEqual(dict(counts[("genus", "xyleborini")][3]), {"xyleborus": 1})   # one genus, once per answer
        self.assertEqual(counts[("genus", "xyleborini")][2], "Xyleborini")              # the name as it is shown

    def test_a_grid_covers_its_group_and_an_odd_one_from_the_same_parent(self):
        xylosandrus = make_taxon(subfamily="Scolytinae", tribe="Xyleborini", genus="Xylosandrus",
                                 species="crassiusculus", scientific_name="Xylosandrus crassiusculus")

        def grid(mode, ok, odd_one=None):
            rnd = GameRound.objects.create(player=self.p, mode=mode, items=[])
            GameAnswer.objects.create(round=rnd, player=self.p, mode=mode, index=0, roi=self.roi(self.t_affinis),
                                      roi_b=odd_one, is_check=True, grid_rank="genus", grid_group=XYLEBORUS,
                                      correct_genus=ok)

        grid("odd", True, odd_one=self.roi(xylosandrus))   # a near relative: another genus of Xyleborini
        grid("odd", True, odd_one=self.roi(self.t_plat))   # from another subfamily: only the group is in Xyleborini
        grid("odd", False, odd_one=self.roi(xylosandrus))   # picked one of the rest
        grid("select", False)                               # Find Them All counts as naming instead (#543)
        correct, judged, _, shown = apart_counts(self.p)[("genus", "xyleborini")]
        self.assertEqual((correct, judged), (2, 3))
        self.assertEqual(dict(shown), {"xyleborus": 3, "xylosandrus": 2})

    def test_it_is_worked_out_when_shown_and_never_stored(self):
        self.tell_apart(self.p, 12)
        recompute_skills(self.p)
        expertise_tree(self.p)
        self.assertFalse(PlayerSkill.objects.filter(player=self.p).exists())


class DistinctionUnlocksNothingTests(ExpertCase):
    def distinction_expert(self, name):
        """A player whose labels reach the curators, a Distinction expert on Xyleborus and above, naming nothing."""
        player = self.player(name)
        PlayerScore.objects.create(player=player, score=1600, rating=0.75)
        self.tell_apart(player, 12)
        recompute_skills(player)
        return player

    def test_their_names_are_never_trusted_or_written_without_review(self):
        d1, d2 = self.distinction_expert("d1"), self.distinction_expert("d2")
        self.assertIn(("species", "Xyleborus"), distinction_experts(d1))
        self.assertFalse(PlayerSkill.objects.filter(proven=True).exists())
        labels = dict(AFFINIS, species="Xyleborus affinis")
        self.assertFalse(TrustContext({d1.id}).trusted_through(d1.id, "species", labels))
        target = self.roi(validated=False)   # no name yet
        for p in (d1, d2):
            self.answer(p, target, AFFINIS)
        entry = consensus(roi_ids=[target.id])[0]
        self.assertEqual((entry["ranks"]["species"]["value"], entry["trusted_rank"]), ("Xyleborus affinis", ""))
        self.assertEqual(auto_apply_expert_labels(), [])
        # the same two, once they also name checked beetles of both species well, are Identification experts
        for p in (d1, d2):
            for fields, taxon in ((AFFINIS, self.t_affinis), (FERR, self.t_ferr)):
                for _ in range(6):
                    self.answer(p, self.roi(taxon), fields)
            recompute_skills(p)
        self.assertEqual(auto_apply_expert_labels(), [target.id])


class TreeTests(ExpertCase):
    """
    The player names Xyleborini's beetles to the right tribe but the wrong genus: an Identification expert on the
    subfamilies and on the tribes of Scolytinae, not on the genera of Xyleborini. And they tell Xyleborus's two
    species apart: a Distinction expert all the way down.
    """

    def setUp(self):
        super().setUp()
        wrong_genus = dict(AFFINIS, genus="Xylosandrus", species="")
        for _ in range(10):
            self.answer(self.p, self.roi(self.t_affinis), wrong_genus)
        self.tell_apart(self.p, 12)
        recompute_skills(self.p)
        self.client.force_login(self.p)
        self.page = self.client.get(reverse("game_expertise")).content.decode()

    def marks(self, name):
        """The Identification and Distinction markers just before a taxon's name on the tree."""
        found = re.search(r'<span class="tree-dot mark-(\w+)"[^>]*></span><span class="tree-tri mark-(\w+)"[^>]*>'
                          r'</span>\s*<span class="tree-name[^"]*">' + re.escape(name) + "<", self.page)
        self.assertIsNotNone(found, name)
        return found.groups()

    def test_each_taxon_has_its_own_two_markers_whatever_its_parent_is(self):
        self.assertEqual(self.marks("Subfamilies"), ("expert", "expert"))
        self.assertEqual(self.marks("Scolytinae"), ("expert", "expert"))
        self.assertEqual(self.marks("Xyleborini"), ("common", "expert"))   # inside an expert subfamily, still grey
        self.assertEqual(self.marks("Xyleborus"), ("unknown", "expert"))
        self.assertNotIn('class="st-', self.page)   # no status classes on the rows around the markers
        # an Identification expert's name is bold
        self.assertIn('<span class="tree-name font-bold text-gray-900">Scolytinae<', self.page)
        self.assertIn('<span class="tree-name text-gray-800">Xyleborini<', self.page)

    def test_the_count_lines_say_naming_and_telling_apart(self):
        text = text_of(self.page)
        self.assertIn("Xyleborini tribe Naming: 0 of 10 correct · covered 1 of 1 genera "
                      "Telling apart: 12 of 12 correct · covered 1 of 1 genera", text)
        self.assertIn("Xyleborus genus Telling apart: 12 of 12 correct · covered 2 of 2 species", text)

    def test_the_legend_keys_both_markers(self):
        self.assertNotIn("Two dots", self.page)
        self.assertIn('data-testid="legend-naming"><span class="tree-dot', self.page)
        self.assertIn('data-testid="legend-apart"><span class="tree-tri', self.page)
        # every colour band shows both shapes
        self.assertIn('data-testid="legend-rare"><span class="tree-dot mark-rare"></span>'
                      '<span class="tree-tri mark-rare"></span>', self.page)
        # and the page above it says how to become each
        self.assertIn("Distinction expert", text_of(self.page[:self.page.index('data-testid="expertise-legend"')]))

    def test_the_profile_lists_both_kinds(self):
        page = self.client.get(reverse("game_profile", args=[self.p.id])).content.decode()

        def chips(testid):
            return text_of(re.search(rf'data-testid="{testid}">(.*?)</ul>', page, re.S).group(1))

        self.assertIn("Identification expert in", page)
        self.assertEqual(chips("identification-expert-in"), "subfamilies tribes of Scolytinae")
        self.assertIn("Distinction expert in", page)
        self.assertEqual(chips("distinction-expert-in"),
                         "genera of Xyleborini species of Xyleborus subfamilies tribes of Scolytinae")

    def test_a_new_players_profile_has_neither_yet(self):
        page = self.client.get(reverse("game_profile", args=[self.user.id])).content.decode()
        self.assertEqual(page.count("None yet."), 2)
        self.assertNotIn("proven expert", page)


class MarkerStyleTests(SimpleTestCase):
    """The tree's CSS: a dot and a triangle, styled by classes on themselves; only the Identification expert glows."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        template = Path(settings.BASE_DIR) / "beetlesgallery/templates/beetles/game_expertise.html"
        page = template.read_text(encoding="utf-8")
        style = re.sub(r"/\*.*?\*/", "", re.search(r"<style>(.*?)</style>", page, re.S).group(1), flags=re.S)
        cls.style = style
        cls.rules = [(selector.strip(), body) for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style)]

    def rule(self, selector):
        return next(body for s, body in self.rules if s == selector)

    def test_a_round_dot_and_a_triangle(self):
        self.assertIn("border-radius: 9999px", self.rule(".tree-dot"))
        self.assertIn("clip-path: polygon(50% 0, 100% 100%, 0 100%)", self.rule(".tree-tri"))

    def test_no_marker_is_styled_by_an_ancestors_class(self):
        self.assertNotIn(".st-", self.style)
        for selector, _ in self.rules:
            self.assertNotRegex(selector, r"[\w\])-]\s+\.tree-(dot|square)")   # a descendant combinator

    def test_only_the_identification_expert_glows(self):
        animated = [s for s, body in self.rules if re.search(r"animation\s*:(?!\s*none)", body)]
        self.assertEqual(animated, [".tree-dot.mark-expert"])
        self.assertIn("box-shadow", self.rule(".tree-dot.mark-expert"))
        square = self.rule(".tree-tri.mark-expert")
        self.assertIn("#ca8a04", square)   # dark gold
        self.assertNotIn("box-shadow", square)
        self.assertNotIn("animation", square)


class WordingTests(ExpertCase):
    def page(self, name, *args):
        self.client.force_login(self.user)
        return self.client.get(reverse(name, args=args)).content.decode()

    def test_how_it_works_defines_both_kinds(self):
        faq = text_of(self.page("game_how").split('id="faq"')[1])
        self.assertIn("What is an expert? There are two kinds, each for one taxon. An Identification expert names "
                      "its beetles reliably, proven on checked beetles across most of its members", faq)
        self.assertIn("A Distinction expert tells its beetles apart just as reliably but may not know their names; "
                      "it doesn’t unlock anything.", faq)

    def test_only_identification_experts_skip_review(self):
        self.assertIn("Only Identification experts skip review.", self.page("game_unlocks"))
        how = text_of(self.page("game_how"))
        self.assertIn("Only Identification experts have their labels written to the database without review.", how)
        for name in ("game_how", "game_unlocks"):
            self.assertNotIn("proven expert", self.page(name))


class PhotoGuidanceTests(ExpertCase):
    """A photo must show a good part of the beetle: one of a leg or a fragment is reported, not named."""

    def page(self, name, *args):
        self.client.force_login(self.user)
        return self.client.get(reverse(name, args=args)).content.decode()

    def test_a_leg_is_no_longer_a_photo_to_name(self):
        how = text_of(self.page("game_how"))
        play = self.page("game_play", "mixed")
        with open(finders.find("js/game_tour.js"), encoding="utf-8") as f:
            tour = f.read()
        for text in (how, play, tour):
            self.assertNotIn("an underside, a leg", text)
            self.assertNotIn("just shows too little isn't bad", text)
        self.assertIn("A photo must show a good part of the beetle. Flag one that shows too little of it (a leg, a "
                      "fragment)", how)
        self.assertIn("When it shows too little to name it (just a leg or a fragment of the beetle)", how)
        self.assertIn("Flag a photo that shows too little of the beetle (a leg, a fragment).", play)   # the rules
        self.assertIn("Too little of the beetle (a leg, a fragment) is a bad photo too.", play)           # the tip
        self.assertIn("Bad photo (too little of the beetle, blurry", tour)

    def test_an_unusual_side_of_a_good_part_is_still_named(self):
        how = text_of(self.page("game_how"))
        self.assertIn("A clear photo of a good part of the beetle from an unusual side (from below, say) isn’t "
                      "bad: name it as far as you can", how)
        self.assertIn("A clear photo of a good part of it from an unusual side (from below, say) isn't bad: name it "
                      "as far as you can.", self.page("game_play", "mixed"))

    def test_the_report_menu_counts_too_little_of_the_beetle_as_a_bad_photo(self):
        self.assertEqual(game_views.FEED_REPORT_HINTS["bad_image"],
                         "Blurry, dark, too little of the beetle, or not a beetle")
        self.assertIn("Blurry, dark, too little of the beetle, or not a beetle", self.page("game_play", "mixed"))
