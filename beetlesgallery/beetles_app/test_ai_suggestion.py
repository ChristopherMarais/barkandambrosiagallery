"""
The AI suggestion on the specimen details page and the annotation page: what a classifier said about an ROI at
each rank (subfamily, tribe, genus, species), with its confidence and whether the current label agrees.
"""
from django.urls import reverse

from beetlesgallery.beetles_app.models import ModelPrediction
from beetlesgallery.beetles_app.predictions import suggestions_for
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_taxon


class AiSuggestionTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.affinis = make_taxon(valid_species_id="1", subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus",
                                  species="affinis", scientific_name="Xyleborus affinis")
        self.ferr = make_taxon(valid_species_id="2", subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus",
                               species="ferrugineus", scientific_name="Xyleborus ferrugineus")
        self.roi = make_beetle(taxon=self.ferr, bbox="unvalidated")
        self.pred = ModelPrediction.objects.create(
            roi=self.roi, valid_species_id="1", taxon=self.affinis, confidence=0.546,
            top_k=[{"valid_species_id": "2", "confidence": 0.263}],
            rank_confidence={"subfamily": {"value": "Scolytinae", "confidence": 1.0},
                             "tribe": {"value": "Xyleborini", "confidence": 0.996},
                             "genus": {"value": "Xyleborus", "confidence": 0.95}},
            model_name="M2e20__dinov3L336", model_version="db20260920_0911",
        )

    def levels(self):
        [s] = suggestions_for([self.roi])[self.roi.id]
        return {l["rank"]: l for l in s["levels"]}, s

    def test_every_rank_has_its_own_confidence_and_agreement(self):
        levels, s = self.levels()
        self.assertEqual([r for r in levels], ["subfamily", "tribe", "genus", "species"])
        self.assertEqual((levels["genus"]["value"], levels["genus"]["confidence"], levels["genus"]["agrees"]),
                         ("Xyleborus", 0.95, True))
        self.assertEqual((levels["species"]["value"], levels["species"]["confidence"], levels["species"]["agrees"]),
                         ("Xyleborus affinis", 0.546, False))   # the label says ferrugineus
        self.assertEqual(s["runners_up"], [{"value": "Xyleborus ferrugineus", "confidence": 0.263}])
        self.assertEqual(s["model_version"], "db20260920_0911")

    def test_a_rank_the_model_gave_no_number_for_is_added_up_from_the_species(self):
        self.pred.rank_confidence = {}
        self.pred.save()
        levels, _ = self.levels()
        self.assertEqual(levels["genus"]["source"], "species")
        self.assertAlmostEqual(levels["genus"]["confidence"], 0.809)   # 0.546 + 0.263

    def test_no_label_means_no_agreement_marks(self):
        bare = make_beetle(bbox="unvalidated")
        ModelPrediction.objects.create(roi=bare, valid_species_id="1", confidence=0.7, model_name="m")
        [s] = suggestions_for([bare])[bare.id]
        self.assertTrue(all(l["agrees"] is None for l in s["levels"]))

    def test_the_details_page_shows_each_rank_with_its_confidence(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("beetle_detail", args=[self.roi.id])).content.decode()
        self.assertIn('data-testid="ai-suggestion"', page)
        block = page[page.index('data-testid="ai-suggestion"'):]
        for rank, pct in (("subfamily", "100%"), ("tribe", "100%"), ("genus", "95%"), ("species", "55%")):
            row = block[block.index(f'data-rank="{rank}"'):]
            self.assertIn(pct, row[:row.index("</tr>")])
        self.assertIn("not checked by a person", block)
        self.assertIn("<i>Xyleborus ferrugineus</i> 26%", block)

    def test_the_details_page_without_a_prediction_shows_nothing(self):
        self.client.force_login(self.user)
        other = make_beetle(bbox="unvalidated")
        self.assertNotIn('data-testid="ai-suggestion"',
                         self.client.get(reverse("beetle_detail", args=[other.id])).content.decode())

    def test_the_annotation_page_gets_it_with_the_image(self):
        self.client.force_login(self.staff)
        data = self.client.get(reverse("game_proposals"), {"image_asset": self.roi.image_asset_id}).json()
        [s] = data["ai"][str(self.roi.id)]
        self.assertEqual([l["confidence"] for l in s["levels"]], [1.0, 0.996, 0.95, 0.546])
        page = self.client.get(reverse("tool_annotate")).content.decode()
        self.assertIn("${aiSuggestionHtml(b)}", page)
