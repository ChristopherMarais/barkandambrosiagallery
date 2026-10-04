"""Classify with AI on the annotation page."""
import io
from unittest import mock

import requests
from django.core.files.base import ContentFile
from PIL import Image

from beetlesgallery.beetles_app.classify_assist import iou, to_fractions
from beetlesgallery.beetles_app.models import Beetles, ImageLock, ModelPrediction
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon


def fake_response(detections, status=200, body_status="success"):
    response = mock.Mock(status_code=status)
    response.json.return_value = {
        "status": body_status, "detections": detections, "model_used": "ibbi-test",
        "class_names": ["Xyleborus affinis", "Ips typographus", "Unlisted species"],
    }
    return response


DETECTION = {"box": [100, 50, 300, 250], "score": 0.91, "label": "Xyleborus_affinis", "probs": [0.91, 0.06, 0.03]}


class ClassifyCase(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.affinis = make_taxon(valid_species_id="2210", genus="Xyleborus", species="affinis", scientific_name="Xyleborus affinis")
        self.typo = make_taxon(valid_species_id="1733", genus="Ips", species="typographus", scientific_name="Ips typographus")
        self.asset = make_image(image_width=1000, image_height=500)
        buffer = io.BytesIO()
        Image.new("RGB", (1000, 500), "white").save(buffer, "JPEG")
        self.asset.image_file.save("a.jpg", ContentFile(buffer.getvalue()))
        self.url = f"/api/v1/image-assets/{self.asset.id}/classify/"
        self.client.force_login(self.staff)

    def classify(self, detections=(DETECTION,), **kwargs):
        with mock.patch("requests.post", return_value=fake_response(list(detections), **kwargs)) as post:
            response = self.client.post(self.url, {"architecture": "rtdetr"}, content_type="application/json")
        return response, post


class BoxMathTests(ClassifyCase):
    def test_pixels_become_clamped_fractions(self):
        self.assertEqual(to_fractions([100, 50, 300, 250], 1000, 500), (0.1, 0.1, 0.2, 0.4))
        self.assertEqual(to_fractions([-20, -5, 1200, 600], 1000, 500), (0.0, 0.0, 1.0, 1.0))
        self.assertIsNone(to_fractions([10, 10, 10, 200], 1000, 500))

    def test_overlap(self):
        self.assertEqual(iou((0, 0, .5, .5), (0, 0, .5, .5)), 1.0)
        self.assertEqual(iou((0, 0, .2, .2), (.5, .5, .2, .2)), 0.0)


class ClassifyTests(ClassifyCase):
    def test_adds_unvalidated_rois_with_the_proposed_species(self):
        response, post = self.classify()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["added"], 1)
        roi = Beetles.objects.get(image_asset=self.asset)
        self.assertEqual((roi.bbox_x, roi.bbox_y, roi.bbox_width, roi.bbox_height), (0.1, 0.1, 0.2, 0.4))
        self.assertFalse(roi.bbox_is_validated)
        self.assertEqual((roi.depicts_valid_name_id, roi.bbox_created_by), ("2210", self.staff))
        self.asset.refresh_from_db()
        self.assertFalse(self.asset.is_validated)
        prediction = ModelPrediction.objects.get(roi=roi)
        self.assertEqual((prediction.valid_species_id, prediction.confidence, prediction.model_name), ("2210", 0.91, "annotator:ibbi-test"))
        self.assertEqual(prediction.top_k, [{"valid_species_id": "1733", "confidence": 0.06}])  # unlisted species left out
        self.assertEqual(post.call_args.kwargs["data"]["architecture"], "rtdetrx")   # the pre-0.3 name, as its new key

    def test_existing_rois_are_kept_and_an_overlapping_box_is_skipped(self):
        existing = make_beetle(image=self.asset, taxon=self.typo, bbox="validated")
        existing.bbox_x, existing.bbox_y, existing.bbox_width, existing.bbox_height = 0.1, 0.1, 0.2, 0.4
        existing.save()
        other = {**DETECTION, "box": [600, 100, 800, 300]}
        response, _ = self.classify([DETECTION, other])
        self.assertEqual(response.json(), {"added": 1, "already_boxed": 1, "model": "ibbi-test"})
        self.assertEqual(Beetles.objects.filter(image_asset=self.asset).count(), 2)
        existing.refresh_from_db()
        self.assertEqual((existing.depicts_valid_name_id, existing.bbox_is_validated), ("1733", True))

    def test_running_twice_adds_nothing_the_second_time(self):
        self.classify()
        response, _ = self.classify()
        self.assertEqual(response.json()["added"], 0)
        self.assertEqual(Beetles.objects.filter(image_asset=self.asset).count(), 1)

    def test_an_unknown_species_still_gets_a_box_without_a_label(self):
        response, _ = self.classify([{**DETECTION, "label": "Not in our list"}])
        roi = Beetles.objects.get(image_asset=self.asset)
        self.assertIsNone(roi.depicts_valid_name_id)
        self.assertFalse(ModelPrediction.objects.exists())

    def test_service_problems_are_reported_and_nothing_is_added(self):
        for kwargs in ({"status": 500}, {"body_status": "error"}):
            response, _ = self.classify(**kwargs)
            self.assertEqual(response.status_code, 502)
        for error in (requests.exceptions.Timeout(), requests.exceptions.ConnectionError()):
            with mock.patch("requests.post", side_effect=error):
                self.assertEqual(self.client.post(self.url, {}, content_type="application/json").status_code, 502)
        self.assertFalse(Beetles.objects.filter(image_asset=self.asset).exists())

    def test_unknown_model_is_refused_before_calling_the_service(self):
        with mock.patch("requests.post") as post:
            response = self.client.post(self.url, {"architecture": "evil"}, content_type="application/json")
        self.assertEqual(response.status_code, 502)
        post.assert_not_called()

    def test_needs_the_annotate_area_and_respects_another_users_lock(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.url, {}, content_type="application/json").status_code, 403)
        self.client.force_login(self.staff)
        ImageLock.objects.create(image_asset=self.asset, locked_by=self.superuser)
        response, post = self.classify()
        self.assertEqual(response.status_code, 409)
        post.assert_not_called()

    def test_the_annotation_page_has_the_button(self):
        self.assertContains(self.client.get("/tools/annotate/"), "classifyCurrentImage")


SPECIMEN = dict(aspect="dorsal", collection_country="Brazil", collection_stateProvince="Sao Paulo",
                specimen_sex="female", specimen_notes="from the gallery", alias_id="ALT-7")


class ProposedRoiMetadataTests(ClassifyCase):
    """A box from the classifier starts with what a box drawn with the mouse starts with."""

    def other_box(self, label="Xyleborus_affinis"):
        return {**DETECTION, "box": [600, 100, 800, 300], "label": label}

    def copied(self, roi):
        return {name: getattr(roi, name) for name in SPECIMEN}

    def test_a_new_box_copies_the_specimen_details_of_the_latest_roi(self):
        existing = make_beetle(image=self.asset, taxon=self.typo, bbox="validated", **SPECIMEN)
        self.classify([self.other_box()])
        roi = Beetles.objects.exclude(pk=existing.pk).get(image_asset=self.asset)
        self.assertEqual(self.copied(roi), SPECIMEN)
        self.assertEqual(roi.depicts_valid_name_id, "2210")          # the model's species, not the copied one
        self.assertEqual(roi.taxon, self.affinis)
        self.assertFalse(roi.bbox_is_validated)

    def test_a_copied_species_is_never_kept_for_a_box_the_model_did_not_label(self):
        existing = make_beetle(image=self.asset, taxon=self.typo, bbox="validated", **SPECIMEN)
        self.classify([self.other_box("Not_in_the_list")])
        roi = Beetles.objects.exclude(pk=existing.pk).get(image_asset=self.asset)
        self.assertEqual(self.copied(roi), SPECIMEN)
        self.assertIsNone(roi.depicts_valid_name_id)
        self.assertIsNone(roi.taxon)

    def test_every_box_of_one_run_gets_the_details_from_before_the_run(self):
        existing = make_beetle(image=self.asset, taxon=self.typo, bbox="validated", **SPECIMEN)
        third = {**DETECTION, "box": [100, 300, 300, 450], "label": "Ips_typographus", "score": 0.5}
        self.classify([self.other_box(), third])
        new = Beetles.objects.exclude(pk=existing.pk).filter(image_asset=self.asset)
        self.assertEqual(new.count(), 2)
        self.assertEqual({tuple(self.copied(r).items()) for r in new}, {tuple(SPECIMEN.items())})
        self.assertEqual({r.depicts_valid_name_id for r in new}, {"2210", "1733"})

    def test_the_first_box_fills_the_images_boxless_template_roi(self):
        template = make_beetle(image=self.asset, **SPECIMEN)
        self.assertIsNone(template.bbox_x)
        self.classify()
        self.assertEqual(Beetles.objects.filter(image_asset=self.asset).count(), 1)
        template.refresh_from_db()
        self.assertEqual((template.bbox_x, template.bbox_is_validated, template.bbox_created_by), (0.1, False, self.staff))
        self.assertEqual(self.copied(template), SPECIMEN)
        self.assertEqual(template.depicts_valid_name_id, "2210")     # it had no species, so the model's is used
        self.assertEqual(ModelPrediction.objects.get(roi=template).valid_species_id, "2210")

    def test_a_template_roi_keeps_a_species_a_person_gave_it(self):
        template = make_beetle(image=self.asset, taxon=self.typo, **SPECIMEN)
        self.classify()
        template.refresh_from_db()
        self.assertEqual(template.depicts_valid_name_id, "1733")
        self.assertEqual(ModelPrediction.objects.get(roi=template).valid_species_id, "2210")  # still kept as a suggestion

    def test_the_second_box_after_a_template_copies_its_details(self):
        template = make_beetle(image=self.asset, **SPECIMEN)
        self.classify([DETECTION, self.other_box()])
        boxes = Beetles.objects.filter(image_asset=self.asset)
        self.assertEqual(boxes.count(), 2)
        for roi in boxes:
            self.assertEqual(self.copied(roi), SPECIMEN)

    def test_it_matches_a_box_drawn_with_the_mouse(self):
        make_beetle(image=self.asset, taxon=self.typo, bbox="validated", **SPECIMEN)
        drawn = self.client.post(
            "/api/v1/beetles/",
            {"image_asset_id": str(self.asset.id), "bbox_x": 0.6, "bbox_y": 0.2, "bbox_width": 0.2, "bbox_height": 0.4},
            content_type="application/json",
        )
        self.assertEqual(drawn.status_code, 201, drawn.content)
        mouse = Beetles.objects.get(pk=drawn.json()["id"])
        Beetles.objects.filter(pk=mouse.pk).update(is_deleted=True)   # so the next box is not judged against it
        self.classify([{**DETECTION, "box": [100, 300, 300, 450], "label": "Xyleborus_affinis"}])
        proposed = Beetles.objects.filter(image_asset=self.asset, is_deleted=False, bbox_is_validated=False).exclude(pk=mouse.pk).get()
        self.assertEqual(self.copied(proposed), self.copied(mouse))


class ClassifierPageSavesImagesTests(PageBehaviourCase):
    """Images sent to the public classifier page that contain a beetle are kept, unvalidated, once."""

    def setUp(self):
        super().setUp()
        make_taxon(valid_species_id="2210", genus="Xyleborus", species="affinis", scientific_name="Xyleborus affinis")
        buffer = io.BytesIO()
        Image.new("RGB", (1000, 500), (200, 180, 160)).save(buffer, "JPEG")
        self.jpeg = buffer.getvalue()

    def submit(self, detections=(DETECTION,), data=None, content=None):
        from django.core.files.uploadedfile import SimpleUploadedFile
        upload = SimpleUploadedFile("beetle.jpg", content or self.jpeg, content_type="image/jpeg")
        with mock.patch("requests.post", return_value=fake_response(list(detections))) as post:
            response = self.client.post("/tools/classify/", {"image": upload, "architecture": "rtdetr"})
        return response

    def test_a_beetle_image_is_kept_unvalidated_with_its_proposed_labels(self):
        from beetlesgallery.beetles_app.models import ImageAsset
        response = self.submit()
        self.assertEqual((response.status_code, response.json()["saved"]), (200, "saved"))
        asset = ImageAsset.objects.get()
        self.assertFalse(asset.is_validated)
        self.assertEqual((asset.image_width, asset.image_height, asset.image_size_bytes), (1000, 500, len(self.jpeg)))
        self.assertTrue(asset.image_file and asset.thumb_small)
        roi = Beetles.objects.get(image_asset=asset)
        self.assertEqual((roi.depicts_valid_name_id, roi.bbox_is_validated, roi.bbox_created_by), ("2210", False, None))
        self.assertEqual(ModelPrediction.objects.count(), 1)

    def test_the_same_image_again_is_not_saved_twice_and_the_platform_copy_is_kept(self):
        from beetlesgallery.beetles_app.models import ImageAsset
        self.submit()
        asset = ImageAsset.objects.get()
        roi_count = Beetles.objects.count()
        again = self.submit().json()
        self.assertEqual(again["saved"], "already_on_platform")
        self.assertEqual((ImageAsset.objects.count(), Beetles.objects.count()), (1, roi_count))

    def test_an_image_already_on_the_platform_gets_nothing_added_even_if_it_was_uploaded_elsewhere(self):
        import hashlib
        from beetlesgallery.beetles_app.models import ImageAsset
        existing = make_image(image_sha256=hashlib.sha256(self.jpeg).hexdigest(), is_validated=False)
        make_beetle(image=existing, bbox="validated")
        self.assertEqual(self.submit().json()["saved"], "already_on_platform")
        self.assertEqual((ImageAsset.objects.count(), Beetles.objects.filter(image_asset=existing).count()), (1, 1))

    def test_a_deleted_image_with_the_same_bytes_also_counts_as_on_the_platform(self):
        import hashlib
        from beetlesgallery.beetles_app.models import ImageAsset
        ImageAsset.objects.create(full_path_at_import="x", image_sha256=hashlib.sha256(self.jpeg).hexdigest(), is_deleted=True)
        self.assertEqual(self.submit().json()["saved"], "already_on_platform")
        self.assertEqual(ImageAsset.objects.count(), 1)

    def test_no_beetle_found_means_nothing_is_kept(self):
        from beetlesgallery.beetles_app.models import ImageAsset
        self.assertEqual(self.submit(detections=()).json()["saved"], "not_saved")
        self.assertFalse(ImageAsset.objects.exists())

    def test_something_that_is_not_an_image_is_not_kept_but_the_answer_still_comes_back(self):
        from beetlesgallery.beetles_app.models import ImageAsset
        response = self.submit(content=b"not really an image")
        self.assertEqual((response.status_code, response.json()["saved"]), (200, "not_saved"))
        self.assertFalse(ImageAsset.objects.exists())

    def test_a_logged_in_user_is_recorded_and_a_failure_never_breaks_the_classification(self):
        from beetlesgallery.beetles_app.models import ImageAsset
        self.client.force_login(self.user)
        self.submit()
        self.assertEqual(Beetles.objects.get().bbox_created_by, self.user)
        with mock.patch("beetlesgallery.beetles_app.classify_assist.save_classifier_submission", side_effect=OSError("disk full")):
            response = self.submit(content=self.jpeg + b"x")
        self.assertEqual((response.status_code, response.json()["saved"]), (200, "not_saved"))
        self.assertEqual(len(response.json()["detections"]), 1)

    def test_saving_is_limited_per_person_per_hour(self):
        from beetlesgallery.beetles_app.classify_assist import SUBMISSIONS_PER_HOUR
        outcomes = []
        for n in range(SUBMISSIONS_PER_HOUR + 1):
            buffer = io.BytesIO()
            Image.new("RGB", (40 + n, 40), (n, 0, 0)).save(buffer, "PNG")
            outcomes.append(self.submit(content=buffer.getvalue()).json()["saved"])
        self.assertEqual(outcomes[:-1], ["saved"] * SUBMISSIONS_PER_HOUR)
        self.assertEqual(outcomes[-1], "not_saved")

    def test_ticking_dont_keep_means_nothing_is_saved_but_the_answer_comes_back(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from beetlesgallery.beetles_app.models import ImageAsset
        upload = SimpleUploadedFile("beetle.jpg", self.jpeg, content_type="image/jpeg")
        with mock.patch("requests.post", return_value=fake_response([DETECTION])):
            data = self.client.post("/tools/classify/", {"image": upload, "keep_image": "0"}).json()
        self.assertEqual((data["saved"], len(data["detections"])), ("opted_out", 1))
        self.assertFalse(ImageAsset.objects.exists())

    def test_opting_out_does_not_count_against_the_hourly_limit_and_keeping_is_the_default(self):
        self.assertEqual(self.submit().json()["saved"], "saved")

    def test_the_page_offers_the_choice_the_notice_and_the_examples(self):
        from django.conf import settings
        from pathlib import Path
        from beetlesgallery.beetles_app.views import CLASSIFIER_EXAMPLES
        page = self.client.get("/tools/classify/").content.decode()
        for needle in ('id="dontKeep"', 'id="termsModal"', "Examples are never added to the gallery"):
            self.assertIn(needle, page)
        self.assertEqual(page.count('class="example-btn'), len(CLASSIFIER_EXAMPLES))
        for ex in CLASSIFIER_EXAMPLES:
            self.assertTrue((Path(settings.BASE_DIR) / "beetlesgallery/static/img/classifier_examples" / ex["file"]).exists(), ex["file"])
            self.assertTrue(ex["credit"] and ex["licence"])

    def test_gps_location_is_removed_from_the_copy_we_keep(self):
        from beetlesgallery.beetles_app.classify_assist import without_location
        image = Image.new("RGB", (60, 40), "white")
        exif = Image.Exif()
        exif[0x010F] = "Camera"
        exif[0x8825] = {1: "N", 2: (1.0, 2.0, 3.0)}
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", exif=exif)
        self.assertIn(0x8825, Image.open(io.BytesIO(buffer.getvalue())).getexif())
        cleaned = Image.open(io.BytesIO(without_location(buffer.getvalue())))
        self.assertNotIn(0x8825, cleaned.getexif())
        self.assertEqual(cleaned.getexif().get(0x010F), "Camera")
        self.assertEqual(without_location(self.jpeg), self.jpeg)       # nothing to remove: unchanged
        self.assertEqual(without_location(b"not an image"), b"not an image")
