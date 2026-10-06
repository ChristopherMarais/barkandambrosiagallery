"""
"Generate AI recommendation" on a specimen page (#502) opens the AI page with the photo's id. IBBI-AI then runs on
the gallery's own copy of the photo, never on what the browser sends, so a record can only get names for its own
photo. The answer is shown, and the AI's name is kept on the photo's ROIs that have no AI suggestion yet. No ROI or
image is ever added for a photo that is already in the gallery.
"""
import json
import uuid
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from beetlesgallery.beetles_app.classify_assist import SUBMISSIONS_PER_HOUR
from beetlesgallery.beetles_app.models import Beetles, ImageAsset, ModelPrediction
from beetlesgallery.beetles_app.test_classify_assist import DETECTION, ClassifyCase, fake_response
from beetlesgallery.beetles_app.testing import make_beetle

OTHER = {**DETECTION, "box": [600, 100, 800, 300]}


class GalleryPhotoTests(ClassifyCase):
    def setUp(self):
        super().setUp()
        self.client.logout()   # the AI page is open to everyone
        with self.asset.image_file.open("rb") as fh:
            self.stored = fh.read()

    def roi(self, box=None, **fields):
        roi = make_beetle(image=self.asset, **fields)
        if box:
            Beetles.objects.filter(pk=roi.pk).update(bbox_x=box[0], bbox_y=box[1], bbox_width=box[2], bbox_height=box[3])
            roi.refresh_from_db()
        return roi

    def run_page(self, detections=(DETECTION,), **extra):
        data = {"asset": str(self.asset.id), "architecture": "ibbi_dinov3", **extra}
        with mock.patch("requests.post", return_value=fake_response(list(detections))) as post:
            response = self.client.post(reverse("tool_classify"), data)
        return response, post

    def test_the_stored_photo_is_classified_not_what_the_browser_sends(self):
        self.roi((0.1, 0.1, 0.2, 0.4))
        upload = SimpleUploadedFile("other.jpg", b"another photo", content_type="image/jpeg")
        response, post = self.run_page(image=upload)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(post.call_args.kwargs["files"]["image"][1], self.stored)
        self.assertEqual(len(response.json()["detections"]), 1)          # the answer is still shown
        self.assertEqual(ImageAsset.objects.count(), 1)                  # nothing new kept

    def test_rois_without_a_suggestion_get_the_ais_name_and_the_others_are_left_alone(self):
        bare = self.roi((0.1, 0.1, 0.2, 0.4))
        named = self.roi((0.6, 0.2, 0.2, 0.4), taxon=self.typo)
        ModelPrediction.objects.create(roi=named, valid_species_id="1733", confidence=0.8, model_name="M2e20__dinov3L336")
        response, _ = self.run_page([DETECTION, OTHER])
        self.assertEqual((response.json()["saved"], response.json()["attached"]), ("attached", 1))
        prediction = ModelPrediction.objects.get(roi=bare)
        self.assertEqual((prediction.valid_species_id, prediction.model_name, prediction.uploaded_by),
                         ("2210", "annotator:ibbi-test", None))   # one name per model, whichever page ran it
        self.assertEqual(list(ModelPrediction.objects.filter(roi=named).values_list("model_name", flat=True)),
                         ["M2e20__dinov3L336"])
        self.assertEqual(Beetles.objects.filter(image_asset=self.asset).count(), 2)   # no new ROI
        bare.refresh_from_db()
        self.assertIsNone(bare.depicts_valid_name_id)                    # a suggestion, never a label

    def test_a_photo_with_one_roi_without_a_box_gets_the_best_box_name(self):
        record = self.roi()
        self.run_page([{**OTHER, "score": 0.4, "label": "Ips_typographus"}, DETECTION])
        record.refresh_from_db()
        self.assertEqual(ModelPrediction.objects.get(roi=record).valid_species_id, "2210")   # the surer box
        self.assertEqual((record.bbox_x, record.depicts_valid_name_id), (None, None))         # still no box or name
        self.assertEqual(Beetles.objects.filter(image_asset=self.asset).count(), 1)

    def test_several_rois_without_a_box_are_left_alone(self):
        self.roi()
        self.roi()
        response, _ = self.run_page()
        self.assertEqual(response.json()["saved"], "nothing_new")
        self.assertFalse(ModelPrediction.objects.exists())

    def test_nothing_new_when_every_roi_already_has_a_suggestion(self):
        roi = self.roi((0.1, 0.1, 0.2, 0.4))
        ModelPrediction.objects.create(roi=roi, valid_species_id="1733", confidence=0.8, model_name="annotator:ibbi-test")
        response, _ = self.run_page()
        self.assertEqual((response.json()["saved"], response.json()["attached"]), ("nothing_new", 0))
        self.assertEqual(ModelPrediction.objects.count(), 1)

    def test_a_signed_in_visitor_is_recorded_with_the_suggestion(self):
        roi = self.roi((0.1, 0.1, 0.2, 0.4))
        self.client.force_login(self.user)
        self.run_page()
        self.assertEqual(ModelPrediction.objects.get(roi=roi).uploaded_by, self.user)

    def test_suggestions_count_against_the_hourly_limit(self):
        roi = self.roi((0.1, 0.1, 0.2, 0.4))
        cache.set("classifier-saves:127.0.0.1", SUBMISSIONS_PER_HOUR, 3600)
        response, _ = self.run_page()
        self.assertEqual((response.json()["saved"], len(response.json()["detections"])), ("not_saved", 1))
        self.assertFalse(ModelPrediction.objects.filter(roi=roi).exists())
        cache.delete("classifier-saves:127.0.0.1")
        self.run_page()
        self.assertEqual(cache.get("classifier-saves:127.0.0.1"), 1)

    def test_curators_who_may_generate_ai_recommendations_are_not_limited(self):
        roi = self.roi((0.1, 0.1, 0.2, 0.4))
        self.client.force_login(self.staff)   # has the AI recommendations area, as on the annotation page
        cache.set(f"classifier-saves:{self.staff.pk}", SUBMISSIONS_PER_HOUR, 3600)
        response, _ = self.run_page()
        self.assertEqual(response.json()["saved"], "attached")
        self.assertEqual(ModelPrediction.objects.get(roi=roi).uploaded_by, self.staff)

    def test_a_photo_that_is_not_in_the_gallery_is_refused(self):
        deleted = ImageAsset.objects.create(full_path_at_import="gone.jpg", image_file=self.asset.image_file.name,
                                            is_deleted=True)
        for asset in (str(uuid.uuid4()), "not-an-id", str(deleted.id)):
            with mock.patch("requests.post") as post:
                response = self.client.post(reverse("tool_classify"), {"asset": asset})
            self.assertEqual((response.status_code, response.json()["status"]), (404, "error"), asset)
            post.assert_not_called()

    def test_the_page_shows_the_gallery_photo_ready_to_classify(self):
        page = self.client.get(reverse("tool_classify"), {"asset": str(self.asset.id)}).content.decode()
        start = page.index('<script id="gallery-photo" type="application/json">') + len('<script id="gallery-photo" type="application/json">')
        photo = json.loads(page[start:page.index("</script>", start)])
        self.assertEqual({k: photo[k] for k in ("id", "url")}, {"id": str(self.asset.id), "url": self.asset.display_url})
        for other in ("not-an-id", str(uuid.uuid4())):
            self.assertNotIn('id="gallery-photo"', self.client.get(reverse("tool_classify"), {"asset": other}).content.decode())

    def test_the_page_sends_the_photo_id_instead_of_the_photo(self):
        page = self.client.get(reverse("tool_classify")).content.decode()
        self.assertIn("document.getElementById('gallery-photo')", page)
        self.assertIn("{ asset: photo.id }", page)
        submit = page[page.index("ui.form.addEventListener('submit'"):]
        self.assertIn("formData.delete('image');", submit)
        self.assertIn("formData.set('asset', state.assetId);", submit)
        self.assertIn("state.assetId = (options && options.asset) || null;", page)   # another image clears it

    def test_the_specimen_page_links_with_the_photo_id(self):
        beetle = make_beetle(image=self.asset)
        self.client.force_login(self.staff)
        page = self.client.get(reverse("beetle_detail", args=[beetle.id])).content.decode()
        self.assertIn(f'{reverse("tool_classify")}?asset={self.asset.id}"', page)
        self.assertNotIn("image_url=", page)
