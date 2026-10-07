"""
UI audit round, staff/tool pages (#618): Data Management's action list and card headings, IBBI-AI's layout and
wording, the model-predictions upload page, and the annotation tool's canvas header, box colours, touch handles
and filter-stat buttons. ann-fit (open an image already fitted to the canvas on mobile) is explicitly skipped,
per the owner.
"""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import reverse

from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_taxon

TEMPLATES = Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles"


class DataManagementActionsTests(PageBehaviourCase):
    """Data Actions reads as a menu list, not ragged buttons; "Browse gallery" and "Field definitions" are links."""

    def page(self):
        self.client.force_login(self.superuser)
        return self.client.get(reverse("data_management")).content.decode()

    def test_card_headings_are_not_as_big_as_the_page_title(self):
        page = self.page()
        self.assertIn('<h1 class="page-title">Data management</h1>', page)
        for heading in ("Data Actions", "Species Tables", "Activity Logs"):
            with self.subTest(heading=heading):
                idx = page.index(heading)
                self.assertIn("text-lg font-semibold", page[max(0, idx - 80):idx])
        self.assertNotIn("text-2xl font-bold", page)

    def test_the_actions_are_a_menu_list_with_icon_title_description_and_chevron(self):
        page = self.page()
        for title, desc in (("Upload New Data", "Add new images and their details."),
                            ("Update Metadata", "Change records that are already here."),
                            ("Upload Model Predictions", "Add an AI model's species guesses for ROIs."),
                            ("Bulk Validation", "Review and validate records in bulk.")):
            with self.subTest(title=title):
                self.assertIn(title, page)
                self.assertIn(desc, page)
        self.assertIn("fi-rr-angle-small-right", page)
        self.assertIn("openModal('modal-upload-new')", page)

    def test_downloading_is_a_link_in_the_intro_not_an_action(self):
        # #618 data-browse: "To download images, use the Image Browser." with the name linked
        page = self.page()
        self.assertNotIn("Browse Gallery to Download", page)
        self.assertNotIn("Browse gallery to download", page)
        intro = page[page.index('data-testid="data-actions-intro"'):page.index('data-testid="data-actions-intro"') + 600]
        self.assertIn("To download images, use the", intro)
        self.assertIn(f'<a href="{reverse("beetles_image_browser")}"', intro)

    def test_field_definitions_is_a_text_link_after_the_intro_sentence(self):
        page = self.page()
        self.assertIn('data-testid="data-actions-intro"', page)
        intro = page[page.index('data-testid="data-actions-intro"'):page.index('data-testid="data-actions-intro"') + 1200]
        self.assertIn("Field definitions", intro)
        self.assertNotIn("fi-rr-info", intro)   # no corner info icon any more

    def test_species_tables_lead_with_one_sentence_and_hide_the_long_definition(self):
        page = self.page()
        self.assertIn("What are these tables?", page)
        self.assertIn("<summary", page[page.index("What are these tables?") - 200:page.index("What are these tables?")])
        self.assertIn("ground truth", page)   # the long definition is still rendered, just behind <details>


class ToolClassifyLayoutTests(PageBehaviourCase):
    """IBBI-AI: a one-line title, the drop zone first, the consent checkbox under it, small example thumbnails."""

    def page(self):
        return self.client.get(reverse("tool_classify")).content.decode()

    def test_the_title_is_one_line_with_an_eyebrow_above_it(self):
        page = self.page()
        self.assertIn('<h1 class="page-title">IBBI-AI</h1>', page)
        eyebrow = page[:page.index('<h1 class="page-title">IBBI-AI</h1>')]
        self.assertIn("Intelligent Bark Beetle Identifier", eyebrow[-400:])

    def test_the_model_picker_comes_first_then_the_drop_zone_the_consent_checkbox_and_examples(self):
        # The picker moved to the top of the page in the owner's phone review (round 6); the rest keep their order.
        page = self.page()
        order = [page.index(marker) for marker in (
            'id="modelSelect"', 'id="canvasContainer"', 'id="dontKeep"', 'class="example-btn')]
        self.assertEqual(order, sorted(order))

    def test_examples_are_small_thumbnails_in_one_row(self):
        page = self.page()
        self.assertIn("Or try an example", page)
        block = page[page.index("Or try an example"):page.index("Or try an example") + 1500]
        self.assertIn("w-16", block)
        self.assertNotIn("grid-cols-2", block)

    def test_photo_credits_are_12px_gray_500_one_line_each(self):
        page = self.page()
        self.assertIn('class="text-xs text-gray-500 mt-1 space-y-0.5"', page)
        self.assertNotIn("text-[10px] text-gray-400", page)

    def test_the_model_is_behind_a_disclosure(self):
        page = self.page()
        details = page[page.index('<details class="group rounded-lg border'):]
        details = details[:details.index('</details>')]
        self.assertIn('id="modelSelect"', details)
        self.assertIn('data-testid="ibbi-link"', details)


class UploadPredictionsPageTests(PageBehaviourCase):
    def page(self):
        self.client.force_login(self.superuser)
        return self.client.get(reverse("upload_predictions")).content.decode()

    def test_the_intro_starts_with_what_the_page_does_not_how_you_got_here(self):
        page = self.page()
        intro_start = page.index("Upload an AI model's species guesses for ROIs")
        usual_way = page.index("Upload Model Predictions action on")
        self.assertLess(intro_start, page.index('<form method="post"'))
        self.assertGreater(usual_way, intro_start)   # the circular "how you got here" note now comes second

    def test_the_submit_button_is_primary_and_disabled_until_a_file_is_chosen(self):
        page = self.page()
        btn = page[page.index('id="submit-predictions"'):]
        btn = btn[:btn.index('</button>')]
        self.assertIn("btn-primary", btn)
        self.assertIn("disabled", page[page.index('id="submit-predictions"') - 40:page.index('id="submit-predictions"') + 40])
        self.assertIn('getElementById("csv_file")', page)

    def test_the_columns_table_stacks_on_mobile(self):
        page = self.page()
        start = page.index('id="columns"')
        table = page[start:page.index("</table>", start)]
        self.assertIn("block sm:table", table)
        self.assertIn("sm:table-cell", table)


class AnnotationFeedSpeciesNameTests(PageBehaviourCase):
    """The annotation feed names each image (or "Unnamed"), so the canvas title and list rows need no lookup."""

    def test_named_and_unnamed_images_report_a_species_name(self):
        self.client.force_login(self.staff)
        taxon = make_taxon(valid_species_id="9001", scientific_name="Xyleborus affinis")
        named = make_beetle(bbox="validated", taxon=taxon).image_asset
        unnamed = make_beetle(bbox="unvalidated").image_asset

        data = self.client.get("/api/v1/beetles/images-with-annotations/").json()
        by_id = {r["image_asset_id"]: r for r in data["results"]}
        self.assertEqual(by_id[str(named.id)]["species_name"], "Xyleborus affinis")
        self.assertIsNone(by_id[str(unnamed.id)]["species_name"])


class AnnotationTemplateTests(SimpleTestCase):
    """Template/script checks for the canvas header, box colours, touch handles and filter-stat buttons."""

    def setUp(self):
        self.page = (TEMPLATES / "tool_annotate.html").read_text(encoding="utf-8")

    def test_the_canvas_title_is_the_image_id_ann_title_skipped(self):
        # ann-title (species name as the title, short id + copy) was skipped by the owner, #618.
        self.assertIn("canvas-title').textContent = img.image_asset_id", self.page)
        self.assertNotIn("species_name || 'Unnamed'", self.page)
        self.assertNotIn('id="canvas-id-copy"', self.page)
        self.assertNotIn('id="main-status-indicator"', self.page)   # replaced by the pill (ann-dot, kept)

    def test_image_list_rows_lead_with_the_full_id_ann_list_skipped(self):
        # ann-list (species name first, short id last) was skipped by the owner, #618.
        row = self.page[self.page.index("function renderImageList"):self.page.index("function renderImageList") + 2200]
        self.assertIn("img.image_asset_id}</p>", row)
        self.assertNotIn("species_name", row)

    def test_validated_status_is_a_pill_with_a_word_not_a_lone_dot(self):
        fn = self.page[self.page.index("function updateMainStatusIndicator"):]
        fn = fn[:fn.index("\n}\n")]
        self.assertIn("canvas-status-pill", fn)
        for word in ("Validated", "Not Validated", "No Bounding Boxes"):
            self.assertIn(word, fn)

    def test_the_fit_button_uses_the_expand_icon_not_the_search_icon(self):
        fit = self.page[self.page.index('title="Fit to Screen"') - 20:self.page.index('title="Fit to Screen"') + 120]
        self.assertIn("fi-rr-expand", fit)
        self.assertNotIn("fi-rr-search", fit)

    def test_human_drawn_boxes_are_gray_900_not_blue(self):
        self.assertNotIn("#3b82f6", self.page)
        fn = self.page[self.page.index("function drawCanvas"):self.page.index("function fitToScreen")]
        self.assertIn("#111827", fn)

    def test_handles_are_28px_on_a_coarse_pointer_and_delete_moves_to_the_toolbar(self):
        self.assertIn("pointer: coarse", self.page)
        self.assertIn("coarse ? 14 : 9", self.page)   # the drawn badge radius (28px/18px diameter)
        self.assertNotIn("delete_x", self.page)       # no more on-canvas red "X" to hit-test or draw
        self.assertIn('id="btn-delete-box"', self.page)
        self.assertIn("function deleteSelectedBox", self.page)

    def test_each_filter_stat_is_a_13px_button(self):
        marker = self.page.index('id="filter-stats"')
        stats = self.page[self.page.rfind("<div", 0, marker):self.page.index(">", marker) + 1]
        self.assertIn("text-[13px]", stats)
        self.assertNotIn("text-[11px]", stats)
        for param, value in (("image_validated", "Yes"), ("image_validated", "No"), ("has_rois", "No"),
                              ("all_rois_val", "Yes"), ("all_rois_val", "No")):
            self.assertIn(f"setAdvancedFilter('{param}', '{value}')", self.page)
        self.assertIn("function setAdvancedFilter", self.page)

    def test_the_ann_fit_item_was_intentionally_skipped(self):
        # #618 ann-fit (open an image fitted to the canvas width by default on mobile) is a deliberate SKIP;
        # this test just documents that fitToScreen() itself is untouched, not auto-invoked on image load.
        self.assertIn("function fitToScreen()", self.page)
