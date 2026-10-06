"""
AI names on boxes that are already there (#503): "Generate AI recommendation" on the annotation page no longer throws
away a found box that overlaps an existing ROI. That ROI keeps its name, tier and box and gets the AI's name as a
suggestion (a ModelPrediction), once per model.
"""
from django.urls import reverse

from beetlesgallery.beetles_app.models import Beetles, ModelPrediction, RoiName
from beetlesgallery.beetles_app.test_classify_assist import DETECTION, SPECIMEN, ClassifyCase
from beetlesgallery.beetles_app.testing import make_beetle

BOX = dict(bbox_x=0.1, bbox_y=0.1, bbox_width=0.2, bbox_height=0.4)   # DETECTION's box on the 1000 x 500 image
OTHER = {**DETECTION, "box": [600, 100, 800, 300]}


def state(roi):
    """What a person set on an ROI, which the AI must never change."""
    roi.refresh_from_db()
    return (roi.depicts_valid_name_id, roi.label_source, roi.bbox_is_validated, roi.bbox_validated_by_id,
            (roi.bbox_x, roi.bbox_y, roi.bbox_width, roi.bbox_height), roi.last_updated_by_id, roi.last_updated_at)


class OverlappingBoxTests(ClassifyCase):
    def existing_roi(self, **fields):
        roi = make_beetle(image=self.asset, taxon=self.typo, bbox="validated", **fields)
        Beetles.objects.filter(pk=roi.pk).update(**BOX)
        return roi

    def test_the_existing_roi_gets_the_ais_name_once_and_nothing_else_changes(self):
        roi = self.existing_roi(label_source=Beetles.LabelSource.TAXONOMIST)
        before, names = state(roi), RoiName.objects.count()
        response, _ = self.classify()
        self.assertEqual(response.json(), {"added": 0, "attached": 1, "already_boxed": 0, "model": "ibbi-test"})
        self.assertEqual(Beetles.objects.filter(image_asset=self.asset).count(), 1)   # no new ROI
        prediction = ModelPrediction.objects.get(roi=roi)
        self.assertEqual((prediction.valid_species_id, prediction.confidence, prediction.model_name, prediction.uploaded_by),
                         ("2210", 0.91, "annotator:ibbi-test", self.staff))
        self.assertEqual(prediction.top_k, [{"valid_species_id": "1733", "confidence": 0.06}])
        self.assertEqual((state(roi), RoiName.objects.count()), (before, names))   # name, tier, box, validation kept

        again, _ = self.classify()   # the same model again: nothing new
        self.assertEqual(again.json(), {"added": 0, "attached": 0, "already_boxed": 1, "model": "ibbi-test"})
        self.assertEqual(ModelPrediction.objects.filter(roi=roi).count(), 1)

    def test_new_boxes_are_still_added_next_to_it(self):
        roi = self.existing_roi()
        response, _ = self.classify([DETECTION, OTHER])
        self.assertEqual(response.json(), {"added": 1, "attached": 1, "already_boxed": 0, "model": "ibbi-test"})
        new = Beetles.objects.exclude(pk=roi.pk).get(image_asset=self.asset)
        self.assertEqual((new.depicts_valid_name_id, new.bbox_is_validated), ("2210", False))
        self.assertEqual(ModelPrediction.objects.filter(roi__image_asset=self.asset).count(), 2)

    def test_a_suggestion_the_model_gave_before_is_kept_as_it_was(self):
        roi = self.existing_roi()   # e.g. from a run on the AI page, which saves under the same model name
        ModelPrediction.objects.create(roi=roi, valid_species_id="1733", confidence=0.4, model_name="annotator:ibbi-test")
        response, _ = self.classify()
        self.assertEqual((response.json()["attached"], response.json()["already_boxed"]), (0, 1))
        self.assertEqual(list(ModelPrediction.objects.filter(roi=roi).values_list("valid_species_id", "confidence")),
                         [("1733", 0.4)])

    def test_another_models_suggestion_does_not_stop_this_one(self):
        roi = self.existing_roi()
        ModelPrediction.objects.create(roi=roi, valid_species_id="1733", confidence=0.8, model_name="M2e20__dinov3L336")
        response, _ = self.classify()
        self.assertEqual(response.json()["attached"], 1)
        self.assertEqual(sorted(ModelPrediction.objects.filter(roi=roi).values_list("model_name", flat=True)),
                         ["M2e20__dinov3L336", "annotator:ibbi-test"])

    def test_a_name_that_is_not_in_the_species_list_adds_nothing(self):
        roi = self.existing_roi()
        response, _ = self.classify([{**DETECTION, "label": "Not in our list"}])
        self.assertEqual((response.json()["attached"], response.json()["already_boxed"]), (0, 1))
        self.assertFalse(ModelPrediction.objects.filter(roi=roi).exists())

    def test_two_found_boxes_over_one_new_roi_add_it_once(self):
        twin = {**DETECTION, "box": [102, 52, 302, 252], "score": 0.5}
        response, _ = self.classify([DETECTION, twin])
        self.assertEqual(response.json(), {"added": 1, "attached": 0, "already_boxed": 1, "model": "ibbi-test"})
        self.assertEqual(ModelPrediction.objects.count(), 1)

    def test_a_boxless_roi_that_already_has_this_models_name_is_filled_without_an_error(self):
        template = make_beetle(image=self.asset, **SPECIMEN)
        ModelPrediction.objects.create(roi=template, valid_species_id="1733", confidence=0.4, model_name="annotator:ibbi-test")
        response, _ = self.classify()
        self.assertEqual(response.status_code, 200, response.content)
        template.refresh_from_db()
        self.assertEqual((template.bbox_x, response.json()["added"]), (0.1, 1))
        self.assertEqual(ModelPrediction.objects.filter(roi=template).count(), 1)   # kept, not duplicated

    def test_the_annotation_page_shows_the_suggestion_after_the_run(self):
        roi = self.existing_roi()
        self.classify()
        data = self.client.get(reverse("game_proposals"), {"image_asset": str(self.asset.id)}).json()
        [suggestion] = data["ai"][str(roi.id)]
        self.assertEqual((suggestion["model_name"], suggestion["model_key"]), ("IBBI-AI · ibbi-test", "annotator:ibbi-test"))
        self.assertEqual(suggestion["levels"][-1]["value"], "Xyleborus affinis")
        self.assertFalse(suggestion["levels"][-1]["agrees"])   # the label (Ips typographus) stays and differs

    def test_the_annotation_page_says_what_was_added_and_what_got_a_suggestion(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse("tool_annotate")).content.decode()
        self.assertIn("Nothing is validated; an existing ROI keeps its box and gets the AI's suggestion.", page)
        self.assertNotIn("boxes over an existing ROI are skipped", page)
        script = page[page.index("async function classifyCurrentImage()"):]
        script = script[:script.index("async function acquireLock")]
        self.assertIn("data.attached", script)
        self.assertIn("got an AI suggestion", script)
        self.assertIn("(nothing validated)", script)
        self.assertIn("await selectImage(img)", script)   # reloads the ROIs and their AI suggestions
