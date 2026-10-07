"""UI audit #618: the Interactions page's header, tabs and About tab (issues int-tabs, int-dup, int-dup-nav,
int-staff, int-mono-units, int-italics, int-amber)."""
from django.urls import reverse

from beetlesgallery.beetles_app.testing import PageBehaviourCase


class HeaderAndTabsTests(PageBehaviourCase):
    def get(self):
        return self.client.get(reverse("interactions_preview")).content.decode()

    def test_the_status_pill_carries_its_own_date(self):
        page = self.get()
        self.assertIn("Under construction", page)
        self.assertIn("v1.0, last search 24 July 2026", page)
        self.assertNotIn(">\n          Under Construction\n        <", page)   # the old bare pill text is gone

    def test_the_tabs_sit_in_a_scrollable_row_with_a_fade_edge_right_after_the_header(self):
        page = self.get()
        header_end = page.index("</div>", page.index("page-title"))
        tabs_start = page.index('id="tab-btn-about"')
        # nothing but the (optional) staff menu sits between the header and the tabs
        between = page[header_end:tabs_start]
        self.assertNotIn("Scope and interpretation", between)
        self.assertIn("overflow-x-auto", page)
        self.assertIn("bg-gradient-to-l", page)   # the scroll-cue fade on the right edge

    def test_staff_tools_are_one_menu_not_three_buttons(self):
        self.client.force_login(self.staff)
        page = self.get()
        self.assertIn("Staff tools", page)
        self.assertIn("<details", page)
        self.assertIn("Review proposed interactions", page)
        self.assertIn("Upload or update interactions", page)

    def test_anonymous_visitors_get_no_staff_menu(self):
        page = self.get()
        self.assertNotIn("Staff tools", page)


class AboutTabTests(PageBehaviourCase):
    def get(self):
        return self.client.get(reverse("interactions_preview")).content.decode()

    def test_the_description_is_not_repeated(self):
        page = self.get()
        sentence = "a literature-derived resource compiling reported pathogens and parasites associated with bark and ambrosia beetles"
        self.assertEqual(page.lower().count(sentence), 1)

    def test_the_scope_note_is_a_note_box_not_italic(self):
        page = self.get()
        self.assertNotIn('text-gray-600 italic leading-relaxed', page)
        self.assertIn("nomenclatural reconciliation", page)

    def test_the_stat_units_are_not_small_caps_mono(self):
        page = self.get()
        self.assertNotIn('text-[11px] text-gray-600 font-mono', page)
        self.assertIn("primary refs", page)
        self.assertIn("% verified", page)

    def test_the_section_summary_cards_have_no_arrows_or_click_handlers(self):
        page = self.get()
        cards = page[page.index("Dataset Sections Summary"):page.index("Scope and interpretation")]
        self.assertNotIn("&rarr;", cards)
        self.assertNotIn("onclick=\"window.switchPathogenTab", cards)
        self.assertIn("Browse dataset", cards)
