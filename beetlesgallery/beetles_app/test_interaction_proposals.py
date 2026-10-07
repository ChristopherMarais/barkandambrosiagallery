"""Loading proposed interactions from a CSV."""
import io
import os
import tempfile

from django.core.management import CommandError, call_command
from django.urls import reverse
from django.test import TestCase

from beetlesgallery.beetles_app.interaction_proposals import clean_doi, import_proposals, source_key_for
from beetlesgallery.beetles_app.models import InteractionProposal, PathogenInteraction, Synonym
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_taxon

HEADER = ("beetle_name,beetle_valid_species_id,partner_name,category,relationship,source_doi,source_url,"
          "source_title,source_authors,source_year,source_db,source_id,open_access,evidence,evidence_location,score,collector")


def csv_text(*rows, header=HEADER):
    return "\n".join([header, *rows]) + "\n"


GOOD = ("Ips typographus,,Ophiostoma polonicum,Fungi,associate,10.1000/ABC,,A paper,Smith J,2013,europepmc,,true,"
        "Ips typographus carries Ophiostoma polonicum.,abstract,0.72,collector v1")


class ProposalCase(TestCase):
    def setUp(self):
        self.typo = make_taxon(valid_species_id="1733", genus="Ips", species="typographus", scientific_name="Ips typographus")
        self.other = make_taxon(valid_species_id="2210", genus="Xyleborus", species="affinis", scientific_name="Xyleborus affinis")
        Synonym.objects.create(taxon=self.typo, name_id="s1", described_scientific_name="Bostrichus typographus")

    def load(self, text, **options):
        return import_proposals(text, **options)


class ParsingTests(ProposalCase):
    def test_doi_and_source_key(self):
        self.assertEqual(clean_doi("https://doi.org/10.1000/ABC "), "10.1000/abc")
        self.assertEqual(clean_doi("doi: 10.1000/abc"), "10.1000/abc")
        self.assertEqual(source_key_for("10.1/x", "europepmc", "1", "http://u"), "10.1/x")
        self.assertEqual(source_key_for("", "EuropePMC", "MED:123", "http://u"), "europepmc:med:123")
        self.assertEqual(source_key_for("", "", "", "http://U"), "http://u")


class ImportTests(ProposalCase):
    def test_a_valid_row_is_saved_waiting_for_review(self):
        result = self.load(csv_text(GOOD))
        self.assertTrue(result.ok, result.errors)
        self.assertEqual((result.rows, result.created, result.refreshed, result.kept), (1, 1, 0, 0))
        p = InteractionProposal.objects.get()
        self.assertEqual((p.status, p.beetle_name, p.beetle_valid_species_id, p.taxon), ("proposed", "Ips typographus", "1733", self.typo))
        self.assertEqual((p.partner_name, p.category, p.source_doi, p.source_key), ("Ophiostoma polonicum", "Fungi", "10.1000/abc", "10.1000/abc"))
        self.assertEqual((p.source_year, p.open_access, p.score, p.collector), (2013, True, 0.72, "collector v1"))
        self.assertEqual((p.source_authors, p.source_title), ("Smith J", "A paper"))
        self.assertEqual(p.source_url, "https://doi.org/10.1000/abc")  # a link is made from the DOI
        self.assertIn("carries Ophiostoma", p.evidence)

    def test_a_synonym_is_matched_to_the_valid_name(self):
        self.load(csv_text(GOOD.replace("Ips typographus,,", "Bostrichus typographus,,", 1)))
        self.assertEqual(InteractionProposal.objects.get().beetle_name, "Ips typographus")

    def test_the_valid_species_id_decides_which_species_is_meant(self):
        self.load(csv_text(GOOD.replace("Ips typographus,,", "anything,2210,", 1)))
        self.assertEqual(InteractionProposal.objects.get().beetle_valid_species_id, "2210")

    def test_the_other_columns_are_optional(self):
        result = self.load("beetle_name,partner_name,source_url\nIps typographus,Pinus sylvestris,https://example.org/p\n")
        self.assertTrue(result.ok, result.errors)
        self.assertIsNone(InteractionProposal.objects.get().score)

    def test_loading_again_refreshes_a_waiting_claim_instead_of_adding_one(self):
        self.load(csv_text(GOOD))
        again = GOOD.replace("0.72", "0.9")
        result = self.load(csv_text(again.replace("Ophiostoma polonicum,Fungi", "ophiostoma POLONICUM,Fungi")))
        self.assertEqual((result.created, result.refreshed), (0, 1))
        p = InteractionProposal.objects.get()
        self.assertEqual((p.score, p.partner_name), (0.9, "ophiostoma POLONICUM"))

    def test_a_claim_an_expert_decided_is_left_exactly_as_decided(self):
        self.load(csv_text(GOOD))
        InteractionProposal.objects.update(status="rejected", review_note="Not this beetle")
        result = self.load(csv_text(GOOD.replace("0.72", "0.99")))
        self.assertEqual((result.created, result.refreshed, result.kept), (0, 0, 1))
        p = InteractionProposal.objects.get()
        self.assertEqual((p.status, p.score, p.review_note), ("rejected", 0.72, "Not this beetle"))

    def test_the_same_claim_from_another_paper_is_another_proposal(self):
        self.load(csv_text(GOOD, GOOD.replace("10.1000/ABC", "10.1000/DEF")))
        self.assertEqual(InteractionProposal.objects.count(), 2)

    def test_check_only_saves_nothing_but_counts(self):
        result = self.load(csv_text(GOOD), dry_run=True)
        self.assertEqual((result.ok, result.created), (True, 1))
        self.assertFalse(InteractionProposal.objects.exists())

    def test_proposals_do_not_touch_the_published_dataset(self):
        PathogenInteraction.objects.create(beetle_host="Ips typographus", pathogen="Beauveria", category="Fungi")
        self.load(csv_text(GOOD))
        self.assertEqual(PathogenInteraction.objects.count(), 1)


class RejectionTests(ProposalCase):
    def assertRejected(self, row, message):
        result = self.load(csv_text(row))
        self.assertFalse(result.ok)
        self.assertTrue(any(message in e for e in result.errors), result.errors)
        self.assertIn("Row 2", result.errors[0])
        self.assertFalse(InteractionProposal.objects.exists())

    def test_each_kind_of_wrong_row(self):
        for row, message in [
            (GOOD.replace("Ips typographus,,", "Ips nonexistens,,", 1), "not in the species list"),
            (GOOD.replace("Ips typographus,,", "Ips typographus,9999,", 1), "beetle_valid_species_id '9999'"),
            (GOOD.replace("Ophiostoma polonicum,Fungi", ",Fungi"), "partner_name is empty"),
            (GOOD.replace("0.72", "72"), "score '72'"),
            (GOOD.replace("0.72", "high"), "score 'high'"),
            (GOOD.replace("2013", "13"), "not a year"),
            (GOOD.replace(",true,", ",maybe,"), "true or false"),
            (GOOD.replace("10.1000/ABC,,A paper", ",,A paper"), "there is no source"),
            (GOOD.replace("10.1000/ABC,,A paper", "10.1000/ABC,javascript:alert(1),A paper"), "must be a web link"),
            (GOOD.replace("10.1000/ABC,,A paper", "10.1000/ABC,ftp://example.org/x,A paper"), "must be a web link"),
            (GOOD.replace("Ips typographus carries", "x" * 2001 + " carries"), "evidence is limited"),
        ]:
            with self.subTest(message=message):
                self.assertRejected(row, message)

    def test_one_bad_row_saves_nothing_and_every_problem_is_listed_with_its_row(self):
        result = self.load(csv_text(GOOD, GOOD.replace("0.72", "72"), GOOD.replace("Ips typographus,,", "Nope nope,,", 1)))
        self.assertFalse(InteractionProposal.objects.exists())
        self.assertEqual(sorted(e.split(":")[0] for e in result.errors), ["Row 3", "Row 4"])

    def test_a_repeated_row_is_rejected(self):
        result = self.load(csv_text(GOOD, GOOD))
        self.assertTrue(any("repeats row 2" in e for e in result.errors), result.errors)

    def test_missing_columns_and_an_empty_file(self):
        self.assertIn("missing column(s): partner_name", self.load("beetle_name\nIps typographus\n").errors[0])
        self.assertIn("no rows", self.load(csv_text()).errors[0])

    def test_only_the_first_problems_are_listed_but_all_are_counted(self):
        rows = [GOOD.replace("0.72", "72").replace("10.1000/ABC", f"10.1000/{n}") for n in range(40)]
        result = self.load(csv_text(*rows))
        self.assertEqual((result.error_count, len(result.errors), result.hidden_errors), (40, 30, 10))


class CommandTests(ProposalCase):
    def write(self, text):
        handle, path = tempfile.mkstemp(suffix=".csv")
        self.addCleanup(os.remove, path)
        with os.fdopen(handle, "w") as fh:
            fh.write(text)
        return path

    def test_loads_a_file(self):
        out = io.StringIO()
        call_command("import_interaction_proposals", self.write(csv_text(GOOD)), stdout=out)
        self.assertIn("1 new", out.getvalue())
        self.assertEqual(InteractionProposal.objects.count(), 1)

    def test_dry_run_saves_nothing(self):
        out = io.StringIO()
        call_command("import_interaction_proposals", self.write(csv_text(GOOD)), "--dry-run", stdout=out)
        self.assertIn("Nothing was saved", out.getvalue())
        self.assertFalse(InteractionProposal.objects.exists())

    def test_a_bad_file_reports_and_saves_nothing(self):
        with self.assertRaisesMessage(CommandError, "nothing was saved"):
            call_command("import_interaction_proposals", self.write(csv_text(GOOD.replace("0.72", "72"))), stdout=io.StringIO(), stderr=io.StringIO())
        self.assertFalse(InteractionProposal.objects.exists())

    def test_the_collector_default_and_uploader_are_recorded(self):
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user("curator")
        call_command("import_interaction_proposals", self.write(csv_text(GOOD.replace("collector v1", ""))),
                     "--collector", "europepmc v9", "--user", "curator", stdout=io.StringIO())
        p = InteractionProposal.objects.get()
        self.assertEqual((p.collector, p.created_by), ("europepmc v9", user))

    def test_an_unknown_user_or_file_is_an_error(self):
        with self.assertRaisesMessage(CommandError, "No user named"):
            call_command("import_interaction_proposals", self.write(csv_text(GOOD)), "--user", "nobody", stdout=io.StringIO())
        with self.assertRaises(CommandError):
            call_command("import_interaction_proposals", "/no/such/file.csv", stdout=io.StringIO())


class PageTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        make_taxon(valid_species_id="1733", genus="Ips", species="typographus", scientific_name="Ips typographus")

    def post(self, text, name="p.csv", **fields):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_login(self.superuser)
        upload = SimpleUploadedFile(name, text.encode(), content_type="text/csv")
        return self.client.post(reverse("upload_interaction_proposals"), {"csv_file": upload, **fields})

    def test_page_is_for_superusers_only(self):
        url = reverse("upload_interaction_proposals")
        for account in (None, self.user, self.staff):
            if account:
                self.client.force_login(account)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn("login", response["Location"])
        self.client.force_login(self.superuser)
        self.assertContains(self.client.get(url), "Proposed interactions")

    def test_uploading_saves_proposals_that_wait_for_review(self):
        response = self.post(csv_text(GOOD), collector="europepmc v1")
        self.assertContains(response, "1 new")
        p = InteractionProposal.objects.get()
        self.assertEqual((p.status, p.created_by, p.collector), ("proposed", self.superuser, "collector v1"))
        self.assertFalse(PathogenInteraction.objects.exists())

    def test_the_page_lists_problems_with_row_numbers_and_saves_nothing(self):
        response = self.post(csv_text(GOOD, GOOD.replace("0.72", "72").replace("10.1000/ABC", "10.1000/Z")))
        self.assertContains(response, "Row 3")
        self.assertContains(response, "score &#x27;72&#x27;")
        self.assertContains(response, "nothing was saved")
        self.assertFalse(InteractionProposal.objects.exists())

    def test_check_only_saves_nothing(self):
        response = self.post(csv_text(GOOD), dry_run="on")
        self.assertContains(response, "nothing was saved")
        self.assertFalse(InteractionProposal.objects.exists())

    def test_refuses_a_non_csv_and_an_oversized_file(self):
        self.assertContains(self.post("x", name="p.txt"), "must be a .csv")
        with self.settings(MAX_UPLOAD_SIZE_INTERACTIONS=10):
            self.assertContains(self.post(csv_text(GOOD)), "too large")

    def test_the_load_button_is_primary_and_starts_disabled_until_a_file_is_chosen(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("upload_interaction_proposals")).content.decode()
        self.assertIn('id="load-proposals-btn" disabled\n      class="btn-primary', page)

    def test_the_file_field_is_the_shared_drop_zone(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("upload_interaction_proposals")).content.decode()
        self.assertIn('data-testid="csv_file-dropzone"', page)
        self.assertIn(".csv, up to", page)
        self.assertIn('id="csv_file"', page)

    def test_the_columns_table_stacks_on_mobile(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("upload_interaction_proposals")).content.decode()
        self.assertIn('class="block sm:table-row-group divide-y divide-gray-100"', page)
        self.assertIn('class="block sm:table-row"', page)
        self.assertIn('class="block sm:table-cell', page)
