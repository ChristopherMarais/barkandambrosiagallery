"""
IBBI 0.4.0's 400-species DINOv3 classifier is IBBI-AI's default model, and the IBBI-AI page says what each model can
name: a line under the model picker ("Names 400 species in 115 genera.") and "See which", a tree from subfamily to
species with a filter, whose species link to the Taxonomy Browser when the gallery has them.
"""
import json
import re

from django.test import SimpleTestCase
from django.urls import reverse

from beetlesgallery.beetles_app import ibbi_species
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_taxon
from beetlesgallery.tools import ibbi_models


class TheNewModelTests(SimpleTestCase):
    def test_it_is_the_default_on_ibbi_0_4_0(self):
        self.assertEqual(ibbi_models.IBBI_VERSION, "0.4.0")
        self.assertEqual(ibbi_models.DEFAULT, "ibbi_dinov3_400")
        self.assertEqual(next(iter(ibbi_models.MODELS)), "ibbi_dinov3_400")   # first in the pickers
        self.assertEqual(ibbi_models.model_name("ibbi_dinov3_400"),
                         "yolo11x_arthropod_detector+dinov3_400species_hierarchical_classifier")
        self.assertIn("dinov3_400species_hierarchical_classifier", ibbi_models.all_ibbi_models())

    def test_its_suggestions_rank_above_the_other_models(self):
        best = ibbi_models.preference("annotator:ibbi_dinov3_400")
        for other in ("annotator:ibbi_dinov3", "ibbi-detect-M2-yolo11x+cls-dinov3L-hier", "annotator:ibbi_bioclip2",
                      "annotator:rtdetrx"):
            self.assertLess(best, ibbi_models.preference(other), other)


class SpeciesListTests(SimpleTestCase):
    def test_every_model_has_a_list(self):
        for key, spec in ibbi_models.MODELS.items():
            self.assertIn(spec["species_list"], ("400_species", "65_species"), key)
        self.assertEqual(ibbi_models.MODELS["ibbi_dinov3_400"]["species_list"], "400_species")

    def test_the_counts(self):
        self.assertEqual(ibbi_species.counts("400_species"),
                         {"subfamily": 2, "tribe": 26, "genus": 115, "species": 400})
        self.assertEqual(ibbi_species.counts("65_species"), {"subfamily": 2, "tribe": 17, "genus": 38, "species": 65})

    def test_the_400_hold_the_65(self):
        self.assertLess(set(ibbi_species.rows("65_species")), set(ibbi_species.rows("400_species")))

    def test_each_species_sits_in_its_genus(self):
        for list_id in ("400_species", "65_species"):
            for subfamily, tribe, genus, species in ibbi_species.rows(list_id):
                self.assertTrue(all((subfamily, tribe, genus, species)))
                self.assertTrue(species.startswith(genus + " "), species)

    def test_the_tree(self):
        tree = ibbi_species.tree("65_species", {"Xylosandrus germanus": "2210"})
        self.assertEqual([node["name"] for node in tree], ["Platypodinae", "Scolytinae"])
        leaves = [leaf for sub in tree for tribe in sub["children"] for genus in tribe["children"]
                  for leaf in genus["children"]]
        self.assertEqual(len(leaves), 65)
        self.assertIn({"name": "Xylosandrus germanus", "id": "2210"}, leaves)
        self.assertIn({"name": "Xyleborus ferrugineus", "id": None}, leaves)

    def test_the_summary_line(self):
        self.assertEqual(ibbi_species.summary("ibbi_dinov3_400"), "Names 400 species in 115 genera.")
        self.assertEqual(ibbi_species.summary("yolov8x"), "Names 65 species in 38 genera.")


class PageTests(PageBehaviourCase):
    def page(self):
        return self.client.get(reverse("tool_classify")).content.decode()

    def test_the_line_and_the_button_under_the_picker(self):
        page = self.page()
        help_line = page[page.index('data-testid="model-help"'):]
        help_line = help_line[:help_line.index("</p>")]
        self.assertIn("Names 400 species in 115 genera.", help_line)
        self.assertRegex(help_line, r'<button type="button" id="showModelTaxa"[^>]*>See which</button>')

    def test_the_tree_data_names_each_models_list_and_links_gallery_species(self):
        make_taxon(valid_species_id="2210", scientific_name="Xylosandrus germanus", genus="Xylosandrus",
                   species="germanus")
        page = self.page()
        data = json.loads(re.search(r'<script id="model-taxa" type="application/json">(.*?)</script>', page, re.S)[1])
        self.assertEqual(list(data["models"]), list(ibbi_models.MODELS))
        self.assertEqual(data["models"]["ibbi_dinov3_400"],
                         {"label": "DINOv3 · 400 species", "kind": "pipeline", "list": "400_species"})
        self.assertEqual(set(data["lists"]), {"400_species", "65_species"})
        self.assertEqual(data["lists"]["400_species"]["counts"]["species"], 400)
        text = json.dumps(data["lists"]["65_species"]["tree"])
        self.assertIn('{"name": "Xylosandrus germanus", "id": "2210"}', text)

    def test_the_dialog(self):
        page = self.page()
        dialog = page[page.index('id="taxaModal"'):]
        dialog = dialog[:dialog.index("</style>")]
        for marker in ('role="dialog"', 'aria-labelledby="taxaTitle"', 'id="taxaFilter"', 'id="taxaTree"',
                       'id="taxaClose"', 'aria-label="Close"', 'id="taxaNote"'):
            self.assertIn(marker, dialog)

    def test_the_script_follows_the_picker_and_filters(self):
        page = self.page()
        script = page[page.index("(function modelTaxa()"):]
        script = script[:script.index("})();")]
        self.assertIn("ui.modelSelect.addEventListener('change', updateSummary)", script)
        self.assertIn("filter.addEventListener('input', applyFilter)", script)
        self.assertIn("?species=${encodeURIComponent(node.id)}", script)   # the Taxonomy Browser's species link
        self.assertIn("e.key === 'Escape'", script)
        self.assertIn("'It names one of these species, or stops at the genus, tribe or subfamily when it is not sure.'",
                      script)
