"""
Site-wide and nav fixes from the UI audit (issue #618, "Site-wide" and "Navigation and menu" batches):
  - the active nav item is matched by URL prefix, not an exact page name, so a sub-page (Leaderboard, Unlocks, ...)
    still highlights its section (nav-active)
  - the mobile top bar names the current section next to the menu button (nav-mobile-title)
  - Account is pinned to the drawer/rail footer with the signed-in name, not in the middle of the list
    (nav-drawer-account)
  - the sidebar uses the fixed icon map, not a mismatched icon (site-icons, nav-play-icon)
  - the rail opens on keyboard focus too, not just :hover (nav-focus)
  - a visible, consistent focus ring and the one "no value" mark (site-focus, site-dash)
"""
from pathlib import Path

from django.template import engines
from django.test import SimpleTestCase
from django.urls import reverse

from beetlesgallery.beetles_app.templatetags.beetle_tags import dash
from beetlesgallery.beetles_app.testing import PageBehaviourCase

REPO = Path(__file__).resolve().parents[2]
BASE_HTML = (REPO / "beetlesgallery" / "templates" / "base.html").read_text(encoding="utf-8")
INPUT_CSS = (REPO / "beetlesgallery" / "static" / "css" / "input.css").read_text(encoding="utf-8")


class DashFilterTests(SimpleTestCase):
    def test_missing_values_show_the_em_dash(self):
        for value in (None, "", "None"):
            self.assertEqual(dash(value), "—")

    def test_a_real_value_passes_through(self):
        self.assertEqual(dash("2037"), "2037")
        self.assertEqual(dash(0), 0)


class SiteFocusRingTests(SimpleTestCase):
    def test_a_visible_focus_ring_is_set_globally(self):
        self.assertIn(":focus-visible {", INPUT_CSS)
        self.assertIn("outline: 2px solid var(--color-gray-600);", INPUT_CSS)
        self.assertIn("outline-offset: 2px;", INPUT_CSS)

    def test_buttons_no_longer_carry_the_faint_ring(self):
        # the old, barely-visible ring (site-focus, #618); the three button classes rely on the global rule above now
        self.assertNotIn("focus-visible:ring-gray-300", INPUT_CSS)


class SharedComponentCssTests(SimpleTestCase):
    def test_progress_bar_is_neutral_by_default(self):
        self.assertIn(".progress-bar {", INPUT_CSS)
        self.assertIn(".progress-bar-fill {", INPUT_CSS)

    def test_section_and_card_classes_exist(self):
        self.assertIn(".section-heading {", INPUT_CSS)
        self.assertIn(".card-label {", INPUT_CSS)
        self.assertIn(".card {", INPUT_CSS)

    def test_table_scroll_fallback_exists(self):
        self.assertIn(".table-scroll {", INPUT_CSS)


class NavFocusWithinTests(SimpleTestCase):
    def test_the_rail_opens_on_keyboard_focus_too(self):
        self.assertIn("#sidenav:focus-within", BASE_HTML)
        self.assertIn("#sidenav:not(:hover):not(:focus-within) #sideItems", BASE_HTML)

    def test_labels_reveal_on_focus_within_everywhere_they_reveal_on_hover(self):
        hovers = BASE_HTML.count("group-hover:opacity-100")
        focuses = BASE_HTML.count("group-focus-within:opacity-100")
        self.assertGreater(hovers, 0)
        self.assertEqual(hovers, focuses)


class NavIconMapTests(SimpleTestCase):
    def test_image_browser_uses_the_picture_icon_not_the_bug(self):
        self.assertIn("fi-rr-picture", BASE_HTML)
        self.assertNotIn("fi-rr-bug", BASE_HTML)

    def test_the_game_uses_the_play_icon_not_the_gamepad(self):
        self.assertIn("fi-rr-play", BASE_HTML)
        self.assertNotIn("fi-rr-gamepad", BASE_HTML)


class ConsentButtonRoleTests(SimpleTestCase):
    def test_accept_commits_so_it_is_the_primary_button(self):
        self.assertIn('class="btn-primary" data-consent="granted"', BASE_HTML)
        self.assertIn('class="btn-secondary" data-consent="denied"', BASE_HTML)


class MobileTopBarTests(PageBehaviourCase):
    def test_the_bar_is_56px_and_names_the_section(self):
        page = self.client.get(reverse("beetles_image_browser")).content.decode()
        self.assertIn('data-testid="mobile-nav-title"', page)
        self.assertIn(">Image Browser<", page)
        self.assertIn("h-14 px-4 border-b", page)

    def test_the_home_page_keeps_the_site_name(self):
        page = self.client.get("/").content.decode()
        self.assertIn("Bark and Ambrosia Gallery", page)


class NavActiveByPrefixTests(PageBehaviourCase):
    def test_a_game_sub_page_still_highlights_the_game_nav_item(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_leaderboard")).content.decode()
        # the game nav link (desktop + mobile) should carry the active classes, not just the hover-only ones
        self.assertGreaterEqual(page.count("bg-gray-200 font-semibold"), 2)


class NavHomeLinkTests(SimpleTestCase):
    def test_the_home_link_keeps_its_own_exact_match(self):
        # "/" is every page's prefix, so home cannot use nav_active's prefix matching like the other sections
        self.assertIn(
            "{% if request.resolver_match.url_name == 'image_browser' %}bg-gray-200 font-semibold{% else %}"
            "hover:bg-gray-200{% endif %}",
            BASE_HTML,
        )


class NavDrawerAccountTests(PageBehaviourCase):
    def test_account_is_pinned_to_the_footer_with_the_username(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("data_management")).content.decode()
        self.assertIn("Account &amp; settings", page)
        self.assertIn(self.user.username, page)
        # the old middle-of-the-list label is gone; the page's own <h1> ("Account management") is untouched
        self.assertNotIn(">Account Management</span>", page)

    def test_logged_out_visitors_see_no_account_footer_row(self):
        page = self.client.get(reverse("beetles_image_browser")).content.decode()
        self.assertNotIn("Account &amp; settings", page)


class SharedIncludesRenderTests(SimpleTestCase):
    """Each new reusable include (site-back, site-empty, site-uuid, site-upload, #618) renders without error."""

    def render(self, template, context):
        return engines["django"].from_string(
            "{% load static %}{% include '" + template + "' %}"
        ).render(context)

    def test_back_link(self):
        html = self.render("beetles/includes/back_link.html", {"url": "/beetles/", "label": "Image browser"})
        self.assertIn("Image browser", html)
        self.assertIn('href="/beetles/"', html)

    def test_empty_state_with_and_without_action(self):
        html = self.render("beetles/includes/empty_state.html", {"text": "Nothing uploaded yet."})
        self.assertIn("Nothing uploaded yet.", html)
        self.assertNotIn("<a ", html)
        html = self.render("beetles/includes/empty_state.html", {
            "text": "Nothing uploaded yet.", "action_url": "/my-uploads/", "action_label": "Upload a file",
        })
        self.assertIn("Upload a file", html)

    def test_short_id_shows_eight_characters_and_full_on_request(self):
        value = "12345678-1234-1234-1234-123456789012"
        short = self.render("beetles/includes/short_id.html", {"value": value})
        self.assertIn("12345678", short)
        # the full value is always in the copy button's data attribute (so it can copy it), but shown as
        # visible text only when full=True: one occurrence here, two once it is also the visible text below
        self.assertEqual(short.count(value), 1)
        full = self.render("beetles/includes/short_id.html", {"value": value, "full": True})
        self.assertEqual(full.count(value), 2)

    def test_drop_zone_shows_the_hint(self):
        html = self.render("beetles/includes/drop_zone.html", {
            "input_id": "fileInput", "accept": "image/*", "hint": "PNG or JPG, up to 25 MB",
        })
        self.assertIn('id="fileInput"', html)
        self.assertIn("PNG or JPG, up to 25 MB", html)
        self.assertIn("data-drop-zone", html)


class PageTitleCasingTests(SimpleTestCase):
    """Sentence case for every page title (site-casing, #618): capitals only for proper names."""

    def test_the_title_case_headings_found_by_the_audit_are_now_sentence_case(self):
        checks = {
            # my_account.html and tool_classify.html later got their own restructure (acct-who/cre-title and
            # cls-title, #618) that replaced the casing-only fix here with a shorter title; still sentence case.
            "accounts/my_account.html": "Account",
            "beetles/data_management.html": "Data management",
            "beetles/image_browser.html": "Beetle image browser",
            "beetles/taxonomy_browser.html": "Taxonomy browser",
            "beetles/tool_annotate.html": "Image annotation",
            "beetles/tool_classify.html": "IBBI-AI",
        }
        for relpath, heading in checks.items():
            with self.subTest(template=relpath):
                source = (REPO / "beetlesgallery" / "templates" / relpath).read_text(encoding="utf-8")
                self.assertIn(f'<h1 class="page-title"', source)
                self.assertIn(heading, source)
