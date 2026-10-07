"""
The specimen page draws every beetle's box on the photo (#536): the haze covers everything outside all of them, this
page's beetle has the inverted box (a dark line with a white outline), and every other box is a link to that beetle's
page. A click outside every box, or the switch in the photo's corner, hides the boxes and the haze and shows them again.
"""
from django.urls import reverse

from beetlesgallery.beetles_app.models import Beetles
from beetlesgallery.beetles_app.test_details_photo_controls import Outline
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image


class DetailsPhotoBoxesTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.image = make_image(image_file="tests/photo.jpg")
        self.big = self.box(0.05, 0.05, 0.6, 0.6)
        self.small = self.box(0.1, 0.1, 0.2, 0.2)   # inside the big one
        self.corner = self.box(0.7, 0.7, 0.25, 0.25)
        self.unboxed = make_beetle(image=self.image)
        # numbered in id order among the boxed ROIs, as in the page's "ROI n of 3" (the unboxed one is not counted)
        rois = sorted([self.big, self.small, self.corner], key=lambda roi: str(roi.id))
        self.number = {roi.id: i for i, roi in enumerate(rois, start=1)}
        self.gone = self.box(0.4, 0.1, 0.1, 0.1, is_deleted=True)

    def box(self, x, y, width, height, **fields):
        return make_beetle(image=self.image, bbox_x=x, bbox_y=y, bbox_width=width, bbox_height=height, **fields)

    def page(self, roi, user=None):
        self.client.force_login(user or self.user)
        response = self.client.get(reverse("beetle_detail", args=[roi.id]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def boxes(self, page):
        """The boxes drawn on the photo, by their ROI number, in the order they are drawn."""
        return {element["data-testid"]: element for element in Outline(page).elements.values()
                if element.get("data-testid", "").startswith("roi-box-")}

    def test_every_live_boxed_beetle_has_its_box_and_the_others_link_to_their_pages(self):
        page = self.page(self.small)
        boxes = self.boxes(page)
        expected = {f"roi-box-{self.number[roi.id]}" for roi in (self.big, self.small, self.corner)}
        self.assertEqual(set(boxes), expected)   # not the box-less one, not the deleted one
        self.assertNotIn(str(self.gone.id), page)
        for roi in (self.big, self.corner):
            link = boxes[f"roi-box-{self.number[roi.id]}"]
            self.assertEqual(link["href"], reverse("beetle_detail", args=[roi.id]))
            label = f"Show ROI {self.number[roi.id]} of 3"
            self.assertEqual((link["aria-label"], link["title"]), (label, label))
            self.assertEqual(link["class"].split(), ["roi-box"])
            self.assertEqual(link["inside"][:2], ["roi-boxes", "roi-frame"])
            self.assertIn(f"left: calc({roi.bbox_x:f} * 100%)", link["style"])
        self.assertEqual(self.client.get(boxes[f"roi-box-{self.number[self.big.id]}"]["href"]).status_code, 200)

    def test_this_beetles_box_is_inverted_and_not_a_link(self):
        page = self.page(self.small)
        current = Outline(page).elements["roi-bbox"]
        self.assertEqual(current["data-testid"], f"roi-box-{self.number[self.small.id]}")
        self.assertEqual(current["class"].split(), ["roi-box", "roi-box-current"])
        self.assertNotIn("href", current)
        self.assertEqual(page.count('id="roi-bbox"'), 1)
        # detail-boxes (numbered tab + thin/thick contrast) was skipped by the owner, #618.
        for rule in (".roi-box { position: absolute; border: 2px solid rgb(255 255 255 / 0.9); border-radius: 0.375rem;",
                     "box-shadow: 0 0 0 1px rgb(0 0 0 / 0.4), inset 0 0 0 1px rgb(0 0 0 / 0.4); }",
                     ".roi-box-current { cursor: default; border-color: rgb(17 24 39 / 0.9);",
                     "box-shadow: 0 0 0 1px rgb(255 255 255 / 0.9), inset 0 0 0 1px rgb(255 255 255 / 0.9); }",
                     "a.roi-box:focus-visible {"):
            self.assertIn(rule, page)

    def test_bigger_boxes_are_drawn_first_so_a_box_inside_another_can_be_clicked(self):
        order = list(self.boxes(self.page(self.corner)))
        self.assertEqual(order, [f"roi-box-{self.number[roi.id]}" for roi in (self.big, self.corner, self.small)])

    def test_the_haze_covers_everything_outside_all_the_boxes(self):
        page = self.page(self.small)
        haze = page[page.index('<svg id="roi-haze"'):page.index("</svg>", page.index('<svg id="roi-haze"'))]
        self.assertIn('viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true"', haze)
        self.assertIn('fill="white" fill-opacity="0.25" mask="url(#roi-haze-holes)"', haze)
        self.assertEqual(haze.count('fill="black"'), 3)   # a hole for each box
        self.assertIn(f'<rect x="{self.corner.bbox_x:f}" y="{self.corner.bbox_y:f}" '
                      f'width="{self.corner.bbox_width:f}" height="{self.corner.bbox_height:f}" fill="black"/>', haze)
        self.assertNotIn("9999px", page)   # the old one-box haze would cover the other boxes

    def test_a_switch_and_a_click_outside_the_boxes_hide_and_show_them(self):
        page = self.page(self.small)
        elements = Outline(page).elements
        switch = elements["roi-boxes-btn"]
        self.assertEqual((switch["type"], switch["aria-pressed"]), ("button", "true"))
        self.assertEqual((switch["aria-label"], switch["title"]), ("Hide the boxes",) * 2)
        self.assertLessEqual({"flex", "h-11", "w-11", "rounded-lg"}, set(switch["class"].split()))
        self.assertEqual(switch["inside"][0], "roi-toolbar")   # in the toolbar under the photo, not on it (#618)
        self.assertEqual(elements["roi-photo"]["data-box"], "shown")
        for code in ('#roi-photo[data-box="hidden"] #roi-boxes { display: none; }',
                     "const label = show ? 'Hide the boxes' : 'Show the boxes';",
                     "toggle.setAttribute('aria-pressed', String(show));",
                     "if (e.target.closest('.roi-box')) return;",
                     "toggle.addEventListener('click'"):
            self.assertIn(code, page)

    def test_a_beetle_without_a_box_still_sees_the_others_but_has_no_flag(self):
        elements = Outline(self.page(self.unboxed)).elements
        self.assertNotIn("roi-bbox", elements)
        self.assertNotIn("report-roi-btn", elements)   # only boxes are flagged here
        self.assertIn("roi-boxes-btn", elements)
        self.assertEqual(len(self.boxes(self.page(self.unboxed))), 3)

    def test_the_flag_says_flag_this(self):
        page = self.page(self.small)
        elements = Outline(page).elements
        self.assertEqual((elements["report-roi-btn"]["aria-label"], elements["report-roi-btn"]["title"]),
                         ("Flag this", "Flag this"))
        self.assertEqual(elements["report-roi-menu"]["aria-label"], "Flag this")
        self.assertNotIn("Flag this photo", page)

    def test_a_photo_with_one_beetle_shows_just_its_box(self):
        roi = make_beetle(image=make_image(image_file="tests/photo.jpg"), bbox="unvalidated")
        boxes = self.boxes(self.page(roi))
        self.assertEqual(list(boxes), ["roi-box-1"])
        self.assertEqual(boxes["roi-box-1"]["id"], "roi-bbox")
        self.assertEqual(Beetles.objects.filter(image_asset=roi.image_asset).count(), 1)
