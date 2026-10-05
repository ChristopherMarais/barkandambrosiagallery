"""
A specimen without an image (#350): its details page opens for a curator, and the "add another specimen to this
image" control is left out, since there is no image to add one to.
"""
from django.urls import reverse

from beetlesgallery.beetles_app.models import Beetles
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle


class DetailWithoutImageTests(PageBehaviourCase):
    def page(self, beetle):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("beetle_detail", args=[beetle.id]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_a_curator_can_open_a_specimen_without_an_image(self):
        page = self.page(Beetles.objects.create())
        self.assertNotIn('id="add-btn"', page)
        self.assertIn('id="edit-btn"', page)   # its own details can still be edited

    def test_with_an_image_the_add_control_is_there(self):
        self.assertIn('id="add-btn"', self.page(make_beetle()))
