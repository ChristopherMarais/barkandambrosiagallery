"""
scripts/ml_predictions_to_uploads.py turns a detector + classifier run into files the site takes as they are.
These tests push its output through the real Update dialog, update pipeline and predictions import.
"""
import contextlib
import importlib.util
import io
import json
import tempfile
from pathlib import Path
from unittest import mock

import pandas as pd
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse

from beetlesgallery.beetles_app.models import Beetles, ModelPrediction, UpdateBatch
from beetlesgallery.beetles_app.predictions import import_predictions
from beetlesgallery.beetles_app.test_pipeline_update import download_row, to_csv
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon

SCRIPT = Path(settings.BASE_DIR) / "scripts" / "ml_predictions_to_uploads.py"
spec = importlib.util.spec_from_file_location("ml_predictions_to_uploads", SCRIPT)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def detection(det_id, image, box, conf=0.9, db_beetle="", depth=3, species=("Xyleborus affinis", "1"), prob=0.55):
    return {
        "detection_id": det_id, "image_asset_id": str(image.id), "det_conf": conf,
        "det_above_operating_conf": conf >= 0.7, "db_beetle_id": db_beetle,
        "bbox_x": box[0], "bbox_y": box[1], "bbox_width": box[2], "bbox_height": box[3],
        "reported_depth": depth,
        "subfamily_taxon": "Scolytinae", "subfamily_prob": 1.0000003,
        "tribe_taxon": "Xyleborini", "tribe_prob": 0.996,
        "genus_taxon": "Xyleborus", "genus_prob": 0.95,
        "species_taxon": species[0], "species_valid_species_id": species[1], "species_prob": prob,
        "species_topk": json.dumps([[species[0], prob], ["Xyleborus ferrugineus", 0.3]]),
    }


def run_tool(*args):
    with contextlib.redirect_stdout(io.StringIO()):
        tool.main([str(a) for a in args])


class MlUploadScriptTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        make_taxon(valid_species_id="1", subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus", species="affinis",
                   scientific_name="Xyleborus affinis")
        make_taxon(valid_species_id="2", subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus",
                   species="ferrugineus", scientific_name="Xyleborus ferrugineus")
        # A box-less record, with details that must survive; a boxed record; an image without any record.
        self.img_a = make_image(photographer="P. Hulcr", photo_usage_statement="CC-BY")
        self.unboxed = make_beetle(image=self.img_a, collection_country="Kenya")
        self.img_b = make_image(photographer="A. Smith")
        self.boxed = make_beetle(image=self.img_b, bbox="unvalidated")
        self.img_c = make_image()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.run = self.dir / "ml_predictions_db20260920_0911" / "M2e20__dinov3L336_npos_oe_supcon_sc0__gallery_op"
        self.run.mkdir(parents=True)
        self.out = self.dir / "out"
        pd.DataFrame([
            detection("d1", self.img_a, (0.1, 0.1, 0.3, 0.3), conf=0.95),
            detection("d2", self.img_a, (0.5, 0.5, 0.6, 0.6), conf=0.80),          # second beetle: NEW, clipped to the edge
            detection("d3", self.img_b, (0.2, 0.2, 0.2, 0.2), db_beetle=str(self.boxed.id)),   # already boxed
            detection("d4", self.img_b, (0.6, 0.1, 0.2, 0.2), depth=0),           # NEW, but "unrecognised"
            detection("d5", self.img_a, (0.0, 0.0, 0.1, 0.1), conf=0.40),          # below the operating point
            detection("d6", self.img_c, (0.1, 0.1, 0.2, 0.2)),                     # no record to copy the image from
        ]).to_csv(self.run / "detections.csv.gz", index=False)

    def download(self, name):
        rows = []
        for b in Beetles.objects.filter(is_deleted=False).exclude(image_asset=self.img_c):
            rows.append({"image_id": str(b.image_asset_id), **download_row(b)})
        path = self.dir / name
        path.write_bytes(to_csv(rows))
        return path

    def upload_update(self, path):
        self.client.force_login(self.staff)
        upload = SimpleUploadedFile(path.name, path.read_bytes(), content_type="text/csv")
        with mock.patch("beetlesgallery.beetles_app.views.process_update_task"):
            self.client.post(reverse("update_upload"), {"csv_file": upload})
        batch = UpdateBatch.objects.latest("created_at")
        self.assertEqual(batch.status, UpdateBatch.Status.STAGING, batch.error_message)
        call_command("process_single_update", id=batch.id, stdout=io.StringIO(), stderr=io.StringIO())
        batch.refresh_from_db()
        self.assertEqual(batch.status, UpdateBatch.Status.APPLIED, batch.error_message)

    def test_boxes_then_predictions_go_through_the_site(self):
        run_tool("boxes", self.run, self.out, "--download", self.download("before.csv"))
        files = sorted(self.out.glob("boxes_update_*.csv"))
        self.assertEqual(len(files), 1)
        for path in files:
            self.upload_update(path)

        self.unboxed.refresh_from_db()
        self.assertAlmostEqual(self.unboxed.bbox_x, 0.1)                 # the best box went on the box-less record
        self.assertEqual(self.unboxed.collection_country, "Kenya")       # and nothing else on it changed
        self.img_a.refresh_from_db()
        self.assertEqual((self.img_a.photographer, self.img_a.photo_usage_statement), ("P. Hulcr", "CC-BY"))
        new_a = Beetles.objects.get(image_asset=self.img_a, bbox_x__gt=0.4)
        self.assertAlmostEqual(new_a.bbox_x + new_a.bbox_width, 1.0)     # clipped inside the image
        self.assertIsNone(new_a.collection_country)                      # a NEW record copies the image, not the neighbour
        self.assertEqual(Beetles.objects.filter(image_asset=self.img_b).count(), 2)
        self.assertFalse(Beetles.objects.filter(image_asset=self.img_c).exists())
        self.assertEqual(Beetles.objects.filter(bbox_x=0.0, bbox_width=0.1).count(), 0)   # below the operating point

        species = self.dir / "species_list.csv"
        species.write_text("valid_species_id,scientificName,genus,species\n1,Xyleborus affinis,Xyleborus,affinis\n"
                           "2,Xyleborus ferrugineus,Xyleborus,ferrugineus\n")
        run_tool("predictions", self.run, self.out, "--download", self.download("after.csv"), "--species-list", species)
        [path] = sorted(self.out.glob("predictions_*.csv"))
        result = import_predictions(path.read_bytes(), user=self.superuser)
        self.assertTrue(result.ok, result.errors)

        preds = {p.roi_id: p for p in ModelPrediction.objects.all()}
        # d1 (box-less record), d2 (NEW) and d3 (already boxed); d4 is unrecognised, d5/d6 have no record.
        self.assertEqual(set(preds), {self.unboxed.id, new_a.id, self.boxed.id})
        p = preds[self.unboxed.id]
        self.assertEqual((p.valid_species_id, p.confidence), ("1", 0.55))
        self.assertEqual(p.top_k, [{"valid_species_id": "2", "confidence": 0.3}])
        self.assertEqual(p.rank_confidence["subfamily"], {"value": "Scolytinae", "confidence": 1.0})   # float noise clipped
        self.assertEqual(p.rank_confidence["genus"], {"value": "Xyleborus", "confidence": 0.95})
        self.assertEqual((p.model_name, p.model_version),
                         ("M2e20__dinov3L336_npos_oe_supcon_sc0__gallery_op", "db20260920_0911"))

    def test_the_boxes_step_needs_a_metadata_download(self):
        bad = self.dir / "bad.csv"
        bad.write_text("record_id,bbox_x\n")
        with self.assertRaises(SystemExit):
            run_tool("boxes", self.run, self.out, "--download", bad)
