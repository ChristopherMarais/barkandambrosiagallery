"""
Behaviour tests for the Image Browser (/beetles/): search, filters, pagination,
the AJAX partial and soft-deleted rows (issue #206, part 3).

The view shows one card per image, so every count below is a count of images.
"""
import re
from datetime import date

from django.urls import reverse

from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon


class GalleryTestCase(PageBehaviourCase):
    URL = "/beetles/"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        ips = make_taxon("T-IPS", scientific_name="Ips typographus", subfamily="Scolytinae", genus="Ips")
        xyl = make_taxon("T-XYL", scientific_name="Xyleborus affinis", subfamily="Scolytinae", genus="Xyleborus")
        img_usa = make_image(image_institution="UF", image_date_taken=date(2020, 5, 17))
        img_brazil = make_image(image_institution="BYU")
        cls.usa = make_beetle(image=img_usa, taxon=ips, bbox="validated", collection_country="USA")
        cls.usa_second = make_beetle(image=img_usa, taxon=xyl, bbox="validated", collection_country="USA")
        cls.brazil = make_beetle(image=img_brazil, taxon=xyl, collection_country="Brazil")

    def get(self, query="", **extra):
        return self.client.get(f"{self.URL}?{query}", **extra)

    def image_ids(self, response):
        """Ids of the images shown on the page, in display order."""
        return [b.image_asset_id for b in response.context["beetles"]]


class GallerySearchTests(GalleryTestCase):
    def test_no_search_lists_every_image_once(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        # Three specimens, but two of them share an image.
        self.assertEqual(response.context["total_matches"], 2)
        self.assertCountEqual(
            self.image_ids(response), [self.usa.image_asset_id, self.brazil.image_asset_id]
        )

    def test_field_search(self):
        response = self.get("q=country:Brazil")
        self.assertEqual(self.image_ids(response), [self.brazil.image_asset_id])
        self.assertEqual(response.context["q"], "country:Brazil")

    def test_free_text_search(self):
        response = self.get("q=typographus")
        self.assertEqual(self.image_ids(response), [self.usa.image_asset_id])

    def test_search_with_no_matches_shows_an_empty_page(self):
        response = self.get("q=country:Atlantis")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_matches"], 0)
        self.assertEqual(self.image_ids(response), [])

    def test_unknown_search_field_is_reported_not_fatal(self):
        response = self.get("q=colour:red")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["ignored_tokens"], ["unknown field 'colour'"])
        self.assertEqual(response.context["total_matches"], 2)

    def test_search_combines_with_filters(self):
        response = self.get("q=xyleborus&country=USA")
        self.assertEqual(self.image_ids(response), [self.usa.image_asset_id])


class GalleryFilterTests(GalleryTestCase):
    def test_dropdown_filter(self):
        response = self.get("country=USA")
        self.assertEqual(self.image_ids(response), [self.usa.image_asset_id])
        self.assertEqual(response.context["selected_filters"], {"country": ["USA"]})

    def test_several_values_are_ored(self):
        response = self.get("country=USA&country=Brazil")
        self.assertEqual(response.context["total_matches"], 2)

    def test_blank_filter_values_are_ignored(self):
        response = self.get("country=&institution=%20")
        self.assertEqual(response.context["selected_filters"], {})
        self.assertEqual(response.context["total_matches"], 2)

    def test_taxonomy_filter(self):
        response = self.get("genus=Ips")
        self.assertEqual(self.image_ids(response), [self.usa.image_asset_id])

    def test_yes_no_filter(self):
        response = self.get("image_validated=Yes")
        self.assertEqual(self.image_ids(response), [self.usa.image_asset_id])

    def test_range_filters_are_passed_through_to_the_page(self):
        response = self.get("size_min=1&res_max=5")
        self.assertEqual(response.context["size_min"], "1")
        self.assertEqual(response.context["res_max"], "5")

    def test_filter_dropdowns_list_the_available_options(self):
        groups = self.get("country=USA").context["filter_groups"]
        self.assertTrue(groups)  # built on a normal page load

    def test_default_view_dropdowns_are_cached(self):
        self.get()
        make_beetle(collection_country="Peru")  # added after the dropdowns were cached
        cached = self.get().context["filter_groups"]
        self.assertNotIn("Peru", repr(cached))


class GalleryPaginationTests(GalleryTestCase):
    def test_per_page_limits_the_cards_and_splits_pages(self):
        response = self.get("per_page=1")
        self.assertEqual(len(response.context["beetles"]), 1)
        self.assertEqual(response.context["paginator"].num_pages, 2)
        self.assertTrue(response.context["is_paginated"])

    def test_second_page(self):
        first = self.image_ids(self.get("per_page=1&page=1"))
        second = self.image_ids(self.get("per_page=1&page=2"))
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first, second)

    def test_out_of_range_page_shows_the_last_page(self):
        response = self.get("per_page=1&page=99")
        self.assertEqual(response.context["beetles"].number, 2)

    def test_non_numeric_page_shows_the_first_page(self):
        response = self.get("per_page=1&page=abc")
        self.assertEqual(response.context["beetles"].number, 1)

    def test_non_numeric_per_page_falls_back_to_twelve(self):
        self.assertEqual(self.get("per_page=lots").context["per_page"], 12)

    def test_page_selector_lists_every_page(self):
        html = str(self.get("per_page=1").context["page_options_html"])
        self.assertIn('<option value="1" selected>1</option>', html)
        self.assertIn('<option value="2">2</option>', html)


class GallerySiblingTests(GalleryTestCase):
    def test_card_knows_how_many_specimens_share_its_image(self):
        cards = {b.image_asset_id: b for b in self.get().context["beetles"]}
        self.assertEqual(cards[self.usa.image_asset_id].siblings_count, 2)
        self.assertEqual(cards[self.brazil.image_asset_id].siblings_count, 1)

    def test_card_flags_when_siblings_disagree(self):
        card = {b.image_asset_id: b for b in self.get().context["beetles"]}[self.usa.image_asset_id]
        self.assertTrue(card.has_multiple_genus)  # Ips and Xyleborus on one image
        self.assertFalse(card.has_multiple_country)  # both USA


class GallerySoftDeleteTests(GalleryTestCase):
    def test_soft_deleted_specimens_are_hidden(self):
        self.brazil.delete()
        response = self.get()
        self.assertEqual(self.image_ids(response), [self.usa.image_asset_id])

    def test_a_deleted_sibling_is_not_counted(self):
        self.usa_second.delete()
        cards = {b.image_asset_id: b for b in self.get().context["beetles"]}
        self.assertEqual(cards[self.usa.image_asset_id].siblings_count, 1)


class GalleryAjaxTests(GalleryTestCase):
    def test_ajax_request_gets_only_the_results_fragment(self):
        response = self.get("per_page=1", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "beetles/includes/gallery_results.html")
        self.assertTemplateNotUsed(response, "beetles/image_browser.html")
        # The heavy dropdowns are skipped on page flips.
        self.assertIsNone(response.context["filter_groups"])

    def test_normal_request_gets_the_full_page(self):
        self.assertTemplateUsed(self.get(), "beetles/image_browser.html")


class LandingPageTests(GalleryTestCase):
    def test_counts_reflect_the_data(self):
        response = self.client.get(reverse("image_browser"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_images"], 2)
        self.assertEqual(response.context["total_species"], 2)
        self.assertEqual(response.context["total_genera"], 2)

    def test_deleted_images_are_not_counted(self):
        self.brazil.image_asset.delete()
        self.assertEqual(self.client.get(reverse("image_browser")).context["total_images"], 1)


class DetailHeadingTests(PageBehaviourCase):
    """The detail page is titled like the gallery card that links to it (#112)."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def heading(self, beetle):
        html = self.client.get(reverse("beetle_detail", args=[beetle.id])).content.decode()
        return re.search(r"<h1[^>]*>\s*(.*?)\s*</h1>", html, re.S).group(1)

    def test_identified_specimen_uses_the_scientific_name(self):
        taxon = make_taxon(genus="Ips", species="typographus", scientific_name="Ips typographus")
        beetle = make_beetle(taxon=taxon, depicts_specimen="Vial_23246", depicts_name_verbatim="Ips sp.")
        self.assertEqual(self.heading(beetle), "Ips typographus")

    def test_unlinked_specimen_is_unidentified(self):
        beetle = make_beetle(depicts_specimen="Vial_1")
        self.assertEqual(self.heading(beetle), "Unidentified")


class GallerySortTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        from beetlesgallery.beetles_app.testing import make_beetle, make_taxon
        self.client.force_login(self.user)
        self.a = make_beetle(taxon=make_taxon(valid_species_id="1", genus="Zeta", species="a", scientific_name="Zeta a"))
        self.b = make_beetle(taxon=make_taxon(valid_species_id="2", genus="Alpha", species="b", scientific_name="Alpha b"))
        self.none = make_beetle()

    def order(self, sort):
        response = self.client.get(reverse("beetles_image_browser"), {"sort": sort})
        self.assertEqual(response.status_code, 200)
        return [b.id for b in response.context["beetles"]]

    def test_species_sort_both_ways_with_unidentified_last(self):
        self.assertEqual(self.order("species"), [self.b.id, self.a.id, self.none.id])
        self.assertEqual(self.order("species_desc"), [self.a.id, self.b.id, self.none.id])

    def test_every_sort_loads_and_keeps_one_row_per_image(self):
        from beetlesgallery.beetles_app.views import GALLERY_SORTS
        for key in GALLERY_SORTS:
            with self.subTest(sort=key):
                self.assertEqual(len(self.order(key)), 3)

    def test_unknown_sort_is_ignored_and_the_choices_are_offered(self):
        self.assertEqual(len(self.order("bogus")), 3)
        self.assertContains(self.client.get(reverse("beetles_image_browser"), {"sort": "species"}), 'value="species" selected')
