"""
The UI audit's taxonomy browser fixes (issue #618): a species opens as its own full-screen page on a phone instead
of a panel you'd have to scroll past a tall tree to see, long synonymies show 5 lines then "Show all N", Expand
All is hidden on a phone (it would open 7800+ species at once), the search placeholder names what rank it
searches, the raw "valid_species_id" becomes a copyable "Accepted name ID", the picture icon has a real label, and
every node (not just subfamilies) shows its species and image counts.
"""
import json

from django.urls import reverse

from beetlesgallery.beetles_app.models import Synonym
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_taxon


class TaxonomyUiPolishTests(PageBehaviourCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ips = make_taxon(
            "T-IPS", scientific_name="Ips typographus", subfamily="Scolytinae", tribe="Ipini",
            genus="Ips", species="typographus",
        )
        cls.xyl = make_taxon(
            "T-XYL", scientific_name="Xyleborus affinis", subfamily="Scolytinae", tribe="Xyleborini",
            genus="Xyleborus", species="affinis",
        )
        for i in range(19):
            Synonym.objects.create(
                taxon=cls.ips, name_id=f"N-{i}", described_scientific_name=f"Bostrichus namus{i}",
                authority="Ratzeburg", year="1837",
            )

    def page(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("taxonomy_browser"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    # --- tax-mobile ----------------------------------------------------------------------------------------------

    def test_a_species_opens_full_screen_on_mobile_with_a_back_button(self):
        page = self.page()
        self.assertIn("mobile-species-open", page)
        self.assertIn('id="mobile-species-back"', page)
        self.assertIn("Back to the tree", page)
        self.assertIn('classList.add("mobile-species-open")', page)
        self.assertIn('classList.remove("mobile-species-open")', page)

    # --- tax-synonyms ----------------------------------------------------------------------------------------------

    def test_synonyms_are_one_line_each_showing_five_then_show_all(self):
        page = self.page()
        self.assertIn("names.slice(0, 5).map", page)
        self.assertIn("names.slice(5).map", page)
        self.assertIn("synonym-extra", page)
        self.assertIn("show-all-synonyms", page)
        self.assertIn("Show all ' + names.length", page)
        # the old four-row card is gone
        self.assertNotIn("synonymCard", page)

    def test_the_backend_still_returns_every_synonym_for_the_js_to_slice(self):
        response = self.client.get("/taxonomy/described-names/", {"species_id": "T-IPS"})
        self.assertEqual(len(response.json()["names"]), 19)

    # --- tax-expand ----------------------------------------------------------------------------------------------

    def test_expand_all_is_hidden_on_a_phone(self):
        page = self.page()
        self.assertIn("#expand-all-btn { display: none; }", page)
        self.assertLess(page.index("@media (max-width: 899px)"), page.index("#expand-all-btn { display: none; }"))

    # --- tax-search ----------------------------------------------------------------------------------------------

    def test_the_search_placeholder_follows_the_selected_rank(self):
        page = self.page()
        self.assertIn("Search genera…", page)
        self.assertIn("Search tribes…", page)
        self.assertIn("Search species…", page)
        self.assertIn("updateSearchPlaceholder", page)

    # --- tax-raw-id ----------------------------------------------------------------------------------------------

    def test_the_species_panel_shows_a_named_copyable_accepted_id_not_the_raw_field(self):
        page = self.page()
        self.assertIn("Accepted name ID", page)
        self.assertIn("copy-accepted-id", page)
        self.assertNotIn("valid_species_id:", page)

    # --- tax-image-icon ------------------------------------------------------------------------------------------

    def test_the_browse_icon_has_an_aria_label(self):
        page = self.page()
        self.assertIn('setAttribute("aria-label", "Browse "', page)

    # --- tax-counts ----------------------------------------------------------------------------------------------

    def test_every_node_gets_a_species_and_image_count(self):
        make_beetle(taxon=self.ips)
        make_beetle(taxon=self.ips)
        self.client.force_login(self.user)
        response = self.client.get(reverse("taxonomy_browser"))
        tree = json.loads(response.context["taxonomy_tree_json"])
        scolytinae = next(n for n in tree if n["name"] == "Scolytinae")
        self.assertEqual(scolytinae["imageCount"], 2)
        ipini = next(t for t in scolytinae["children"] if t["name"] == "Ipini")
        self.assertEqual(ipini["imageCount"], 2)
        ips_genus = next(g for g in ipini["children"] if g["name"] == "Ips")
        self.assertEqual(ips_genus["imageCount"], 2)
        ips_species = ips_genus["children"][0]
        self.assertEqual(ips_species["imageCount"], 2)

        xyl_genus = next(g for g in scolytinae["children"] if g["name"] == "Xyleborini")["children"][0]
        self.assertEqual(xyl_genus["imageCount"], 0)

        page = self.page()
        self.assertIn("node.speciesCount + ' species, ' + (node.imageCount || 0) + ' images", page)
        self.assertIn("(node.imageCount || 0) + ' images)", page)

    def test_images_sharing_one_specimen_photo_are_only_counted_once(self):
        first = make_beetle(taxon=self.ips)
        make_beetle(image=first.image_asset, taxon=self.ips)   # a second specimen on the same photo
        self.client.force_login(self.user)
        tree = json.loads(self.client.get(reverse("taxonomy_browser")).context["taxonomy_tree_json"])
        scolytinae = next(n for n in tree if n["name"] == "Scolytinae")
        self.assertEqual(scolytinae["imageCount"], 1)

    def test_deleted_specimens_are_not_counted(self):
        make_beetle(taxon=self.ips).delete()
        self.client.force_login(self.user)
        tree = json.loads(self.client.get(reverse("taxonomy_browser")).context["taxonomy_tree_json"])
        scolytinae = next(n for n in tree if n["name"] == "Scolytinae")
        self.assertEqual(scolytinae["imageCount"], 0)
