"""
The specimen page's photo and pen (#505): a click on the photo shows the whole photo and the next one the beetle's box
again, a round flag in the photo's corner sends the beetle to the curators (only while its box shows), and the pen
opens the annotation page, where names, boxes, details and validation are changed. The page's own edit, add, toggle and
validate controls are gone, with the endpoints only they used.
"""
import json
import re
import uuid
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.urls import NoReverseMatch, reverse

from beetlesgallery.beetles_app.areas import ANNOTATE, BOXES, DETAILS
from beetlesgallery.beetles_app.models import AreaGrant, Beetles, GameReport, UpdateBatch
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image

PEN = "M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"
CORNERS = "M4 8V6a2 2 0 012-2h2M4 16v2a2 2 0 002 2h2m8-16h2a2 2 0 012 2v2m-4 12h2a2 2 0 002-2v-2"   # the old link's icon
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Outline(HTMLParser):
    """The elements with an id or a data-testid: their attributes, and in "inside" the ids of the elements around them
    (innermost first), so a test can tell what sits in what."""

    def __init__(self, html):
        super().__init__()
        self.open, self.elements = [], {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        key = attrs.get("id") or attrs.get("data-testid")
        if key:
            self.elements[key] = {**attrs, "inside": [a["id"] for _, a in reversed(self.open) if a.get("id")]}
        if tag not in VOID:
            self.open.append((tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.open) - 1, -1, -1):   # up to its start tag (HTML lets some end tags be left out)
            if self.open[i][0] == tag:
                del self.open[i:]
                return


class DetailsPhotoControlsTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.roi = make_beetle(image=make_image(image_file="tests/photo.jpg"), bbox="unvalidated",
                               depicts_specimen="SP-7", collection_country="Peru", specimen_notes="Under bark")

    def account(self, *areas):
        user = get_user_model().objects.create_user(f"user-{uuid.uuid4().hex[:8]}", password="pw")
        AreaGrant.objects.bulk_create([AreaGrant(user=user, area=area) for area in areas])
        return user

    def page(self, user, roi=None):
        self.client.force_login(user)
        response = self.client.get(reverse("beetle_detail", args=[(roi or self.roi).id]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    # --- what is gone, and what stays -----------------------------------------------------------------------------

    def test_the_edit_add_toggle_and_validate_controls_are_gone_and_the_details_stay(self):
        for user in (self.staff, self.superuser):
            page = self.page(user)
            for gone in ('id="edit-btn"', 'id="add-btn"', 'id="edit-controls"', 'id="toggle-bbox-btn"',
                         "metadata-form", "validation-form", "toggleEditMode", "toggleAddMode", "Edit Current Metadata",
                         "Add Another Specimen", "Toggle Region of Interest", "Open in Annotation Tool", CORNERS,
                         ">Validate</button>", ">Unvalidate</button>"):
                self.assertNotIn(gone, page)
            content = page[page.index("<main"):]
            for field in ("<form", "<input", "<textarea", "<select"):
                self.assertNotIn(field, content)
            self.assertNotIn("{#", content)   # a template comment over two lines shows on the page as text
            for shown in ("Specimen ID", "SP-7", "Country", "Peru", "Specimen notes", "Under bark",
                          "Multiple individuals", "This ROI", "Not validated", "Download"):
                self.assertIn(shown, content)

    def test_the_endpoints_only_those_controls_used_are_gone(self):
        for name in ("update_single_beetle", "create_specimen_for_image", "toggle_beetle_validation",
                     "toggle_image_validation"):
            with self.assertRaises(NoReverseMatch):
                reverse(name, args=[uuid.uuid4()])
        image = self.roi.image_asset_id
        self.client.force_login(self.superuser)
        for path in (f"/update_single/{self.roi.id}/", f"/beetles/add_specimen/{image}/",
                     f"/beetles/{self.roi.id}/toggle-validation/", f"/images/{image}/toggle-validation/"):
            response = self.client.post(path, {"state": "validate", "collection_country": "Chile"})
            self.assertEqual(response.status_code, 404, path)
        self.roi.refresh_from_db()
        self.assertEqual((self.roi.collection_country, self.roi.bbox_is_validated), ("Peru", False))
        self.assertEqual(Beetles.objects.filter(image_asset_id=image).count(), 1)
        self.assertFalse(UpdateBatch.objects.exists())

    # --- the pen ----------------------------------------------------------------------------------------------------

    def test_the_pen_opens_the_annotation_page_at_this_beetle(self):
        url = f'{reverse("tool_annotate")}?image={self.roi.image_asset_id}&roi={self.roi.id}'
        for user in (self.account(DETAILS, BOXES), self.account(DETAILS, ANNOTATE), self.staff, self.superuser):
            page = self.page(user)
            pen = Outline(page).elements["open-annotation"]
            self.assertEqual(pen["href"], url)
            self.assertEqual((pen["title"], pen["aria-label"]), ("Open in the annotation page",) * 2)
            start = page.index('data-testid="open-annotation"')
            self.assertIn(PEN, page[start:page.index("</a>", start)])   # the pen the edit button had
            self.assertEqual(self.client.get(url).status_code, 200)     # and the page it opens is theirs to use

    def test_no_pen_for_those_who_cannot_open_the_annotation_page(self):
        for user in (self.account(DETAILS), self.user):
            page = self.page(user)
            self.assertNotIn("open-annotation", Outline(page).elements)
            self.assertNotIn(reverse("tool_annotate"), page)
            self.assertNotIn(PEN, page)
            self.assertEqual(self.client.get(reverse("tool_annotate")).status_code, 403)

    # --- a click on the photo -----------------------------------------------------------------------------------------

    def test_a_click_on_the_photo_hides_the_boxes_and_back(self):
        # Since #536 the photo holds links (the other beetles' boxes), so it is no longer a button itself: the switch in
        # its corner is the keyboard's way (test_details_photo_boxes.py).
        page = self.page(self.user)
        frame = Outline(page).elements["roi-frame"]
        for attribute in ("role", "tabindex", "aria-label"):
            self.assertNotIn(attribute, frame)
        self.assertIn("cursor-pointer", frame["class"].split())
        for code in ("frame.addEventListener('click'", "photo.dataset.box = show ? 'shown' : 'hidden';"):
            self.assertIn(code, page)

    def test_without_a_box_the_photo_is_just_a_photo_and_has_no_flag(self):
        roi = make_beetle(image=make_image(image_file="tests/photo.jpg"))
        elements = Outline(self.page(self.user, roi)).elements
        for attribute in ("role", "tabindex", "aria-label"):
            self.assertNotIn(attribute, elements["roi-frame"])
        for absent in ("roi-bbox", "report-roi-btn", "report-roi-menu", "report-roi-status"):
            self.assertNotIn(absent, elements)   # only boxes are flagged here
        self.assertNotIn("data-box", elements["roi-photo"])

    # --- the flag -----------------------------------------------------------------------------------------------------

    def test_the_flag_is_an_icon_on_the_box_and_its_menu_is_not_clipped(self):
        page = self.page(self.user)
        elements = Outline(page).elements
        flag, menu = elements["report-roi-btn"], elements["report-roi-menu"]
        self.assertEqual((flag["aria-label"], flag["title"]), ("Flag this",) * 2)
        self.assertIn("roi-flag-btn", flag["class"].split())   # a round button (round 7)
        start = page.index('id="report-roi-btn"')
        inside = page[page.index(">", start) + 1:page.index("</button>", start)]
        self.assertIn('class="fi fi-rr-flag"', inside)
        self.assertEqual(re.sub(r"<[^>]+>", "", inside).strip(), "")   # the icon only, no word
        # On the photo at the corner of this beetle's box (the owner reversed detail-toolbar for the flag, round 7),
        # outside the frame: the frame clips (for the box's shading) where the menu must not be.
        self.assertNotIn("roi-toolbar", flag["inside"])
        self.assertIn("roi-photo", flag["inside"])
        self.assertIn("roi-photo", menu["inside"])
        self.assertNotIn("roi-frame", flag["inside"])
        self.assertNotIn("roi-frame", menu["inside"])
        self.assertIn("overflow-hidden", elements["roi-frame"]["class"].split())
        self.assertNotIn("overflow-hidden", elements["roi-photo"]["class"].split())
        self.assertNotIn("roi-photo", elements["report-roi-status"]["inside"])   # thanks under the photo, always seen
        for word in ("Report this beetle", "<span>Report</span>", "Already reported"):
            self.assertNotIn(word, page)

    def test_the_flag_never_shows_with_the_whole_photo(self):
        page = self.page(self.user)
        self.assertIn('.roi-figure:has(#roi-photo[data-box="hidden"]) #report-roi-wrap { display: none; }', page)
        self.assertEqual(Outline(page).elements["roi-photo"]["data-box"], "shown")
        self.assertIn("photo.dataset.box = show ? 'shown' : 'hidden';", page)
        self.assertIn("if (!menuOpen()) toggleBoxes();", page)   # with the menu open, a click on the photo only closes it

    def test_anyone_who_can_open_the_page_can_flag_the_beetle(self):
        member = self.account(DETAILS)
        page = self.page(member)
        url = Outline(page).elements["report-roi-menu"]["data-url"]
        self.assertEqual(url, reverse("report_roi", args=[self.roi.id]))
        for reason in ("wrong_label", "bad_box", "bad_image", "other"):
            self.assertIn(f'data-reason="{reason}"', page)
        self.assertIn('<meta name="csrf-token" content="', page)   # what the script sends, now the page has no form
        self.assertIn("data.already ? 'Already flagged. Thanks!' : 'Thanks! Sent to the curators to check.'", page)

        def flag():
            return self.client.post(url, json.dumps({"reason": "bad_box"}), content_type="application/json")

        self.assertEqual(flag().json(), {"status": "open", "already": False})
        self.assertEqual(flag().json(), {"status": "open", "already": True})
        report = GameReport.objects.get()
        self.assertEqual((report.roi_id, report.reporter, report.reason), (self.roi.id, member, "bad_box"))
        # a Basic account can't open specimen pages; it flags beetles in the game
        self.client.force_login(self.account())
        self.assertEqual(self.client.get(reverse("beetle_detail", args=[self.roi.id])).status_code, 403)
