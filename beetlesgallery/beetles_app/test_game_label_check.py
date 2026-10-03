"""
Validated beetles that players get wrong more often than not, the same way, are flagged as likely mislabelled:
out of the game, everyone's answers on them held, listed for curators like a report (#387).
"""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command

from beetlesgallery.beetles_app import game, game_feedback, game_label_check
from beetlesgallery.beetles_app.models import GameAnswer, GameReport, GameRound, PlayerScore
from beetlesgallery.beetles_app.test_game import GameCase


class LabelCheckTests(GameCase):
    def setUp(self):
        super().setUp()
        self.roi_ok = self.roi(self.t_affinis)        # validated as Xyleborus affinis
        self.players = [get_user_model().objects.create_user(f"p{i}", password="pw") for i in range(6)]
        for p in self.players:
            PlayerScore.objects.create(player=p, rating=0.8, score=100)

    def answer(self, player, roi, genus="Xyleborus", species="affinis"):
        rnd = GameRound.objects.create(player=player, mode="classify", items=[])
        return GameAnswer.objects.create(round=rnd, player=player, mode="classify", index=0, roi=roi, is_check=True,
                                         subfamily="Scolytinae", tribe="Xyleborini", genus=genus, species=species)

    def dispute(self, alternative=("ferrugineus",) * 5, agree=1):
        for p, sp in zip(self.players, list(alternative) + ["affinis"] * agree):
            self.answer(p, self.roi_ok, species=sp)

    def test_most_players_naming_one_other_species_is_suspect(self):
        self.dispute()
        [s] = game_label_check.suspects()
        self.assertEqual((s["roi"], s["rank"], s["label"], s["alternative"]),
                         (self.roi_ok, "species", "Xyleborus affinis", "Xyleborus ferrugineus"))
        self.assertIn("ferrugineus", game_label_check.note(s))

    def test_scattered_wrong_answers_are_a_hard_photo_not_a_wrong_label(self):
        self.dispute(alternative=("ferrugineus", "dispar", "pfeili", "perforans", "volvulus"))
        self.assertEqual(game_label_check.suspects(), [])

    def test_too_few_answers_or_a_majority_for_the_label(self):
        self.dispute(alternative=("ferrugineus",) * 3, agree=0)   # 3 answers < 5
        self.assertEqual(game_label_check.suspects(), [])
        self.dispute(alternative=("ferrugineus",) * 2, agree=4)   # most agree with the label
        self.assertEqual(game_label_check.suspects(), [])

    def test_unreliable_players_count_for_less(self):
        self.dispute(alternative=("ferrugineus",) * 3, agree=3)
        PlayerScore.objects.filter(player__in=self.players[:3]).update(rating=0.1)   # the dissenters are unreliable
        self.assertEqual(game_label_check.suspects(), [])

    def test_flagging_takes_it_out_of_the_game_and_holds_everyones_answers(self):
        self.dispute()
        out = StringIO()
        call_command("check_game_labels", stdout=out)
        self.assertIn("Flagged 1 beetle", out.getvalue())
        report = GameReport.objects.get(roi=self.roi_ok)
        self.assertEqual((report.reporter.username, report.reason, report.status),
                         ("label-check", "wrong_label", "open"))
        self.assertFalse(GameAnswer.objects.filter(roi=self.roi_ok, score_hold=False).exists())
        self.assertNotIn(self.roi_ok, game.check_rois())
        self.assertEqual(game_label_check.suspects(), [])          # not flagged twice

    def test_label_confirmed_releases_everyone_and_is_not_raised_again(self):
        self.dispute()
        game_label_check.check()
        game_feedback.resolve_reports(self.roi_ok, GameReport.Status.CONFIRMED, self.staff)
        self.assertFalse(GameAnswer.objects.filter(roi=self.roi_ok, score_hold=True).exists())
        self.assertEqual(game_label_check.suspects(), [])

    def test_label_fixed_rescores_everyone_against_the_new_label(self):
        self.dispute()
        game_label_check.check()
        self.roi_ok.taxon = self.t_ferr
        self.roi_ok.depicts_valid_name_id = self.t_ferr.valid_species_id
        self.roi_ok.save()
        game_feedback.resolve_reports(self.roi_ok, GameReport.Status.CORRECTED, self.staff)
        right = GameAnswer.objects.filter(roi=self.roi_ok, species="ferrugineus")
        self.assertEqual(set(right.values_list("correct_species", "score_hold")), {(True, False)})

    def test_dry_run_flags_nothing(self):
        self.dispute()
        out = StringIO()
        call_command("check_game_labels", "--dry-run", stdout=out)
        self.assertIn("Would flag 1 beetle", out.getvalue())
        self.assertFalse(GameReport.objects.exists())

    def test_the_annotation_list_can_show_only_disputed_labels(self):
        other = self.roi(self.t_ferr)
        game_feedback.create_report(self.players[0], other, GameReport.Reason.BAD_BOX)   # a player's report: not listed
        self.dispute()
        game_label_check.check()
        self.client.force_login(self.staff)
        data = self.client.get("/api/v1/beetles/images-with-annotations/?game=disputed").json()
        self.assertEqual([r["image_asset_id"] for r in data["results"]], [str(self.roi_ok.image_asset_id)])
