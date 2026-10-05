"""Reporting a beetle from its details page, and "Wrong name" only once a player has answered."""
import json

from django.urls import reverse

from beetlesgallery.beetles_app.models import GameReport
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image


class ReportFromDetailsTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.roi = make_beetle(image=make_image(image_file="tests/photo.jpg"), bbox="validated")
        self.client.force_login(self.user)   # a member: specimen pages, no editing

    def report(self, reason="bad_box"):
        return self.client.post(reverse("report_roi", args=[self.roi.id]), json.dumps({"reason": reason}),
                                content_type="application/json")

    def test_the_details_page_has_the_report_menu(self):
        page = self.client.get(reverse("beetle_detail", args=[self.roi.id])).content.decode()
        self.assertIn('data-testid="report-roi"', page)
        for reason in ("wrong_label", "bad_box", "bad_image", "other"):
            self.assertIn(f'data-reason="{reason}"', page)

    def test_a_report_goes_to_the_curators(self):
        response = self.report()
        self.assertEqual(response.json(), {"status": "open", "already": False})
        report = GameReport.objects.get()
        self.assertEqual((report.roi_id, report.reporter, report.reason, report.was_validated),
                         (self.roi.id, self.user, "bad_box", True))
        self.assertTrue(self.report().json()["already"])
        self.assertEqual(GameReport.objects.count(), 1)

    def test_bad_requests(self):
        self.assertEqual(self.report("nope").status_code, 400)
        self.client.logout()
        self.assertEqual(self.report().status_code, 302)
        self.assertFalse(GameReport.objects.exists())
