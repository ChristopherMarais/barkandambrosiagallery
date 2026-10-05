"""Identification tiers: every name kept, the most reliable shown, only Taxonomist and Expert IDs validated."""
import importlib
import json

from django.apps import apps
from django.urls import reverse

from beetlesgallery.beetles_app import identification
from beetlesgallery.beetles_app.models import Beetles, RoiName
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon


class TierTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.affinis = make_taxon(valid_species_id="1733", genus="Xyleborus", species="affinis", scientific_name="Xyleborus affinis")
        self.ferr = make_taxon(valid_species_id="2210", genus="Xyleborus", species="ferrugineus", scientific_name="Xyleborus ferrugineus")

    def test_every_name_is_kept_and_the_most_reliable_shows(self):
        roi = make_beetle(taxon=self.affinis, label_source="taxonomist")
        roi.depicts_valid_name_id, roi.label_source = "2210", "community"
        roi.save()
        roi.refresh_from_db()
        self.assertEqual((roi.depicts_valid_name_id, roi.label_source), ("1733", "taxonomist"))   # still shown
        self.assertEqual(list(roi.names.values_list("valid_species_id", "tier")),
                         [("2210", "community"), ("1733", "taxonomist")])
        roi.depicts_valid_name_id, roi.label_source = "2210", "taxonomist"   # as reliable: replaces it
        roi.save()
        roi.refresh_from_db()
        self.assertEqual(roi.depicts_valid_name_id, "2210")

    def test_a_curators_hand_edit_always_shows(self):
        roi = make_beetle(taxon=self.affinis, label_source="taxonomist", bbox="unvalidated")
        self.client.force_login(self.staff)
        res = self.client.patch(f"/api/v1/beetles/{roi.id}/", json.dumps({"depicts_valid_name_id": "2210", "label_source": "external"}),
                                content_type="application/json")
        self.assertEqual(res.status_code, 200, res.content)
        roi.refresh_from_db()
        self.assertEqual((roi.depicts_valid_name_id, roi.label_source), ("2210", "external"))

    def test_validating_makes_a_lower_name_an_expert_id(self):
        roi = make_beetle(taxon=self.affinis, label_source="community", bbox="unvalidated")
        roi.validate(user=self.staff)
        roi.refresh_from_db()
        self.assertEqual((roi.label_source, roi.label_source_detail), ("expert", "Validated by staff"))
        image = make_image()
        other = make_beetle(image=image, taxon=self.ferr, bbox="unvalidated")
        image.validate(user=self.staff)
        self.assertEqual(Beetles.objects.get(pk=other.pk).label_source, "expert")
        self.assertTrue(RoiName.objects.filter(roi=other, tier="expert").exists())
        kept = make_beetle(taxon=self.affinis, label_source="taxonomist", bbox="unvalidated")
        kept.validate(user=self.staff)
        self.assertEqual(Beetles.objects.get(pk=kept.pk).label_source, "taxonomist")

    def test_spreadsheet_words(self):
        self.assertEqual(identification.parse_tier("Vial label"), ("taxonomist", True))
        self.assertEqual(identification.parse_tier("game_consensus"), ("expert", True))
        self.assertEqual(identification.parse_tier("Community ID"), ("community", True))
        self.assertEqual(identification.parse_tier(""), ("", True))
        self.assertFalse(identification.parse_tier("my uncle")[1])

    def test_the_details_page_lists_other_names(self):
        roi = make_beetle(image=make_image(image_file="tests/p.jpg"), taxon=self.affinis, label_source="expert")
        RoiName.objects.create(roi=roi, valid_species_id="2210", taxon=self.ferr, tier="community")
        self.client.force_login(self.user)
        page = self.client.get(reverse("beetle_detail", args=[roi.id])).content.decode()
        self.assertIn("Expert ID", page)
        self.assertIn('data-testid="other-names"', page)
        self.assertIn("Xyleborus ferrugineus", page)

    def test_the_migration_maps_old_sources_and_records_names(self):
        vial = make_beetle(taxon=self.affinis)
        game = make_beetle(taxon=self.ferr, bbox="validated")
        Beetles.objects.filter(pk=vial.pk).update(label_source="vial_label")
        Beetles.objects.filter(pk=game.pk).update(label_source="game_consensus")
        RoiName.objects.all().delete()
        migration = importlib.import_module("beetlesgallery.beetles_app.migrations.0038_identification_tiers")
        migration.to_tiers(apps, None)
        vial.refresh_from_db()
        game.refresh_from_db()
        self.assertEqual((vial.label_source, vial.label_source_detail), ("taxonomist", "Vial / specimen label"))
        self.assertEqual(game.label_source, "expert")
        self.assertEqual(RoiName.objects.filter(roi__in=[vial, game]).count(), 2)
