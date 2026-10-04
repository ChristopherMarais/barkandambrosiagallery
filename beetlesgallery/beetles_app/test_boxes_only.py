"""An account that may only edit boxes: draw, move and remove boxes, but never change names, details or validation."""
import json

from django.contrib.auth import get_user_model
from django.urls import reverse

from beetlesgallery.beetles_app import areas
from beetlesgallery.beetles_app.models import AreaGrant, Beetles
from beetlesgallery.beetles_app.test_pages import PageTestCase
from beetlesgallery.beetles_app.testing import make_beetle, make_image, make_taxon


class BoxesOnlyTests(PageTestCase):
    def setUp(self):
        super().setUp()
        self.boxer = get_user_model().objects.create_user("boxer", password="pw")
        AreaGrant.objects.create(user=self.boxer, area=areas.BOXES)
        self.client.force_login(self.boxer)
        self.image = make_image()
        self.roi = make_beetle(image=self.image, bbox="unvalidated", collection_country="Kenya")

    def patch(self, roi, data):
        return self.client.patch(f"/api/v1/beetles/{roi.id}/", json.dumps(data), content_type="application/json")

    def test_the_annotation_page_opens_read_only_for_names(self):
        page = self.client.get(reverse("tool_annotate"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "const CAN_EDIT_RECORDS = false;")

    def test_boxes_can_be_moved_and_drawn(self):
        self.assertEqual(self.patch(self.roi, {"bbox_x": 0.3, "bbox_y": 0.3, "bbox_width": 0.1, "bbox_height": 0.1}).status_code, 200)
        self.roi.refresh_from_db()
        self.assertAlmostEqual(self.roi.bbox_x, 0.3)
        res = self.client.post("/api/v1/beetles/", json.dumps({"image_asset_id": str(self.image.id), "bbox_x": 0.6,
                                                                 "bbox_y": 0.6, "bbox_width": 0.2, "bbox_height": 0.2}),
                               content_type="application/json")
        self.assertEqual(res.status_code, 201, res.content)

    def test_names_details_and_validation_are_refused(self):
        taxon = make_taxon(valid_species_id="9", genus="Ips", species="typographus")
        for data in ({"depicts_valid_name_id": "9"}, {"collection_country": "Peru"}, {"bbox_is_validated": True},
                     {"bbox_x": 0.2, "specimen_notes": "x"}):
            with self.subTest(data=data):
                self.assertEqual(self.patch(self.roi, data).status_code, 403)
        self.roi.refresh_from_db()
        self.assertEqual((self.roi.collection_country, self.roi.taxon, self.roi.bbox_is_validated), ("Kenya", None, False))
        self.assertEqual(self.client.post(f"/api/v1/beetles/{self.roi.id}/validate/").status_code, 403)
        bulk = self.client.patch("/api/v1/beetles/bulk-update/", json.dumps([{"id": str(self.roi.id), "collection_country": "Peru"}]),
                                 content_type="application/json")
        self.assertEqual(bulk.status_code, 403)
        self.assertIsNotNone(taxon)

    def test_a_plain_box_can_be_removed_but_not_a_named_or_validated_roi(self):
        extra = make_beetle(image=self.image, bbox="unvalidated")
        self.assertEqual(self.client.delete(f"/api/v1/beetles/{extra.id}/").status_code, 204)
        named = make_beetle(image=self.image, taxon=make_taxon(valid_species_id="7"), bbox="unvalidated")
        self.assertEqual(self.client.delete(f"/api/v1/beetles/{named.id}/").status_code, 403)
        self.assertTrue(Beetles.objects.filter(id=named.id, is_deleted=False).exists())
        # removing the last box keeps the record and clears the box, which is still a box edit
        res = self.patch(self.roi, {"bbox_x": None, "bbox_y": None, "bbox_width": None, "bbox_height": None, "bbox_is_validated": False})
        self.assertEqual(res.status_code, 200, res.content)

    def test_image_details_and_classifying_are_refused(self):
        res = self.client.patch(f"/api/v1/image-assets/{self.image.id}/", json.dumps({"photographer": "X"}), content_type="application/json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/image-assets/{self.image.id}/classify/").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/v1/image-assets/{self.image.id}/").status_code, 403)

    def test_editing_records_can_do_everything(self):
        AreaGrant.objects.create(user=self.boxer, area=areas.ANNOTATE)
        self.client.force_login(self.boxer)
        self.assertEqual(self.patch(self.roi, {"collection_country": "Peru"}).status_code, 200)
        self.assertContains(self.client.get(reverse("tool_annotate")), "const CAN_EDIT_RECORDS = true;")
