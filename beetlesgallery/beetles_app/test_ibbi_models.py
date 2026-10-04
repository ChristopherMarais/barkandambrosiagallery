"""
IBBI 0.3.1 on the site: the model list, the service's answer (beetlesgallery/tools/ibbi_models.py), and how the
hierarchical classifier's ranks reach Classify with AI and the classifier page.
"""
from unittest import mock

from django.urls import reverse

from beetlesgallery.beetles_app.models import Beetles, ModelPrediction
from beetlesgallery.beetles_app.test_classify_assist import ClassifyCase, fake_response
from beetlesgallery.beetles_app.testing import make_taxon
from beetlesgallery.tools import ibbi_models


def level(taxon, prob, known=True, top3=None):
    return {"taxon": taxon, "prob": prob, "score": 0.9, "known": known, "threshold": 0.5, "entropy": 0.1,
            "top3": top3 or [(taxon, prob)]}


def record(depth=4, species="Xyleborus_affinis", species_prob=0.82):
    """A record as ibbi 0.3.1's HierarchicalClassifier._record makes it."""
    return {
        "subfamily": level("Scolytinae", 0.99), "tribe": level("Xyleborini", 0.97), "genus": level("Xyleborus", 0.93),
        "species": level(species, species_prob, known=depth == 4,
                         top3=[(species, species_prob), ("Ips_typographus", 0.1), ("Unlisted species", 0.05)]),
        "depth": depth, "depth_by_op": {"gallery": depth},
        "reported": species.replace("_", " ") if depth == 4 else "Xyleborus sp. (species undetermined)",
    }


def pipeline_output(*records):
    return {"boxes": [[100, 50, 300, 250]] * len(records), "det_scores": [0.88] * len(records),
            "scores": [0.7] * len(records), "labels": [r["reported"] for r in records],
            "species": [r["species"]["taxon"] for r in records], "classifications": list(records)}


class CatalogueTests(ClassifyCase):
    def test_the_models_and_the_old_names(self):
        self.assertEqual(ibbi_models.DEFAULT, "ibbi_dinov3")
        self.assertEqual(ibbi_models.resolve("rtdetr"), "rtdetrx")
        self.assertEqual(ibbi_models.resolve("yolov8"), "yolov8x")
        self.assertIsNone(ibbi_models.resolve("yolov99"))
        self.assertEqual(ibbi_models.model_name("ibbi_dinov3"), "yolo11x_arthropod_detector+dinov3_hierarchical_classifier")
        self.assertEqual(len(ibbi_models.all_ibbi_models()), 9)   # 1 arthropod detector, 2 classifiers, 6 detectors

    def test_a_pipeline_answer(self):
        dets = ibbi_models.from_pipeline(pipeline_output(record(), record(depth=3)))
        sure, unsure = dets
        self.assertEqual((sure["label"], sure["species"], sure["confidence"], sure["score"], sure["depth"]),
                         ("Xyleborus affinis", "Xyleborus affinis", 0.82, 0.88, 4))
        self.assertEqual(sure["levels"]["tribe"], {"taxon": "Xyleborini", "prob": 0.97, "known": True,
                                                   "top3": [{"name": "Xyleborini", "prob": 0.97}]})
        self.assertEqual(sure["candidates"][1], {"name": "Ips typographus", "prob": 0.1})
        # not sure of the species: the label says so and its probability is the genus's
        self.assertEqual((unsure["label"], unsure["confidence"]), ("Xyleborus sp. (species undetermined)", 0.93))
        self.assertFalse(unsure["levels"]["species"]["known"])

    def test_a_species_detector_answer_and_the_fields_older_pages_read(self):
        answer = ibbi_models.response("rtdetrx", ibbi_models.from_detector(
            {"boxes": [[1, 2, 3, 4]], "scores": [0.77], "labels": ["Ips_typographus"], "class_ids": [3]}))
        det = answer["detections"][0]
        self.assertEqual((det["label"], det["confidence"], det["levels"]), ("Ips typographus", 0.77, None))
        self.assertEqual((answer["class_names"], det["probs"]), (["Ips typographus"], [0.77]))
        self.assertEqual((answer["status"], answer["model_used"]), ("success", "rtdetrx"))


class ClassifyWithThePipelineTests(ClassifyCase):
    def setUp(self):
        super().setUp()
        type(self.affinis).objects.filter(pk=self.affinis.pk).update(subfamily="Scolytinae", tribe="Xyleborini")

    def run_pipeline(self, *records):
        dets = ibbi_models.response("ibbi_dinov3", ibbi_models.from_pipeline(pipeline_output(*records)))["detections"]
        with mock.patch("requests.post", return_value=fake_response(dets)) as post:
            response = self.client.post(self.url, {"architecture": "ibbi_dinov3"}, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(post.call_args.kwargs["data"]["architecture"], "ibbi_dinov3")
        return Beetles.objects.get(image_asset=self.asset)

    def test_a_sure_species_labels_the_box_and_the_ranks_are_kept(self):
        roi = self.run_pipeline(record())
        self.assertEqual(roi.depicts_valid_name_id, "2210")
        prediction = ModelPrediction.objects.get(roi=roi)
        self.assertEqual((prediction.valid_species_id, prediction.confidence), ("2210", 0.82))
        self.assertEqual(prediction.rank_confidence["tribe"], {"value": "Xyleborini", "confidence": 0.97})
        self.assertEqual(prediction.rank_confidence["genus"], {"value": "Xyleborus", "confidence": 0.93})
        self.assertEqual(prediction.top_k, [{"valid_species_id": "1733", "confidence": 0.1}])

    def test_an_unsure_species_gives_a_suggestion_but_no_label(self):
        roi = self.run_pipeline(record(depth=3))
        self.assertIsNone(roi.depicts_valid_name_id)
        self.assertEqual(ModelPrediction.objects.get(roi=roi).valid_species_id, "2210")


class PagesListTheModelsTests(ClassifyCase):
    def test_both_pickers_offer_the_new_models_with_the_recommended_one_first(self):
        page = self.client.get("/tools/classify/").content.decode()
        self.assertIn('<option value="ibbi_dinov3" selected>Detector + DINOv3 classifier (recommended)</option>', page)
        self.assertIn('<option value="yolov8x">YOLOv8 species detector</option>', page)
        self.assertNotIn('value="rtdetr"', page)
        self.assertIn('data-testid="ranks-panel"', page)
        annotate = self.client.get(reverse("tool_annotate")).content.decode()
        self.assertIn('<option value="ibbi_bioclip2">Detector + BioCLIP 2 classifier</option>', annotate)

    def test_the_classifier_page_sends_the_chosen_model_or_the_default(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        for sent, expected in (("ibbi_bioclip2", "ibbi_bioclip2"), ("yolov11", "yolo11x"), ("nonsense", "ibbi_dinov3")):
            with mock.patch("requests.post", return_value=fake_response([])) as post:
                self.client.post("/tools/classify/", {"image": SimpleUploadedFile("a.jpg", b"x", "image/jpeg"),
                                                      "architecture": sent})
            self.assertEqual(post.call_args.kwargs["data"]["architecture"], expected)
