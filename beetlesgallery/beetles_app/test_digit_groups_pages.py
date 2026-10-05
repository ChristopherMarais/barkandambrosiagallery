"""Page counters, upload summaries and the annotation page's statistics show large numbers in groups of three."""
from django.conf import settings
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from beetlesgallery.beetles_app.templatetags.beetle_tags import digit_groups_text

TEMPLATES = settings.BASE_DIR / "beetlesgallery" / "templates" / "beetles"


class PageCountersTests(SimpleTestCase):
    def setUp(self):
        self.page = Paginator(range(2_500_000), 1000).page(1234)

    def test_page_x_of_y_is_grouped_but_links_keep_the_plain_number(self):
        html = render_to_string("beetles/includes/pager.html",
                                {"page": self.page, "param": "p", "anchor": "x", "request": RequestFactory().get("/")})
        self.assertIn(f"Page {digit_groups_text(1234)} of {digit_groups_text(2500)}", html)
        self.assertIn("p=1233", html)   # the link itself is not grouped
        history = render_to_string("beetles/includes/game_history_pager.html", {"page": self.page, "tab": "all"})
        self.assertIn(f"Page {digit_groups_text(1234)} of {digit_groups_text(2500)}", history)
        self.assertIn("page=1235", history)

    def test_the_annotation_statistics_and_counts_go_through_digit_grouping(self):
        source = (TEMPLATES / "tool_annotate.html").read_text()
        for name in ("images_validated", "images_unvalidated", "images_no_bbox", "rois_validated", "rois_unvalidated"):
            self.assertIn(f"digitGroupsText(data.stats.{name})", source)
        self.assertIn("digitGroupsText(img.annotation_count)", source)

    def test_upload_summaries_and_the_gallery_page_picker_are_grouped(self):
        for name in ("upload_predictions.html", "upload_interaction_proposals.html", "upload_interactions.html"):
            source = (TEMPLATES / name).read_text()
            self.assertIn("result.rows|digit_groups_text", source, name)
            self.assertIn("result.error_count|digit_groups_text", source, name)
        self.assertIn("paginator.num_pages|digit_groups_text", (TEMPLATES / "includes" / "gallery_results.html").read_text())
        views = (settings.BASE_DIR / "beetlesgallery" / "beetles_app" / "views.py").read_text()
        self.assertIn('>{digit_groups_text(p)}</option>', views)   # the label is grouped; value="{p}" stays plain
