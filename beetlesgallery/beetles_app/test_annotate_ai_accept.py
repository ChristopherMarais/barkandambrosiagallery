"""
Accepting the IBBI-AI suggestion on the annotation page (#503): "Use <species>" names the ROI as the model's top
species. A curator choosing it makes it their Expert ID, shown over any earlier name; it is not validated.
"""
import json

from django.contrib.auth import get_user_model
from django.urls import reverse

from beetlesgallery.beetles_app import areas
from beetlesgallery.beetles_app.models import AreaGrant, ImageLock, ModelPrediction, RoiName
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon


def section(page, start, end):
    return page[page.index(start):page.index(end, page.index(start))]


class AcceptAiSuggestionTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        genus = {"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus"}
        self.affinis = make_taxon(valid_species_id="1733", species="affinis", scientific_name="Xyleborus affinis", **genus)
        self.ferr = make_taxon(valid_species_id="2210", species="ferrugineus", scientific_name="Xyleborus ferrugineus",
                               **genus)
        # a vial label says ferrugineus; the model says affinis
        self.roi = make_beetle(image=make_image(), taxon=self.ferr, bbox="unvalidated", label_source="taxonomist",
                               label_source_detail="Vial label")
        ModelPrediction.objects.create(roi=self.roi, valid_species_id="1733", taxon=self.affinis, confidence=0.82,
                                       top_k=[{"valid_species_id": "2210", "confidence": 0.11}],
                                       model_name="annotator:ibbi_dinov3")
        self.client.force_login(self.staff)

    def accept(self, species="1733", roi=None):
        return self.client.post(f"/api/v1/beetles/{(roi or self.roi).id}/accept-ai-suggestion/",
                                json.dumps({"valid_species_id": species}), content_type="application/json")

    def assertUnchanged(self):
        self.roi.refresh_from_db()
        self.assertEqual((self.roi.depicts_valid_name_id, self.roi.label_source), ("2210", "taxonomist"))

    def test_the_roi_takes_the_species_as_the_curators_expert_id(self):
        res = self.accept()
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()["depicts_valid_name_id"], res.json()["label_source"]), ("1733", "expert"))
        self.roi.refresh_from_db()
        self.assertEqual(self.roi.taxon, self.affinis)   # shown over the Taxonomist ID: a curator's choice wins
        self.assertEqual((self.roi.label_source, self.roi.label_source_detail),
                         ("expert", "IBBI-AI suggestion accepted by staff"))
        self.assertFalse(self.roi.bbox_is_validated)
        self.assertEqual(self.roi.last_updated_by, self.staff)
        newest = RoiName.objects.filter(roi=self.roi).first()
        self.assertEqual((newest.valid_species_id, newest.tier, newest.detail, newest.added_by),
                         ("1733", "expert", "IBBI-AI suggestion accepted by staff", self.staff))
        self.assertTrue(RoiName.objects.filter(roi=self.roi, valid_species_id="2210", tier="taxonomist").exists())
        change = self.roi.history.first()
        self.assertEqual(change.history_change_reason, "Accepted the IBBI-AI suggestion (annotator:ibbi_dinov3)")

    def test_refused_without_the_right_to_edit_names(self):
        boxer = get_user_model().objects.create_user("boxer", password="pw")
        AreaGrant.objects.create(user=boxer, area=areas.BOXES)
        for account in (boxer, self.user):
            with self.subTest(account=account.username):
                self.client.force_login(account)
                self.assertEqual(self.accept().status_code, 403)
        self.client.logout()
        self.assertEqual(self.accept().status_code, 403)
        self.assertUnchanged()

    def test_refused_while_someone_else_edits_the_image(self):
        lock = ImageLock.objects.create(image_asset=self.roi.image_asset, locked_by=self.superuser)
        res = self.accept()
        self.assertEqual(res.status_code, 409)
        self.assertIn("super is editing", res.json()["error"])
        self.assertUnchanged()
        lock.locked_by = self.staff   # their own lock is no obstacle
        lock.save()
        self.assertEqual(self.accept().status_code, 200)

    def test_only_a_species_the_model_ranks_first_and_that_is_in_the_species_list(self):
        self.assertEqual(self.accept("2210").status_code, 400)   # a runner-up, not the suggestion
        self.assertEqual(self.accept("").status_code, 400)
        unknown = make_beetle(image=make_image(), bbox="unvalidated")
        ModelPrediction.objects.create(roi=unknown, valid_species_id="9999", confidence=0.9, model_name="m")
        res = self.accept("9999", roi=unknown)
        self.assertEqual(res.status_code, 400)
        self.assertIn("species list", res.json()["error"])
        unknown.refresh_from_db()
        self.assertIsNone(unknown.depicts_valid_name_id)
        self.assertUnchanged()

    def test_the_page_gets_the_top_species_only_when_it_is_known(self):
        bare = make_beetle(image=self.roi.image_asset, bbox="unvalidated")
        ModelPrediction.objects.create(roi=bare, valid_species_id="9999", confidence=0.9, model_name="m")
        ai = self.client.get(reverse("game_proposals"), {"image_asset": self.roi.image_asset_id}).json()["ai"]
        self.assertEqual(ai[str(self.roi.id)][0]["top_species"],
                         {"valid_species_id": "1733", "name": "Xyleborus affinis", "confidence": 0.82})
        self.assertIsNone(ai[str(bare.id)][0]["top_species"])

    def test_the_annotation_page_offers_it_as_a_light_main_button(self):
        page = self.client.get(reverse("tool_annotate")).content.decode().replace("\r\n", "\n")
        block = section(page, "function aiSuggestionHtml(b)", "\n}\n")
        self.assertIn('class="btn-main', block)
        self.assertIn("Use <i>${escHtml(top.name)}</i> (${pct(top.confidence)})", block)   # which name, how sure
        # only for those who may edit names, and not when it is the label already
        self.assertIn("if (CAN_EDIT_RECORDS && top && String(top.valid_species_id) !== current", block)
        self.assertIn("onclick=\"acceptAiSuggestion(${idx}, ${n})\"", block)
        handler = section(page, "async function acceptAiSuggestion(idx, n)", "\n}\n")
        self.assertIn("/accept-ai-suggestion/", handler)
        self.assertIn("state.bboxes[idx] = data;", handler)   # the ROI is refreshed from the answer
        self.assertIn("await loadGameProposals(state.selectedImageId);", handler)
