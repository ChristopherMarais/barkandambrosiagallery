"""
Expertise covers a share of a taxon's children, not every species (#381): 75% of a genus's species, a tribe's genera
or a subfamily's tribes (rounded up, so all of them up to three), each with 5 validated images answered (all of them
for one with fewer), still 90% correct over at least 10 answers.
"""
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils.html import strip_tags

from beetlesgallery.beetles_app import game_trust
from beetlesgallery.beetles_app.game_trust import recompute_skills
from beetlesgallery.beetles_app.models import PlayerSkill
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase
from beetlesgallery.beetles_app.testing import make_taxon


class ChildrenShareTests(SimpleTestCase):
    def test_three_quarters_of_the_children_are_enough(self):
        available = {f"genus {i}": 20 for i in range(8)}
        five = game_trust.coverage(available, {f"genus {i}": 5 for i in range(5)})
        six = game_trust.coverage(available, {f"genus {i}": 5 for i in range(6)})
        self.assertEqual((five["children_needed"], five["complete"]), (6, False))
        self.assertEqual((six["children_done"], six["complete"]), (6, True))
        self.assertEqual((six["required"], six["covered"]), (30, 30))

    def test_up_to_three_children_all_are_needed(self):
        for total in (1, 2, 3):
            self.assertEqual(game_trust.children_needed(total), total)
        self.assertEqual(game_trust.children_needed(4), 3)

    def test_a_child_with_few_images_is_covered_by_all_of_them(self):
        cover = game_trust.coverage({"common": 20, "rare": 2}, {"common": 8, "rare": 2})
        self.assertEqual(cover["children_done"], 2)
        self.assertTrue(cover["complete"])

    @override_settings(GAME_TRUST_CHILDREN_SHARE=0.5)
    def test_the_share_is_a_setting(self):
        self.assertEqual(game_trust.children_needed(8), 4)


class TribeExpertTests(ScoringCase):
    """Genus calls within Xyleborini: its genera are the children, whatever their species."""

    def setUp(self):
        super().setUp()
        self.t_cras = make_taxon(subfamily="Scolytinae", tribe="Xyleborini", genus="Xylosandrus",
                                 species="crassiusculus", scientific_name="Xylosandrus crassiusculus")
        self.t_euw = make_taxon(subfamily="Scolytinae", tribe="Xyleborini", genus="Euwallacea",
                                species="fornicatus", scientific_name="Euwallacea fornicatus")
        rare = make_taxon(subfamily="Scolytinae", tribe="Xyleborini", genus="Ambrosiodmus",
                          species="rubricollis", scientific_name="Ambrosiodmus rubricollis")
        # four genera with validated images; the fourth is rare and nobody has played it
        for taxon, n in ((self.t_affinis, 5), (self.t_cras, 5), (self.t_euw, 5), (rare, 1)):
            for _ in range(n):
                self.roi(taxon)
        self.p = self.player("p")

    def name(self, taxon, times):
        fields = {"subfamily": taxon.subfamily, "tribe": taxon.tribe, "genus": taxon.genus, "species": taxon.species}
        for _ in range(times):
            self.answer(self.p, self.roi(taxon), fields)

    def tribe_skill(self):
        recompute_skills(self.p)
        return PlayerSkill.objects.get(player=self.p, rank="genus", branch="Xyleborini")

    def test_one_genus_is_not_enough(self):
        self.name(self.t_affinis, 6)
        self.name(self.t_ferr, 6)   # two species, one genus
        skill = self.tribe_skill()
        self.assertEqual((skill.children_total, skill.children_needed, skill.children_done, skill.proven), (4, 3, 1, False))

    def test_three_of_four_genera_make_a_tribe_expert_without_the_rare_one(self):
        self.name(self.t_affinis, 5)
        self.name(self.t_cras, 5)
        self.name(self.t_euw, 5)
        skill = self.tribe_skill()
        self.assertEqual((skill.children_done, skill.proven), (3, True))

    def test_the_tree_counts_genera_for_a_tribe(self):
        self.name(self.t_affinis, 6)
        recompute_skills(self.p)
        self.client.force_login(self.p)
        page = strip_tags(self.client.get(reverse("game_expertise")).content.decode())   # numbers are wrapped
        self.assertIn("covered 1 of 4 genera (3 needed)", page)
        self.assertIn("75% of its members", page)
