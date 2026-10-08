"""
The owner's round 8 on the IBBI-AI page: the title with its subtitle under it, one Options disclosure (model, "don't
keep my image", examples), Classify between it and the photo, a crop tool, a hover zoom, only the confidence bar
between the photo and the plot, less text, centred pills; and site-wide, every loading spinner moves (E13).
"""
import io
import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.urls import reverse
from PIL import Image

from beetlesgallery.beetles_app.models import Beetles, ImageAsset
from beetlesgallery.beetles_app.test_classify_assist import DETECTION, ClassifyCase, fake_response
from beetlesgallery.beetles_app.testing import PageBehaviourCase

ROOT = Path(settings.BASE_DIR) / "beetlesgallery"
TEMPLATES = ROOT / "templates"
CSS = ROOT / "static" / "css"
TEMPLATE = (TEMPLATES / "beetles" / "tool_classify.html").read_text(encoding="utf-8")


def opening_tag(html, marker):
    start = html.rfind("<", 0, html.index(marker))
    return html[start:html.index(">", start) + 1]


def between(html, start, end):
    return html[html.index(start):html.index(end)]


class PageCase(PageBehaviourCase):
    def page(self):
        self.client.logout()   # the AI page is open to everyone
        return self.client.get(reverse("tool_classify")).content.decode()


class TitleTests(PageCase):
    def test_the_subtitle_is_under_the_title_and_neither_has_an_icon(self):
        page = self.page()
        title = page.index('<h1 class="page-title">IBBI-AI</h1>')
        subtitle = page.index("Intelligent Bark Beetle Identifier")
        self.assertLess(title, subtitle)
        heading = page[page.rfind("<section", 0, title):page.index("</section>", title)]
        self.assertNotIn("fi fi-", heading)
        self.assertNotIn("fi-rr-sparkles", heading)


class OptionsTests(PageCase):
    def options(self, page):
        return between(page, '<details id="optionsPanel"', "</details>")

    def test_one_disclosure_holds_the_model_the_checkbox_and_the_examples(self):
        page = self.page()
        tool = between(page, '<h1 class="page-title">IBBI-AI</h1>', 'id="canvasContainer"')
        self.assertEqual(tool.count("<details"), 1)   # one disclosure, not one per setting
        options = self.options(page)
        self.assertRegex(options, r"</i>\s*Options\s*</span>")
        for marker in ('id="modelSelect"', 'data-testid="ibbi-link"', 'data-testid="model-help"', 'id="dontKeep"',
                       'class="example-btn', "Try an example"):
            self.assertIn(marker, options, marker)
        self.assertNotRegex(opening_tag(page, 'id="optionsPanel"'), r"\sopen[\s>=]")   # folded away until wanted

    def test_the_model_and_the_checkbox_are_sent_with_the_form(self):
        page = self.page()
        self.assertIn('form="classifyForm"', opening_tag(page, 'id="modelSelect"'))
        box = opening_tag(page, 'id="dontKeep"')
        for needed in ('form="classifyForm"', 'name="keep_image"', 'value="0"'):
            self.assertIn(needed, box)
        self.assertIn("formData.set('keep_image', '0')", page)   # examples are never kept either

    def test_the_terms_dialog_still_sets_the_page_checkbox(self):
        page = self.page()
        self.assertIn('id="termsDontKeep"', page)
        self.assertIn("box.checked = modalBox.checked;", page)
        self.assertIn("box.dispatchEvent(new Event('change'));", page)
        self.assertIn("const box = ui.dontKeep;", page)

    def test_the_folded_line_says_which_model_and_whether_the_image_is_kept(self):
        self.assertIn('id="optionsSummary"', self.options(self.page()))
        self.assertIn("' · image not kept'", TEMPLATE)


class ClassifyButtonTests(PageCase):
    def test_classify_sits_between_the_options_and_the_photo(self):
        page = self.page()
        order = [page.index(m) for m in ('id="optionsPanel"', 'id="classifyForm"', 'id="submitBtn"', 'id="canvasContainer"')]
        self.assertEqual(order, sorted(order))
        button = opening_tag(page, 'id="submitBtn"')
        self.assertIn("btn-main", button)
        self.assertNotIn("fixed", button)
        self.assertNotIn("hidden", button)   # always there, greyed until a photo is in
        self.assertIn("disabled", button)


class CropTests(PageCase):
    def test_a_crop_button_and_its_way_back(self):
        page = self.page()
        button = opening_tag(page, 'id="cropBtn"')
        self.assertIn('aria-pressed="false"', button)
        self.assertIn(">Crop<", page)
        self.assertIn("'Use whole photo'", TEMPLATE)
        self.assertIn("Drag over the part to classify", page)

    def test_the_part_is_cut_out_in_the_browser_and_sent_in_place_of_the_photo(self):
        self.assertIn("canvas.toBlob(resolve, type, 0.92)", TEMPLATE)
        self.assertIn("['image/jpeg', 'image/png', 'image/webp'].includes(file.type) ? file.type", TEMPLATE)
        self.assertIn("let name = file.name", TEMPLATE)   # the same name
        submit = TEMPLATE[TEMPLATE.index("ui.form.addEventListener('submit'"):]
        self.assertLess(submit.index("await applyCrop();"), submit.index("formData.set('image', state.currentFile);"))
        self.assertIn("state.fullFile", TEMPLATE)   # the whole photo, to go back to

    def test_no_crop_for_a_gallery_photo(self):
        # a gallery photo is classified from the server's copy and its names go on its records: no crop there
        self.assertIn("!state.currentFile || !!state.assetId", TEMPLATE)


class CroppedUploadIsKeptAsSentTests(ClassifyCase):
    """The server needs no change: a cropped part is an ordinary upload, kept as sent, with its boxes on it."""

    def test_a_cropped_part_is_kept_at_its_own_size(self):
        self.client.logout()
        buffer = io.BytesIO()
        Image.new("RGB", (400, 300), (200, 180, 160)).save(buffer, "JPEG")
        part = SimpleUploadedFile("beetle.jpg", buffer.getvalue(), content_type="image/jpeg")
        with mock.patch("requests.post", return_value=fake_response([DETECTION])):
            response = self.client.post(reverse("tool_classify"), {"image": part})
        self.assertEqual(response.json()["saved"], "saved")
        photo = ImageAsset.objects.get(full_path_at_import__startswith="classifier/")
        self.assertEqual((photo.image_width, photo.image_height), (400, 300))
        roi = Beetles.objects.get(image_asset=photo, bbox_x__isnull=False)
        self.assertAlmostEqual(roi.bbox_x, 100 / 400)   # the box is on the part, as IBBI-AI saw it

    def test_a_ticked_checkbox_keeps_nothing(self):
        self.client.logout()
        buffer = io.BytesIO()
        Image.new("RGB", (400, 300), (200, 180, 160)).save(buffer, "JPEG")
        part = SimpleUploadedFile("beetle.jpg", buffer.getvalue(), content_type="image/jpeg")
        with mock.patch("requests.post", return_value=fake_response([DETECTION])):
            response = self.client.post(reverse("tool_classify"), {"image": part, "keep_image": "0"})
        self.assertEqual(response.json()["saved"], "opted_out")
        self.assertFalse(ImageAsset.objects.filter(full_path_at_import__startswith="classifier/").exists())


class WheelZoomTests(SimpleTestCase):
    """Owner: no round lens; the mouse wheel over the photo zooms it in place, boxes and crop included."""

    def test_the_photo_and_its_boxes_zoom_together_and_the_lens_is_gone(self):
        self.assertNotIn("zoomLens", TEMPLATE)
        self.assertNotIn("drawLens", TEMPLATE)
        layer = TEMPLATE[TEMPLATE.index('<div id="clsZoom"'):]
        layer = layer[:layer.index("</div>")]
        self.assertIn('id="previewImage"', layer)
        self.assertIn('id="detectionCanvas"', layer)
        self.assertIn('id="imageWrapper" class="relative hidden inline-block cursor-default overflow-hidden"', TEMPLATE)

    def test_the_wheel_zooms_where_the_pointer_is_and_scrolling_down_still_scrolls(self):
        self.assertIn("ui.imageWrapper.addEventListener('wheel'", TEMPLATE)
        self.assertIn("if (zoom <= 1 && e.deltaY >= 0) return;", TEMPLATE)
        self.assertIn("panX = cx - (cx - panX) * next / zoom;", TEMPLATE)
        self.assertIn('id="zoomReset" class="hidden cls-pill', TEMPLATE)
        self.assertIn("resetZoom();   // a new photo", TEMPLATE)


class CentredPageTests(SimpleTestCase):
    def test_title_tool_and_about_share_one_centred_column(self):
        self.assertIn('<div class="pt-8 max-w-4xl mx-auto">', TEMPLATE)
        self.assertIn('<div class="max-w-4xl mx-auto flex flex-col gap-6">', TEMPLATE)
        self.assertIn('<section class="max-w-3xl mx-auto mt-12 text-center', TEMPLATE)


class OnlyTheSliderBetweenPhotoAndPlotTests(PageCase):
    def test_nothing_else_between_the_photo_and_the_plot(self):
        page = self.page()
        gap = between(page, 'id="canvasContainer"', 'id="analysisDetails"')
        gap = gap[gap.index('id="fileInput"'):]
        for marker in ('id="serverNote"', 'id="dontKeep"', 'class="example-btn', 'id="classifyMessage"',
                       'id="loadingContainer"', 'id="classifyForm"'):
            self.assertNotIn(marker, gap, marker)
        self.assertIn('data-testid="confidence-bar"', gap)
        panel = page[page.index('id="analysisDetails"'):]
        chart = panel.index('id="probsChart"')
        for marker in ('id="ranksPanel"', 'id="tagsContainer"', 'id="savedNote"'):
            self.assertLess(chart, panel.index(marker), marker)


class LessTextTests(PageCase):
    def test_the_example_lines_and_the_credits_list_are_gone(self):
        page = self.page()
        self.assertNotIn("Loads a photo so you can try IBBI-AI", page)
        self.assertNotIn("Examples are never added to the gallery", page)
        self.assertNotIn("space-y-0.5", between(page, 'id="optionsPanel"', "</details>"))
        # the credit stays with each photo, as its tooltip and alt text (the photos are CC-BY)
        button = page[page.index('class="example-btn'):]
        button = button[:button.index("</button>")]
        self.assertIn("title=", button)
        self.assertIn("TH Atkinson", page[page.index('class="example-btn'):page.index("</details>")])


class CentredPillTests(SimpleTestCase):
    def test_round_buttons_and_pills_centre_their_text_and_icon(self):
        style = TEMPLATE[TEMPLATE.index("<style>"):TEMPLATE.index("</style>")]
        for rule in (".cls-pill {", ".cls-round {", ".cls-icon {"):
            body = style[style.index(rule):]
            body = body[:body.index("}")]
            for needed in ("display: flex", "align-items: center", "justify-content: center"):
                self.assertIn(needed, body, rule)
        self.assertIn(".cls-icon::before { display: block; line-height: 1; }", style)
        for marker, look in (('id="cropBtn"', "cls-pill"), ('id="clearBtn"', "cls-round"), ('id="cameraBtn"', "cls-pill"),
                             ('id="cancelCameraBtn"', "cls-pill")):
            self.assertIn(look, opening_tag(TEMPLATE, marker), marker)
        self.assertIn('btn.className = "cls-pill', TEMPLATE)   # the detection pills
        self.assertIn("@layer components", style)   # so hidden and the size utilities still win


class SpinnersMoveTests(SimpleTestCase):
    """E13: an inline icon cannot rotate, so animate-spin on an <i> stood still; and reduced motion still moves."""

    def test_icon_spinners_are_inline_blocks_and_reduced_motion_fades(self):
        source = (CSS / "input.css").read_text(encoding="utf-8")
        self.assertIn(":where(i, span).animate-spin { display: inline-block; }", source)
        reduced = source[source.rindex("@media (prefers-reduced-motion: reduce)"):]
        self.assertIn(".animate-spin { animation: spin-calm", reduced)
        self.assertIn("@keyframes spin-calm", source)
        self.assertIn(".animate-spin {", (CSS / "style.css").read_text(encoding="utf-8"))   # Tailwind builds it

    def test_every_spinner_icon_is_animated(self):
        for path in TEMPLATES.rglob("*.html"):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"""<i\b[^>]*fi-rr-spinner[^>]*>""", text):
                tag = match.group(0)
                with self.subTest(template=path.name, tag=tag):
                    self.assertTrue("animate-spin" in tag or 'id="submit-spin"' in tag)

    def test_the_games_next_spinner_turns_and_the_browsers_spinner_fades_with_reduced_motion(self):
        game = (TEMPLATES / "beetles" / "game_play.html").read_text(encoding="utf-8")
        self.assertIn("#submit #submit-spin { display: inline-block;", game)
        self.assertIn("#submit #submit-spin { animation: spin-calm", game)
        browser = (TEMPLATES / "beetles" / "image_browser.html").read_text(encoding="utf-8")
        self.assertIn(".loading-spinner { animation: spin-calm", browser)

    def test_loading_words_pulse(self):
        self.assertIn('class="animate-pulse" style="font-size:0.85rem; color:#6b7280; font-style:italic;">Loading…',
                      (TEMPLATES / "beetles" / "taxonomy_browser.html").read_text(encoding="utf-8"))
        self.assertIn('<li class="text-gray-500 animate-pulse">Loading…</li>',
                      (TEMPLATES / "beetles" / "tool_annotate.html").read_text(encoding="utf-8"))
        self.assertIn('<span class="animate-pulse">Loading file...</span>',
                      (TEMPLATES / "beetles" / "data_management.html").read_text(encoding="utf-8"))


class ButtonWordingTests(SimpleTestCase):
    def test_the_button_says_identify_beetles_or_identify_cropped_beetles(self):
        self.assertIn('<span id="submitLabel">Identify beetles</span>', TEMPLATE)
        self.assertIn("'Identify cropped beetles' : 'Identify beetles'", TEMPLATE)
        self.assertNotIn("Classify image", TEMPLATE)
