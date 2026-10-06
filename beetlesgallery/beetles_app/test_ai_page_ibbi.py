"""
The AI page as IBBI-AI (#502): the picker shows just the model names with the recommended one first, the IBBI text
sits under the tool with the server note on top, the page says what happened to the photo, and photos it keeps say
"AI page" for their institution and who added them (when signed in). Older AI-page photos get the same (migration).
IBBI-AI's suggestions read as "IBBI-AI · DINOv3" wherever people see them.
"""
import importlib
import io
import re
from pathlib import Path
from unittest import mock

import requests
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from beetlesgallery.beetles_app import game_tuning
from beetlesgallery.beetles_app.classify_assist import readable_model_name
from beetlesgallery.beetles_app.models import ImageAsset, ModelPrediction
from beetlesgallery.beetles_app.predictions import suggestions_for
from beetlesgallery.beetles_app.test_classify_assist import DETECTION, ClassifyCase, fake_response
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_taxon
from beetlesgallery.tools import ibbi_models

TEMPLATES = Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles"
MIGRATION = "beetlesgallery.beetles_app.migrations.0047_ai_page_photos"


class AiPageTests(PageBehaviourCase):
    def page(self):
        return self.client.get(reverse("tool_classify")).content.decode()

    def test_the_picker_shows_the_model_names_with_the_recommended_one_first(self):
        page = self.page()
        picker = page[page.index('id="modelSelect"'):]
        picker = picker[:picker.index("</select>")]
        options = re.findall(r'<option value="([^"]+)"( selected)?>([^<]+)</option>', picker)
        self.assertEqual([value for value, _, _ in options], list(ibbi_models.MODELS))   # the values are unchanged
        self.assertEqual(options[0], ("ibbi_dinov3", " selected", "DINOv3 (recommended)"))
        self.assertEqual([label for _, _, label in options[1:]],
                         ["BioCLIP 2", "RT-DETR", "YOLO12", "YOLO11", "YOLOv10", "YOLOv9", "YOLOv8"])
        self.assertEqual([selected for _, selected, _ in options].count(" selected"), 1)
        self.assertEqual(picker.count("recommended"), 1)
        self.assertNotRegex(picker.lower(), "classifier|detector")
        self.assertEqual((ibbi_models.resolve("rtdetr"), ibbi_models.resolve("yolov8")), ("rtdetrx", "yolov8x"))

    def test_one_short_line_explains_the_models(self):
        page = self.page()
        help_text = page[page.index('data-testid="model-help"'):]
        help_text = help_text[:help_text.index("</p>")]
        self.assertIn("All models find and name bark beetles.", help_text)   # the picker marks the recommended one (#534)
        self.assertIn('data-testid="ibbi-link"', help_text)
        self.assertNotIn("Species classifier", page)
        self.assertNotIn("Species detector", page)

    def test_the_server_note_is_on_top_and_the_ibbi_text_under_the_tool(self):
        page = self.page()
        server, tool, results, about = (page.index(marker) for marker in (
            'data-testid="server-info"', 'id="classifyForm"', 'id="analysisDetails"', 'data-testid="ibbi-about"'))
        self.assertLess(server, tool)
        self.assertLess(results, about)
        for text in ("our open-source package", "Treat the answer as a screening tool"):
            self.assertEqual(page.count(text), 1, text)
            self.assertGreater(page.index(text), about, text)

    def test_it_is_called_ibbi_ai_not_the_classifier(self):
        page = self.page()
        self.assertIn('<h1 class="page-title">IBBI-AI: Intelligent Bark Beetle Identifier</h1>', page)
        self.assertIn("Before you use IBBI-AI", page)
        self.assertIn("IBBI-AI is a screening tool", page)
        self.assertIn("so you can try IBBI-AI", page)
        self.assertNotRegex(page, r"(?i)\bthe classifier\b")

    def test_after_a_result_the_page_says_what_happened_to_the_photo(self):
        page = self.page()
        panel = page[page.index('id="analysisDetails"'):]
        self.assertLess(panel.index('id="savedNote"'), panel.index('id="ranksPanel"'))   # one line by the result
        script = page[page.index("function showSaved(data)"):]
        script = script[:script.index("\n    }")]
        for status, words in (("saved", "added to the gallery, unchecked"), ("already_on_platform", "already in the gallery"),
                              ("opted_out", "not kept, as you asked"), ("attached", "Saved as the AI suggestion"),
                              ("nothing_new", "Nothing new to save")):
            self.assertIn(f"{status}:", script)
            self.assertIn(words, script)
        self.assertNotIn("not_saved", script)   # nothing to say then
        self.assertIn("state.isExample ? ''", script)   # examples are never kept, and the page already says so
        self.assertIn("showSaved(data);", page)

    def test_the_confidence_slider_starts_where_the_service_does(self):
        self.assertIn('id="confRange" name="box_threshold" min="0.05"', self.page())


class SavedPhotoTests(ClassifyCase):
    """A photo the AI page keeps (it shows straight away, unchecked) is labelled as coming from the AI page."""

    def setUp(self):
        super().setUp()
        self.client.logout()   # the AI page is open to everyone

    def submit(self, detections=(DETECTION,), **extra):
        buffer = io.BytesIO()
        Image.new("RGB", (1000, 500), (200, 180, 160)).save(buffer, "JPEG")
        upload = SimpleUploadedFile("beetle.jpg", buffer.getvalue(), content_type="image/jpeg")
        with mock.patch("requests.post", return_value=fake_response(list(detections))) as post:
            response = self.client.post(reverse("tool_classify"), {"image": upload, **extra})
        return response, post

    def kept(self):
        return ImageAsset.objects.get(full_path_at_import__startswith="classifier/")

    def test_an_anonymous_photo_says_ai_page_and_nobody_is_recorded(self):
        response, _ = self.submit()
        self.assertEqual(response.json()["saved"], "saved")
        photo = self.kept()
        self.assertEqual((photo.image_institution, photo.added_by, photo.last_updated_by, photo.is_validated),
                         ("AI page", None, None, False))
        self.assertIn("IBBI-AI", photo.image_notes)
        prediction = ModelPrediction.objects.get(roi__image_asset=photo)
        # the same name as the annotation page's runs, so the game counts one track record per model
        self.assertEqual((prediction.model_name, prediction.uploaded_by), ("annotator:ibbi-test", None))

    def test_a_signed_in_visitor_is_recorded_as_who_added_it(self):
        self.client.force_login(self.user)
        self.submit()
        photo = self.kept()
        self.assertEqual((photo.image_institution, photo.added_by, photo.last_updated_by), ("AI page", self.user, self.user))
        self.assertEqual(list(self.user.added_images.all()), [photo])
        self.assertEqual(ModelPrediction.objects.get(roi__image_asset=photo).uploaded_by, self.user)

    def test_who_added_a_photo_cannot_be_changed_through_the_api(self):
        self.client.force_login(self.user)
        self.submit()
        photo = self.kept()
        self.client.force_login(self.staff)
        response = self.client.patch(f"/api/v1/image-assets/{photo.id}/", {"added_by": self.staff.pk},
                                     content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        photo.refresh_from_db()
        self.assertEqual(photo.added_by, self.user)

    def test_the_threshold_is_kept_between_5_and_100_percent(self):
        for sent, expected in (("0.01", 0.05), ("0.4", 0.4), ("7", 1.0), ("abc", 0.25)):
            _, post = self.submit(detections=(), box_threshold=sent)
            self.assertEqual(post.call_args.kwargs["data"]["box_threshold"], expected, sent)

    def test_service_problems_come_back_as_a_short_message(self):
        # one generic message whatever went wrong: the service's details stay in the server log (CodeQL, ca7a6c1);
        # only a timeout gets its own fixed hint (#534)
        message = "Classification service is temporarily unavailable. Please try again later."
        for kwargs in ({"status": 500}, {"body_status": "error"}):
            upload = SimpleUploadedFile("a.jpg", b"x", content_type="image/jpeg")
            with mock.patch("requests.post", return_value=fake_response([], **kwargs)):
                response = self.client.post(reverse("tool_classify"), {"image": upload})
            self.assertEqual((response.status_code, response.json()["message"]), (502, message))
            self.assertNotIn("500", response.json()["message"])
        with mock.patch("requests.post", side_effect=requests.exceptions.Timeout()):
            upload = SimpleUploadedFile("a.jpg", b"x", content_type="image/jpeg")
            response = self.client.post(reverse("tool_classify"), {"image": upload})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["message"], "The AI model is waking up. Please try again in a minute.")
        self.assertFalse(ImageAsset.objects.filter(full_path_at_import__startswith="classifier/").exists())


class ReadableModelNameTests(PageBehaviourCase):
    """People read "IBBI-AI · DINOv3", not "annotator:ibbi_dinov3"; other models' names are shown as they came."""

    def test_ibbi_ai_runs_read_as_ibbi_ai_and_the_model(self):
        for stored, shown in (
            ("annotator:ibbi_dinov3", "IBBI-AI · DINOv3"), ("annotator:ibbi_bioclip2", "IBBI-AI · BioCLIP 2"),
            ("annotator:yolo12x", "IBBI-AI · YOLO12"),
            ("annotator:rtdetr", "IBBI-AI · RT-DETR"), ("annotator:yolov8", "IBBI-AI · YOLOv8"),   # names from before ibbi 0.3
            ("annotator:ibbi-test", "IBBI-AI · ibbi-test"),                                        # a key the site no longer lists
            ("M2e20__dinov3L336", "M2e20__dinov3L336"), ("ibbi-rtdetr", "ibbi-rtdetr"),            # uploaded predictions
        ):
            self.assertEqual(readable_model_name(stored), shown, stored)

    def test_the_specimen_and_annotation_pages_show_the_readable_name(self):
        taxon = make_taxon(valid_species_id="2210", genus="Xyleborus", species="affinis", scientific_name="Xyleborus affinis")
        roi = make_beetle(bbox="unvalidated")
        ModelPrediction.objects.create(roi=roi, valid_species_id="2210", taxon=taxon, confidence=0.9,
                                       model_name="annotator:ibbi_dinov3")
        [s] = suggestions_for([roi])[roi.id]
        self.assertEqual((s["model_name"], s["model_key"]), ("IBBI-AI · DINOv3", "annotator:ibbi_dinov3"))
        self.client.force_login(self.staff)
        detail = self.client.get(reverse("beetle_detail", args=[roi.id])).content.decode()
        block = detail[detail.index('data-testid="ai-suggestion"'):]
        self.assertIn("IBBI-AI · DINOv3", block)
        self.assertNotIn("annotator:", detail)
        ai = self.client.get(reverse("game_proposals"), {"image_asset": str(roi.image_asset_id)}).json()["ai"]
        self.assertEqual(ai[str(roi.id)][0]["model_name"], "IBBI-AI · DINOv3")   # what aiSuggestionHtml shows


class OldAiPagePhotosTests(PageBehaviourCase):
    """Migration 0047 labels the photos the AI page kept before, and finds who sent them in their history."""

    def photo(self, path, **fields):
        return ImageAsset.objects.create(full_path_at_import=path, **fields)

    def test_old_ai_page_photos_get_the_institution_and_who_sent_them(self):
        users = get_user_model().objects
        sender, gone, other = (users.create_user(name, password="pw") for name in ("sender", "gone", "other"))
        signed_in = self.photo("classifier/aa/beetle.jpg", last_updated_by=sender)
        anonymous = self.photo("classifier/bb/beetle.jpg", image_institution="")
        museum = self.photo("classifier/cc/beetle.jpg", image_institution="Museum X", last_updated_by=sender)
        known = self.photo("classifier/dd/beetle.jpg", last_updated_by=sender, added_by=other)
        deleted_sender = self.photo("classifier/ee/beetle.jpg", last_updated_by=gone)
        upload = self.photo("uploads/beetle.jpg", last_updated_by=sender)
        ImageAsset.objects.filter(pk=signed_in.pk).update(last_updated_by=other)   # edited later by a curator
        gone.delete()   # its id stays in the photo's history

        importlib.import_module(MIGRATION).label_ai_page_photos(apps, None)
        found = {p.pk: (p.image_institution, p.added_by_id) for p in ImageAsset.objects.all()}
        self.assertEqual(found[signed_in.pk], ("AI page", sender.pk))   # the sender, not the later editor
        self.assertEqual(found[anonymous.pk], ("AI page", None))
        self.assertEqual(found[museum.pk], ("Museum X", sender.pk))     # an institution someone set is kept
        self.assertEqual(found[known.pk], ("AI page", other.pk))
        self.assertEqual(found[deleted_sender.pk], ("AI page", None))
        self.assertEqual(found[upload.pk], (None, None))                # not from the AI page

    def test_the_migration_is_marked_as_reviewed_and_can_be_undone(self):
        module = importlib.import_module(MIGRATION)
        self.assertTrue(module.Migration.DATA_MIGRATION_REVIEWED)
        self.assertEqual(module.Migration.dependencies[0], ("beetles_app", "0046_remove_board_privacy"))
        run = module.Migration.operations[-1]
        self.assertTrue(run.reversible)


class IbbiAiWordingTests(PageBehaviourCase):
    """Elsewhere the AI is IBBI-AI, or "an AI model" where the names can come from any model."""

    def test_the_other_pages_and_messages(self):
        self.assertIn("From an AI model, not checked by a person.", (TEMPLATES / "includes" / "ai_suggestion.html").read_text())
        self.assertIn("Upload an AI model's species guesses", (TEMPLATES / "upload_predictions.html").read_text())
        self.assertIn("nobody has named yet that IBBI-AI places there", (TEMPLATES / "game_how.html").read_text())
        self.assertTrue(game_tuning.TUNABLES["GAME_REF_MODEL_MIN_CONFIDENCE"]["help"].startswith("The AI's name counts"))
