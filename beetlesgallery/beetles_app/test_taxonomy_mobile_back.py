"""
Taxonomy browser on a phone, from the owner's review: the tree rows link to a taxon's images with the Image Browser's
picture icon and the count (not the word "images"), with a finger-sized target; and a species page opened over the
tree has a "Taxonomy" back bar that is not hidden under the site's sticky header, plus a history entry so the
phone's Back button returns to the tree at the species that was tapped. The desktop layout is untouched.
"""
from django.urls import reverse

from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_taxon


class TaxonomyMobileBackTests(PageBehaviourCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        make_taxon(
            "T-IPS", scientific_name="Ips typographus", subfamily="Scolytinae", tribe="Ipini",
            genus="Ips", species="typographus",
        )

    def page(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("taxonomy_browser"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    # --- the picture icon in place of the word "images" -----------------------------------------------------------

    def test_rows_link_to_images_with_the_picture_icon_and_count(self):
        page = self.page()
        self.assertIn('<i class="fi fi-rr-picture tree-browse-icon" aria-hidden="true"></i>', page)
        self.assertIn("groupDigits(count)", page)
        self.assertNotIn("' images)", page)

    def test_the_icon_link_is_labelled_for_screen_readers(self):
        page = self.page()
        self.assertIn('link.title = "Browse images of " + taxonName;', page)
        self.assertIn('"Browse " + count + " images of " + taxonName', page)

    def test_the_icon_link_is_always_shown_and_finger_sized_on_touch(self):
        page = self.page()
        self.assertNotIn(".tree-node-container:hover .tree-browse-link", page)
        self.assertIn(".tree-browse-link { min-width: 44px; min-height: 44px; }", page)
        self.assertLess(page.index("@media (pointer: coarse)"), page.index("min-width: 44px"))

    # --- getting back to the tree --------------------------------------------------------------------------------

    def test_the_species_page_sits_above_the_sticky_site_header(self):
        page = self.page()
        # base.html's #sidenavWrap is sticky at z-index 50; the old 40 hid the back bar under it
        self.assertIn("position: fixed; inset: 0; z-index: 60;", page)
        self.assertIn("#mobile-species-back { min-height: 44px;", page)

    def test_the_back_bar_reads_taxonomy(self):
        page = self.page()
        self.assertIn('id="mobile-species-back" aria-label="Back to the taxonomy tree"', page)
        bar = page[page.index('id="mobile-species-back"'):]
        bar = bar[:bar.index("</button>")]
        self.assertIn("Taxonomy", bar)
        self.assertNotIn("Back to the tree", bar)

    def test_the_phone_back_button_returns_to_the_tree(self):
        page = self.page()
        self.assertIn("history.pushState(state, \"\")", page)
        self.assertIn('window.addEventListener("popstate"', page)
        self.assertIn("history.back()", page)

    def test_closing_returns_to_the_tapped_species(self):
        page = self.page()
        self.assertIn('rootEl.querySelector(".tree-species-btn--active")', page)
        self.assertIn('active.scrollIntoView({ block: "center" })', page)

    def test_only_phones_open_the_full_screen_page(self):
        page = self.page()
        check = page.index('if (window.matchMedia("(max-width: 899px)").matches)')
        self.assertLess(check, page.index("openMobileSpecies(node.species_id)"))
        self.assertLess(page.index("openMobileSpecies(node.species_id)") - check, 100)
        self.assertLess(page.index("@media (max-width: 899px)"), page.index(".taxonomy-detail-section.mobile-species-open {"))
