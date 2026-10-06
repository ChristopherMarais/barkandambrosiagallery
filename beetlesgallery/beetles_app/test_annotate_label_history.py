"""Issue #504: each ROI's labelling history on the annotation page (names, validations, game decisions, reports)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import areas, game_applied, label_history
from beetlesgallery.beetles_app.models import AreaGrant, Beetles, GameReport, LabelReview, RoiName
from beetlesgallery.beetles_app.test_game import GameCase
from beetlesgallery.beetles_app.testing import make_beetle, make_image


def url(roi):
    return f"/api/v1/beetles/{roi.id}/label-history/"


COUNTS = "/api/v1/beetles/label-history-counts/"


class LabelHistoryTests(GameCase):
    def setUp(self):
        super().setUp()
        self.image = make_image()
        self.roi = make_beetle(image=self.image, taxon=self.t_ferr, bbox="unvalidated",
                               label_source=Beetles.LabelSource.COMMUNITY, label_source_detail="12 players")
        self.t0 = timezone.now() - timedelta(days=10)
        self.client.force_login(self.staff)

    def at(self, days):
        return self.t0 + timedelta(days=days)

    def history(self):
        return self.client.get(url(self.roi)).json()

    def test_events_from_every_source_newest_first(self):
        self.roi.validate(user=self.staff)          # validated; the Community ID becomes the curator's Expert ID
        LabelReview.objects.create(roi=self.roi, decision=LabelReview.Decision.ACCEPTED, reviewed_by=None,
                                   taxon=self.t_affinis, genus="Xyleborus", species="affinis", answers=5)
        for player in (self.user, self.superuser):   # closed together: one event
            GameReport.objects.create(roi=self.roi, reporter=player, reason=GameReport.Reason.WRONG_LABEL,
                                      status=GameReport.Status.CONFIRMED, resolved_by=self.staff,
                                      resolved_at=self.at(3))
        self.image.unvalidate(user=self.superuser)  # the whole image, in bulk

        # Fix the times so the order is certain
        first, second = RoiName.objects.filter(roi=self.roi).order_by("id")
        RoiName.objects.filter(id=first.id).update(created_at=self.at(0))
        RoiName.objects.filter(id=second.id).update(created_at=self.at(1) + timedelta(minutes=1))
        records = list(Beetles.history.filter(id=self.roi.id).order_by("history_id"))
        self.assertEqual(len(records), 3)
        for record, when in zip(records, (self.at(0), self.at(1), self.at(4))):
            Beetles.history.filter(history_id=record.history_id).update(history_date=when)
        LabelReview.objects.filter(roi=self.roi).update(reviewed_at=self.at(2))

        data = self.history()
        self.assertEqual(data["total"], 6)
        kinds = [e["kind"] for e in data["events"]]
        self.assertEqual(kinds, ["unvalidated", "report_confirmed", "game_applied", "name", "validated", "name"])
        unval, report, applied, expert, validated, community = data["events"]
        self.assertEqual((unval["what"], unval["by"]), ("Unvalidated with the whole image", "super"))
        self.assertEqual((report["what"], report["by"]), ("2 player reports closed: label is correct", "staff"))
        self.assertEqual((applied["name"], applied["by"]), ("Xyleborus affinis", ""))
        self.assertIn("automatically", applied["what"])
        self.assertEqual((expert["name"], expert["tier"], expert["detail"]),
                         ("Xyleborus ferrugineus", "Expert ID", "Validated by staff"))
        self.assertEqual((validated["what"], validated["by"]), ("Validated", "staff"))
        self.assertEqual((community["tier"], community["detail"]), ("Community ID", "12 players"))
        self.assertTrue(community["at"].startswith(self.at(0).date().isoformat()))

    def test_validating_through_the_page_records_who(self):
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.post(f"/api/v1/beetles/{self.roi.id}/validate/").status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/beetles/{self.roi.id}/unvalidate/").status_code, 200)
        events = [(e["kind"], e["by"]) for e in self.history()["events"] if e["kind"] != "name"]
        self.assertEqual(events, [("unvalidated", "super"), ("validated", "super")])

    def test_a_whole_image_validation_is_kept_per_roi(self):
        other = make_beetle(image=self.image, bbox="unvalidated")
        self.image.validate(user=self.staff)
        for roi in (self.roi, other):
            with self.subTest(roi=roi.id):
                events = label_history.events_for([roi.id])[roi.id][0]
                validated = [e for e in events if e["kind"] == "validated"]
                self.assertEqual([(e["what"], e["by"]) for e in validated], [("Validated with the whole image", "staff")])

    def test_game_proposals_accepted_dismissed_and_reverted(self):
        self.client.force_login(self.staff)
        LabelReview.objects.create(roi=self.roi, decision=LabelReview.Decision.DISMISSED, reviewed_by=self.staff,
                                   genus="Xyleborus")
        LabelReview.objects.filter(roi=self.roi).update(reviewed_at=self.at(0))
        # a curator accepts the game's label, then takes it back
        self.roi.depicts_valid_name_id = self.t_affinis.valid_species_id
        self.roi.label_source = Beetles.LabelSource.EXPERT
        self.roi.save()
        LabelReview.objects.create(roi=self.roi, decision=LabelReview.Decision.ACCEPTED, reviewed_by=self.superuser,
                                   taxon=self.t_affinis, genus="Xyleborus", species="affinis", answers=4)
        game_applied.revert(self.roi, self.staff)
        reviews = [(e["kind"], e["name"], e["by"]) for e in self.history()["events"] if e["kind"].startswith("game")]
        self.assertEqual(reviews, [
            ("game_reverted", "Xyleborus affinis", "staff"),
            ("game_accepted", "Xyleborus affinis", "super"),
            ("game_dismissed", "Xyleborus", "staff"),
        ])

    def test_the_list_is_capped_but_counts_everything(self):
        for n in range(label_history.LIMIT + 10):
            RoiName.objects.create(roi=self.roi, valid_species_id=f"X-{n}", created_at=timezone.now() + timedelta(hours=n + 1))
        data = self.history()
        self.assertEqual(len(data["events"]), label_history.LIMIT)
        self.assertEqual(data["total"], label_history.LIMIT + 11)   # and the name the ROI was created with
        self.assertEqual(data["events"][0]["name"], f"X-{label_history.LIMIT + 9}")   # newest first

    def test_counts_for_every_roi_on_an_image(self):
        second = make_beetle(image=self.image, bbox="unvalidated")
        elsewhere = make_beetle(image=make_image(), taxon=self.t_plat, bbox="unvalidated")
        counts = self.client.get(COUNTS, {"image_asset": self.image.id}).json()["counts"]
        self.assertEqual(counts, {str(self.roi.id): 1, str(second.id): 0})
        self.assertNotIn(str(elsewhere.id), counts)
        self.assertEqual(self.client.get(COUNTS).status_code, 400)
        self.assertEqual(self.client.get(COUNTS, {"image_asset": "not-an-id"}).status_code, 400)

    def test_a_fixed_number_of_queries_for_many_rois(self):
        for _ in range(5):
            make_beetle(image=self.image, taxon=self.t_plat, bbox="validated")
        ids = list(Beetles.objects.filter(image_asset=self.image).values_list("id", flat=True))
        with self.assertNumQueries(4):
            label_history.events_for(ids)


class LabelHistoryAccessTests(GameCase):
    def setUp(self):
        super().setUp()
        self.roi = make_beetle(image=make_image(), taxon=self.t_ferr, bbox="unvalidated")

    def test_anyone_who_may_edit_boxes_can_read_it(self):
        boxer = get_user_model().objects.create_user("boxer", password="pw")
        AreaGrant.objects.create(user=boxer, area=areas.BOXES)
        for who in (boxer, self.staff, self.superuser):
            with self.subTest(who=who.username):
                self.client.force_login(who)
                self.assertEqual(self.client.get(url(self.roi)).status_code, 200)
                self.assertEqual(self.client.get(COUNTS, {"image_asset": self.roi.image_asset_id}).status_code, 200)

    def test_players_and_visitors_cannot(self):
        self.assertIn(self.client.get(url(self.roi)).status_code, (401, 403))
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url(self.roi)).status_code, 403)
        self.assertEqual(self.client.get(COUNTS, {"image_asset": self.roi.image_asset_id}).status_code, 403)

    def test_a_deleted_roi_has_none(self):
        self.client.force_login(self.staff)
        self.roi.delete(deleted_by=self.staff)
        self.assertEqual(self.client.get(url(self.roi)).status_code, 404)


class LabelHistoryPageTests(GameCase):
    def test_each_roi_panel_has_a_closed_history_section(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse("tool_annotate")).content.decode()
        self.assertIn("function labelHistoryHtml(b)", page)
        self.assertIn('data-testid="label-history"', page)
        self.assertIn("Label history", page)
        self.assertIn("/api/v1/beetles/label-history-counts/", page)
        self.assertIn("/label-history/", page)
        # one insertion point, between the name fields and "Identity & Details", away from the game and AI blocks
        start = page.index("function renderRoiAccordions()")
        hook = page.index("${labelHistoryHtml(b)}", start)
        self.assertEqual(page.count("${labelHistoryHtml(b)}"), 1)
        self.assertLess(page.index('data-testid="label-source"', start), hook)
        self.assertLess(hook, page.index("Identity & Details", start))
        # the section stays closed unless it was open before a re-render, and loads only when opened
        self.assertIn("ontoggle=\"toggleLabelHistory(this)\"", page)
        self.assertIn("if (!el.open)", page)
