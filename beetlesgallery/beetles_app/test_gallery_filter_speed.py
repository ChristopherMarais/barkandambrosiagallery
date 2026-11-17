"""
The Image Browser's filter dropdowns are worked out in one query, plus one per active filter (2026-11-17: a search
for "Ips" took ~30 s because each of the ~44 queries repeated the whole search). The dropdowns must come out exactly
as they did before: same options, same order, same "None" entries. The old code is kept below as the reference.
"""
from collections import defaultdict
from datetime import date

from django.db.models import Q
from django.test import TestCase

from beetlesgallery.beetles_app.models import Beetles
from beetlesgallery.beetles_app.testing import make_beetle, make_image, make_taxon
from beetlesgallery.beetles_app.utils import FILTERS_CONFIG, build_query_q, filter_beetles_queryset
from beetlesgallery.beetles_app.views import _build_gallery_filter_context


def reference_filter_context(base_search_qs, active_filters):
    """The dropdowns as built before 2026-11-17: two queries per dropdown."""
    grouped_filters = defaultdict(list)
    categories = list(dict.fromkeys(cfg["category"] for cfg in FILTERS_CONFIG))
    for cfg in FILTERS_CONFIG:
        param = cfg["param"]
        ctx_qs = filter_beetles_queryset(base_search_qs, active_filters, exclude_param=param)
        options = []
        has_na = False
        if cfg["type"] == "db":
            field = cfg["field"]
            strict = field in ["image_asset__image_date_taken", "image_asset__resolution_in_ppmm"]
            raw = ctx_qs.exclude(**{f"{field}__isnull": True})
            if not strict:
                raw = raw.exclude(**{field: ""})
            for o in raw.values_list(field, flat=True).distinct().order_by(field):
                val = o.strftime("%Y-%m-%d") if hasattr(o, "strftime") else str(o).strip()
                if val and val not in options:
                    options.append(val)
            if strict:
                has_na = ctx_qs.filter(**{f"{field}__isnull": True}).exists()
            else:
                has_na = ctx_qs.filter(Q(**{f"{field}__isnull": True}) | Q(**{field: ""})).exists()
        elif cfg["type"] == "bool":
            options = ["Yes", "No"]
            has_na = ctx_qs.filter(**{f"{cfg['field']}__isnull": True}).exists()
        elif cfg["type"] == "custom_has_type_status":
            options = ["Yes", "No"]
        elif cfg["type"] == "ref":
            field_name = f"taxon__{cfg['field']}"
            raw = ctx_qs.exclude(taxon__isnull=True).exclude(**{f"{field_name}__isnull": True}) \
                        .exclude(**{field_name: ""}).values_list(field_name, flat=True).distinct().order_by(field_name)
            for o in raw:
                val = str(o).strip()
                if val and val not in options:
                    options.append(val)
            has_na = ctx_qs.filter(Q(taxon__isnull=True) | Q(**{f"{field_name}__isnull": True})
                                   | Q(**{field_name: ""})).exists()
        if has_na:
            options.insert(0, "None")
        if options or active_filters.get(param):
            grouped_filters[cfg["category"]].append({
                "param": param, "label": cfg["label"], "options": options,
                "selected": active_filters.get(param, []),
            })
    return [(cat, grouped_filters[cat]) for cat in categories if grouped_filters[cat]]


class FilterDropdownTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        ips = make_taxon("T-IPS", scientific_name="Ips typographus", subfamily="Scolytinae", tribe="Ipini",
                         genus="Ips", species="typographus", authority="Linnaeus", authority_year="1758")
        ips2 = make_taxon("T-IPS2", scientific_name="Ips avulsus", subfamily="Scolytinae", tribe="Ipini",
                          genus="Ips", species="avulsus", authority="", original_genus="Tomicus")
        xyl = make_taxon("T-XYL", scientific_name="Xyleborus affinis", subfamily="Scolytinae", tribe="Xyleborini",
                         genus="Xyleborus", species="affinis")
        a = make_image(image_institution="UF", photographer="Ana", image_date_taken=date(2020, 5, 17),
                       is_validated=True, image_has_multiple_individuals=False)
        b = make_image(image_institution="BYU", photographer="", image_date_taken=date(2019, 1, 2),
                       image_has_multiple_individuals=True)
        c = make_image(image_institution="", photographer="bob", photo_usage_statement="CC BY")
        make_beetle(image=a, taxon=ips, bbox="validated", collection_country="USA", specimen_sex="female",
                    aspect="dorsal", specimen_type_status="Holotype")
        make_beetle(image=a, taxon=ips2, bbox="unvalidated", collection_country="usa ", specimen_sex="",
                    aspect="lateral")
        make_beetle(image=b, taxon=xyl, collection_country="Brazil", collection_stateProvince="Bahia",
                    specimen_sex="male")
        make_beetle(image=c, collection_country="", aspect="dorsal", specimen_notes="Ips? needs checking")
        make_beetle(image=c, taxon=ips, collection_country="Peru", is_deleted=True)

    def base(self, q=""):
        qs = Beetles.objects.filter(is_deleted=False).select_related("image_asset", "taxon").order_by("-id")
        return qs.filter(build_query_q(q)[0]) if q else qs

    CASES = [
        ("", {}),
        ("Ips", {}),
        ("Ips", {"country": ["USA"]}),
        ("", {"country": ["USA", "None"], "genus": ["Ips"]}),
        ("", {"image_validated": ["Yes"], "has_type_status": ["No"]}),
        ("", {"has_rois": ["Yes"], "date_taken": ["None"]}),
        ("", {"species": ["None"], "multiple": ["No"]}),
        ("nothing-matches-this", {}),
        ("Ips", {"photographer": ["Ana"], "institution": ["UF", "BYU"]}),
    ]

    def test_the_dropdowns_are_the_same_as_before(self):
        for q, active in self.CASES:
            with self.subTest(q=q, active=active):
                self.assertEqual(_build_gallery_filter_context(self.base(q), active),
                                 reference_filter_context(self.base(q), active))

    def test_one_query_plus_one_per_active_filter(self):
        with self.assertNumQueries(1):
            _build_gallery_filter_context(self.base("Ips"), {})
        with self.assertNumQueries(3):
            _build_gallery_filter_context(self.base("Ips"), {"country": ["USA"], "genus": ["Ips"]})

    def test_options_keep_their_order_and_blanks_show_as_none(self):
        groups = dict(_build_gallery_filter_context(self.base(), {}))
        country = next(f for f in groups["Collection"] if f["param"] == "country")
        self.assertEqual(country["options"][0], "None")             # the blank country
        self.assertIn("Brazil", country["options"])
        self.assertNotIn("Peru", country["options"])                 # only on a deleted specimen
        dates = next(f for f in groups["Image Details"] if f["param"] == "date_taken")
        self.assertEqual(dates["options"], ["None", "2019-01-02", "2020-05-17"])
