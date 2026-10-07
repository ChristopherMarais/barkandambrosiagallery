"""
The UI audit's specimen detail fixes (issue #618): the photo never overflows a narrow screen, every identifier
lives in one copyable card at the end of the page (not above the photo), the current ROI's box stands out, the
photo has one toolbar instead of two overlay buttons, there are two clear actions under it, empty fields are
hidden behind "Show all N fields", the verbatim name only shows when it differs, the cards are reordered, a DOI in
a note becomes a link, the specimen pager is a small bordered control beside the title, and the resolution is
rounded.
"""
from django.urls import reverse

from beetlesgallery.beetles_app.test_details_photo_controls import Outline
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon


class DetailUiPolishTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.taxon = make_taxon(scientific_name="Ips typographus", authority="Linnaeus")
        self.roi = make_beetle(
            image=make_image(image_file="tests/photo.jpg"), bbox="unvalidated", taxon=self.taxon,
            depicts_specimen="SP-9", depicts_name_verbatim="Ips typographus", alias_id="CAT-1",
            collection_country="Peru",
            specimen_notes="See dx.doi.org/10.17504/protocols.io.xyz for the protocol.",
        )

    def page(self, roi=None, user=None):
        self.client.force_login(user or self.user)
        response = self.client.get(reverse("beetle_detail", args=[(roi or self.roi).id]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    # --- detail-overflow ---------------------------------------------------------------------------------------

    def test_the_image_wrapper_and_its_flex_parent_can_shrink(self):
        page = self.page()
        elements = Outline(page).elements
        self.assertIn("min-w-0", elements["roi-photo"]["class"].split())
        self.assertIn(".roi-left-column { min-width: 0; }", page)
        self.assertIn(".roi-figure { max-width: 100%; min-width: 0; }", page)

    # --- detail-ids-first / detail-ids-copy ---------------------------------------------------------------------

    def test_ids_are_not_above_the_photo_and_every_identifier_is_in_one_card(self):
        page = self.page()
        self.assertNotIn("Region of Interest (ROI) ID:", page)
        self.assertLess(page.index('data-testid="roi-toolbar"'), page.index(">Identifiers<"))
        elements = Outline(page).elements
        for testid, value in (
            ("roi-id-value", str(self.roi.id)), ("image-id-value", str(self.roi.image_asset_id)),
            ("specimen-id-value", "SP-9"), ("valid-name-id-value", self.taxon.valid_species_id),
            ("alias-id-value", "CAT-1"),
        ):
            start = page.index(f'data-testid="{testid}"')
            self.assertIn(value, page[start:start + 400])
        # detail-ids-copy skipped by the owner (#618): only ROI ID, Image ID and Alias ID are copyable, same as
        # before this batch; Specimen ID and Valid Name ID stay plain text.
        for copy_testid in ("copy-roi-id", "copy-image-id", "copy-alias-id"):
            self.assertIn(copy_testid, elements)
        self.assertNotIn("copy-specimen-id", elements)
        self.assertNotIn("copy-valid-name-id", elements)
        self.assertIn("copy-id-btn", page)
        self.assertIn("Copied</span>", page)

    def test_an_id_without_a_value_is_hidden(self):
        bare = make_beetle(image=make_image(image_file="tests/photo.jpg"))
        elements = Outline(self.page(bare)).elements
        self.assertNotIn("copy-alias-id", elements)
        self.assertTrue(elements["specimen-id-value"]["class"].split().__contains__("field-hidden"))

    # --- detail-boxes (skipped by the owner, #618: no numbered tab) ------------------------------------------------

    def test_the_current_box_still_has_its_own_style_and_the_switch_still_works(self):
        page = self.page()
        self.assertNotIn("roi-box-tab", page)
        self.assertIn('class="roi-box roi-box-current"', page)
        self.assertIn("roi-boxes-btn", Outline(page).elements)

    # --- detail-toolbar / detail-roi-pager ------------------------------------------------------------------------

    def test_the_toolbar_sits_under_the_photo_with_40px_controls(self):
        page = self.page()
        elements = Outline(page).elements
        boxes_btn = elements["roi-boxes-btn"]
        self.assertEqual(boxes_btn["inside"][0], "roi-toolbar")   # beside the pager, not overlaid on the photo
        self.assertIn("h-10", boxes_btn["class"].split())
        self.assertIn("w-10", boxes_btn["class"].split())
        full = elements["roi-fullsize"]
        self.assertEqual(full["inside"][0], "roi-toolbar")
        self.assertIn("h-10", full["class"].split())
        self.assertEqual(full["href"], self.roi.display_url)
        self.assertEqual(full["target"], "_blank")

    def test_the_roi_pager_moved_into_the_toolbar(self):
        # Boxed siblings are ordered by id (not creation order), so this beetle may land either side of the one
        # made here; check whichever arrow(s) turn up, rather than assume which one.
        make_beetle(image=self.roi.image_asset, bbox="unvalidated")
        page = self.page()
        elements = Outline(page).elements
        found = [elements[t] for t in ("roi-pager-prev", "roi-pager-next") if t in elements]
        self.assertTrue(found)
        for link in found:
            self.assertEqual(link["inside"][0], "roi-toolbar")
            self.assertIn("h-10", link["class"].split())
        self.assertIn("ROI", page[page.index('data-testid="roi-toolbar"'):page.index('data-testid="roi-toolbar"') + 600])

    # --- detail-actions ------------------------------------------------------------------------------------------

    def test_download_and_edit_are_two_equal_48px_buttons_under_the_toolbar(self):
        page = self.page(user=self.staff)
        elements = Outline(page).elements
        edit = elements["open-annotation"]
        self.assertIn("h-12", edit["class"].split())
        self.assertIn("btn-secondary", edit["class"].split())
        start = page.index('data-testid="detail-actions"')
        actions = page[start:page.index("</div>", start)]
        self.assertIn("Download", actions)
        self.assertIn("btn-primary h-12", actions)
        self.assertNotIn("Download Original Image", page)

    def test_a_member_without_boxes_access_sees_only_download(self):
        page = self.page(user=self.user)
        self.assertNotIn("open-annotation", Outline(page).elements)
        start = page.index('data-testid="detail-actions"')
        actions = page[start:page.index("</div>", start)]
        self.assertIn("col-span-2", actions)

    # --- detail-empty --------------------------------------------------------------------------------------------

    def test_empty_fields_are_hidden_behind_show_all_fields(self):
        bare = make_beetle(image=make_image(image_file="tests/photo.jpg"))
        page = self.page(bare)
        self.assertIn("show-all-fields", page)
        self.assertIn("field-hidden", page)
        self.assertIn("Show all 11 fields", page)   # the Taxonomy card
        self.assertIn(":has(.field-hidden)", page)

    def test_a_fully_filled_card_has_no_show_all_button(self):
        # Validation's two rows are always filled with a status, so that card never gets the toggle at all.
        page = self.page()
        validation = page[page.index(">Validation<"):page.index(">Technical<")]
        self.assertNotIn("show-all-fields", validation)

    # --- detail-verbatim -----------------------------------------------------------------------------------------

    def test_verbatim_name_only_shown_when_it_differs_from_the_accepted_name(self):
        same = self.page()
        self.assertNotIn("As written on the label", same)

        differs = make_beetle(
            image=make_image(image_file="tests/photo.jpg"), taxon=self.taxon, depicts_name_verbatim="Ips typo.",
        )
        page = self.page(differs)
        self.assertIn("As written on the label", page)
        self.assertIn("Ips typo.", page)

    # --- detail-order --------------------------------------------------------------------------------------------

    def test_cards_are_ordered_taxonomy_then_specimen_then_attribution_then_validation_then_technical_then_identifiers(self):
        page = self.page()
        order = [">Taxonomy<", ">Specimen and Collection<", ">Attribution<", ">Validation<", ">Technical<", ">Identifiers<"]
        positions = [page.index(tag) for tag in order]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn(">Identity<", page)
        self.assertNotIn(">Image Technical<", page)
        self.assertNotIn("Collection / Specimen", page)

    # --- detail-doi ----------------------------------------------------------------------------------------------

    def test_a_doi_in_a_note_becomes_a_link(self):
        page = self.page()
        self.assertIn('<a href="https://dx.doi.org/10.17504/protocols.io.xyz"', page)
        self.assertIn("overflow-wrap: anywhere", page)

    # --- detail-prevnext -----------------------------------------------------------------------------------------

    def test_the_specimen_pager_is_a_small_bordered_control_beside_the_title(self):
        make_beetle(image=self.roi.image_asset, bbox="unvalidated")
        page = self.page()
        pager = Outline(page).elements["specimen-pager"]
        self.assertIn("h-9", pager["class"].split())
        self.assertIn("border", pager["class"].split())
        self.assertLess(page.index("page-title"), page.index('data-testid="specimen-pager"'))

    def test_no_specimen_pager_without_a_box(self):
        bare = make_beetle(image=make_image(image_file="tests/photo.jpg"))
        self.assertNotIn("specimen-pager", Outline(self.page(bare)).elements)

    # --- detail-resolution ---------------------------------------------------------------------------------------

    def test_resolution_is_rounded_to_two_decimals_with_the_full_value_on_hover(self):
        roi = make_beetle(image=make_image(image_file="tests/photo.jpg", resolution_in_ppmm="2.8346"))
        page = self.page(roi)
        self.assertIn('title="2.8346 px/mm">2.83 px/mm</span>', page)

    # --- detail-more -----------------------------------------------------------------------------------------------

    def test_other_images_heading_shows_a_count(self):
        second = make_beetle(image=make_image(image_file="tests/photo2.jpg"), depicts_specimen="SP-9")
        page = self.page(second)
        self.assertIn("Other images of this specimen (1)", page)
        self.assertNotIn("More images of the same specimen", page)
