"""
Logic tests for the image-browser filter dropdowns: filter_beetles_queryset and
FILTERS_CONFIG in utils.py (issue #206, part 2).

The fixture has five specimens across three images, chosen so every filter type
has something to include and something to leave out:

    image A  validated, UF, 2020-05-17, 5 MB, 10 ppmm, multiple individuals
        a1  Ips        USA     m   box (validated)
        a2  Xyleborus  USA     f   box (validated)
    image B  not validated (has an unvalidated box), BYU, no date, 20 MB, 2 ppmm
        b1  Ips        Brazil  m   no box
        b2  (no taxon) (none)      box (unvalidated)
    image C  no boxes at all, nothing filled in
        c1  (no taxon) ""(blank)   no box
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from beetlesgallery.beetles_app.models import Beetles
from beetlesgallery.beetles_app.testing import make_beetle, make_image, make_taxon
from beetlesgallery.beetles_app.utils import FILTERS_CONFIG, filter_beetles_queryset

MB = 1024 * 1024


class FilterFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        ips = make_taxon(
            "T-IPS", scientific_name="Ips typographus",
            subfamily="Scolytinae", tribe="Ipini", genus="Ips", species="typographus",
        )
        xyl = make_taxon(
            "T-XYL", scientific_name="Xyleborus affinis",
            subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus", species="affinis",
        )
        img_a = make_image(
            image_institution="UF", image_date_taken=date(2020, 5, 17),
            image_size_bytes=5 * MB, resolution_in_ppmm=Decimal("10"),
            image_has_multiple_individuals=True,
        )
        img_b = make_image(
            image_institution="BYU", image_size_bytes=20 * MB,
            resolution_in_ppmm=Decimal("2"), image_has_multiple_individuals=False,
        )
        img_c = make_image()
        cls.rows = {
            "a1": make_beetle(image=img_a, taxon=ips, bbox="validated",
                              collection_country="USA", specimen_sex="m"),
            "a2": make_beetle(image=img_a, taxon=xyl, bbox="validated",
                              collection_country="USA", specimen_sex="f"),
            "b1": make_beetle(image=img_b, taxon=ips,
                              collection_country="Brazil", specimen_sex="m"),
            "b2": make_beetle(image=img_b, bbox="unvalidated"),
            "c1": make_beetle(image=img_c, collection_country=""),
        }

    def names(self, filters=None, **kwargs):
        """Names of the fixture rows that survive filter_beetles_queryset."""
        qs = filter_beetles_queryset(Beetles.objects.all(), filters or {}, **kwargs)
        by_pk = {row.pk: name for name, row in self.rows.items()}
        return {by_pk[pk] for pk in qs.values_list("pk", flat=True)}


ALL = {"a1", "a2", "b1", "b2", "c1"}


class DropdownFilterTests(FilterFixtureMixin, TestCase):
    """Plain "pick values from a list" filters on the specimen or its image."""

    def test_no_filters_returns_everything(self):
        self.assertEqual(self.names(), ALL)

    def test_unknown_filter_is_ignored(self):
        self.assertEqual(self.names({"colour": ["red"]}), ALL)

    def test_specimen_field(self):
        self.assertEqual(self.names({"country": ["USA"]}), {"a1", "a2"})
        self.assertEqual(self.names({"country": ["Brazil"]}), {"b1"})
        self.assertEqual(self.names({"sex": ["m"]}), {"a1", "b1"})

    def test_several_values_for_one_filter_are_ored(self):
        self.assertEqual(self.names({"country": ["USA", "Brazil"]}), {"a1", "a2", "b1"})

    def test_image_field(self):
        self.assertEqual(self.names({"institution": ["UF"]}), {"a1", "a2"})
        self.assertEqual(self.names({"institution": ["BYU"]}), {"b1", "b2"})

    def test_date_field(self):
        self.assertEqual(self.names({"date_taken": ["2020-05-17"]}), {"a1", "a2"})

    def test_none_finds_null_and_blank_values(self):
        # b2's country is NULL, c1's is "" - both count as "no country".
        self.assertEqual(self.names({"country": ["None"]}), {"b2", "c1"})

    def test_none_can_be_mixed_with_real_values(self):
        self.assertEqual(self.names({"country": ["USA", "None"]}), {"a1", "a2", "b2", "c1"})

    def test_none_on_a_date_finds_images_without_one(self):
        self.assertEqual(self.names({"date_taken": ["None"]}), {"b1", "b2", "c1"})

    def test_different_filters_are_anded(self):
        self.assertEqual(self.names({"country": ["USA"], "sex": ["m"]}), {"a1"})
        self.assertEqual(self.names({"country": ["USA"], "sex": ["x"]}), set())

    def test_exclude_param_skips_that_filter(self):
        # Used to build a dropdown's own options: it must not filter itself.
        filters = {"country": ["USA"], "sex": ["m"]}
        self.assertEqual(self.names(filters, exclude_param="country"), {"a1", "b1"})
        self.assertEqual(self.names(filters, exclude_param="sex"), {"a1", "a2"})


class TaxonomyFilterTests(FilterFixtureMixin, TestCase):
    """Taxonomy filters go through the specimen's linked Taxon."""

    def test_match_by_rank(self):
        self.assertEqual(self.names({"genus": ["Ips"]}), {"a1", "b1"})
        self.assertEqual(self.names({"subfamily": ["Scolytinae"]}), {"a1", "a2", "b1"})
        self.assertEqual(self.names({"tribe": ["Xyleborini"]}), {"a2"})

    def test_value_that_matches_no_taxon_returns_nothing(self):
        self.assertEqual(self.names({"genus": ["Nonexistent"]}), set())

    def test_none_finds_specimens_with_no_taxon(self):
        self.assertEqual(self.names({"genus": ["None"]}), {"b2", "c1"})

    def test_none_can_be_mixed_with_real_values(self):
        self.assertEqual(self.names({"genus": ["Ips", "None"]}), {"a1", "b1", "b2", "c1"})


class YesNoFilterTests(FilterFixtureMixin, TestCase):
    """Boolean and box-status filters."""

    def test_image_validated(self):
        self.assertEqual(self.names({"image_validated": ["Yes"]}), {"a1", "a2"})
        self.assertEqual(self.names({"image_validated": ["No"]}), {"b1", "b2", "c1"})

    def test_yes_and_no_together_means_no_filter(self):
        self.assertEqual(self.names({"image_validated": ["Yes", "No"]}), ALL)

    def test_unrecognised_answer_is_ignored(self):
        self.assertEqual(self.names({"image_validated": ["maybe"]}), ALL)

    def test_multiple_individuals(self):
        self.assertEqual(self.names({"multiple": ["Yes"]}), {"a1", "a2"})
        self.assertEqual(self.names({"multiple": ["No"]}), {"b1", "b2"})

    def test_has_bounding_boxes(self):
        # Yes: specimens that are themselves a box.
        self.assertEqual(self.names({"has_rois": ["Yes"]}), {"a1", "a2", "b2"})
        # No: specimens whose whole image has no boxes (b1 shares an image with box b2).
        self.assertEqual(self.names({"has_rois": ["No"]}), {"c1"})
        self.assertEqual(self.names({"has_rois": ["Yes", "No"]}), ALL)

    def test_has_all_rois_validated(self):
        # Yes: boxes on images where every box is validated (b2's image is not).
        self.assertEqual(self.names({"all_rois_val": ["Yes"]}), {"a1", "a2"})
        # No: anything on an image that has at least one unvalidated box.
        self.assertEqual(self.names({"all_rois_val": ["No"]}), {"b1", "b2"})


class RangeFilterTests(FilterFixtureMixin, TestCase):
    """Image size (in MB) and resolution ranges."""

    def test_size_minimum_and_maximum(self):
        self.assertEqual(self.names(size_min="10"), {"b1", "b2"})
        self.assertEqual(self.names(size_max="10"), {"a1", "a2"})
        self.assertEqual(self.names(size_min="1", size_max="10"), {"a1", "a2"})

    def test_images_without_a_size_are_excluded_by_a_size_range(self):
        self.assertNotIn("c1", self.names(size_min="0"))
        self.assertNotIn("c1", self.names(size_max="1000"))

    def test_resolution_minimum_and_maximum(self):
        self.assertEqual(self.names(res_min="5"), {"a1", "a2"})
        self.assertEqual(self.names(res_max="5"), {"b1", "b2"})

    def test_invalid_or_empty_numbers_are_ignored(self):
        for kwargs in ({"size_min": "abc"}, {"res_max": "fast"}, {"size_min": ""}, {"res_min": None}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.names(**kwargs), ALL)

    def test_ranges_combine_with_dropdown_filters(self):
        self.assertEqual(self.names({"country": ["USA"]}, res_min="5"), {"a1", "a2"})
        self.assertEqual(self.names({"country": ["Brazil"]}, res_min="5"), set())


class FiltersConfigTests(TestCase):
    """FILTERS_CONFIG drives both the dropdowns and the queries; keep it consistent."""

    KNOWN_TYPES = {"db", "bool", "ref", "custom_has_rois", "custom_all_rois_val", "custom_has_type_status"}

    def test_each_entry_has_the_keys_the_views_read(self):
        for cfg in FILTERS_CONFIG:
            with self.subTest(param=cfg.get("param")):
                for key in ("category", "param", "type", "field", "label"):
                    self.assertIn(key, cfg)
                    self.assertIsInstance(cfg[key], str)

    def test_params_are_unique(self):
        params = [cfg["param"] for cfg in FILTERS_CONFIG]
        self.assertEqual(len(params), len(set(params)))

    def test_types_are_all_handled_by_the_filter_function(self):
        for cfg in FILTERS_CONFIG:
            with self.subTest(param=cfg["param"]):
                self.assertIn(cfg["type"], self.KNOWN_TYPES)

    def test_every_configured_field_exists_on_the_model(self):
        for cfg in FILTERS_CONFIG:
            with self.subTest(param=cfg["param"]):
                if cfg["type"] in ("db", "bool"):
                    Beetles.objects.filter(**{f"{cfg['field']}__isnull": True}).exists()
                elif cfg["type"] == "ref":
                    Beetles.objects.filter(**{f"taxon__{cfg['field']}__isnull": True}).exists()
