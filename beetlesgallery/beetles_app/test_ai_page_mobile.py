"""The AI page on phones (#559): answers well inside Cloudflare's limit, a warm-up, short messages, phone layout."""
import io
from pathlib import Path
from unittest import mock

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from beetlesgallery.beetles_app import classify_assist
from beetlesgallery.tools import ibbi_models

TEMPLATE = Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles" / "tool_classify.html"
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "ai-page-mobile"}}


def upload():
    return SimpleUploadedFile("a.jpg", b"x", content_type="image/jpeg")


class AnswerInTimeTests(TestCase):
    def test_the_site_stops_waiting_before_cloudflare_does(self):
        # Cloudflare shows its own HTML page after 100 s; the site must answer first
        self.assertLess(classify_assist.CONNECT_SECONDS + classify_assist.ANSWER_SECONDS, 100)
        with mock.patch("requests.post", side_effect=requests.exceptions.Timeout()) as post:
            response = self.client.post(reverse("tool_classify"), {"image": upload()})
        self.assertEqual(post.call_args.kwargs["timeout"],
                         (classify_assist.CONNECT_SECONDS, classify_assist.ANSWER_SECONDS))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json(), {"status": "error", "message": classify_assist.TIMEOUT_MESSAGE, "waking": True})

    def test_an_html_answer_from_the_service_is_a_plain_json_error(self):
        html = mock.Mock(status_code=200)
        html.json.side_effect = ValueError("Expecting value")
        with mock.patch("requests.post", return_value=html):
            response = self.client.post(reverse("tool_classify"), {"image": upload()})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertNotIn("waking", response.json())
        self.assertNotIn("Expecting", response.content.decode())


@override_settings(CACHES=LOCMEM)
class WarmUpTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_opening_the_page_wakes_ibbi_ai_once_per_model_for_a_while(self):
        url = reverse("tool_classify_warm")
        with mock.patch("threading.Thread") as thread:
            first = self.client.post(url, {"architecture": ibbi_models.DEFAULT}).json()
            again = self.client.post(url, {"architecture": ibbi_models.DEFAULT}).json()
            unknown = self.client.post(url, {"architecture": "no-such-model"}).json()   # the default model
        self.assertEqual((first["status"], again["status"], unknown["status"]), ("warming", "already_warming", "already_warming"))
        self.assertEqual(thread.call_count, 1)
        self.assertEqual(thread.call_args.kwargs["args"], (ibbi_models.DEFAULT,))
        self.assertTrue(thread.call_args.kwargs["daemon"])
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_the_warm_up_sends_a_tiny_photo_and_ignores_failures(self):
        with mock.patch("requests.post") as post:
            classify_assist._warm(ibbi_models.DEFAULT)
        (address,), kwargs = post.call_args
        self.assertEqual(address, settings.MODAL_API_URL)
        self.assertEqual(kwargs["data"]["architecture"], ibbi_models.DEFAULT)
        name, photo, kind = kwargs["files"]["image"]
        self.assertEqual(Image.open(io.BytesIO(photo)).size, (64, 64))
        with mock.patch("requests.post", side_effect=requests.exceptions.ConnectionError()):
            classify_assist._warm(ibbi_models.DEFAULT)   # no error


class PageTests(SimpleTestCase):
    def setUp(self):
        self.page = TEMPLATE.read_text(encoding="utf-8")

    def test_no_raw_errors_reach_the_visitor(self):
        self.assertNotIn("Network Error", self.page)
        self.assertNotIn("await response.json()", self.page.split("async function post")[0])
        self.assertIn("includes('application/json')", self.page)   # an HTML error page is never parsed as JSON
        self.assertIn('id="classifyMessage"', self.page)
        for status in ("413", "403", "524"):
            self.assertIn(status, self.page)
        self.assertIn("WAKE_TRIES", self.page)   # asks again by itself while IBBI-AI wakes up

    def test_phone_photos_are_shrunk_and_previewed_from_an_object_url(self):
        self.assertIn("MAX_SIDE = 2048", self.page)
        self.assertIn("URL.createObjectURL(file)", self.page)
        self.assertNotIn("readAsDataURL", self.page)

    def test_the_page_wakes_ibbi_ai_as_it_opens(self):
        self.assertIn("{% url 'tool_classify_warm' %}", self.page)

    def test_the_floating_button_never_covers_the_end_of_the_page(self):
        self.assertIn('class="pt-8 pb-28 lg:pb-8" data-testid="ai-page"', self.page)
        self.assertIn("env(safe-area-inset-bottom)", self.page)
