"""
The beetle-pathogen page is called "Pathogens" (it was "Interactions"), with its own icon: a beetle on its back, legs
up, X-eyed. Its address stays /interactions/ so old links keep working.
"""
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import reverse

from beetlesgallery.beetles_app import areas

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
ICON = '<i class="fi fi-rr-bug-slash text-xl text-gray-700 w-8 text-center"></i>'


def read(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


class PathogensNameTests(SimpleTestCase):
    def test_the_address_stays(self):
        self.assertEqual(reverse("interactions_preview"), "/interactions/")

    def test_the_rail_and_the_drawer_say_pathogens(self):
        base = read("base.html")
        self.assertIn('<span class="font-medium text-gray-700 ml-1">Pathogens</span>', base)   # the drawer
        self.assertIn('title="Pathogens">', base)                                              # the rail
        self.assertNotIn("Ecological Interactions", base)
        self.assertNotIn(">Interactions<", base)

    def test_the_phone_header_and_the_back_links_say_pathogens(self):
        tags = (Path(__file__).resolve().parent / "templatetags" / "beetle_tags.py").read_text(encoding="utf-8")
        self.assertIn('("/interactions/", "Pathogens"),', tags)
        for name in ("interaction_review.html", "upload_interaction_proposals.html", "upload_interactions.html"):
            self.assertIn('label="Pathogens" %}', read("beetles/" + name), name)

    def test_the_access_area_is_named_for_it(self):
        source = Path(areas.__file__).read_text(encoding="utf-8")
        self.assertIn('("Pathogens", [', source)
        self.assertIn('(INTERACTIONS, "Pathogen records",', source)


class CrossedOutBeetleIconTests(SimpleTestCase):
    def test_both_menus_and_the_about_box_use_it(self):
        self.assertEqual(read("base.html").count(ICON), 2)
        self.assertIn('<i class="fi fi-rr-bug-slash text-xl text-gray-700"></i>', read("beetles/interactions_preview.html"))
        for name in ("base.html", "beetles/interactions_preview.html"):
            self.assertNotIn("fi-rr-diagram-project", read(name))
            self.assertNotIn("icon_dead_beetle", read(name))
