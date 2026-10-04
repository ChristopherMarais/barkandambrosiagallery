"""
Behaviour tests for linking pages together (issue #185):
  - taxonomy terms on the gallery cards and the specimen detail page link to
    that taxon's node in the Taxonomy Browser
  - the specimen detail page offers a "Classify with AI" link, but only for
    specimens that have not been identified yet
  - the specimen detail page's toolbar links staff to the Annotation Tool,
    focused on that image
"""
from unittest import expectedFailure

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon

TAXONOMY_URL = reverse("taxonomy_browser")


class GalleryTaxonomyLinkTests(PageBehaviourCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.taxon = make_taxon(
            "T-IPS", scientific_name="Ips typographus",
            subfamily="Scolytinae", tribe="Ipini", genus="Ips", species="typographus",
        )
        cls.beetle = make_beetle(taxon=cls.taxon)
        cls.unidentified = make_beetle()

    def gallery_html(self):
        return self.client.get("/beetles/").content.decode()

    def test_taxonomy_terms_link_to_that_species_in_the_taxonomy_browser(self):
        html = self.gallery_html()
        self.assertIn(f'{TAXONOMY_URL}?species=T-IPS', html)
        # higher ranks open the tree only as deep as themselves (#419)
        self.assertIn(f'{TAXONOMY_URL}?subfamily=Scolytinae"', html)
        self.assertIn(f'{TAXONOMY_URL}?subfamily=Scolytinae&amp;tribe=Ipini&amp;genus=Ips"', html)

    def test_unidentified_specimens_get_no_taxonomy_link(self):
        # nothing in the fixture links to a blank species id
        self.assertNotIn(f'{TAXONOMY_URL}?species=', self.gallery_html().replace(f'{TAXONOMY_URL}?species=T-IPS', ''))


class DetailTaxonomyLinkTests(PageBehaviourCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.taxon = make_taxon("T-XYL", scientific_name="Xyleborus affinis", genus="Xyleborus", species="affinis")

    def detail_html(self, beetle):
        self.client.force_login(self.user)
        response = self.client.get(reverse("beetle_detail", args=[beetle.id]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_identified_specimen_taxonomy_fields_link_to_its_species(self):
        beetle = make_beetle(taxon=self.taxon)
        html = self.detail_html(beetle)
        self.assertIn(f'{TAXONOMY_URL}?species=T-XYL', html)
        self.assertIn("Xyleborus affinis", html)  # scientific name still shown, just as a link now

    def test_unidentified_specimen_has_no_taxonomy_link(self):
        beetle = make_beetle()
        html = self.detail_html(beetle)
        self.assertNotIn(f'{TAXONOMY_URL}?species=', html)


class DetailClassifyLinkTests(PageBehaviourCase):
    """The AI classifier link only shows for specimens not yet identified, so
    staff don't burn GPU time reclassifying something already known."""

    def detail_html(self, beetle, user=None):
        self.client.force_login(user or self.user)
        return self.client.get(reverse("beetle_detail", args=[beetle.id])).content.decode()

    def test_unidentified_specimen_shows_a_classify_link(self):
        image = make_image(image_file=SimpleUploadedFile("specimen.jpg", b"fake image bytes"))
        beetle = make_beetle(image=image)
        html = self.detail_html(beetle)
        self.assertIn("Classify with AI", html)
        self.assertIn(f'{reverse("tool_classify")}?image_url=', html)

    def test_identified_specimen_has_no_classify_link(self):
        taxon = make_taxon("T-PLA", scientific_name="Platypus cylindrus")
        image = make_image(image_file=SimpleUploadedFile("specimen.jpg", b"fake image bytes"))
        beetle = make_beetle(image=image, taxon=taxon)
        self.assertNotIn("Classify with AI", self.detail_html(beetle))

    def test_specimen_with_no_image_file_has_no_classify_link(self):
        # make_image() defaults to no actual image_file, matching "No image found." on the page.
        beetle = make_beetle(image=make_image())
        self.assertNotIn("Classify with AI", self.detail_html(beetle))

    @expectedFailure
    def test_specimen_with_no_image_at_all_does_not_crash_the_page(self):
        """KNOWN BUG, pre-existing and unrelated to #185: detail.html has
        {% url 'create_specimen_for_image' beetle.image_asset.id %} inside a <script>
        block with no guard. For a beetle with image_asset=None this is a Django
        template tag evaluated at render time (not just JS), so it raises
        NoReverseMatch and the whole page 500s instead of just hiding the
        "add specimen" control. Remove @expectedFailure when that's guarded."""
        from beetlesgallery.beetles_app.models import Beetles
        beetle = Beetles.objects.create()  # no image_asset at all
        self.assertNotIn("Classify with AI", self.detail_html(beetle))


class DetailAnnotateLinkTests(PageBehaviourCase):
    """The pencil-adjacent link on the detail page opens the full Annotation
    Tool focused on this specimen's image; only staff see it."""

    def test_staff_sees_a_link_into_the_annotation_tool_for_this_image(self):
        beetle = make_beetle()
        self.client.force_login(self.staff)

        html = self.client.get(reverse("beetle_detail", args=[beetle.id])).content.decode()

        self.assertIn(f'{reverse("tool_annotate")}?image={beetle.image_asset_id}', html)

    def test_non_staff_does_not_see_the_annotation_link(self):
        beetle = make_beetle()
        self.client.force_login(self.user)

        html = self.client.get(reverse("beetle_detail", args=[beetle.id])).content.decode()

        self.assertNotIn("Open in Annotation Tool", html)
