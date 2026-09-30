"""Classifier predictions: the CSV importer, the command and the superuser upload page."""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from beetlesgallery.beetles_app.models import ModelPrediction, RoiDifficulty
from beetlesgallery.beetles_app.predictions import import_predictions, parse_top_k, parse_confidence, suggestions_for
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon

HEADER = "record_id,valid_species_id,confidence,model_name,model_version,top_k"


def csv_text(*rows, header=HEADER):
    return "\n".join([header, *rows]) + "\n"


class PredictionCase(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        self.affinis = make_taxon(valid_species_id="1733", genus="Xyleborus", species="affinis",
                                  scientific_name="Xyleborus affinis")
        self.ferr = make_taxon(valid_species_id="2210", genus="Xyleborus", species="ferrugineus",
                               scientific_name="Xyleborus ferrugineus")
        self.roi = make_beetle()
        self.other = make_beetle()

    def load(self, text, **options):
        return import_predictions(text, user=self.staff, **options)


class ParserTests(PredictionCase):
    def test_confidence_must_be_a_fraction(self):
        self.assertEqual(parse_confidence("0.87"), (0.87, None))
        for bad in ("87", "-0.1", "high", "nan", "inf", ""):
            with self.subTest(value=bad):
                number, error = parse_confidence(bad)
                self.assertIsNone(number)
                self.assertTrue(error)
        self.assertIn("0.87, not 87", parse_confidence("87")[1])

    def test_top_k_forms_are_sorted_and_drop_the_primary(self):
        ids = {"1733": 1, "2210": 2, "3000": 3}
        self.assertEqual(
            parse_top_k("2210:0.03; 3000:0.08;1733:0.5", ids, primary="1733"),
            ([{"valid_species_id": "3000", "confidence": 0.08}, {"valid_species_id": "2210", "confidence": 0.03}], None),
        )
        as_json = '[{"valid_species_id": "3000", "confidence": 0.1}]'
        self.assertEqual(parse_top_k(as_json, ids, "1733"), ([{"valid_species_id": "3000", "confidence": 0.1}], None))
        self.assertEqual(parse_top_k("", ids, "1733"), ([], None))

    def test_top_k_problems(self):
        ids = {"1733": 1, "2210": 2}
        for bad in ("9999:0.1", "2210:high", "2210:0.1;2210:0.2", "2210", "[not json"):
            with self.subTest(top_k=bad):
                self.assertIsNone(parse_top_k(bad, ids, "1733")[0])


class ImportTests(PredictionCase):
    def test_valid_file_is_saved(self):
        result = self.load(csv_text(
            f"{self.roi.id},1733,0.91,ibbi,v1,2210:0.05",
            f"{self.other.id},2210,0.40,ibbi,v1,",
        ))
        self.assertTrue(result.ok, result.errors)
        self.assertEqual((result.rows, result.created, result.updated), (2, 2, 0))
        prediction = ModelPrediction.objects.get(roi=self.roi)
        self.assertEqual((prediction.valid_species_id, prediction.confidence, prediction.model_name), ("1733", 0.91, "ibbi"))
        self.assertEqual(prediction.taxon, self.affinis)
        self.assertEqual(prediction.top_k, [{"valid_species_id": "2210", "confidence": 0.05}])
        self.assertEqual(prediction.uploaded_by, self.staff)

    def test_predictions_never_change_the_roi(self):
        self.load(csv_text(f"{self.roi.id},1733,0.91,ibbi,v1,"))
        self.roi.refresh_from_db()
        self.assertIsNone(self.roi.depicts_valid_name_id)
        self.assertIsNone(self.roi.taxon)

    def test_confidence_sets_the_games_difficulty_and_leaves_game_data_alone(self):
        RoiDifficulty.objects.create(roi=self.other, game_difficulty=0.7, game_answers=9)
        self.load(csv_text(f"{self.roi.id},1733,0.90,ibbi,v1,", f"{self.other.id},2210,0.25,ibbi,v1,"))
        easy, hard = RoiDifficulty.objects.get(roi=self.roi), RoiDifficulty.objects.get(roi=self.other)
        self.assertEqual((easy.model_difficulty, easy.model_name), (0.1, "ibbi"))
        self.assertEqual(hard.model_difficulty, 0.75)
        self.assertEqual((hard.game_difficulty, hard.game_answers), (0.7, 9))
        self.assertEqual(hard.value, 0.75)  # the model's view wins once there is one

    def test_uploading_a_model_version_again_replaces_it(self):
        self.load(csv_text(f"{self.roi.id},1733,0.50,ibbi,v1,"))
        first = ModelPrediction.objects.get()
        result = self.load(csv_text(f"{self.roi.id},2210,0.80,ibbi,v1,"))
        self.assertEqual((result.created, result.updated), (0, 1))
        self.assertEqual(ModelPrediction.objects.count(), 1)
        prediction = ModelPrediction.objects.get()
        self.assertEqual((prediction.valid_species_id, prediction.confidence), ("2210", 0.8))
        self.assertEqual(prediction.pk, first.pk)
        self.assertGreaterEqual(prediction.created_at, first.created_at)
        self.assertEqual(RoiDifficulty.objects.get(roi=self.roi).model_difficulty, 0.2)

    def test_a_different_version_or_model_is_kept_alongside(self):
        self.load(csv_text(f"{self.roi.id},1733,0.50,ibbi,v1,"))
        self.load(csv_text(f"{self.roi.id},2210,0.80,ibbi,v2,", f"{self.roi.id},1733,0.60,other,,"))
        self.assertEqual(ModelPrediction.objects.filter(roi=self.roi).count(), 3)

    def test_default_model_fills_empty_cells(self):
        header = "record_id,valid_species_id,confidence"
        result = self.load(csv_text(f"{self.roi.id},1733,0.9", header=header), default_model="ibbi", default_version="v3")
        self.assertTrue(result.ok, result.errors)
        prediction = ModelPrediction.objects.get()
        self.assertEqual((prediction.model_name, prediction.model_version), ("ibbi", "v3"))

    def test_spreadsheet_habits_are_tolerated(self):
        text = "﻿" + csv_text(f"{self.roi.id}, 1733.0 ,0.9,ibbi,,", header="Record_ID,Predicted_Valid_Species_ID,Confidence,Model,Version,Top_K")
        text = text.replace("\n", "\r\n")
        result = self.load(text.encode("utf-8"))
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(ModelPrediction.objects.get().valid_species_id, "1733")

    def test_dry_run_checks_and_counts_but_saves_nothing(self):
        result = self.load(csv_text(f"{self.roi.id},1733,0.91,ibbi,v1,"), dry_run=True)
        self.assertTrue(result.ok)
        self.assertEqual((result.created, result.updated, result.dry_run), (1, 0, True))
        self.assertFalse(ModelPrediction.objects.exists())
        self.assertFalse(RoiDifficulty.objects.exists())


class RejectionTests(PredictionCase):
    def assertRejected(self, result, *fragments):
        self.assertFalse(result.ok)
        for fragment in fragments:
            self.assertTrue(any(fragment in e for e in result.errors), (fragment, result.errors))
        self.assertFalse(ModelPrediction.objects.exists())
        self.assertFalse(RoiDifficulty.objects.exists())

    def test_one_bad_row_saves_nothing_and_names_the_row(self):
        result = self.load(csv_text(
            f"{self.roi.id},1733,0.91,ibbi,v1,",
            f"{self.other.id},1733,87,ibbi,v1,",
        ))
        self.assertRejected(result, "Row 3", "0.87, not 87")

    def test_each_kind_of_problem_is_reported(self):
        gone = make_beetle()
        gone.delete()
        cases = {
            "unknown roi": ("00000000-0000-0000-0000-000000000000,1733,0.5,ibbi,v1,", "not an ROI in the gallery"),
            "deleted roi": (f"{gone.id},1733,0.5,ibbi,v1,", "not an ROI in the gallery"),
            "not an id": ("abc,1733,0.5,ibbi,v1,", "not a valid id"),
            "unknown species": (f"{self.roi.id},99999,0.5,ibbi,v1,", "not in the species list"),
            "empty species": (f"{self.roi.id},,0.5,ibbi,v1,", "valid_species_id is empty"),
            "no model": (f"{self.roi.id},1733,0.5,,v1,", "model_name is empty"),
            "bad top_k": (f"{self.roi.id},1733,0.5,ibbi,v1,9999:0.1", "not in the species list"),
        }
        for name, (row, fragment) in cases.items():
            with self.subTest(case=name):
                self.assertRejected(self.load(csv_text(row)), "Row 2", fragment)

    def test_a_repeated_key_is_refused(self):
        result = self.load(csv_text(f"{self.roi.id},1733,0.5,ibbi,v1,", f"{self.roi.id},2210,0.6,ibbi,v1,"))
        self.assertRejected(result, "Row 3", "repeats row 2")

    def test_missing_columns_and_empty_files(self):
        self.assertRejected(self.load("record_id,confidence\n"), "missing column(s): valid_species_id")
        self.assertRejected(self.load(HEADER + "\n"), "no rows")
        self.assertRejected(self.load(""), "missing column")

    def test_many_errors_are_counted_but_only_some_shown(self):
        rows = [f"{self.roi.id},99999,0.5,m{i},," for i in range(45)]
        result = self.load(csv_text(*rows))
        self.assertEqual((result.error_count, len(result.errors)), (45, 30))


class CommandAndPageTests(PredictionCase):
    def test_command_loads_a_file(self):
        path = f"{self.media_root}/p.csv"
        with open(path, "w") as fh:
            fh.write(csv_text(f"{self.roi.id},1733,0.9,,,"))
        out = io.StringIO()
        call_command("import_model_predictions", path, model="ibbi", model_version="v1", user="staff", stdout=out)
        prediction = ModelPrediction.objects.get()
        self.assertEqual((prediction.model_name, prediction.uploaded_by), ("ibbi", self.staff))
        self.assertIn("1 new", out.getvalue())

    def test_command_reports_problems_and_saves_nothing(self):
        path = f"{self.media_root}/bad.csv"
        with open(path, "w") as fh:
            fh.write(csv_text(f"{self.roi.id},99999,0.9,ibbi,v1,"))
        with self.assertRaisesMessage(CommandError, "not in the species list"):
            call_command("import_model_predictions", path, stdout=io.StringIO())
        self.assertFalse(ModelPrediction.objects.exists())

    def test_page_is_for_superusers_only(self):
        url = reverse("upload_predictions")
        self.assertRedirectsToLogin(self.client.get(url))
        self.client.force_login(self.user)
        self.assertRedirectsToLogin(self.client.get(url))
        self.client.force_login(self.staff)
        self.assertRedirectsToLogin(self.client.get(url))
        self.client.force_login(self.superuser)
        self.assertContains(self.client.get(url), "Model predictions")

    def post(self, text, **fields):
        self.client.force_login(self.superuser)
        upload = SimpleUploadedFile("p.csv", text.encode(), content_type="text/csv")
        return self.client.post(reverse("upload_predictions"), {"csv_file": upload, **fields})

    def test_uploading_through_the_page(self):
        response = self.post(csv_text(f"{self.roi.id},1733,0.9,,,"), model_name="ibbi", model_version="v1")
        self.assertContains(response, "1 new")
        self.assertEqual(ModelPrediction.objects.get().uploaded_by, self.superuser)

    def test_page_shows_row_errors_and_saves_nothing(self):
        response = self.post(csv_text(f"{self.roi.id},99999,0.9,ibbi,v1,"))
        self.assertContains(response, "Row 2")
        self.assertContains(response, "not in the species list")
        self.assertFalse(ModelPrediction.objects.exists())

    def test_check_only_saves_nothing(self):
        response = self.post(csv_text(f"{self.roi.id},1733,0.9,ibbi,v1,"), dry_run="on")
        self.assertContains(response, "nothing was saved")
        self.assertFalse(ModelPrediction.objects.exists())

    def test_page_refuses_a_non_csv_and_an_oversized_file(self):
        self.client.force_login(self.superuser)
        response = self.client.post(reverse("upload_predictions"), {"csv_file": SimpleUploadedFile("p.txt", b"x")})
        self.assertContains(response, "must be a .csv")
        with self.settings(MAX_UPLOAD_SIZE_PREDICTIONS=10):
            response = self.post(csv_text(f"{self.roi.id},1733,0.9,ibbi,v1,"))
        self.assertContains(response, "too large")


class SuggestionTests(PredictionCase):
    """Predictions as suggestions: the helper, the annotator's endpoint and the detail page."""

    def upload(self, *rows):
        result = self.load(csv_text(*rows))
        self.assertTrue(result.ok, result.errors)

    def test_one_suggestion_per_model_from_its_latest_upload_best_first(self):
        self.upload(f"{self.roi.id},2210,0.30,old-model,v1,")
        self.upload(f"{self.roi.id},1733,0.60,other-model,v1,")
        self.upload(f"{self.roi.id},1733,0.80,old-model,v2,2210:0.10")
        found = suggestions_for([self.roi.id])[self.roi.id]
        self.assertEqual([(s["model_name"], s["model_version"], s["valid_species_id"]) for s in found],
                         [("old-model", "v2", "1733"), ("other-model", "v1", "1733")])
        self.assertEqual(found[0]["scientific_name"], "Xyleborus affinis")
        self.assertEqual(found[0]["alternatives"],
                         [{"valid_species_id": "2210", "scientific_name": "Xyleborus ferrugineus", "confidence": 0.1}])

    def test_rois_without_predictions_are_left_out(self):
        self.upload(f"{self.roi.id},1733,0.9,ibbi,v1,")
        self.assertEqual(list(suggestions_for([self.roi.id, self.other.id])), [self.roi.id])
        self.assertEqual(suggestions_for([]), {})

    def test_query_count_does_not_grow_with_the_number_of_rois(self):
        def queries_for(rois):
            with CaptureQueriesContext(connection) as ctx:
                suggestions_for([r.id for r in rois])
            return len(ctx)

        image = make_image()
        few = [make_beetle(image=image) for _ in range(2)]
        many = few + [make_beetle(image=image) for _ in range(8)]
        self.upload(*[f"{r.id},1733,0.5,ibbi,v1,2210:0.2" for r in many])
        self.assertEqual(queries_for(few), queries_for(many))

    def test_annotator_endpoint_returns_predictions_for_the_image(self):
        image = make_image()
        here = make_beetle(image=image)
        make_beetle(image=image)
        self.upload(f"{here.id},1733,0.9,ibbi,v1,", f"{self.roi.id},2210,0.9,ibbi,v1,")
        url = reverse("game_proposals")

        self.client.force_login(self.user)
        self.assertRedirectsToLogin(self.client.get(url, {"image_asset": image.id}))

        self.client.force_login(self.staff)
        data = self.client.get(url, {"image_asset": image.id}).json()
        self.assertEqual(list(data["predictions"]), [str(here.id)])
        [suggestion] = data["predictions"][str(here.id)]
        self.assertEqual((suggestion["scientific_name"], suggestion["confidence"]), ("Xyleborus affinis", 0.9))

    def test_annotator_endpoint_query_count_ignores_the_number_of_rois(self):
        def queries_for(image):
            for _ in range(2):  # the second, warm request: the game's consensus is cached
                with CaptureQueriesContext(connection) as ctx:
                    response = self.client.get(reverse("game_proposals"), {"image_asset": image.id})
                self.assertEqual(response.status_code, 200)
            return len(ctx)

        self.client.force_login(self.staff)
        small, large = make_image(), make_image()
        self.upload(*[f"{make_beetle(image=small).id},1733,0.5,ibbi,v1,2210:0.2" for _ in range(2)])
        self.upload(*[f"{make_beetle(image=large).id},1733,0.5,ibbi,v1,2210:0.2" for _ in range(9)])
        self.assertEqual(queries_for(small), queries_for(large))

    def test_annotator_page_offers_the_suggestion(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("tool_annotate")), "useModelSuggestion")

    def detail(self, roi):
        self.client.force_login(self.user)
        return self.client.get(reverse("beetle_detail", args=[roi.id]))

    def test_detail_page_marks_the_suggestion_as_unverified(self):
        self.upload(f"{self.roi.id},1733,0.876,ibbi,v1,")
        response = self.detail(self.roi)
        self.assertContains(response, "Model suggestion (unverified)")
        self.assertContains(response, "Xyleborus affinis")
        self.assertContains(response, "(88%)")
        self.assertContains(response, "ibbi v1")
        self.assertContains(response, "Unidentified")  # the ROI itself is still unidentified

    def test_detail_page_shows_nothing_for_an_identified_roi_or_without_predictions(self):
        identified = make_beetle(taxon=self.ferr)
        self.upload(f"{identified.id},1733,0.9,ibbi,v1,")
        self.assertNotContains(self.detail(identified), "Model suggestion")
        self.assertNotContains(self.detail(self.other), "Model suggestion")
