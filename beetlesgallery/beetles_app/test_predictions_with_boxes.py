"""
Predictions and the boxes they belong to in one file: a row without a record_id gives the image and the box,
which is matched to the box already there or added (then the prediction is attached to it), all or nothing.
"""
from beetlesgallery.beetles_app.models import Beetles, ModelPrediction, RoiDifficulty
from beetlesgallery.beetles_app.test_predictions import PredictionCase, csv_text
from beetlesgallery.beetles_app.testing import make_beetle, make_image

HEADER = "record_id,image_id,bbox_x,bbox_y,bbox_width,bbox_height,valid_species_id,confidence,model_name,model_version"


def rows(*lines):
    return csv_text(*lines, header=HEADER)


class BoxesInThePredictionsFileTests(PredictionCase):
    def setUp(self):
        super().setUp()
        self.image = make_image()
        self.boxed = make_beetle(image=self.image, bbox="validated", depicts_valid_name_id="1733",
                                 specimen_notes="from the vial")   # box 0.1, 0.1, 0.2, 0.2

    def live(self):
        return Beetles.objects.filter(image_asset=self.image, is_deleted=False)

    def test_the_same_box_already_on_the_image_gets_the_prediction(self):
        result = self.load(rows(f",{self.image.id},0.11,0.1,0.2,0.21,2210,0.9,m,v1"))
        self.assertTrue(result.ok, result.errors)
        self.assertEqual((result.boxes_matched, result.boxes_created), (1, 0))
        self.assertEqual(self.live().count(), 1)
        self.assertEqual(ModelPrediction.objects.get().roi_id, self.boxed.id)

    def test_a_new_box_becomes_a_new_roi_with_the_images_details_and_no_label(self):
        result = self.load(rows(f",{self.image.id},0.6,0.6,0.3,0.3,2210,0.8,m,v1"))
        self.assertTrue(result.ok, result.errors)
        self.assertEqual((result.boxes_matched, result.boxes_created), (0, 1))
        new = self.live().exclude(id=self.boxed.id).get()
        self.assertEqual((new.bbox_x, new.bbox_y, new.bbox_width, new.bbox_height), (0.6, 0.6, 0.3, 0.3))
        self.assertFalse(new.bbox_is_validated)
        self.assertIsNone(new.depicts_valid_name_id)            # a prediction never labels the beetle
        self.assertEqual(new.specimen_notes, "from the vial")   # the image's details, as for a drawn box
        self.assertEqual(new.bbox_created_by, self.staff)
        self.assertEqual(ModelPrediction.objects.get().roi_id, new.id)
        self.assertEqual(RoiDifficulty.objects.get(roi=new).model_difficulty, 0.2)

    def test_the_images_box_less_roi_takes_the_first_new_box(self):
        image = make_image()
        template = make_beetle(image=image, alias_id="V-12")
        result = self.load(rows(f",{image.id},0.1,0.1,0.2,0.2,1733,0.7,m,v1",
                                f",{image.id},0.5,0.5,0.2,0.2,2210,0.6,m,v1"))
        self.assertTrue(result.ok, result.errors)
        template.refresh_from_db()
        self.assertEqual(template.bbox_x, 0.1)
        second = Beetles.objects.filter(image_asset=image).exclude(id=template.id).get()
        self.assertEqual(second.alias_id, "V-12")
        self.assertEqual(result.boxes_created, 2)

    def test_two_models_on_the_same_new_box_share_it(self):
        result = self.load(rows(f",{self.image.id},0.6,0.6,0.3,0.3,2210,0.8,model-a,v1",
                                f",{self.image.id},0.61,0.6,0.3,0.3,1733,0.5,model-b,v1"))
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.boxes_created, 1)
        self.assertEqual(ModelPrediction.objects.values("roi").distinct().count(), 1)

    def test_rows_with_a_record_id_work_as_before_in_the_same_file(self):
        result = self.load(rows(f"{self.roi.id},,,,,,1733,0.9,m,v1",
                                f",{self.image.id},0.6,0.6,0.3,0.3,2210,0.8,m,v1"))
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(ModelPrediction.objects.count(), 2)
        self.assertTrue(ModelPrediction.objects.filter(roi=self.roi).exists())

    def test_a_check_only_run_adds_no_box(self):
        result = self.load(rows(f",{self.image.id},0.6,0.6,0.3,0.3,2210,0.8,m,v1"), dry_run=True)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.boxes_created, 1)
        self.assertEqual(self.live().count(), 1)
        self.assertFalse(ModelPrediction.objects.exists())

    def test_any_problem_saves_nothing_not_even_the_boxes(self):
        result = self.load(rows(f",{self.image.id},0.6,0.6,0.3,0.3,2210,0.8,m,v1",
                                f",{self.image.id},0.9,0.9,0.3,0.3,2210,0.8,m,v1",       # past the edge
                                f",not-an-id,0.1,0.1,0.1,0.1,2210,0.8,m,v1",
                                ",,,,,,2210,0.8,m,v1",
                                f",{self.image.id},,,,,2210,0.8,m,v1"))
        self.assertFalse(result.ok)
        text = " ".join(result.errors)
        self.assertIn("Row 3: the box extends past the right edge", text)
        self.assertIn("Row 4: image_id 'not-an-id' is not a valid id", text)
        self.assertIn("Row 5: give a record_id, or an image_id with the box", text)
        self.assertIn("Row 6: a row without a record_id needs the box", text)
        self.assertEqual(self.live().count(), 1)
        self.assertFalse(ModelPrediction.objects.exists())

    def test_a_file_needs_record_id_or_the_image_and_box_columns(self):
        result = self.load(csv_text("1733,0.8,m", header="valid_species_id,confidence,model_name"))
        self.assertFalse(result.ok)
        self.assertIn("record_id (or image_id with bbox_x", result.errors[0])
