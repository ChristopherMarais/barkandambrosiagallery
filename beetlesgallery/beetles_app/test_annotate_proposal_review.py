"""
The game proposal on the annotation page (#503). "Use <species>" is the light main button, and a curator accepting it
always names the ROI, even over a Taxonomist ID. "Reject" (it was "Dismiss") still does what it did: the proposal
leaves the queue until new answers arrive, and the game never writes that label onto the ROI on its own.
"""
from django.urls import reverse

from beetlesgallery.beetles_app import game_queue
from beetlesgallery.beetles_app.game_trust import auto_apply_expert_labels, recompute_skills
from beetlesgallery.beetles_app.models import Beetles, LabelReview, RoiName
from beetlesgallery.beetles_app.test_game import AFFINIS, GameCase, TrustCase
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase


class AcceptTests(TrustCase):
    def test_accepting_replaces_even_a_taxonomist_id(self):
        self.prove(self.user, self.t_affinis)
        target = self.roi(self.t_affinis, validated=False)
        Beetles.objects.filter(pk=target.pk).update(label_source="taxonomist", label_source_detail="Vial label")
        self.label(self.user, target, self.t_ferr)   # a proven expert says ferrugineus
        self.client.force_login(self.staff)
        res = self.post("game_proposal_review", {"decision": "accept"}, target.id)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["depicts_valid_name_id"], self.t_ferr.valid_species_id)
        target.refresh_from_db()
        self.assertEqual(target.taxon, self.t_ferr)
        self.assertEqual((target.label_source, target.label_source_detail),
                         ("expert", "1 game answers, accepted by staff"))
        self.assertFalse(target.bbox_is_validated)
        self.assertEqual(RoiName.objects.filter(roi=target).first().valid_species_id, self.t_ferr.valid_species_id)


class RejectTests(ScoringCase):
    def expert(self, name):
        p = self.strong(name, right=40)
        recompute_skills(p)
        return p

    def reject(self, roi):
        self.client.force_login(self.staff)
        res = self.post("game_proposal_review", {"decision": "dismiss"}, roi.id)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["decision"], LabelReview.Decision.DISMISSED)

    def test_a_rejected_proposal_leaves_the_queue_until_new_answers_arrive(self):
        target = self.roi(validated=False)
        e1, e2 = self.expert("e1"), self.expert("e2")
        self.answer(e1, target, AFFINIS)
        image = str(target.image_asset_id)
        self.assertIn(image, game_queue.by_image())
        self.reject(target)
        self.assertNotIn(image, game_queue.by_image())   # rejecting cleared the cached queue too
        self.answer(e2, target, AFFINIS)
        game_queue.forget()
        self.assertIn(image, game_queue.by_image())

    def test_the_game_never_writes_a_rejected_label_on_its_own(self):
        rejected, untouched = self.roi(validated=False), self.roi(validated=False)
        experts = [self.expert("e1"), self.expert("e2")]
        for roi in (rejected, untouched):
            for e in experts:
                self.answer(e, roi, AFFINIS)
        self.reject(rejected)
        self.assertEqual(auto_apply_expert_labels(), [untouched.id])   # two proven experts agree on both
        rejected.refresh_from_db()
        self.assertIsNone(rejected.taxon)


def section(page, start, end="\n}\n"):
    return page[page.index(start):page.index(end, page.index(start))]


class ProposalButtonTests(GameCase):
    def test_accept_is_the_light_main_button_and_dismiss_is_now_reject(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse("tool_annotate")).content.decode().replace("\r\n", "\n")
        block = section(page, "function gameProposalHtml(b, i)")
        accept = section(block, 'id="btn-game-accept-${i}"', "</button>")
        self.assertIn("onclick=\"reviewGameProposal(${i}, 'accept')\" ${locked}", accept)   # still disabled when locked
        self.assertIn('class="btn-main px-3 py-1.5 text-xs', accept)
        self.assertIn('<i class="fi fi-rr-check"></i> Use ${escHtml(p.taxon.scientific_name)}', accept)
        self.assertNotIn("bg-gray-800", block)
        reject = section(block, 'id="btn-game-reject-${i}"', "</button>")
        self.assertIn("onclick=\"reviewGameProposal(${i}, 'dismiss')\" ${locked}", reject)   # the same as Dismiss
        self.assertIn('title="Hide until new answers arrive; the game won\'t apply this label on its own."', reject)
        self.assertTrue(reject.endswith(">Reject"))
        self.assertIn("'Accepted' : 'Rejected'", block)
        self.assertNotIn(">Dismiss</button>", page)
