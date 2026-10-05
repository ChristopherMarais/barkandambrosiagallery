"""The AI tools say plainly which model is which, link to IBBI for how well each does, and the annotation page asks
for an "AI recommendation" in a block whose picker and button fit the panel."""
from django.urls import reverse

from beetlesgallery.beetles_app.test_classify_assist import ClassifyCase
from beetlesgallery.tools import ibbi_models


class AiWordingTests(ClassifyCase):
    def test_model_names_are_short_and_say_classifier_or_detector(self):
        for key, spec in ibbi_models.MODELS.items():
            expected = "Species classifier: " if spec["kind"] == "pipeline" else "Species detector: "
            self.assertTrue(spec["label"].startswith(expected), key)
            self.assertLessEqual(len(spec["label"]), 30, key)
        self.assertEqual(next(iter(ibbi_models.MODELS)), ibbi_models.DEFAULT)   # the first option is the default

    def test_the_classifier_page_links_to_ibbi(self):
        page = self.client.get(reverse("tool_classify")).content.decode()
        self.assertIn(f'href="{ibbi_models.IBBI_DOCS_URL}"', page)
        self.assertIn('data-testid="ibbi-link"', page)
        self.assertIn("<strong>Species classifier:</strong>", page)
        self.assertNotIn("63 distinct species", page)

    def test_the_annotation_page_offers_an_ai_recommendation(self):
        page = self.client.get(reverse("tool_annotate")).content.decode()
        self.assertIn("Generate AI recommendation", page)
        self.assertNotIn("Classify with AI", page)
        block = page[page.index('data-testid="ai-recommendation"'):]
        block = block[:block.index("</div>")]
        self.assertIn('id="classify-model"', block)
        self.assertIn("w-full", block.split('id="classify-btn"')[1].split(">")[0] + block.split('id="classify-model"')[1].split(">")[0])
