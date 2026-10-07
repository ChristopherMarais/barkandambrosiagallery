"""
UI audit (issue #618), the Image Browser batch: the name never truncates, fewer fields per card, the thumbnail
leads the card, real download/filter buttons, a helpful search placeholder, a "No preview" placeholder, a grid
checkbox and ROI badge, the detail page's label/value table, and a pager/result-line that match the rest of the
site.
"""
import re

from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon

# A bare `disabled` attribute, not Tailwind's `disabled:opacity-40` etc. variants (which are always present).
DISABLED_ATTR_RE = re.compile(r"\bdisabled\b(?!:)")


class BrowserTestCase(PageBehaviourCase):
    URL = "/beetles/"

    def get(self, query="", **extra):
        return self.client.get(f"{self.URL}?{query}", **extra)

    @staticmethod
    def opening_tag(html, marker):
        start = html.rfind("<", 0, html.index(marker))
        return html[start:html.index(">", start) + 1]

    def assertDisabled(self, tag):
        self.assertRegex(tag, DISABLED_ATTR_RE)

    def assertNotDisabled(self, tag):
        self.assertNotRegex(tag, DISABLED_ATTR_RE)


class TruncateAndDensityTests(BrowserTestCase):
    """browser-truncate, browser-density, browser-pairs."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        taxon = make_taxon(
            scientific_name="Xyleborinus saginatus", genus="Xyleborinus", species="saginatus", subfamily="Scolytinae",
        )
        image = make_image(image_institution="University of Florida Forest Entomology Lab")
        cls.beetle = make_beetle(
            image=image, taxon=taxon, bbox="validated", aspect="dorsal", specimen_type_status="Holotype",
            depicts_specimen="SP-1", specimen_sex="female", collection_country="USA",
        )

    def test_the_name_is_not_truncated(self):
        html = self.get().content.decode()
        # The name's own <h3> (not the thumbnail's "alt" text, nor an unrelated <h3> elsewhere on the page)
        # must wrap (line-clamp-2), not clip with "truncate".
        match = re.search(r'<h3 class="([^"]*)">\s*<a[^>]*>\s*Xyleborinus saginatus', html)
        self.assertIsNotNone(match, "couldn't find the name's own <h3>")
        classes = match.group(1)
        self.assertIn("line-clamp-2", classes)
        self.assertNotIn("truncate", classes)

    def test_all_fields_still_show_on_the_card(self):
        # browser-density (cut the card down to name/institution/two facts) was skipped by the owner (#618):
        # the fuller field set stays, just in the browser-pairs label/value table style.
        html = self.get().content.decode()
        self.assertIn('<dt class="col-span-1 text-text/60">Institution</dt>', html)
        self.assertIn("University of Florida Forest Entomology Lab", html)
        self.assertIn('<dt class="col-span-1 text-text/60">Aspect</dt>', html)
        self.assertIn('<dt class="col-span-1 text-text/60">Type status</dt>', html)
        self.assertIn("Holotype", html)
        self.assertIn('<dt class="col-span-1 text-text/60">Specimen ID</dt>', html)
        self.assertIn('<dt class="col-span-1 text-text/60">Sex</dt>', html)
        self.assertIn('<dt class="col-span-1 text-text/60">Country</dt>', html)
        self.assertIn('<dt class="col-span-1 text-text/60">Subfamily</dt>', html)

    def test_the_label_value_table_matches_the_detail_page_style(self):
        html = self.get().content.decode()
        self.assertIn('<dd class="col-span-2 text-text/80 truncate">University', html)


class ThumbnailTests(BrowserTestCase):
    """browser-thumb, browser-empty-thumb."""

    def test_the_thumbnail_stays_a_small_side_column(self):
        # browser-thumb (full-width/40%-column leading thumbnail) was skipped by the owner (#618).
        image = make_image(image_institution="UF")
        make_beetle(image=image, bbox="validated")
        html = self.get().content.decode()
        self.assertIn("w-32 sm:w-36", html)

    def test_a_missing_thumbnail_shows_a_no_preview_placeholder(self):
        image = make_image(image_institution="UF")  # no thumb_small set
        make_beetle(image=image, bbox="validated")
        html = self.get().content.decode()
        self.assertIn("No preview", html)
        self.assertNotIn(">No Image<", html)

    def test_a_broken_thumbnail_retries_through_javascript(self):
        image = make_image(image_institution="UF", thumb_small="thumbnails/aa/bb/x_96.webp")
        make_beetle(image=image, bbox="validated")
        html = self.get().content.decode()
        self.assertIn("handleThumbError", html)
        self.assertIn("thumb-retry-btn", html)
        self.assertIn('onerror="handleThumbError(this)"', html)

    def test_the_retry_handler_validates_the_url_before_building_img_src(self):
        # Same-origin-checked via the URL constructor, not a raw attribute read straight into img.src
        # (CodeQL DOM text reinterpreted as HTML / js/xss-through-dom, #618).
        html = self.get().content.decode()
        self.assertIn("new URL(src, location.href)", html)
        self.assertIn("parsed.origin !== location.origin", html)
        self.assertIn("parsed.protocol !== 'http:'", html)


class DownloadButtonTests(BrowserTestCase):
    """browser-download."""

    def test_download_selected_looks_like_a_button_and_starts_disabled(self):
        self.client.force_login(self.user)
        html = self.get().content.decode()
        tag = self.opening_tag(html, 'data-testid="download-selected"')
        self.assertIn("btn-secondary", tag)
        self.assertDisabled(tag)

    def test_download_all_looks_like_a_button(self):
        self.client.force_login(self.user)
        image = make_image(image_institution="UF")
        make_beetle(image=image, bbox="validated")
        html = self.get().content.decode()
        tag = self.opening_tag(html, 'data-testid="download-all"')
        self.assertIn("btn-secondary", tag)


class SearchAndFilterTests(BrowserTestCase):
    """browser-placeholder, browser-filter."""

    def test_the_search_placeholder_is_short_with_helper_text_below(self):
        html = self.get().content.decode()
        self.assertIn('placeholder="Search names or IDs"', html)
        self.assertIn("Searches", html)

    def test_filters_button_is_a_real_button_with_no_active_count(self):
        html = self.get().content.decode()
        tag = self.opening_tag(html, 'id="toggle-filters-btn"')
        self.assertIn("btn-secondary", tag)
        self.assertNotIn("Filters &middot;", html)

    def test_filters_button_shows_an_active_count_pill(self):
        image = make_image(image_institution="UF")
        make_beetle(image=image, collection_country="USA")
        html = self.get("country=USA").content.decode()
        self.assertIn("Filters &middot; 1", html)
        self.assertEqual(self.get("country=USA").context["active_filter_count"], 1)

    def test_active_filter_count_adds_the_size_and_resolution_ranges(self):
        response = self.get("size_min=1&res_max=5")
        self.assertEqual(response.context["active_filter_count"], 2)

    def test_no_filters_means_no_count(self):
        self.assertEqual(self.get().context["active_filter_count"], 0)


class GridViewTests(BrowserTestCase):
    """browser-grid-select, browser-roi-badge."""

    def test_the_grid_checkbox_sits_top_left_not_top_right(self):
        image = make_image(image_institution="UF")
        make_beetle(image=image, bbox="validated")
        html = self.get().content.decode()
        self.assertIn('grid-select-overlay absolute top-2 left-2', html)
        self.assertNotIn('grid-select-overlay absolute top-2 right-2', html)

    def test_a_photo_with_several_rois_gets_a_badge_not_a_duplicate_tile(self):
        image = make_image(image_institution="UF")
        make_beetle(image=image, bbox="validated")
        make_beetle(image=image, bbox="validated")
        make_beetle(image=image, bbox="unvalidated")
        response = self.get()
        card = next(b for b in response.context["beetles"] if b.image_asset_id == image.id)
        self.assertEqual(card.boxed_count, 3)
        self.assertIn("3 ROIs", response.content.decode())

    def test_a_single_roi_gets_no_badge(self):
        image = make_image(image_institution="UF")
        make_beetle(image=image, bbox="validated")
        response = self.get()
        card = next(b for b in response.context["beetles"] if b.image_asset_id == image.id)
        self.assertEqual(card.boxed_count, 1)
        self.assertNotIn("1 ROIs", response.content.decode())

    def test_an_unboxed_sibling_does_not_count_towards_the_badge(self):
        image = make_image(image_institution="UF")
        make_beetle(image=image, bbox="validated")
        make_beetle(image=image)  # no box at all
        response = self.get()
        card = next(b for b in response.context["beetles"] if b.image_asset_id == image.id)
        self.assertEqual(card.boxed_count, 1)


class PagerAndResultLineTests(BrowserTestCase):
    """browser-pager, browser-found."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for _ in range(2):
            make_beetle(image=make_image(image_institution="UF"), bbox="validated")

    def test_the_result_line_combines_the_count_and_the_page(self):
        response = self.get("per_page=1")
        html = response.content.decode()
        self.assertEqual(response.context["total_matches"], 2)
        # The count (digit_groups wraps it in a span) and the page sit on one line, no separate "Found ... ." text.
        self.assertIn("images &middot; page 1 of 2", html)
        self.assertNotIn("Found ", html)

    def test_previous_is_disabled_but_present_on_the_first_page(self):
        html = self.get("per_page=1&page=1").content.decode()
        self.assertDisabled(self.opening_tag(html, "Previous"))
        self.assertNotDisabled(self.opening_tag(html, "Next"))

    def test_next_is_disabled_but_present_on_the_last_page(self):
        html = self.get("per_page=1&page=2").content.decode()
        self.assertDisabled(self.opening_tag(html, "Next"))
        self.assertNotDisabled(self.opening_tag(html, "Previous"))

    def test_both_arrows_are_enabled_on_a_middle_page(self):
        make_beetle(image=make_image(image_institution="UF"), bbox="validated")  # 3 specimens, 3 pages of 1
        html = self.get("per_page=1&page=2").content.decode()
        self.assertNotDisabled(self.opening_tag(html, "Previous"))
        self.assertNotDisabled(self.opening_tag(html, "Next"))


class TypeStatusFilterTests(BrowserTestCase):
    """The landing page's "Type Specimens" count links to ?has_type_status=Yes."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.typed = make_beetle(image=make_image(image_institution="UF"), specimen_type_status="Holotype")
        cls.untyped = make_beetle(image=make_image(image_institution="UF"), specimen_type_status="")

    def ids(self, response):
        return {b.id for b in response.context["beetles"]}

    def test_yes_keeps_only_specimens_with_a_type_status(self):
        self.assertEqual(self.ids(self.get("has_type_status=Yes")), {self.typed.id})

    def test_no_keeps_only_specimens_without_one(self):
        self.assertEqual(self.ids(self.get("has_type_status=No")), {self.untyped.id})

    def test_unfiltered_keeps_both(self):
        self.assertEqual(self.ids(self.get()), {self.typed.id, self.untyped.id})
