"""
Where a name came from (#390): vial / specimen label, a taxonomist, an external database or website, or game
consensus accepted by a curator. Set on the annotation page, by an update file, or when a game proposal is accepted;
shown on the details page and in downloads.
"""
import io

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.urls import reverse

from beetlesgallery.beetles_app.models import Beetles, UpdateBatch
from beetlesgallery.beetles_app.test_game import TrustCase
from beetlesgallery.beetles_app.test_pipeline_update import download_row, fresh, to_csv
from beetlesgallery.beetles_app.testing import make_beetle


class LabelSourceTests(TrustCase):
    def run_update(self, *rows):
        batch = UpdateBatch.objects.create(uploaded_by=self.staff, original_filename="u.csv",
                                           status=UpdateBatch.Status.STAGING)
        batch.file.save("u.csv", ContentFile(to_csv(rows)), save=False)
        batch.size_bytes = batch.file.size
        batch.compute_sha256_from_disk()
        batch.save()
        call_command("process_single_update", id=batch.id, stdout=io.StringIO(), stderr=io.StringIO())
        batch.refresh_from_db()
        return batch

    def test_accepting_a_game_proposal_records_game_consensus(self):
        self.prove(self.user, self.t_affinis)
        target = self.roi(validated=False)
        self.label(self.user, target, self.t_ferr)
        self.client.force_login(self.staff)
        self.post("game_proposal_review", {"decision": "accept"}, target.id)
        target.refresh_from_db()
        self.assertEqual(target.label_source, Beetles.LabelSource.GAME_CONSENSUS)
        self.assertIn(f"accepted by {self.staff.username}", target.label_source_detail)

    def test_an_update_file_can_set_it_by_key_or_label(self):
        a, b = make_beetle(), make_beetle()
        batch = self.run_update(
            dict(download_row(a), label_source="vial_label", label_source_detail="Lab vial 2019-044"),
            dict(download_row(b), label_source="Taxonomist examined it", label_source_detail="A. Cognato"),
        )
        self.assertEqual(batch.status, UpdateBatch.Status.APPLIED, batch.error_message)
        self.assertEqual((fresh(a).label_source, fresh(a).label_source_detail), ("vial_label", "Lab vial 2019-044"))
        self.assertEqual(fresh(b).label_source, "taxonomist")

    def test_an_unknown_source_is_refused(self):
        batch = self.run_update(dict(download_row(make_beetle()), label_source="a friend told me"))
        self.assertEqual(batch.status, UpdateBatch.Status.APPLY_FAILED)
        self.assertIn("label_source 'a friend told me' must be one of", batch.error_message)

    def test_the_details_page_shows_it(self):
        roi = make_beetle(label_source="external", label_source_detail="https://www.gbif.org/occurrence/1")
        self.client.force_login(self.user)
        page = self.client.get(reverse("beetle_detail", args=[roi.id])).content.decode()
        block = page[page.index('data-testid="label-source"'):][:400]
        self.assertIn("External database or website", block)
        self.assertIn('href="https://www.gbif.org/occurrence/1"', block)
        plain = make_beetle()
        page = self.client.get(reverse("beetle_detail", args=[plain.id])).content.decode()
        self.assertIn("Not recorded", page[page.index('data-testid="label-source"'):][:200])

    def test_the_annotation_api_saves_it(self):
        roi = make_beetle()
        self.client.force_login(self.staff)
        res = self.client.patch(f"/api/v1/beetles/{roi.id}/", {"label_source": "taxonomist",
                                                                "label_source_detail": "J. Hulcr"},
                                content_type="application/json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((fresh(roi).label_source, fresh(roi).label_source_detail), ("taxonomist", "J. Hulcr"))
        page = self.client.get(reverse("tool_annotate")).content.decode()
        self.assertIn('name="label_source"', page)
