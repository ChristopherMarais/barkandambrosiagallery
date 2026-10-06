"""The open code-scanning alerts (#5 polynomial ReDoS, #37 and #57 stack-trace exposure) stay fixed."""
import io
import random
import re
import time
from contextlib import redirect_stdout
from unittest import mock

import requests
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.urls import reverse

from beetlesgallery.beetles_app import classify_assist, game_applied, utils
from beetlesgallery.beetles_app.test_classify_assist import ClassifyCase, fake_response
from beetlesgallery.beetles_app.models import Beetles
from beetlesgallery.beetles_app.test_game import GameCase
from beetlesgallery.beetles_app.test_pipeline_taxonomy import IPS, TaxonomyRebuildCase, species
from beetlesgallery.beetles_app.testing import make_beetle, make_image

# The pattern _parse_numeric used before the fix, to check the new parser reads every short input the same way.
OLD_NUM_OP_RE = re.compile(r"^\s*(<=|>=|<|>|=)?\s*([+-]?\d+(?:\.\d+)?)\s*$")
OLD_OPS = {None: "exact", "": "exact", "=": "exact", "<": "lt", "<=": "lte", ">": "gt", ">=": "gte"}


def old_parse_numeric(value):
    m = OLD_NUM_OP_RE.match(value or "")
    return (OLD_OPS[m.group(1)], float(m.group(2))) if m else (None, None)


class NumericFilterParsingTests(SimpleTestCase):
    """Alert #5: a numeric search term such as ">= 10" is parsed in linear time."""

    def test_reads_every_short_input_as_before(self):
        rng = random.Random(5)
        alphabet = " \t<>=+-.0123456789x"
        samples = ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 9))) for _ in range(20000)]
        samples += ["  >=  10  ", "<5", "=-3.25", "\n7\n", "> +1.0", "<=>5", "1.", ".5", "1e5", "٣"]
        for text in samples:
            self.assertEqual(utils._parse_numeric(text), old_parse_numeric(text), repr(text))

    def test_long_runs_of_spaces_are_quick(self):
        n = 200_000
        for text in (" " * n + "x", "<" + " " * n + "x", ">=" + " " * n, " " * n + "5" + " " * n + "x",
                     "=" + "\t" * n + "1.", "1" * n + "x"):
            start = time.perf_counter()
            self.assertEqual(utils._parse_numeric(text), (None, None))
            self.assertLess(time.perf_counter() - start, 0.5, repr(text[:12]))
        self.assertEqual(utils._parse_numeric(" " * n + ">= 12" + " " * n), ("gte", 12.0))


class ClassifyErrorMessageTests(ClassifyCase):
    """Alert #57: "Generate AI recommendation" shows a fixed text and logs the details on the server."""

    def post(self, **patch):
        with mock.patch("requests.post", **patch):
            with self.assertLogs("beetlesgallery.beetles_app.api.views", level="WARNING") as logs:
                response = self.client.post(self.url, {"architecture": "rtdetr"}, content_type="application/json")
        self.assertEqual(response.status_code, 502)
        return response.json()["error"], "\n".join(logs.output)

    def test_unreachable_service(self):
        error, log = self.post(side_effect=requests.exceptions.ConnectionError("secret-host:8443 refused"))
        self.assertEqual(error, "The AI service could not be reached. Please try again later.")
        self.assertIn("ConnectionError", log)
        self.assertNotIn("secret", error)

    def test_service_error_status_is_logged_not_shown(self):
        error, log = self.post(return_value=fake_response([], status=503))
        self.assertEqual(error, "The AI service returned an error. Please try again later.")
        self.assertIn("HTTP 503", log)

    def test_service_message_is_logged_not_shown(self):
        response = fake_response([], body_status="error")
        response.json.return_value["message"] = "Server error: secret traceback"
        error, log = self.post(return_value=response)
        self.assertEqual(error, "The AI service could not process this image.")
        self.assertIn("secret traceback", log)
        self.assertNotIn("secret", error)

    def test_timeout_says_the_model_is_waking(self):
        error, _ = self.post(side_effect=requests.exceptions.Timeout())
        self.assertEqual(error, classify_assist.TIMEOUT_MESSAGE)

    def test_every_code_has_a_message_and_unknown_codes_fall_back(self):
        self.assertEqual(classify_assist.user_message(classify_assist.ClassifyError("unknown_model", "'x'")),
                         "Unknown model.")
        self.assertEqual(classify_assist.user_message(classify_assist.ClassifyError("no-such-code")),
                         classify_assist.MESSAGES["failed"])
        self.assertEqual(classify_assist.ClassifyTimeout().code, "timeout")
        self.assertIsInstance(classify_assist.ClassifyTimeout(), classify_assist.ClassifyError)


class RevertMessageTests(GameCase):
    """Alert #37: taking back a game label answers with a fixed text for each reason."""

    def setUp(self):
        super().setUp()
        self.beetle = make_beetle(image=make_image(), taxon=self.t_ferr, bbox="unvalidated",
                                  label_source=Beetles.LabelSource.COMMUNITY)
        self.client.force_login(self.staff)

    def revert(self):
        return self.client.post(reverse("game_applied_revert", args=[self.beetle.id]))

    def test_nothing_to_revert(self):
        res = self.revert()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"], game_applied.REVERT_MESSAGES["nothing"])

    def test_unknown_reason_gets_a_plain_text(self):
        with mock.patch.object(game_applied, "revert", side_effect=game_applied.RevertError("something new")):
            res = self.revert()
        self.assertEqual((res.status_code, res.json()["error"]), (409, "This label cannot be reverted."))


class TaxonomyRebuildErrorTests(TaxonomyRebuildCase):
    """An unexpected failure while rebuilding the taxonomy is logged, not printed into the page."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.superuser)

    def test_unexpected_error_is_logged_not_shown(self):
        with mock.patch("beetlesgallery.beetles_app.views.call_command", side_effect=RuntimeError("secret /opt/x")):
            with self.assertLogs("beetlesgallery.beetles_app.views", level="ERROR"), redirect_stdout(io.StringIO()):
                response = self.client.post(reverse("admin_valid_species"),
                                            {"csv_file": SimpleUploadedFile("reference.csv", species(IPS))})
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("database rebuild failed" in m for m in messages), messages)
        self.assertFalse(any("secret" in m for m in messages), messages)
