"""
The specimen page counts only the ROIs that have a box ("ROI n of m"), and its arrows walk only those. A beetle
without a box is shown on its own with no count and no arrows; an image with no boxes at all shows no count.
"""
import re

from django.urls import reverse

from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image

COUNT = re.compile(r"ROI (\S+) of (\S+?)\s*<")


def plain(html):
    """The page without the digit-group spans (numbers are grouped in threes, so "ROI 1 of 2" can be marked up)."""
    return re.sub(r"</?span\b[^>]*>", "", html)


class BoxedCountTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.image = make_image(image_file="tests/photo.jpg")
        self.first = make_beetle(image=self.image, bbox_x=0.1, bbox_y=0.1, bbox_width=0.2, bbox_height=0.2)
        self.second = make_beetle(image=self.image, bbox_x=0.5, bbox_y=0.5, bbox_width=0.2, bbox_height=0.2)
        self.unboxed = make_beetle(image=self.image)
        self.client.force_login(self.user)

    def page(self, beetle):
        response = self.client.get(reverse("beetle_detail", args=[beetle.id]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_count_and_arrows_only_walk_the_boxed_rois(self):
        # the boxed ROIs in id order, as the page orders them; the first of them is "ROI 1"
        boxed = sorted([self.first, self.second], key=lambda roi: str(roi.id))
        html = self.page(boxed[0])
        self.assertEqual(COUNT.findall(plain(html)), [("1", "2")])               # "ROI 1 of 2", not "of 3"
        next_link = re.search(r'href="([^"]+)"[^>]*data-testid="roi-pager-next"', html)
        self.assertEqual(next_link.group(1), reverse("beetle_detail", args=[boxed[1].id]))
        self.assertNotIn(reverse("beetle_detail", args=[self.unboxed.id]), html)   # no arrow to the unboxed ROI

    def test_a_beetle_without_a_box_has_no_count_and_no_arrows(self):
        html = self.page(self.unboxed)
        self.assertEqual(COUNT.findall(plain(html)), [])
        self.assertNotIn('title="Next ROI"', html)
        self.assertNotIn('title="Previous ROI"', html)
        self.assertIn("Technical", html)                                  # its information is still shown

    def test_an_image_with_no_boxes_shows_its_information_without_a_count(self):
        bare = make_image(image_file="tests/bare.jpg")
        roi = make_beetle(image=bare)
        make_beetle(image=bare)
        html = self.page(roi)
        self.assertEqual(COUNT.findall(plain(html)), [])
        self.assertIn("Technical", html)
        self.assertNotIn("Show ROI", html)                                # no boxes drawn either
