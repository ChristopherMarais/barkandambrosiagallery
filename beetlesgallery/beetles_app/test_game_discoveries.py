"""New species found through the game, and expertise that scales with the size of a taxon."""
from django.urls import reverse

from beetlesgallery.beetles_app import game_discoveries
from beetlesgallery.beetles_app.game_trust import recompute_skills
from beetlesgallery.beetles_app.models import Beetles, PlayerSkill, SpeciesDiscovery
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase
from beetlesgallery.beetles_app.testing import make_taxon

NEW = {"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xylosandrus", "species": "crassiusculus"}


class DiscoveryTests(ScoringCase):
    def setUp(self):
        super().setUp()
        self.t_new = make_taxon(scientific_name="Xylosandrus crassiusculus", **NEW)

    def name_it(self, player, roi, fields=NEW):
        ans = self.answer(player, roi, fields, check=False)
        ans.new_species = game_discoveries.is_new_species(fields["genus"], fields["species"])
        ans.save(update_fields=["new_species"])
        return ans

    def validate(self, roi, taxon):
        Beetles.objects.filter(pk=roi.pk).update(bbox_is_validated=True, taxon=taxon)

    def test_it_knows_which_species_have_no_validated_images(self):
        self.roi(self.t_affinis)
        self.assertFalse(game_discoveries.is_new_species("Xyleborus", "affinis"))
        self.assertTrue(game_discoveries.is_new_species("Xylosandrus", "crassiusculus"))

    def test_naming_a_new_species_that_a_curator_confirms_is_a_discovery(self):
        target = self.roi(validated=False)
        self.name_it(self.user, target)
        self.assertEqual(game_discoveries.find(), [])          # not validated yet
        self.validate(target, self.t_new)
        [found] = game_discoveries.find()
        self.assertEqual((found.player, found.genus, found.species), (self.user, "Xylosandrus", "crassiusculus"))
        self.assertEqual(game_discoveries.find(), [])          # only once

    def test_a_different_validated_name_is_not_a_discovery(self):
        target = self.roi(validated=False)
        self.name_it(self.user, target)
        self.validate(target, self.t_affinis)
        self.assertEqual(game_discoveries.find(), [])

    def test_a_species_already_known_is_not_a_discovery(self):
        self.roi(self.t_affinis)
        target = self.roi(validated=False)
        self.name_it(self.user, target, AFFINIS)
        self.validate(target, self.t_affinis)
        self.assertEqual(game_discoveries.find(), [])

    def test_the_pop_up_shows_once_and_the_badge_and_profile_stay(self):
        target = self.roi(validated=False)
        self.name_it(self.user, target)
        self.validate(target, self.t_new)
        self.client.force_login(self.user)
        first = self.client.get(reverse("game_home")).content.decode()
        self.assertIn("You found a new species!", first)
        self.assertNotIn("You found a new species!", self.client.get(reverse("game_home")).content.decode())
        profile = self.client.get(reverse("game_profile", args=[self.user.id])).content.decode()
        self.assertIn("New species found", profile)
        self.assertIn("New species finder", profile)
        self.assertEqual(SpeciesDiscovery.objects.count(), 1)


class ScalingExpertiseTests(ScoringCase):
    def test_what_a_genus_needs_depends_on_its_species(self):
        others = [make_taxon(subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus", species=f"sp{i}",
                             scientific_name=f"Xyleborus sp{i}") for i in range(3)]
        for taxon in [self.t_affinis, *others]:
            for _ in range(6):
                self.roi(taxon)
        p = self.player("p")
        for _ in range(12):
            self.answer(p, self.roi(self.t_affinis), AFFINIS)    # only one of the four species
        recompute_skills(p)
        skill = PlayerSkill.objects.get(player=p, rank="species", branch="Xyleborus")
        # 3 of the 4 species are needed (75%, #381)
        self.assertEqual((skill.required, skill.children_total, skill.children_needed, skill.children_done, skill.proven),
                         (15, 4, 3, 1, False))
