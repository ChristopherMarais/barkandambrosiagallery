"""
UI audit (issue #618), the Home page batch: the three actions above the fold, a left-aligned institutions list
with "Show all", a compact team grid with real icon buttons, no duplicate ways in, a "Team" heading that matches
the rest of the site, a working lab link, clickable stat counts and sponsor logos visible without a hover.
"""
import re

from django.urls import reverse

from beetlesgallery.beetles_app.testing import PageBehaviourCase


def opening_tag(html, marker):
    start = html.rfind("<", 0, html.index(marker))
    return html[start:html.index(">", start) + 1]


class HomeTestCase(PageBehaviourCase):
    def page(self):
        return self.client.get(reverse("image_browser")).content.decode()

    def team_page(self):
        """The team, institutions and Discussions link moved to their own page (#618 home-team)."""
        return self.client.get(reverse("team")).content.decode()


class AboveTheFoldTests(HomeTestCase):
    """home-actions: title, one sentence, the beetle, then the three cards; attribution moves to the footer."""

    def test_attribution_and_maintained_by_are_in_the_footer_not_the_header(self):
        page = self.page()
        header = page[page.index("<header"):page.index("</header>")]
        self.assertNotIn("Image Attribution", header)
        self.assertNotIn("Maintained by", header)
        footer = page[page.index("<footer"):page.index("</footer>")]
        self.assertIn("Image Attribution", footer)
        self.assertIn("Maintained by", footer)

    def test_the_three_cards_come_right_after_the_header(self):
        page = self.page()
        header_end = page.index("</header>")
        # "AI Identification" is also the sidebar's label elsewhere on the page, so look for the hero's
        # own copy (the first one after the header, not wherever it happens to sit first in the DOM).
        browse_at = page.index("Image Browser", header_end)
        ai_at = page.index("AI Identification", header_end)
        self.assertLess(header_end, browse_at)
        self.assertLess(browse_at, ai_at)
        # Nothing else (no attribution block, no team section) sits between the header and the first card.
        self.assertNotIn("Image Attribution", page[header_end:browse_at])


class NoDuplicateButtonsTests(HomeTestCase):
    """home-dup-buttons: the three cards are the only way in, no separate hero button row repeats them."""

    def test_each_card_appears_once_in_the_hero(self):
        page = self.page()
        hero = page[page.index("<header"):page.index("border-t border-b border-gray-100")]
        self.assertEqual(hero.count("Image Browser"), 1)
        self.assertEqual(hero.count("AI Identification"), 1)


class StatsLinkTests(HomeTestCase):
    """home-stats-link: Images/Genera/Species/Type Specimens are no longer dead numbers."""

    @staticmethod
    def stat_anchor(html, label):
        """The <a ...> that wraps the number + ``label`` spans for one stat."""
        idx = html.index(f">{label}</span>")
        start = html.rfind("<a ", 0, idx)
        return html[start:html.index(">", start) + 1]

    def test_images_links_to_the_browser(self):
        page = self.page()
        tag = self.stat_anchor(page, "Images")
        self.assertIn(reverse("beetles_image_browser"), tag)

    def test_genera_and_species_link_to_the_browser(self):
        page = self.page()
        for label in ("Genera", "Species"):
            with self.subTest(label=label):
                tag = self.stat_anchor(page, label)
                self.assertIn(reverse("beetles_image_browser"), tag)

    def test_type_specimens_links_to_the_has_type_status_filter(self):
        page = self.page()
        tag = self.stat_anchor(page, "Type Specimens")
        self.assertIn("has_type_status=Yes", tag)

    def test_the_stat_links_do_not_require_an_account(self):
        # taxonomy_browser needs login; the stats must stay reachable from the public home page.
        page = self.page()
        for label in ("Images", "Genera", "Species", "Type Specimens"):
            with self.subTest(label=label):
                tag = self.stat_anchor(page, label)
                self.assertNotIn(reverse("taxonomy_browser"), tag)


class LogoAndLabLinkTests(HomeTestCase):
    """home-logos, home-lab-link."""

    def test_sponsor_logos_are_visible_at_rest(self):
        page = self.page()
        footer = page[page.index("<footer"):page.index("</footer>")]
        self.assertIn("opacity-70", footer)
        self.assertNotIn("opacity-40", footer)

    def test_the_lab_link_is_underlined_and_the_full_stop_is_outside_it(self):
        page = self.page()
        tag = opening_tag(page, "University of Florida Forest Entomology Lab</a>")
        self.assertIn("underline", tag)
        self.assertIn("University of Florida Forest Entomology Lab</a>.", page)


class TeamHeadingTests(HomeTestCase):
    """home-created: "Team", not "Created by:"."""

    def heading_tag_and_text(self, page):
        team = page[page.index('id="team-section"'):]
        match = re.search(r"<h2\b[^>]*>\s*(.*?)\s*</h2>", team, re.S)
        self.assertIsNotNone(match, "no <h2> heading on the team section")
        start = match.start()
        return team[start:team.index(">", start) + 1], match.group(1).strip()

    def test_the_heading_says_team(self):
        page = self.team_page()
        _, text = self.heading_tag_and_text(page)
        self.assertEqual(text, "Team")
        self.assertNotIn("Created by", page)

    def test_the_heading_is_not_heavier_than_the_rest(self):
        page = self.team_page()
        tag, _ = self.heading_tag_and_text(page)
        self.assertNotIn("font-black", tag)


class TeamGridTests(HomeTestCase):
    """home-team, home-team-icons: a compact grid, 24px icons in 44px hit areas, no "|" separators, aria-labels."""

    def test_name_and_role_sit_on_one_line(self):
        page = self.team_page()
        self.assertIn("Christopher Marais", page)
        self.assertIn("Co-Lead Full Stack Developer", page)

    def test_icons_are_24px_in_a_44px_hit_area_with_no_separators(self):
        page = self.team_page()
        team = page[page.index('id="team-section"'):page.index("Contributing institutions")]
        self.assertIn("w-11 h-11", team)   # 2.75rem = 44px
        self.assertIn("text-2xl", team)    # 1.5rem = 24px
        self.assertNotIn('text-gray-300 text-xs">|<', team)

    def test_every_icon_link_has_its_own_aria_label(self):
        page = self.team_page()
        team = page[page.index('id="team-section"'):page.index("Contributing institutions")]
        # One aria-label per github/linkedin/lab icon link; Andrew + Jiri (lab only) plus the rest (github/linkedin).
        self.assertGreaterEqual(team.count("aria-label="), 10)


class InstitutionsListTests(HomeTestCase):
    """home-institutions: left-aligned, two columns on desktop, top 10 then "Show all"."""

    def test_the_list_is_left_aligned_and_two_columns_on_desktop(self):
        page = self.team_page()
        tag = opening_tag(page, 'id="institutions-list"')
        self.assertIn("sm:grid-cols-2", tag)
        self.assertIn('<section class="mt-12 text-left">', page)

    def test_only_ten_institutions_show_by_default(self):
        page = self.team_page()
        list_html = page[page.index('id="institutions-list"'):page.index("</ul>")]
        total = list_html.count("<li>") + list_html.count('<li class="institution-extra hidden">')
        extra = list_html.count('<li class="institution-extra hidden">')
        self.assertEqual(total - extra, 10)
        self.assertGreater(extra, 0)

    def test_a_show_all_button_exists_with_a_live_count(self):
        page = self.team_page()
        self.assertIn('id="institutions-toggle"', page)
        self.assertIn('id="institutions-count"', page)
        self.assertIn("list.children.length", page)


class OneScreenHomeTests(HomeTestCase):
    """home-team: the home page is one screen; a compact "Who built this" line links to Team and partners."""

    def test_the_home_page_links_to_the_team_page_instead_of_listing_everyone(self):
        page = self.page()
        self.assertIn('data-testid="who-built-this"', page)
        self.assertIn(f'href="{reverse("team")}"', page)
        self.assertNotIn('id="team-section"', page)
        self.assertNotIn('id="institutions-list"', page)
        self.assertNotIn("Christopher Marais", page)
        self.assertNotIn("snap-mandatory", page)

    def test_the_team_page_is_public_and_links_back_home(self):
        response = self.client.get(reverse("team"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(reverse("team"), "/team/")
        page = response.content.decode()
        self.assertIn(f'href="{reverse("image_browser")}"', page)
        self.assertIn("github.com/ChristopherMarais/barkandambrosiagallery/discussions", page)


class HomeCardIconTests(HomeTestCase):
    """home-cards-icons, site-icons: the three cards use the sidebar's names and icons."""

    def hero(self):
        page = self.page()
        return page[page.index("</header>"):page.index("border-t border-b border-gray-100")]

    def test_the_cards_use_the_sidebar_icons(self):
        hero = self.hero()
        for icon in ("fi-rr-picture", "fi-rr-sparkles", "fi-rr-play"):
            with self.subTest(icon=icon):
                self.assertIn(icon, hero)
        self.assertNotIn("fi-rr-bug", hero)
        self.assertNotIn("fi-rr-gamepad", hero)

    def test_the_cards_use_the_sidebar_names(self):
        hero = self.hero()
        self.assertIn(">Image Browser</h2>", hero)
        self.assertIn(">AI Identification</h2>", hero)
        self.assertNotIn("Browse Images", hero)
