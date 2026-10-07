"""Uploading, updating and downloading interactions as CSV."""
import csv
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from beetlesgallery.beetles_app.interaction_upload import EXPORT_COLUMNS, import_interactions, normalise_link
from beetlesgallery.beetles_app.models import Synonym, PathogenInteraction
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_taxon

HEADER = "record_id,beetle_host,beetle_host_id,pathogen,category,ecological_relationship,source,year,title,doi_or_full_text"


def csv_text(*rows, header=HEADER):
    return "\n".join([header, *rows]) + "\n"


NEW_ROW = "NEW,Ips typographus,,Ophiostoma polonicum,Fungi,associate,\"Smith et al., 2020\",2020,A paper,10.1000/ABC"


class UploadCase(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.typo = make_taxon(valid_species_id="1733", genus="Ips", species="typographus", scientific_name="Ips typographus")
        self.affinis = make_taxon(valid_species_id="2210", genus="Xyleborus", species="affinis", scientific_name="Xyleborus affinis")
        Synonym.objects.create(taxon=self.typo, name_id="s1", described_scientific_name="Bostrichus typographus")

    def load(self, text, **options):
        return import_interactions(text, user=self.staff, **options)

    def uploaded(self, **fields):
        return PathogenInteraction.objects.create(
            beetle_host="Ips typographus", beetle_host_id="1733", pathogen="Ophiostoma polonicum", category="Fungi",
            origin="upload", added_by=self.staff, **fields)


class AddTests(UploadCase):
    def test_new_rows_are_added_as_uploads(self):
        result = self.load(csv_text(NEW_ROW, ",Xyleborus affinis,,Raffaelea lauricola,Fungi,symbiont,,,,"))
        self.assertTrue(result.ok, result.errors)
        self.assertEqual((result.rows, result.created, result.updated, result.unchanged), (2, 2, 0, 0))
        row = PathogenInteraction.objects.get(pathogen="Ophiostoma polonicum")
        self.assertEqual((row.origin, row.added_by, row.beetle_host, row.beetle_host_id), ("upload", self.staff, "Ips typographus", "1733"))
        self.assertEqual((row.category, row.ecological_relationship, row.source, row.year, row.title),
                         ("Fungi", "associate", "Smith et al., 2020", "2020", "A paper"))
        self.assertEqual(row.doi_or_full_text, "https://doi.org/10.1000/abc")
        bare = PathogenInteraction.objects.get(pathogen="Raffaelea lauricola")
        self.assertEqual((bare.source, bare.year, bare.doi_or_full_text), (None, "", None))

    def test_a_synonym_or_an_id_names_the_beetle(self):
        self.load(csv_text(NEW_ROW.replace("NEW,Ips typographus,", "NEW,Bostrichus typographus,"),
                           "NEW,,2210,Beauveria bassiana,Fungi,pathogen,,,,"))
        self.assertEqual(PathogenInteraction.objects.get(pathogen="Ophiostoma polonicum").beetle_host, "Ips typographus")
        self.assertEqual(PathogenInteraction.objects.get(pathogen="Beauveria bassiana").beetle_host, "Xyleborus affinis")

    def test_check_only_saves_nothing_but_counts(self):
        result = self.load(csv_text(NEW_ROW), dry_run=True)
        self.assertEqual((result.ok, result.created), (True, 1))
        self.assertFalse(PathogenInteraction.objects.exists())

    def test_the_same_row_twice_is_refused_in_the_file_and_in_the_database(self):
        self.assertIn("repeats row 2", self.load(csv_text(NEW_ROW, NEW_ROW)).errors[0])
        self.load(csv_text(NEW_ROW))
        again = self.load(csv_text(NEW_ROW))
        self.assertIn("already in the database", again.errors[0])
        self.assertEqual(PathogenInteraction.objects.count(), 1)

    def test_the_same_pair_from_another_source_is_allowed(self):
        self.load(csv_text(NEW_ROW))
        result = self.load(csv_text(NEW_ROW.replace("10.1000/ABC", "10.1000/DEF")))
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(PathogenInteraction.objects.count(), 2)


class RejectionTests(UploadCase):
    def assertRejected(self, row, message):
        result = self.load(csv_text(row))
        self.assertFalse(result.ok)
        self.assertTrue(any(message in e for e in result.errors), result.errors)
        self.assertIn("Row 2", result.errors[0])
        self.assertFalse(PathogenInteraction.objects.exists())

    def test_each_kind_of_wrong_new_row(self):
        for row, message in [
            (NEW_ROW.replace("NEW,Ips typographus,", "NEW,Ips nonexistens,"), "not in the species list"),
            (NEW_ROW.replace("NEW,Ips typographus,,", "NEW,,,"), "give the beetle"),
            (NEW_ROW.replace("NEW,Ips typographus,,", "NEW,Ips typographus,9999,"), "beetle_host_id"),
            (NEW_ROW.replace("NEW,Ips typographus,,", "NEW,Xyleborus affinis,1733,"), "different species"),
            (NEW_ROW.replace("Ophiostoma polonicum", ""), "pathogen is required"),
            (NEW_ROW.replace("Fungi", ""), "category is required"),
            (NEW_ROW.replace("Fungi", "x" * 65), "category is limited to 64"),
            (NEW_ROW.replace("2020,A paper", "20,A paper"), "not a year"),
            (NEW_ROW.replace("10.1000/ABC", "not a doi"), "should be a DOI"),
            (NEW_ROW.replace("10.1000/ABC", "javascript:alert(1)"), "should be a DOI"),
            (NEW_ROW.replace("NEW,", "banana,"), "not an interaction id"),
        ]:
            with self.subTest(message=message):
                self.assertRejected(row, message)

    def test_one_bad_row_saves_nothing_and_every_problem_is_listed_with_its_row(self):
        result = self.load(csv_text(NEW_ROW, NEW_ROW.replace("Fungi", "").replace("10.1000/ABC", "10.1234/x"),
                                    NEW_ROW.replace("Ips typographus", "Nope nope").replace("10.1000/ABC", "10.1234/y")))
        self.assertFalse(PathogenInteraction.objects.exists())
        self.assertEqual(sorted(e.split(":")[0] for e in result.errors), ["Row 3", "Row 4"])

    def test_the_file_must_have_a_record_id_column_and_rows(self):
        self.assertIn("missing column: record_id", self.load("beetle_host,pathogen,category\nIps typographus,x,Fungi\n").errors[0])
        self.assertIn("no rows", self.load(csv_text()).errors[0])

    def test_links(self):
        self.assertEqual(normalise_link("10.1000/ABC"), ("https://doi.org/10.1000/abc", None))
        self.assertEqual(normalise_link("https://doi.org/10.1000/ABC"), ("https://doi.org/10.1000/abc", None))
        self.assertEqual(normalise_link("https://example.org/paper.pdf"), ("https://example.org/paper.pdf", None))
        self.assertEqual(normalise_link(""), ("", None))
        self.assertTrue(normalise_link("ftp://example.org/x")[1])


class UpdateTests(UploadCase):
    def test_an_update_changes_only_the_columns_in_the_file(self):
        row = self.uploaded(ecological_relationship="associate", year="2019", title="Old title", source="Old, 2019")
        result = self.load(f"record_id,ecological_relationship,year\n{row.id},symbiont,2020\n")
        self.assertEqual((result.created, result.updated, result.unchanged), (0, 1, 0))
        row.refresh_from_db()
        self.assertEqual((row.ecological_relationship, row.year, row.title, row.source, row.pathogen),
                         ("symbiont", "2020", "Old title", "Old, 2019", "Ophiostoma polonicum"))

    def test_a_blank_cell_empties_an_optional_field_and_a_required_one_cannot_be_emptied(self):
        row = self.uploaded(ecological_relationship="associate", title="Old title")
        self.assertTrue(self.load(f"record_id,ecological_relationship,title\n{row.id},,\n").ok)
        row.refresh_from_db()
        self.assertEqual((row.ecological_relationship, row.title), (None, None))
        result = self.load(f"record_id,category\n{row.id},\n")
        self.assertIn("category is required", result.errors[0])

    def test_the_beetle_can_be_changed(self):
        row = self.uploaded()
        self.load(f"record_id,beetle_host\n{row.id},Xyleborus affinis\n")
        row.refresh_from_db()
        self.assertEqual((row.beetle_host, row.beetle_host_id), ("Xyleborus affinis", "2210"))

    def test_a_file_that_changes_nothing_says_so(self):
        row = self.uploaded(year="2019")
        result = self.load(f"record_id,year,pathogen\n{row.id},2019,Ophiostoma polonicum\n")
        self.assertEqual((result.updated, result.unchanged), (0, 1))

    def test_mixed_new_and_updated_rows(self):
        row = self.uploaded(year="2019")
        result = self.load(csv_text(f"{row.id},Ips typographus,,Ophiostoma polonicum,Fungi,,,2021,,", NEW_ROW.replace("Ophiostoma", "Ceratocystis")))
        self.assertEqual((result.created, result.updated), (1, 1))

    def test_the_published_dataset_can_be_corrected_and_stays_the_published_dataset(self):
        row = PathogenInteraction.objects.create(beetle_host="Ips typographus", beetle_host_id="1733", pathogen="Beauveria", category="Fungi", origin="dataset")
        result = self.load(f"record_id,year\n{row.id},2020\n")
        self.assertTrue(result.ok, result.errors)
        row.refresh_from_db()
        self.assertEqual((row.year, row.origin), ("2020", "dataset"))

    def test_a_published_row_whose_beetle_name_is_not_in_the_species_list_can_still_be_corrected(self):
        row = PathogenInteraction.objects.create(beetle_host="Ips notinlist", beetle_host_id="9999", pathogen="Beauveria", category="Fungi", origin="dataset")
        result = self.load(f"record_id,beetle_host,beetle_host_id,year\n{row.id},Ips notinlist,9999,2021\n")
        self.assertTrue(result.ok, result.errors)
        row.refresh_from_db()
        self.assertEqual(row.year, "2021")
        # ...but naming a different beetle is still checked against the species list
        result = self.load(f"record_id,beetle_host\n{row.id},Not a beetle\n")
        self.assertFalse(result.ok)

    def test_accepted_proposals_can_be_changed(self):
        row = PathogenInteraction.objects.create(beetle_host="Ips typographus", beetle_host_id="1733", pathogen="Beauveria", category="Fungi", origin="proposal")
        self.assertTrue(self.load(f"record_id,year\n{row.id},2020\n").ok)

    def test_unknown_or_repeated_records_are_refused(self):
        row = self.uploaded()
        self.assertIn("not an interaction in the database", self.load("record_id,year\n11111111-1111-1111-1111-111111111111,2020\n").errors[0])
        self.assertIn("repeats row 2", self.load(f"record_id,year\n{row.id},2020\n{row.id},2021\n").errors[0])

    def test_one_bad_update_saves_nothing(self):
        good, bad = self.uploaded(year="2019"), self.uploaded(year="2019", source="Other")
        result = self.load(f"record_id,year\n{good.id},2020\n{bad.id},20\n")
        self.assertFalse(result.ok)
        good.refresh_from_db()
        self.assertEqual(good.year, "2019")


class PageTests(UploadCase):
    def post(self, text, user=None, **fields):
        self.client.force_login(user or self.staff)
        upload = SimpleUploadedFile("i.csv", text.encode(), content_type="text/csv")
        return self.client.post(reverse("upload_interactions"), {"csv_file": upload, **fields})

    def test_the_upload_page_is_for_staff(self):
        url = reverse("upload_interactions")
        self.assertRedirectsToLogin(self.client.get(url))
        self.client.force_login(self.user)
        self.assertRedirectsToLogin(self.client.get(url))
        self.assertRedirectsToLogin(self.client.post(url))
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(url), "Upload or update interactions")

    def test_uploading_through_the_page(self):
        self.assertContains(self.post(csv_text(NEW_ROW)), "1 added")
        self.assertEqual(PathogenInteraction.objects.get().added_by, self.staff)

    def test_the_page_lists_problems_with_row_numbers_and_saves_nothing(self):
        response = self.post(csv_text(NEW_ROW, NEW_ROW.replace("Fungi", "").replace("10.1000/ABC", "10.1234/x")))
        self.assertContains(response, "Row 3")
        self.assertContains(response, "nothing was saved")
        self.assertFalse(PathogenInteraction.objects.exists())

    def test_check_only_and_bad_files(self):
        self.assertContains(self.post(csv_text(NEW_ROW), dry_run="on"), "nothing was saved")
        self.assertFalse(PathogenInteraction.objects.exists())
        self.client.force_login(self.staff)
        self.assertContains(self.client.post(reverse("upload_interactions"), {"csv_file": SimpleUploadedFile("i.txt", b"x")}), "must be a .csv")
        with self.settings(MAX_UPLOAD_SIZE_INTERACTIONS=10):
            self.assertContains(self.post(csv_text(NEW_ROW)), "too large")

    def test_the_page_says_when_the_published_dataset_is_not_in_the_database(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("upload_interactions")), "not loaded into the database")
        PathogenInteraction.objects.create(beetle_host="x", pathogen="y", category="Fungi", origin="dataset")
        self.assertNotContains(self.client.get(reverse("upload_interactions")), "not loaded into the database")

    def test_the_counts_show_as_three_small_stats(self):
        self.uploaded()
        PathogenInteraction.objects.create(beetle_host="x", pathogen="y", category="Fungi", origin="dataset")
        self.client.force_login(self.staff)
        page = self.client.get(reverse("upload_interactions")).content.decode()
        self.assertIn('data-testid="interaction-counts"', page)
        self.assertIn("Published", page)
        self.assertIn("Accepted", page)
        self.assertIn("Uploaded", page)

    def test_the_upload_button_is_primary(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse("upload_interactions")).content.decode()
        self.assertIn('<button type="submit" class="btn-primary', page)

    def test_downloads_are_a_list_with_a_description_each(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse("upload_interactions")).content.decode()
        self.assertIn("Downloads", page)
        self.assertIn("Current interactions (CSV)", page)
        self.assertIn("Blank template", page)
        self.assertIn("Initial file (v1.0)", page)
        self.assertIn("ready to edit and upload again", page)

    def test_the_columns_table_stacks_on_mobile(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse("upload_interactions")).content.decode()
        self.assertIn('class="block sm:table-row-group divide-y divide-gray-100"', page)
        self.assertIn('class="block sm:table-row"', page)
        self.assertIn('class="block sm:table-cell', page)


class ExportTests(UploadCase):
    def download(self, user=None):
        self.client.force_login(user or self.user)
        return self.client.get(reverse("interactions_export"))

    def test_the_download_is_for_logged_in_users(self):
        self.assertRedirectsToLogin(self.client.get(reverse("interactions_export")))
        self.assertEqual(self.download().status_code, 200)

    def test_the_download_has_record_ids_and_can_be_uploaded_again_unchanged(self):
        self.uploaded(year="2019", source="Smith, 2019", doi_or_full_text="https://doi.org/10.1234/x")
        PathogenInteraction.objects.create(beetle_host="Ips typographus", beetle_host_id="1733", pathogen="Beauveria", category="Fungi", origin="dataset", record_number="3")
        response = self.download()
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("interactions_", response["Content-Disposition"])
        text = b"".join(response.streaming_content).decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(list(rows[0]), EXPORT_COLUMNS)
        self.assertEqual({r["origin"] for r in rows}, {"upload", "dataset"})
        self.assertEqual({r["pathogen"] for r in rows}, {"Ophiostoma polonicum", "Beauveria"})
        # The editable rows (not the published dataset) come back as "unchanged"
        edited = "\n".join([",".join(EXPORT_COLUMNS)] + [
            ",".join(f'"{r[c]}"' for c in EXPORT_COLUMNS) for r in rows if r["origin"] == "upload"])
        result = self.load(edited)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual((result.created, result.updated, result.unchanged), (0, 0, 1))
