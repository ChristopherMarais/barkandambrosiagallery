"""
The owner's review of the specimen page, round 7: the full-size photo opens over the page (A1) with zoom, lighting
and the flag (A2); the flag is back on the corner of this beetle's box, out of the toolbar (A3); Edit only for those
who may open the annotation page (A4); the other images of the specimen under the photo on desktop, last on a phone
(A5); a hover lens on a computer (A6); the authority out of the title (E2); round buttons centred (E6).
"""
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse

from beetlesgallery.beetles_app.areas import ANNOTATE, BOXES, DETAILS
from beetlesgallery.beetles_app.models import AreaGrant
from beetlesgallery.beetles_app.test_details_photo_controls import Outline
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon

STATIC_JS = Path(settings.BASE_DIR) / "beetlesgallery" / "static" / "js"


class R7DetailTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.taxon = make_taxon(scientific_name="Ips typographus", authority="Linnaeus")
        self.roi = make_beetle(image=make_image(image_file="tests/photo.jpg"), bbox="unvalidated", taxon=self.taxon,
                               depicts_specimen="SP-R7")

    def page(self, roi=None, user=None):
        self.client.force_login(user or self.user)
        response = self.client.get(reverse("beetle_detail", args=[(roi or self.roi).id]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def account(self, *areas):
        user = get_user_model().objects.create_user(f"user-{uuid.uuid4().hex[:8]}", password="pw")
        AreaGrant.objects.bulk_create([AreaGrant(user=user, area=area) for area in areas])
        return user

    # --- A1: the whole photo over the page ---------------------------------------------------------------------------

    def test_full_size_opens_an_overlay_on_this_page(self):
        page = self.page()
        elements = Outline(page).elements
        full = elements["roi-fullsize"]
        self.assertNotIn("target", full)   # no new tab
        self.assertEqual((full["aria-controls"], full["aria-haspopup"]), ("photo-viewer", "dialog"))
        viewer = elements["photo-viewer"]
        self.assertEqual((viewer["role"], viewer["aria-modal"]), ("dialog", "true"))
        self.assertIn("hidden", viewer["class"].split())
        self.assertEqual(viewer["data-src"], self.roi.display_url)
        self.assertIn("pv-close", elements)
        self.assertIn("js/photo_viewer.js", page)
        self.assertIn("#photo-viewer { position: fixed; inset: 0;", page)

    def test_the_overlay_shows_every_box(self):
        make_beetle(image=self.roi.image_asset, bbox="unvalidated")
        page = self.page()
        viewer = page[page.index('id="pv-boxes"'):page.index('id="pv-bar"')]
        self.assertEqual(viewer.count('class="roi-box'), 2)
        self.assertEqual(viewer.count("roi-box-current"), 1)

    def test_the_script_closes_on_esc_the_cross_and_outside(self):
        script = (STATIC_JS / "photo_viewer.js").read_text(encoding="utf-8")
        for code in ('e.key === "Escape"', '$("pv-close").addEventListener("click", close);',
                     "if (e.target === viewer) close();", "e.preventDefault();\n      open();"):
            self.assertIn(code, script)

    def test_no_overlay_without_a_photo_file(self):
        bare = make_beetle(image=make_image())
        self.assertNotIn("photo-viewer", Outline(self.page(bare)).elements)

    # --- A2: zoom, lighting and the flag in the overlay -------------------------------------------------------------

    def test_the_overlay_has_zoom_lighting_and_a_flag(self):
        elements = Outline(self.page()).elements
        for control in ("pv-zoom-in", "pv-zoom-out", "pv-zoom-level", "pv-light-btn", "pv-light-reset", "pv-flag-btn"):
            self.assertIn("photo-viewer", elements[control]["inside"], control)
        brightness, contrast = elements["pv-brightness"], elements["pv-contrast"]
        # the game's own ranges (game_play.html)
        self.assertEqual((brightness["min"], brightness["max"], brightness["value"]), ("50", "250", "100"))
        self.assertEqual((contrast["min"], contrast["max"], contrast["value"]), ("50", "200", "100"))
        self.assertEqual(elements["pv-flag-btn"]["aria-label"], "Flag this")
        script = (STATIC_JS / "photo_viewer.js").read_text(encoding="utf-8")
        for code in ('frame.addEventListener("wheel"', "pinch", "startDrag", "brightness(${b}%) contrast(${c}%)",
                     "zoomBy: (factor)"):
            self.assertIn(code, script)

    def test_the_overlay_flag_posts_like_the_page_flag(self):
        page = self.page()
        viewer = page[page.index('id="photo-viewer"'):]
        for reason in ("wrong_label", "bad_box", "bad_image", "other"):
            self.assertIn(f'data-reason="{reason}"', viewer)
        self.assertIn("document.querySelectorAll('.report-roi-reason')", page)
        self.assertIn("if (viewerFlag) viewerFlag.disabled = true;", page)

    def test_no_overlay_flag_without_a_box(self):
        roi = make_beetle(image=make_image(image_file="tests/photo.jpg"))
        elements = Outline(self.page(roi)).elements
        self.assertIn("photo-viewer", elements)
        self.assertNotIn("pv-flag-btn", elements)

    # --- A3: the flag on the box's corner ---------------------------------------------------------------------------

    def test_the_flag_hovers_on_the_corner_of_this_box(self):
        page = self.page()
        elements = Outline(page).elements
        wrap = elements["report-roi-wrap"]
        self.assertIn("roi-photo", wrap["inside"])
        self.assertNotIn("roi-toolbar", elements["report-roi-btn"]["inside"])
        toolbar = page[page.index('id="roi-toolbar"'):page.index('id="report-roi-status"')]
        self.assertNotIn("fi-rr-flag", toolbar)
        right = f"({self.roi.bbox_x:f} + {self.roi.bbox_width:f}) * 100% - 1.25rem"
        self.assertIn(right, wrap["style"])
        self.assertIn(f"{self.roi.bbox_y:f} * 100% - 1.25rem", wrap["style"])
        self.assertIn("#roi-photo:hover #report-roi-btn", page)
        self.assertIn('#roi-photo[data-box="hidden"] #report-roi-wrap { display: none; }', page)

    # --- A4: Edit for annotation access only ------------------------------------------------------------------------

    def test_edit_follows_the_annotation_page_access(self):
        for user in (self.account(DETAILS, BOXES), self.account(DETAILS, ANNOTATE), self.superuser):
            self.assertIn("open-annotation", Outline(self.page(user=user)).elements)
            self.assertTrue(self.client.get(reverse("beetle_detail", args=[self.roi.id])).context["can_annotate"])
            self.assertEqual(self.client.get(reverse("tool_annotate")).status_code, 200)
        for user in (self.account(DETAILS), self.user):
            self.assertNotIn("open-annotation", Outline(self.page(user=user)).elements)
            self.assertFalse(self.client.get(reverse("beetle_detail", args=[self.roi.id])).context["can_annotate"])
            self.assertEqual(self.client.get(reverse("tool_annotate")).status_code, 403)

    # --- A5: the other images ---------------------------------------------------------------------------------------

    def test_other_images_are_one_copy_placed_by_the_grid(self):
        make_beetle(image=make_image(image_file="tests/photo2.jpg"), depicts_specimen="SP-R7")
        page = self.page()
        elements = Outline(page).elements
        self.assertEqual(page.count('data-testid="related-specimens"'), 1)
        self.assertEqual(elements["related-specimens"]["inside"][0], "detail-grid")
        self.assertLess(page.index("</aside>"), page.index('data-testid="related-specimens"'))   # last on a phone
        for rule in ("@media (min-width: 1024px)", "#detail-grid { grid-template-rows: auto 1fr; }",
                     "#detail-grid > #detail-aside { grid-column: 9 / span 4; grid-row: 1 / span 2; }",
                     "#detail-grid > #related-specimens { grid-column: 1 / span 8; grid-row: 2;"):
            self.assertIn(rule, page)

    # --- A6: hover zoom ---------------------------------------------------------------------------------------------

    def test_a_lens_magnifies_the_photo_on_a_computer(self):
        page = self.page()
        elements = Outline(page).elements
        self.assertIn("roi-photo", elements["roi-lens"]["inside"])
        self.assertEqual(elements["roi-photo"]["data-zoom-src"], self.roi.display_url)
        self.assertIn("@media (hover: hover) and (pointer: fine) { #roi-lens.on { display: block; } }", page)
        script = (STATIC_JS / "photo_viewer.js").read_text(encoding="utf-8")
        self.assertIn('matchMedia("(hover: hover) and (pointer: fine)")', script)
        self.assertIn("lens.style.backgroundPosition", script)

    # --- E2 / E6 ----------------------------------------------------------------------------------------------------

    def test_the_title_has_no_authority(self):
        page = self.page()
        h1 = page[page.index('<h1 class="page-title">'):page.index("</h1>")]
        self.assertNotIn("Linnaeus", h1)
        self.assertIn("Linnaeus", page[page.index(">Taxonomy<"):])

    def test_round_buttons_centre_their_icon(self):
        page = self.page()
        for rule in (".roi-flag-btn { display: flex; align-items: center; justify-content: center;",
                     ".pv-btn { display: flex; align-items: center; justify-content: center;",
                     ".pv-pill { display: inline-flex; align-items: center; justify-content: center;",
                     ".roi-flag-btn i::before, .pv-btn i::before, .pv-pill i::before { display: block; line-height: 1; }"):
            self.assertIn(rule, page)
