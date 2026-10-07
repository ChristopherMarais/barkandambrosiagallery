"""The interactions page is built from the database (interaction_data.py), and the published v1.0 dataset loads into it intact."""
import json
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from beetlesgallery.beetles_app.testing import PageBehaviourCase
from django.test import TestCase
from django.urls import reverse

from beetlesgallery.beetles_app import interaction_data as data
from beetlesgallery.beetles_app.models import PathogenInteraction

STATIC = Path(settings.BASE_DIR) / "beetlesgallery" / "data" / "interactions" / "v1.0"


def published(name):
    return json.loads((STATIC / name).read_text(encoding="utf-8"))


class PublishedDatasetRoundTripTests(TestCase):
    """Load the real v1.0 files into the database and read them back out: nothing may be lost or changed."""

    @classmethod
    def setUpTestData(cls):
        call_command("import_pathogen_interactions", stdout=open("/dev/null", "w"))
        cls.master = published("bark_beetle_pathogens_master.json")

    def test_every_record_comes_back_with_the_same_values(self):
        records = data.master_records()
        self.assertEqual(len(records), len(self.master))
        for original, back in zip(self.master, records):
            for column, value in original.items():
                if column == "Beetle Host IDs":
                    value = value or ""
                self.assertEqual(back[column] or None, value or None, (original["Records ID"], column))

    def test_the_headline_numbers_are_the_published_ones(self):
        stats = data.page_stats()
        self.assertEqual((stats["records"], stats["hosts"], stats["genera"], stats["taxa"], stats["sources"], stats["validated"]),
                         (1015, 108, 28, 274, 281, 245))
        self.assertEqual((stats["first_year"], stats["last_year"]), (1914, 2026))
        self.assertEqual(stats["validated_pct"], 24.1)

    def test_the_category_counts_are_the_published_ones(self):
        counts = {label: (records, taxa) for _, label, records, taxa in data.group_stats()}
        self.assertEqual(counts, {"Fungi": (408, 59), "Nematodes": (221, 121), "Protists": (146, 21),
                                  "Microsporidia": (142, 25), "Bacteria": (49, 30), "Viruses": (47, 18)})

    def test_the_references_are_the_published_list(self):
        self.assertEqual(data.references(), published("references.json"))

    def test_the_beetle_summary_matches_the_published_one(self):
        summary = {h["Beetle Host ID"]: h for h in data.hosts_summary()}
        original = published("beetle_hosts_summary.json")
        self.assertEqual(len(summary), len(original))
        for h in original:
            mine = summary[h["Beetle Host ID"]]
            for column in ("Beetle Species", "No. records", "No. pathogen taxa", "No. countries"):
                self.assertEqual(mine[column], h[column], (h["Beetle Species"], column))
            self.assertEqual(set(mine["Associated Pathogens"].split("; ")), set(h["Associated Pathogens"].split("; ")))
            self.assertEqual(mine["Pathogen Record IDs"].split(", "), h["Pathogen Record IDs"].split(", "))

    def test_a_second_load_changes_nothing(self):
        before = PathogenInteraction.objects.count()
        call_command("import_pathogen_interactions", stdout=open("/dev/null", "w"))
        self.assertEqual(PathogenInteraction.objects.count(), before)


class PageFromTheDatabaseTests(PageBehaviourCase):
    def row(self, **fields):
        fields = {"beetle_host": "Ips typographus", "beetle_host_id": "1733", "pathogen": "Beauveria bassiana", "category": "Fungi", **fields}
        return PathogenInteraction.objects.create(**fields)

    def test_the_page_shows_the_numbers_in_the_database_not_fixed_ones(self):
        self.row(record_number="1", year="2013", title="A", validation_type="virulence test")
        self.row(record_number="2", beetle_host="Scolytus ventralis", beetle_host_id="1186", pathogen="Other", year="1970", title="B")
        page = self.client.get(reverse("interactions_preview")).content.decode()
        self.assertIn("Showing", page)
        self.assertNotIn("1,015 records</div>", page)
        self.assertIn("1970–2013", page)
        self.assertIn("All categories (2)", page)
        self.assertIn('<option value="Fungi">Fungi (2 records / 2 taxa)</option>', page)
        self.assertIn("50.0% verified", page)

    def test_an_empty_database_still_renders(self):
        self.assertEqual(self.client.get(reverse("interactions_preview")).status_code, 200)
        self.assertEqual(self.client.get(reverse("interactions_records")).json(), [])
        self.assertEqual(self.client.get(reverse("interactions_hosts")).json(), [])
        self.assertEqual(self.client.get(reverse("interactions_references")).json(), [])

    def test_a_correction_is_on_the_page_straight_away(self):
        row = self.row(record_number="1", country_or_region="Romania")
        self.assertEqual(self.client.get(reverse("interactions_records")).json()[0]["country or region"], "Romania")
        row.country_or_region = "Austria"
        row.save()
        self.assertEqual(self.client.get(reverse("interactions_records")).json()[0]["country or region"], "Austria")

    def test_records_are_in_dataset_order_then_the_rest_as_added(self):
        self.row(record_number="10", pathogen="ten")
        self.row(record_number="2", pathogen="two")
        self.row(record_number=None, origin="upload", pathogen="later")
        names = [r["pathogens"] for r in self.client.get(reverse("interactions_records")).json()]
        self.assertEqual(names, ["two", "ten", "later"])
        ids = [r["Records ID"] for r in self.client.get(reverse("interactions_records")).json()]
        self.assertEqual(ids[:2], [2, 10])
        self.assertTrue(str(ids[2]).startswith("A"))

    def test_a_category_nobody_planned_for_is_in_the_filter(self):
        self.row(category="Host plant", pathogen="Pinus sylvestris")
        page = self.client.get(reverse("interactions_preview")).content.decode()
        self.assertIn('<option value="Host plant">Host plant (1 records / 1 taxa)</option>', page)

    def test_summary_counts_distinct_pathogens_and_countries(self):
        self.row(record_number="1", country_or_region="Romania")
        self.row(record_number="2", pathogen="Other", country_or_region="Romania")
        self.row(record_number="3", country_or_region="Austria")
        [host] = data.hosts_summary()
        self.assertEqual((host["No. records"], host["No. pathogen taxa"], host["No. countries"]), (3, 2, 2))
        self.assertEqual(host["Pathogen Record IDs"], "1, 2, 3")
