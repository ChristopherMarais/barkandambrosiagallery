"""The Upload Model Predictions dialog on Data Management: the file is checked and saved in the background."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from beetlesgallery.beetles_app import areas
from beetlesgallery.beetles_app.models import AreaGrant, ModelPrediction, PredictionUpload
from beetlesgallery.beetles_app.predictions import import_predictions, run_upload
from beetlesgallery.beetles_app.test_predictions import PredictionCase, csv_text

XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


class PredictionsDialogTests(PredictionCase):
    def setUp(self):
        super().setUp()
        self.predictor = get_user_model().objects.create_user("predictor", password="pw")
        AreaGrant.objects.create(user=self.predictor, area=areas.PREDICTIONS)
        self.client.force_login(self.predictor)

    def send(self, text, **fields):
        upload = SimpleUploadedFile("p.csv", text.encode(), content_type="text/csv")
        with mock.patch("beetlesgallery.beetles_app.tasks.import_predictions_task.delay") as delay, \
                self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("upload_predictions"), {"csv_file": upload, **fields}, **XHR)
        return response, delay

    def test_the_button_and_dialog_sit_with_the_other_data_actions(self):
        page = self.client.get(reverse("data_management")).content.decode()
        self.assertIn("openModal('modal-predictions')", page)
        self.assertIn('id="form-predictions"', page)
        self.assertIn("predictions_upload_template.csv", page)
        self.assertNotIn("<h2 class=\"text-2xl font-bold mb-2 text-gray-800\">Model Predictions</h2>", page)
        self.client.force_login(self.staff)   # curators don't have this permission unless granted
        self.assertNotIn("openModal('modal-predictions')", self.client.get(reverse("data_management")).content.decode())

    def test_uploading_starts_a_background_job_and_the_status_follows_it(self):
        response, delay = self.send(csv_text(f"{self.roi.id},1733,0.9,,,"), model_name="ibbi", model_version="v1")
        self.assertEqual(response.status_code, 200)
        job = PredictionUpload.objects.get()
        delay.assert_called_once_with(str(job.id))
        self.assertEqual(response.json()["status_url"], reverse("upload_predictions_status", args=[job.id]))
        self.assertFalse(ModelPrediction.objects.exists())   # nothing saved until the job runs
        self.assertEqual(self.client.get(response.json()["status_url"]).json()["status"], "queued")

        run_upload(job.id)
        status = self.client.get(response.json()["status_url"]).json()
        self.assertTrue(status["done"])
        self.assertEqual((status["percent"], status["result"]["ok"], status["result"]["created"]), (100, True, 1))
        self.assertEqual(ModelPrediction.objects.get().model_name, "ibbi")
        self.assertEqual(ModelPrediction.objects.get().uploaded_by, self.predictor)

    def test_problems_come_back_listed_and_nothing_is_saved(self):
        response, _ = self.send(csv_text(f"{self.roi.id},99999,0.9,ibbi,v1,"))
        run_upload(PredictionUpload.objects.get().id)
        result = self.client.get(response.json()["status_url"]).json()["result"]
        self.assertFalse(result["ok"])
        self.assertIn("Row 2", result["errors"][0])
        self.assertFalse(ModelPrediction.objects.exists())

    def test_a_check_only_saves_nothing(self):
        self.send(csv_text(f"{self.roi.id},1733,0.9,ibbi,v1,"), dry_run="on")
        job = run_upload(PredictionUpload.objects.get().id)
        self.assertTrue(job.result["ok"] and job.result["dry_run"])
        self.assertFalse(ModelPrediction.objects.exists())

    def test_a_wrong_file_is_refused_at_once(self):
        upload = SimpleUploadedFile("p.txt", b"x")
        response = self.client.post(reverse("upload_predictions"), {"csv_file": upload}, **XHR)
        self.assertEqual(response.status_code, 400)
        self.assertIn(".csv", response.json()["error"])
        self.assertFalse(PredictionUpload.objects.exists())

    def test_only_the_uploader_or_a_superuser_sees_a_job(self):
        response, _ = self.send(csv_text(f"{self.roi.id},1733,0.9,ibbi,v1,"))
        url = response.json()["status_url"]
        other = get_user_model().objects.create_user("other", password="pw")
        AreaGrant.objects.create(user=other, area=areas.PREDICTIONS)
        self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_progress_is_reported_while_checking_and_saving(self):
        seen = []
        import_predictions(csv_text(f"{self.roi.id},1733,0.9,ibbi,v1,"), progress=lambda phase, f: seen.append((phase, f)))
        self.assertEqual([phase for phase, _ in seen], ["Checking rows", "Saving", "Saving"])
        self.assertTrue(all(0 <= f < 1 for _, f in seen))

    def test_the_job_runs_on_the_heavy_queue(self):
        from beetlesgallery.beetles_app import tasks
        from beetlesgallery.celery import app
        self.assertEqual(app.amqp.router.route({}, tasks.import_predictions_task.name)["queue"].name, "heavy")
