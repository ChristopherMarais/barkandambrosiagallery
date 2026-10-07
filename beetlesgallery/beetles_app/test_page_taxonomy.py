"""
Behaviour tests for the Taxonomy Browser page and its AJAX endpoints:
the search box, described names and species images (issue #206, part 3).
"""
import json

from django.urls import reverse

from beetlesgallery.beetles_app.models import Synonym
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_taxon


class TaxonomyTestCase(PageBehaviourCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ips = make_taxon(
            "T-IPS", scientific_name="Ips typographus", subfamily="Scolytinae", tribe="Ipini",
            genus="Ips", species="typographus", original_genus="Bostrichus",
        )
        cls.xyl = make_taxon(
            "T-XYL", scientific_name="Xyleborus affinis", subfamily="Scolytinae", tribe="Xyleborini",
            genus="Xyleborus", species="affinis", subspecies="minor",
        )
        cls.platy = make_taxon(
            "T-PLA", scientific_name="Platypus cylindrus", subfamily="Platypodinae", tribe="Platypodini",
            genus="Platypus", species="cylindrus",
        )
        cls.syn = Synonym.objects.create(
            taxon=cls.ips, name_id="N-1", described_scientific_name="Bostrichus typographus",
            genus="Bostrichus", species="typographus", authority="Linnaeus", year="1758",
        )
        Synonym.objects.create(
            taxon=cls.xyl, name_id="N-2", described_scientific_name="Bostrichus affinis",
        )


class TaxonomyBrowserTreeTests(TaxonomyTestCase):
    def tree(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("taxonomy_browser"))
        self.assertEqual(response.status_code, 200)
        return response, json.loads(response.context["taxonomy_tree_json"])

    def test_tree_is_grouped_subfamily_tribe_genus_species(self):
        _, tree = self.tree()
        self.assertEqual([n["name"] for n in tree], ["Platypodinae", "Scolytinae"])
        scolytinae = tree[1]
        self.assertEqual(scolytinae["level"], "subfamily")
        self.assertEqual([t["name"] for t in scolytinae["children"]], ["Ipini", "Xyleborini"])
        genus = scolytinae["children"][0]["children"][0]
        self.assertEqual((genus["name"], genus["level"]), ("Ips", "genus"))
        self.assertEqual(genus["children"][0]["species_id"], "T-IPS")

    def test_species_counts_add_up(self):
        _, tree = self.tree()
        counts = {n["name"]: n["speciesCount"] for n in tree}
        self.assertEqual(counts, {"Platypodinae": 1, "Scolytinae": 2})

    def test_subspecies_is_shown_after_the_species_name(self):
        _, tree = self.tree()
        xyleborus = tree[1]["children"][1]["children"][0]
        self.assertEqual(xyleborus["children"][0]["name"], "affinis minor")

    def test_species_details_are_available_by_id(self):
        response, _ = self.tree()
        details = json.loads(response.context["species_map_json"])
        self.assertEqual(details["T-IPS"]["scientificName"], "Ips typographus")
        self.assertEqual(set(details), {"T-IPS", "T-XYL", "T-PLA"})

    def test_missing_ranks_are_grouped_under_unknown(self):
        make_taxon("T-BARE", scientific_name="Mystery beetle")
        _, tree = self.tree()
        unknown = next(n for n in tree if n["name"] == "Unknown Subfamily")
        self.assertEqual(unknown["children"][0]["name"], "Unknown Tribe")
        self.assertEqual(unknown["children"][0]["children"][0]["name"], "Unknown Genus")


class TaxonomySearchTests(TaxonomyTestCase):
    URL = "/taxonomy/search/"

    def search(self, **params):
        return self.client.get(self.URL, params)

    def test_original_genus_search_returns_matching_species(self):
        response = self.search(field="original_genus", query="bostri")  # substring, any case
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"species_ids": ["T-IPS"], "matches": ["Bostrichus"]})

    def test_described_name_search_returns_the_valid_species(self):
        response = self.search(field="described_name", query="bostrichus")
        self.assertEqual(
            response.json(),
            {
                "species_ids": ["T-IPS", "T-XYL"],
                "matches": ["Bostrichus affinis", "Bostrichus typographus"],
            },
        )

    def test_search_with_no_match_returns_empty_lists(self):
        response = self.search(field="described_name", query="zzz")
        self.assertEqual(response.json(), {"species_ids": [], "matches": []})

    def test_missing_field_or_query_is_a_400(self):
        for params in ({}, {"field": "original_genus"}, {"query": "x"}, {"field": "original_genus", "query": "  "}):
            with self.subTest(params=params):
                self.assertEqual(self.search(**params).status_code, 400)

    def test_unknown_field_is_a_400(self):
        response = self.search(field="colour", query="red")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "invalid field"})

    def test_post_is_not_allowed(self):
        self.assertEqual(self.client.post(self.URL, {"field": "original_genus", "query": "x"}).status_code, 405)

    def test_search_is_open_to_logged_out_visitors(self):
        self.assertEqual(self.search(field="original_genus", query="bostri").status_code, 200)


class DescribedNamesTests(TaxonomyTestCase):
    URL = "/taxonomy/described-names/"

    def test_lists_the_synonyms_of_a_species(self):
        response = self.client.get(self.URL, {"species_id": "T-IPS"})
        self.assertEqual(response.status_code, 200)
        names = response.json()["names"]
        self.assertEqual(len(names), 1)
        self.assertEqual(names[0]["name_id"], "N-1")
        self.assertEqual(names[0]["described_scientific_name"], "Bostrichus typographus")
        self.assertEqual(names[0]["year"], "1758")

    def test_species_without_synonyms_gets_an_empty_list(self):
        self.assertEqual(self.client.get(self.URL, {"species_id": "T-PLA"}).json(), {"names": []})

    def test_unknown_species_gets_an_empty_list(self):
        self.assertEqual(self.client.get(self.URL, {"species_id": "nope"}).json(), {"names": []})

    def test_species_id_is_required(self):
        response = self.client.get(self.URL, {"species_id": "  "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "species_id is required"})

    def test_post_is_not_allowed(self):
        self.assertEqual(self.client.post(self.URL, {"species_id": "T-IPS"}).status_code, 405)


class SpeciesImagesTests(TaxonomyTestCase):
    URL = "/taxonomy/species-images/"

    def test_one_entry_per_image(self):
        first = make_beetle(taxon=self.ips)
        second = make_beetle(taxon=self.ips)
        make_beetle(image=second.image_asset, taxon=self.ips)  # same image again
        make_beetle(taxon=self.xyl)  # a different species

        images = self.client.get(self.URL, {"species_id": "T-IPS"}).json()["images"]

        self.assertEqual(len(images), 2)  # three specimens, but two share an image
        self.assertIn(str(first.id), [i["beetle_id"] for i in images])

    def test_each_entry_links_to_the_detail_page(self):
        beetle = make_beetle(taxon=self.ips)
        image = self.client.get(self.URL, {"species_id": "T-IPS"}).json()["images"][0]
        self.assertEqual(image["detail_url"], reverse("beetle_detail", args=[beetle.id]))

    def test_image_without_a_thumbnail_has_no_thumb_url(self):
        make_beetle(taxon=self.ips)
        self.assertIsNone(self.client.get(self.URL, {"species_id": "T-IPS"}).json()["images"][0]["thumb_url"])

    def test_deleted_specimens_are_left_out(self):
        make_beetle(taxon=self.ips).delete()
        self.assertEqual(self.client.get(self.URL, {"species_id": "T-IPS"}).json(), {"images": []})

    def test_species_with_no_images_gets_an_empty_list(self):
        self.assertEqual(self.client.get(self.URL, {"species_id": "T-PLA"}).json(), {"images": []})

    def test_species_id_is_required(self):
        self.assertEqual(self.client.get(self.URL).status_code, 400)

    def test_post_is_not_allowed(self):
        self.assertEqual(self.client.post(self.URL, {"species_id": "T-IPS"}).status_code, 405)


class TaxonomyBrowserPageLoadTests(TaxonomyTestCase):
    """Guards for what the browser showed wrongly while the page loaded (see the page's own comments)."""

    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("taxonomy_browser")).content.decode()

    def test_styles_are_in_the_head_so_the_tree_is_never_painted_unstyled(self):
        # The tree is built by the inline script; if its sizing rules came after it, the small picture icons
        # beside each name were painted at full width for a moment. The icon is now the icon font's picture, which
        # base.html keeps hidden until the font is ready.
        page = self.page()
        self.assertLess(page.index(".tree-browse-icon {"), page.index("<body"))
        self.assertIn("fi fi-rr-picture tree-browse-icon", page)

    def test_expand_all_finds_the_toggle_buttons_where_they_are(self):
        # The toggle sits inside .tree-node-container; looking for it as a direct child opened nothing.
        page = self.page()
        self.assertIn('li.querySelector(":scope > .tree-node-container > .tree-toggle")', page)
        self.assertNotIn('li.querySelector(":scope > .tree-toggle")', page)

    def test_icon_fonts_are_preloaded_and_shown_together(self):
        page = self.page()
        self.assertIn('rel="preload" as="font"', page)
        self.assertIn("icons-ready", page)
