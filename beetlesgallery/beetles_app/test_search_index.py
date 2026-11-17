"""
The search bar's free text is asked of each table on its own, so Postgres can use the trigram indexes (2026-11-17:
one OR across three tables read every specimen on every search). It must find exactly the same specimens as before:
the old condition is kept below as the reference.
"""
from django.db.models import Q
from django.test import TestCase

from beetlesgallery.beetles_app import utils
from beetlesgallery.beetles_app.models import Beetles, ImageAsset
from beetlesgallery.beetles_app.testing import make_beetle, make_image, make_taxon
from beetlesgallery.beetles_app.utils import FREE_TEXT_FIELDS, build_query_q


def old_free_text_q(val):
    """The free text condition before 2026-11-17."""
    q_any = Q()
    for f in FREE_TEXT_FIELDS:
        q_any |= Q(**{f"{f}__icontains": val})
    return q_any


class SearchResultsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        ips = make_taxon("T-IPS", scientific_name="Ips typographus", subfamily="Scolytinae", tribe="Ipini",
                         genus="Ips", species="typographus", original_genus="Bostrichus")
        xyl = make_taxon("T-XYL", scientific_name="Xyleborus affinis", subfamily="Scolytinae", genus="Xyleborus",
                         species="affinis", tribe=None)
        a = make_image(image_institution="University of Florida", photographer="Ana Smith",
                       image_email="ana@ufl.edu", photo_usage_statement="CC BY 4.0", image_notes="50% crop_area")
        b = make_image(image_institution="BYU", photographer=None, image_notes="Ips on label")
        c = make_image()
        make_beetle(image=a, taxon=ips, collection_country="USA", collection_stateProvince="Florida",
                    specimen_sex="female", aspect="dorsal", specimen_type_status="Holotype", alias_id="UF-001")
        make_beetle(image=a, taxon=xyl, collection_country="usa", specimen_notes="det. J. Hulcr", aspect="lateral")
        make_beetle(image=b, taxon=xyl, collection_country="Brazil", depicts_specimen="BYU-77",
                    depicts_name_verbatim="Xyleborus cf. affinis")
        make_beetle(image=c, collection_country="", specimen_notes="ips? check")
        make_beetle(image=c, taxon=ips, collection_country="Peru", is_deleted=True)
        orphan = make_beetle(collection_country="Chile", specimen_notes="no photo")
        Beetles.objects.filter(pk=orphan.pk).update(image_asset=None)
        ImageAsset.objects.filter(pk=c.pk).update(image_institution=None, photographer="")

    SEARCHES = [
        "Ips", "ips", "IPS", "typographus", "Xyleborus", "affinis", "Scolytinae", "Ipini", "Bostrichus",
        "Florida", "florida", "Ana", "ufl.edu", "CC BY", "50%", "crop_area", "%", "_", "USA", "Brazil", "Holotype",
        "UF-001", "BYU", "Hulcr", "dorsal", "female", "male", "cf.", "Chile", "photo", "zzz-nothing",
        "ip", "i", "Ips OR Brazil", "Ips NOT typographus", "NOT Ips", "NOT Florida", "Ips AND USA",
        "Xyleborus NOT Brazil", "NOT zzz-nothing", "(Ips OR Chile) NOT Peru", "country:USA Ips", "genus:Ips",
    ]

    def found(self, q_obj, qs=None):
        return set((qs if qs is not None else Beetles.objects.all()).filter(q_obj).values_list("pk", flat=True))

    def test_every_search_finds_the_same_specimens_as_before(self):
        original = utils.free_text_q
        for text in self.SEARCHES:
            with self.subTest(search=text):
                new_q, new_ignored = build_query_q(text)
                utils.free_text_q = old_free_text_q
                try:
                    old_q, old_ignored = build_query_q(text)
                finally:
                    utils.free_text_q = original
                self.assertEqual(new_ignored, old_ignored)
                self.assertEqual(self.found(new_q), self.found(old_q))
                live = Beetles.objects.filter(is_deleted=False).select_related("image_asset", "taxon")
                self.assertEqual(self.found(new_q, live), self.found(old_q, live))

    def test_the_searches_above_actually_find_things(self):
        # guard against a comparison of two empty sets
        self.assertEqual(len(self.found(build_query_q("Ips")[0])), 4)          # taxon, image notes, specimen notes
        self.assertEqual(len(self.found(build_query_q("NOT Ips")[0])), 2)      # the other two of the six
        self.assertEqual(len(self.found(build_query_q("Chile")[0])), 1)          # the specimen without an image


class SearchIndexCoverageTests(TestCase):
    """Every searched field of the two big tables has its index; one without would make Postgres read the table."""

    def index_fields(self, model):
        names = set()
        for index in model._meta.indexes:
            if index.name.startswith(("beetles_trgm_", "image_asset_trgm_")):
                names.add(index.expressions[0].source_expressions[0].source_expressions[0].name)
        return names

    def test_the_specimen_and_image_fields_are_indexed(self):
        self.assertEqual(self.index_fields(Beetles), set(utils.FREE_TEXT_BEETLE_FIELDS))
        self.assertEqual(self.index_fields(ImageAsset), set(utils.FREE_TEXT_IMAGE_FIELDS))

    def test_the_fields_are_split_by_table_without_losing_any(self):
        self.assertEqual(
            len(utils.FREE_TEXT_BEETLE_FIELDS) + len(utils.FREE_TEXT_IMAGE_FIELDS) + len(utils.FREE_TEXT_TAXON_FIELDS),
            len(FREE_TEXT_FIELDS))
