"""
The AI page's confidence slider works live: IBBI-AI is asked once for every box down to the lowest threshold, and the
browser shows and hides boxes, the list and the chart as the slider moves, with no new request. The slider sits right
under the photo. Whatever the slider says, only boxes at KEEP_THRESHOLD or more are kept: a photo added to the gallery
and a gallery photo's records never get a weak box. The annotation page's Generate is unchanged (#577).
"""
import io
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from beetlesgallery.beetles_app import classify_assist
from beetlesgallery.beetles_app.models import Beetles, ImageAsset, ModelPrediction
from beetlesgallery.beetles_app.test_classify_assist import DETECTION, ClassifyCase, fake_response
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle

SURE = {**DETECTION, "score": 0.91}                                                   # kept
EDGE = {**DETECTION, "box": [400, 50, 600, 250], "score": 0.25}                       # exactly at the line: kept
WEAK = {**DETECTION, "box": [700, 100, 900, 300], "score": 0.1, "label": "Ips_typographus"}   # shown, never kept


def photo_upload():
    buffer = io.BytesIO()
    Image.new("RGB", (1000, 500), (200, 180, 160)).save(buffer, "JPEG")
    return SimpleUploadedFile("beetle.jpg", buffer.getvalue(), content_type="image/jpeg")


class OneRequestTests(ClassifyCase):
    def setUp(self):
        super().setUp()
        self.client.logout()   # the AI page is open to everyone

    def post(self, detections, **extra):
        with mock.patch("requests.post", return_value=fake_response(list(detections))) as post:
            response = self.client.post(reverse("tool_classify"), {"image": photo_upload(), **extra})
        return response, post

    def test_one_request_asks_for_every_box_and_answers_with_all_of_them(self):
        response, post = self.post([SURE, EDGE, WEAK], box_threshold="0.6")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["data"]["box_threshold"], classify_assist.LOWEST_THRESHOLD)
        body = response.json()
        self.assertEqual([d["score"] for d in body["detections"]], [0.91, 0.25, 0.1])   # each with its score
        self.assertEqual(body["keep_threshold"], classify_assist.KEEP_THRESHOLD)

    def test_a_kept_photo_gets_only_the_boxes_at_the_keep_threshold_or_more(self):
        response, _ = self.post([SURE, EDGE, WEAK])
        self.assertEqual(response.json()["saved"], "saved")
        photo = ImageAsset.objects.get(full_path_at_import__startswith="classifier/")
        rois = Beetles.objects.filter(image_asset=photo, bbox_x__isnull=False)
        self.assertEqual(sorted(r.bbox_x for r in rois), [0.1, 0.4])   # SURE and EDGE, not WEAK at 0.7
        self.assertFalse(ModelPrediction.objects.filter(roi__image_asset=photo, valid_species_id="1733").exists())

    def test_a_photo_with_only_weak_boxes_is_not_kept_but_its_boxes_are_shown(self):
        response, _ = self.post([WEAK])
        self.assertEqual(response.json()["saved"], "not_saved")
        self.assertEqual(len(response.json()["detections"]), 1)
        self.assertFalse(ImageAsset.objects.filter(full_path_at_import__startswith="classifier/").exists())

    def test_save_classifier_submission_drops_weak_boxes_itself(self):
        image = photo_upload().read()
        result = {"status": "success", "model_used": "ibbi-test", "class_names": [], "detections": [WEAK]}
        self.assertEqual(classify_assist.save_classifier_submission(image, "b.jpg", result), "not_saved")
        result["detections"] = [WEAK, SURE]
        self.assertEqual(classify_assist.save_classifier_submission(image, "b.jpg", result), "saved")
        photo = ImageAsset.objects.get(full_path_at_import__startswith="classifier/")
        self.assertEqual(Beetles.objects.filter(image_asset=photo, bbox_x__isnull=False).count(), 1)


class GalleryPhotoTests(ClassifyCase):
    """A gallery photo run from its specimen page: a weak box never names one of its records."""

    def setUp(self):
        super().setUp()
        self.client.logout()

    def roi(self, box):
        roi = make_beetle(image=self.asset)
        Beetles.objects.filter(pk=roi.pk).update(bbox_x=box[0], bbox_y=box[1], bbox_width=box[2], bbox_height=box[3])
        return roi

    def run_page(self, detections):
        data = {"asset": str(self.asset.id), "architecture": "ibbi_dinov3"}
        with mock.patch("requests.post", return_value=fake_response(list(detections))) as post:
            response = self.client.post(reverse("tool_classify"), data)
        return response, post

    def test_only_sure_boxes_name_the_photos_records(self):
        sure_roi, weak_roi = self.roi((0.1, 0.1, 0.2, 0.4)), self.roi((0.7, 0.2, 0.2, 0.4))
        response, post = self.run_page([SURE, WEAK])
        self.assertEqual(post.call_args.kwargs["data"]["box_threshold"], classify_assist.LOWEST_THRESHOLD)
        self.assertEqual((response.json()["saved"], response.json()["attached"]), ("attached", 1))
        self.assertEqual(len(response.json()["detections"]), 2)   # both still shown
        self.assertTrue(ModelPrediction.objects.filter(roi=sure_roi).exists())
        self.assertFalse(ModelPrediction.objects.filter(roi=weak_roi).exists())

    def test_only_weak_boxes_keep_nothing(self):
        roi = self.roi((0.7, 0.2, 0.2, 0.4))
        response, _ = self.run_page([WEAK])
        self.assertEqual((response.json()["saved"], response.json()["attached"]), ("not_saved", 0))
        self.assertFalse(ModelPrediction.objects.filter(roi=roi).exists())


class AnnotationGenerateTests(ClassifyCase):
    """The annotation page's Generate still sends its own threshold and keeps what comes back."""

    def test_generate_keeps_its_threshold_and_its_boxes(self):
        with mock.patch("requests.post", return_value=fake_response([WEAK])) as post:
            response = self.client.post(self.url, {"architecture": "rtdetr", "box_threshold": 0.1},
                                        content_type="application/json")
        self.assertEqual(post.call_args.kwargs["data"]["box_threshold"], 0.1)
        self.assertEqual(response.json()["added"], 1)


class SliderPageTests(PageBehaviourCase):
    def page(self):
        return self.client.get(reverse("tool_classify")).content.decode()

    def test_the_slider_sits_right_under_the_photo_and_is_not_sent(self):
        page = self.page()
        photo, bar = page.index('id="canvasContainer"'), page.index('data-testid="confidence-bar"')
        self.assertLess(photo, bar)
        self.assertLess(bar, page.index('id="analysisDetails"'))   # between the photo and the names
        form = page[page.index('id="classifyForm"'):page.index("</form>")]
        self.assertNotIn("confRange", form)
        self.assertNotIn('name="box_threshold"', page)
        bar_html = page[bar:page.index('id="analysisDetails"')]
        self.assertIn('<label for="confRange"', bar_html)
        self.assertIn(">Confidence</label>", bar_html)
        self.assertIn('type="range"', bar_html)          # a native slider: arrow keys move it by 1%
        self.assertIn('aria-live="polite"', bar_html)    # the count is read out as it changes
        self.assertIn("Boxes under 25% are not kept in the gallery.", bar_html)

    def test_the_browser_filters_boxes_list_and_chart_without_a_new_request(self):
        page = self.page()
        script = page[page.index("function applyThreshold()"):]
        script = script[:script.index("ui.confRange.addEventListener('input', applyThreshold);")]
        self.assertIn("boxScore(state.detections[i]) >= threshold()", page)
        self.assertIn("total === 1 ? 'beetle' : 'beetles'", script)   # "3 of 7 beetles above 40%"
        self.assertIn("of ${total}", script)
        self.assertIn("above ${pct}%", script)
        self.assertIn("tag.classList.toggle('hidden'", script)     # the list
        self.assertIn("select(", script)                          # the chart follows a box still shown
        self.assertIn("requestAnimationFrame(draw)", script)      # the boxes
        self.assertNotIn("fetch(", script)                        # never asks IBBI-AI again
        self.assertIn("if (!isShown(i)) return;", page)           # draw skips hidden boxes
        self.assertIn("if (!isShown(i)) continue;", page)         # and so does picking one with the mouse
