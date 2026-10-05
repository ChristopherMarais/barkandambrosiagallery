"""Bulk validation: a list of unvalidated images for people granted "Bulk validate" to check and validate together."""
from django.contrib.auth import get_user_model
from django.urls import reverse

from beetlesgallery.beetles_app import areas
from beetlesgallery.beetles_app.models import AreaGrant, Beetles, ImageAsset
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon


class BulkValidateTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.checker = get_user_model().objects.create_user("checker", password="pw")
        AreaGrant.objects.create(user=self.checker, area=areas.BULK_VALIDATE)
        self.client.force_login(self.checker)
        self.taxon = make_taxon(valid_species_id="1733", genus="Xyleborus", species="affinis",
                                scientific_name="Xyleborus affinis")
        self.open_image = make_image()
        self.roi = make_beetle(image=self.open_image, bbox="unvalidated", taxon=self.taxon)
        self.unnamed_image = make_image()
        make_beetle(image=self.unnamed_image, bbox="unvalidated")
        self.boxless = make_image()
        make_beetle(image=self.boxless)
        self.done = make_image(is_validated=True)
        make_beetle(image=self.done, bbox="validated")

    def listed(self, **params):
        page = self.client.get(reverse("bulk_validate"), params)
        return {str(i.pk) for i in page.context["page"].object_list}

    def test_only_people_with_the_permission_get_in(self):
        self.client.force_login(self.staff)   # the curator preset does not include bulk validation
        self.assertEqual(self.client.get(reverse("bulk_validate")).status_code, 403)
        self.assertNotIn('data-testid="open-bulk-validate"', self.client.get(reverse("data_management")).content.decode())
        self.client.force_login(self.checker)
        self.assertIn('data-testid="open-bulk-validate"', self.client.get(reverse("data_management")).content.decode())

    def test_it_lists_unvalidated_images_with_boxes(self):
        self.assertEqual(self.listed(), {str(self.open_image.pk), str(self.unnamed_image.pk)})
        page = self.client.get(reverse("bulk_validate")).content.decode()
        self.assertIn("Xyleborus affinis", page)
        self.assertIn(f"?image={self.open_image.pk}", page)

    def test_filters(self):
        self.assertEqual(self.listed(named="1"), {str(self.open_image.pk)})
        self.assertEqual(self.listed(q="affinis"), {str(self.open_image.pk)})
        Beetles.objects.filter(pk=self.roi.pk).update(label_source="vial_label")
        self.assertEqual(self.listed(source="vial_label"), {str(self.open_image.pk)})

    def test_validating_the_ticked_images(self):
        response = self.client.post(reverse("bulk_validate"), {"image": [self.open_image.pk, self.done.pk, self.boxless.pk]})
        self.assertRedirects(response, reverse("bulk_validate"), fetch_redirect_response=False)
        self.assertTrue(ImageAsset.objects.get(pk=self.open_image.pk).is_validated)
        roi = Beetles.objects.get(pk=self.roi.pk)
        self.assertEqual((roi.bbox_is_validated, roi.bbox_validated_by), (True, self.checker))
        self.assertFalse(ImageAsset.objects.get(pk=self.unnamed_image.pk).is_validated)   # not ticked
        self.assertFalse(ImageAsset.objects.get(pk=self.boxless.pk).is_validated)          # no box: cannot be

    def test_one_at_a_time_validators_cannot_post(self):
        one = get_user_model().objects.create_user("one", password="pw")
        AreaGrant.objects.create(user=one, area=areas.VALIDATE)
        self.client.force_login(one)
        self.assertEqual(self.client.post(reverse("bulk_validate"), {"image": [self.open_image.pk]}).status_code, 403)
        self.assertFalse(ImageAsset.objects.get(pk=self.open_image.pk).is_validated)
