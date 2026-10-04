"""
"Edit bounding boxes" on its own never validates or un-validates anything: not an ROI, not an image, not by
creating a box that says it is validated, and not through the image's details.
"""
import json

from django.contrib.auth import get_user_model

from beetlesgallery.beetles_app import areas
from beetlesgallery.beetles_app.models import AreaGrant, Beetles, ImageAsset
from beetlesgallery.beetles_app.test_pages import PageTestCase
from beetlesgallery.beetles_app.testing import make_beetle, make_image


class BoxesNeverValidateTests(PageTestCase):
    def setUp(self):
        super().setUp()
        boxer = get_user_model().objects.create_user("boxer", password="pw")
        AreaGrant.objects.create(user=boxer, area=areas.BOXES)
        self.client.force_login(boxer)
        self.image = make_image()
        self.open_roi = make_beetle(image=self.image, bbox="unvalidated")
        self.done = make_image(is_validated=True)
        self.done_roi = make_beetle(image=self.done, bbox="validated")

    def post(self, url, data=None):
        return self.client.post(url, json.dumps(data or {}), content_type="application/json")

    def test_validating_and_unvalidating_images_and_rois_is_refused(self):
        for url in (f"/api/v1/image-assets/{self.image.id}/validate/", f"/api/v1/beetles/{self.open_roi.id}/validate/",
                    f"/api/v1/image-assets/{self.done.id}/unvalidate/", f"/api/v1/beetles/{self.done_roi.id}/unvalidate/"):
            with self.subTest(url=url):
                self.assertEqual(self.post(url).status_code, 403)
        self.assertFalse(ImageAsset.objects.get(id=self.image.id).is_validated)
        self.assertFalse(Beetles.objects.get(id=self.open_roi.id).bbox_is_validated)
        self.assertTrue(ImageAsset.objects.get(id=self.done.id).is_validated)
        self.assertTrue(Beetles.objects.get(id=self.done_roi.id).bbox_is_validated)

    def test_a_new_box_cannot_arrive_validated_and_image_validation_cannot_be_set_directly(self):
        res = self.post("/api/v1/beetles/", {"image_asset_id": str(self.image.id), "bbox_x": 0.6, "bbox_y": 0.6,
                                             "bbox_width": 0.2, "bbox_height": 0.2, "bbox_is_validated": True})
        self.assertEqual(res.status_code, 403)
        res = self.client.patch(f"/api/v1/image-assets/{self.image.id}/", json.dumps({"is_validated": True}),
                                content_type="application/json")
        self.assertEqual(res.status_code, 403)
        self.assertFalse(ImageAsset.objects.get(id=self.image.id).is_validated)

    def test_moving_a_box_on_a_validated_image_is_still_a_box_edit_and_validates_nothing(self):
        res = self.client.patch(f"/api/v1/beetles/{self.open_roi.id}/", json.dumps({"bbox_x": 0.25}),
                                content_type="application/json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(Beetles.objects.get(id=self.open_roi.id).bbox_is_validated)
