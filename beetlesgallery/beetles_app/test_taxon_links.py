"""Issue #419: a taxon link opens the taxonomy tree only as deep as that taxon."""
from django.template import Context, Template
from django.test import SimpleTestCase
from django.urls import reverse

from beetlesgallery.beetles_app.test_pages import PageTestCase
from beetlesgallery.beetles_app.testing import make_beetle, make_image, make_taxon


def render(snippet, **ctx):
    return Template("{% load beetle_tags %}" + snippet).render(Context(ctx))


class TaxonUrlTagTests(SimpleTestCase):
    def test_each_rank_links_only_as_deep_as_itself(self):
        base = reverse("taxonomy_browser")
        self.assertEqual(render("{% taxon_url 'Platypodinae' %}"), base + "?subfamily=Platypodinae")
        self.assertEqual(render("{% taxon_url 'Scolytinae' 'Xyleborini' %}"), base + "?subfamily=Scolytinae&amp;tribe=Xyleborini")
        self.assertEqual(render("{% taxon_url 'Scolytinae' 'Xyleborini' 'Xyleborus' %}"),
                         base + "?subfamily=Scolytinae&amp;tribe=Xyleborini&amp;genus=Xyleborus")
        self.assertEqual(render("{% taxon_url 'Scolytinae' '' 'Xyleborus' %}"), base + "?subfamily=Scolytinae&amp;genus=Xyleborus")


class DetailPageLinkTests(PageTestCase):
    def test_the_details_page_links_each_rank_at_its_own_depth(self):
        taxon = make_taxon(valid_species_id="77", subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus",
                           species="affinis", scientific_name="Xyleborus affinis")
        beetle = make_beetle(image=make_image(), taxon=taxon)
        self.client.force_login(self.user)
        page = self.client.get(reverse("beetle_detail", args=[beetle.pk])).content.decode()
        browser = reverse("taxonomy_browser")
        self.assertIn(f'href="{browser}?subfamily=Scolytinae"', page)
        self.assertIn(f'href="{browser}?subfamily=Scolytinae&amp;tribe=Xyleborini"', page)
        self.assertIn(f'href="{browser}?subfamily=Scolytinae&amp;tribe=Xyleborini&amp;genus=Xyleborus"', page)
        self.assertIn(f'href="{browser}?species=77"', page)   # the species itself still opens the species


class BrowserTests(PageTestCase):
    def test_the_browser_knows_how_to_open_at_a_branch(self):
        make_taxon(subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus", species="affinis")
        self.client.force_login(self.user)
        res = self.client.get(reverse("taxonomy_browser") + "?subfamily=Scolytinae")
        self.assertEqual(res.status_code, 200)
        page = res.content.decode()
        self.assertIn("focusBranchFromUrl", page)
        self.assertIn("li.dataset.level = node.level", page)
