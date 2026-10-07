"""
Only the best model's AI suggestion (#534): a beetle named by several models shows the best one's name only
(ibbi_models.MODEL_PREFERENCE), on the details and annotation pages and in the game, and a worse model's name is not
saved where a better one already named the beetle. Also the AI page polish: the model help line, the chart's titles,
a gallery photo as an example, and the timeout hint.
"""
import io
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import mock

import requests
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from beetlesgallery.beetles_app import game_answer_review, game_reference
from beetlesgallery.beetles_app.classify_assist import TIMEOUT_MESSAGE, outranked
from beetlesgallery.beetles_app.models import Beetles, ModelPrediction, RoiDifficulty
from beetlesgallery.beetles_app.predictions import best_predictions, import_predictions, suggestions_for
from beetlesgallery.beetles_app.test_classify_assist import DETECTION, ClassifyCase, fake_response
from beetlesgallery.beetles_app.testing import make_beetle, make_image
from beetlesgallery.beetles_app.views import CLASSIFIER_EXAMPLES, CLASSIFIER_GALLERY_EXAMPLES
from beetlesgallery.tools import ibbi_models

TEMPLATES = Path(__file__).resolve().parent.parent / "templates" / "beetles"
DINO_UPLOAD = "ibbi-detect-M2-yolo11x+cls-dinov3L-hier"   # the owner's example (#534)
BOX = dict(bbox_x=0.1, bbox_y=0.1, bbox_width=0.2, bbox_height=0.4)   # DETECTION's box on the 1000 x 500 image


def answer_from(model_used, detections=(DETECTION,)):
    response = fake_response(list(detections))
    response.json.return_value["model_used"] = model_used
    return response


class PreferenceTests(ClassifyCase):
    def test_the_dinov3_pipeline_first_then_other_pipelines_then_detectors(self):
        ranked = ["annotator:ibbi_dinov3", DINO_UPLOAD, "annotator:ibbi_bioclip2", "cls-effnet-hier",
                  "annotator:rtdetrx", "annotator:yolo11x", "some-new-model"]
        ranks = [ibbi_models.preference(name) for name in ranked]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(ranks[0], ranks[1])                                  # an IBBI-AI run or an upload of it
        self.assertLess(ibbi_models.preference(DINO_UPLOAD), ibbi_models.preference("annotator:rtdetrx"))
        self.assertEqual(ibbi_models.preference("SOME-NEW-MODEL"), len(ibbi_models.MODEL_PREFERENCE))
        self.assertTrue(outranked("annotator:rtdetrx", [DINO_UPLOAD]))
        self.assertFalse(outranked("annotator:ibbi_dinov3", [DINO_UPLOAD, "annotator:rtdetrx"]))

    def test_one_suggestion_per_roi_from_the_best_model(self):
        roi = make_beetle(image=self.asset)
        ModelPrediction.objects.create(roi=roi, valid_species_id="2210", confidence=0.98, model_name=DINO_UPLOAD,
                                       model_version="2026-10-03")
        ModelPrediction.objects.create(roi=roi, valid_species_id="1733", confidence=0.39,
                                       model_name="annotator:rtdetrx")   # newer, but a detector
        [shown] = suggestions_for([roi])[roi.id]
        self.assertEqual((shown["model_key"], shown["top_species"]["valid_species_id"]), (DINO_UPLOAD, "2210"))

    def test_the_newest_run_when_models_rank_the_same(self):
        roi = make_beetle(image=self.asset)
        old = ModelPrediction.objects.create(roi=roi, valid_species_id="2210", confidence=0.9, model_name=DINO_UPLOAD)
        ModelPrediction.objects.filter(pk=old.pk).update(created_at=old.created_at - timedelta(days=2))
        new = ModelPrediction.objects.create(roi=roi, valid_species_id="1733", confidence=0.6,
                                             model_name="annotator:ibbi_dinov3")
        self.assertEqual(best_predictions(ModelPrediction.objects.all()), {roi.id: new})

    def test_the_game_uses_the_best_models_name_too(self):
        roi = make_beetle(image=self.asset)
        ModelPrediction.objects.create(roi=roi, valid_species_id="2210", confidence=0.98, model_name=DINO_UPLOAD,
                                       rank_confidence={"genus": {"value": "Xyleborus", "confidence": 0.99}})
        ModelPrediction.objects.create(roi=roi, valid_species_id="1733", confidence=0.97, model_name="annotator:rtdetrx",
                                       rank_confidence={"genus": {"value": "Ips", "confidence": 0.97}})
        self.assertEqual(game_answer_review._ai([roi.id])[roi.id]["genus"]["value"], "Xyleborus")
        trusted = {(DINO_UPLOAD, "genus", "xyleborus"): (50, 50), ("annotator:rtdetrx", "genus", "ips"): (50, 50)}
        with mock.patch.object(game_reference, "model_precision", return_value=trusted):
            self.assertEqual(game_reference.model_references([roi.id])[roi.id]["genus"], "Xyleborus")


class SavingTests(ClassifyCase):
    """The annotation page's "Generate AI recommendation" and the AI page save through the same code."""

    def boxed_roi(self):
        roi = make_beetle(image=self.asset)
        Beetles.objects.filter(pk=roi.pk).update(**BOX)
        return roi

    def run_annotation_page(self, model_used):
        with mock.patch("requests.post", return_value=answer_from(model_used)):
            return self.client.post(self.url, {}, content_type="application/json").json()

    def test_a_worse_model_adds_nothing_where_a_better_one_named_the_beetle(self):
        roi = self.boxed_roi()
        ModelPrediction.objects.create(roi=roi, valid_species_id="1733", confidence=0.98, model_name=DINO_UPLOAD)
        self.assertEqual(self.run_annotation_page("rtdetrx")["attached"], 0)
        self.assertEqual(list(ModelPrediction.objects.filter(roi=roi).values_list("model_name", flat=True)),
                         [DINO_UPLOAD])

    def test_a_better_model_adds_its_name_and_the_worse_one_is_hidden_not_deleted(self):
        roi = self.boxed_roi()
        ModelPrediction.objects.create(roi=roi, valid_species_id="1733", confidence=0.39, model_name="annotator:rtdetrx")
        self.assertEqual(self.run_annotation_page("ibbi_dinov3")["attached"], 1)
        self.assertEqual(ModelPrediction.objects.filter(roi=roi).count(), 2)
        [shown] = suggestions_for([roi])[roi.id]
        self.assertEqual(shown["model_name"], "IBBI-AI · DINOv3")

    def test_the_ai_page_names_a_gallery_photo_only_with_a_better_model(self):
        self.client.logout()
        roi = self.boxed_roi()
        ModelPrediction.objects.create(roi=roi, valid_species_id="1733", confidence=0.39, model_name="annotator:rtdetrx")
        for model, expected in (("yolov8x", ("nothing_new", 0)), ("ibbi_dinov3", ("attached", 1)),
                                ("ibbi_dinov3", ("nothing_new", 0))):
            with mock.patch("requests.post", return_value=answer_from(model)):
                data = self.client.post(reverse("tool_classify"), {"asset": str(self.asset.id)}).json()
            self.assertEqual((data["saved"], data["attached"]), expected, model)
        self.assertEqual(sorted(ModelPrediction.objects.filter(roi=roi).values_list("model_name", flat=True)),
                         ["annotator:ibbi_dinov3", "annotator:rtdetrx"])


class UploadTests(ClassifyCase):
    HEADER = "record_id,valid_species_id,confidence,model_name"

    def load(self, *rows, **options):
        return import_predictions("\n".join([self.HEADER, *rows]) + "\n", user=self.staff, **options)

    def test_a_worse_models_rows_are_left_out_in_the_file_and_against_the_site(self):
        roi, named = make_beetle(image=self.asset), make_beetle(image=self.asset)
        ModelPrediction.objects.create(roi=named, valid_species_id="2210", confidence=0.9, model_name=DINO_UPLOAD)
        rows = (f"{roi.id},2210,0.4,rtdetrx", f"{roi.id},1733,0.8,{DINO_UPLOAD}", f"{named.id},1733,0.99,yolo11x")
        check = self.load(*rows, dry_run=True)
        self.assertEqual((check.ok, check.created, check.skipped), (True, 1, 2))
        self.assertFalse(ModelPrediction.objects.filter(roi=roi).exists())
        result = self.load(*rows)
        self.assertEqual((result.created, result.updated, result.skipped), (1, 0, 2))
        self.assertEqual(list(ModelPrediction.objects.filter(roi=roi).values_list("model_name", flat=True)),
                         [DINO_UPLOAD])
        self.assertEqual(list(ModelPrediction.objects.filter(roi=named).values_list("model_name", flat=True)),
                         [DINO_UPLOAD])
        # the game's difficulty comes from the model that is kept, not from the surer but worse one
        self.assertEqual(RoiDifficulty.objects.get(roi=roi).model_name, DINO_UPLOAD)
        self.assertFalse(RoiDifficulty.objects.filter(roi=named).exists())

    def test_a_better_model_is_added_and_the_same_model_replaces_its_rows(self):
        roi = make_beetle(image=self.asset)
        ModelPrediction.objects.create(roi=roi, valid_species_id="2210", confidence=0.4, model_name="rtdetrx")
        self.assertEqual(self.load(f"{roi.id},1733,0.8,{DINO_UPLOAD}").skipped, 0)
        again = self.load(f"{roi.id},2210,0.7,{DINO_UPLOAD}")
        self.assertEqual((again.created, again.updated, again.skipped), (0, 1, 0))
        self.assertEqual(ModelPrediction.objects.filter(roi=roi).count(), 2)

    def test_the_upload_pages_say_how_many_were_left_out(self):
        for page in ("upload_predictions.html", "data_management.html"):
            self.assertIn("left out: a better model already named those beetles.", (TEMPLATES / page).read_text(encoding="utf-8"))


class PageTextTests(ClassifyCase):
    def page(self):
        return self.client.get(reverse("tool_classify")).content.decode()

    def test_the_details_page_says_the_percentages_are_the_models_confidence(self):
        text = (TEMPLATES / "includes" / "ai_suggestion.html").read_text(encoding="utf-8")
        self.assertIn("The percentages are the model's confidence.", text)

    def test_the_model_help_line_and_link(self):
        page = self.page()
        help_text = page[page.index('data-testid="model-help"'):]
        help_text = help_text[:help_text.index("</p>")]
        self.assertIn("All models find and name bark beetles.", help_text)
        self.assertNotIn("is recommended", help_text)
        # the link sits next to the select as "Compare models" (14px), not buried in the tiny help line (#618)
        marker = page.index('data-testid="ibbi-link"')
        tag_start = page.rfind("<a", 0, marker)
        link = page[tag_start:page.index("</a>", marker)]
        self.assertIn("Compare models", link)
        self.assertIn("text-sm", link)

    def test_the_chart_has_a_title_and_axis_titles(self):
        page = self.page()
        chart = page[page.index("function initChart"):page.index("function updateChartData")]
        self.assertIn("title: { display: true, text: 'Most likely species'", chart)
        self.assertIn("""title: { display: true, text: "Model's confidence (%)\"""", chart)
        self.assertIn("title: { display: true, text: 'Species'", chart)

    def test_a_gallery_photo_is_an_example_while_it_is_in_the_gallery(self):
        self.assertEqual(CLASSIFIER_GALLERY_EXAMPLES[0]["asset"], "0050ca5a-e88d-4d09-8ff6-9644f2a10775")
        self.assertEqual(self.page().count('class="example-btn'), len(CLASSIFIER_EXAMPLES))   # not here: left out
        buffer = io.BytesIO()
        Image.new("RGB", (40, 30), "white").save(buffer, "JPEG")
        photo = make_image(id=uuid.UUID(CLASSIFIER_GALLERY_EXAMPLES[0]["asset"]), photographer="A. Photographer")
        photo.image_file.save("several.jpg", ContentFile(buffer.getvalue()))
        page = self.page()
        self.assertEqual(page.count('class="example-btn'), len(CLASSIFIER_EXAMPLES) + 1)
        self.assertIn(f'data-src="{photo.display_url}"', page)
        self.assertIn("Several beetles", page)
        self.assertIn("A. Photographer", page)

    def test_a_timeout_says_the_model_is_waking_up_and_other_errors_stay_generic(self):
        self.assertEqual(TIMEOUT_MESSAGE, "IBBI-AI is waking up. Please try again in a minute.")
        upload = SimpleUploadedFile("a.jpg", b"x", content_type="image/jpeg")
        with mock.patch("requests.post", side_effect=requests.exceptions.Timeout("secret detail")):
            response = self.client.post(reverse("tool_classify"), {"image": upload})
        self.assertEqual((response.status_code, response.json()["message"]), (502, TIMEOUT_MESSAGE))
        self.assertNotIn("secret", response.content.decode())
        upload = SimpleUploadedFile("a.jpg", b"x", content_type="image/jpeg")
        with mock.patch("requests.post", side_effect=requests.exceptions.ConnectionError("secret host")):
            response = self.client.post(reverse("tool_classify"), {"image": upload})
        self.assertEqual(response.json()["message"],
                         "Classification service is temporarily unavailable. Please try again later.")
        with mock.patch("requests.post", side_effect=requests.exceptions.Timeout()):
            response = self.client.post(self.url, {}, content_type="application/json")   # the annotation page
        self.assertEqual(response.json()["error"], TIMEOUT_MESSAGE)
