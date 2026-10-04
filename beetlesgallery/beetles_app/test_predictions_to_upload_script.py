"""
scripts/predictions_to_upload.py turns a model's own output (its labels, its box format) into the one-file
Model predictions upload. This pushes its output through the real importer.
"""
import contextlib
import importlib.util
import io
import tempfile
from pathlib import Path

import pandas as pd
from django.conf import settings

from beetlesgallery.beetles_app.models import Beetles, ModelPrediction
from beetlesgallery.beetles_app.predictions import import_predictions
from beetlesgallery.beetles_app.testing import PageBehaviourCase, make_beetle, make_image, make_taxon

SCRIPT = Path(settings.BASE_DIR) / "scripts" / "predictions_to_upload.py"
spec = importlib.util.spec_from_file_location("predictions_to_upload", SCRIPT)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class PredictionsToUploadScriptTests(PageBehaviourCase):
    def setUp(self):
        super().setUp()
        make_taxon(valid_species_id="1733", subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus",
                   species="affinis", scientific_name="Xyleborus affinis")
        make_taxon(valid_species_id="2210", subfamily="Scolytinae", tribe="Xyleborini", genus="Xyleborus",
                   species="ferrugineus", scientific_name="Xyleborus ferrugineus")
        self.open_image = make_image(is_validated=False)
        self.done_image = make_image(is_validated=True)
        self.boxed = make_beetle(image=self.open_image, bbox="unvalidated")   # 0.1, 0.1, 0.2, 0.2
        self.dir = Path(tempfile.mkdtemp())

    def write(self, name, rows):
        pd.DataFrame(rows).to_csv(self.dir / name, index=False)
        return self.dir / name

    def run_script(self, predictions):
        download = self.write("download.csv", [
            {"record_id": str(self.boxed.id), "image_id": str(self.open_image.id), "is_validated": "False"},
            {"record_id": "x", "image_id": str(self.done_image.id), "is_validated": "True"},
        ])
        species = self.write("valid_species.csv", [
            {"valid_species_id": "1733", "subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus", "species": "affinis"},
            {"valid_species_id": "2210", "subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus", "species": "ferrugineus"},
        ])
        synonyms = self.write("described_names.csv", [
            {"name_valid_species_id": "2210", "describedScientificName": "Xyleborus trypanaeoides"}])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            tool.main(self.write("pred.csv", predictions), download, species, synonyms, self.dir / "out")
        return out.getvalue()

    def pred(self, image, box, species, p, **extra):
        row = {"image": f"photos/{image.id}.jpg", "x": box[0], "y": box[1], "w": box[2], "h": box[3],
               "subfamily": "scolytinae", "subfamily_prob": 99, "tribe": "Xyleborini", "tribe_prob": 0.9,
               "genus": "Xyleborus", "genus_prob": 0.8, "species": species, "species_prob": p,
               "species_2": "Xyleborus ferrugineus", "species_2_prob": 0.05, "species_3": "", "species_3_prob": ""}
        row.update(extra)
        return row

    def test_its_file_is_accepted_and_adds_boxes_and_predictions_on_unvalidated_images_only(self):
        out = self.run_script([
            self.pred(self.open_image, (0.11, 0.1, 0.2, 0.2), "Xyleborus_affinis", 0.91),      # the box already there
            self.pred(self.open_image, (0.6, 0.6, 0.5, 0.3), "Xyleborus trypanaeoides", 60),   # new, a synonym, a %
            self.pred(self.open_image, (0.3, 0.6, 0.1, 0.1), "Xyleborus mysterius", 0.5),      # not in the list
            self.pred(self.done_image, (0.1, 0.1, 0.2, 0.2), "Xyleborus affinis", 0.9),        # validated image
        ])
        self.assertIn("1 not matched", out)
        self.assertIn("Left out 1 rows on validated images", out)
        unmatched = pd.read_csv(self.dir / "out" / "unmatched_labels.csv")
        self.assertEqual(unmatched["model_label"].tolist(), ["Xyleborus mysterius"])

        upload = (self.dir / "out" / "upload_01.csv").read_text()
        result = import_predictions(upload, user=self.staff)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual((result.boxes_matched, result.boxes_created), (1, 1))

        on_box = ModelPrediction.objects.get(roi=self.boxed)
        self.assertEqual((on_box.valid_species_id, on_box.confidence), ("1733", 0.91))
        self.assertEqual(on_box.top_k, [{"valid_species_id": "2210", "confidence": 0.05}])
        self.assertEqual(on_box.rank_confidence["subfamily"], {"value": "Scolytinae", "confidence": 0.99})
        new = Beetles.objects.filter(image_asset=self.open_image).exclude(id=self.boxed.id).get()
        self.assertEqual((new.bbox_x, new.bbox_width), (0.6, 0.4))   # clipped to the image
        self.assertEqual(ModelPrediction.objects.get(roi=new).confidence, 0.6)
        self.assertFalse(Beetles.objects.filter(image_asset=self.done_image).exists())

    def test_yolo_and_pixel_boxes_become_top_left_fractions(self):
        frame = pd.DataFrame({"a": [0.5], "b": [0.5], "c": [0.2], "d": [0.4], "W": [1000], "H": [500]})
        with self._settings(BOX_FORMAT="cxcywh_fraction", BOX_COLUMNS=["a", "b", "c", "d"]):
            self.assertEqual([float(v.iloc[0]) for v in tool.to_fraction_boxes(frame)], [0.4, 0.3, 0.2, 0.4])
        pixels = pd.DataFrame({"a": [100], "b": [50], "c": [300], "d": [250], "W": [1000], "H": [500]})
        with self._settings(BOX_FORMAT="xyxy_pixels", BOX_COLUMNS=["a", "b", "c", "d"], WIDTH_COLUMN="W", HEIGHT_COLUMN="H"):
            self.assertEqual([float(v.iloc[0]) for v in tool.to_fraction_boxes(pixels)], [0.1, 0.1, 0.2, 0.4])

    @contextlib.contextmanager
    def _settings(self, **values):
        old = {k: getattr(tool, k) for k in values}
        for k, v in values.items():
            setattr(tool, k, v)
        try:
            yield
        finally:
            for k, v in old.items():
                setattr(tool, k, v)
