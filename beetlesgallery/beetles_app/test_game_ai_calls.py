"""
IBBI-AI's calls for the grid games' AI beetles come from memory (game_ai_calls), not from a join over every prediction
per draw: on a large collection that join was most of the time it took to build a grid. A draw finds exactly the
beetles the old query found, only beetles the grid may use, and sees new predictions as soon as they are uploaded.
"""
from unittest import mock

from django.db import connection
from django.db.models import Q
from django.test.utils import CaptureQueriesContext

from beetlesgallery.beetles_app import game, game_ai_calls, game_grid_ladder
from beetlesgallery.beetles_app.models import ModelPrediction
from beetlesgallery.beetles_app.test_game import GameCase
from beetlesgallery.beetles_app.test_grid_builders import GridCase, at
from beetlesgallery.beetles_app.testing import make_taxon

BANDS = [(0.9, 1.01), (0.0, 0.6), (0.6, 0.9), (0.0, 1.01)]


def old_query(rank, value, low, high):
    """The Q the grid builder used before (game._predicted), as the reference for what a call is."""
    if rank == "species":
        named = game.rank_q(rank, value, "predictions__taxon__") if value else Q()
        return named & Q(predictions__confidence__gte=low, predictions__confidence__lt=high)
    said = Q(**{f"predictions__rank_confidence__{rank}__confidence__gte": low,
                f"predictions__rank_confidence__{rank}__confidence__lt": high})
    implied = (Q(**{f"predictions__rank_confidence__{rank}__isnull": True})
               & Q(predictions__confidence__gte=low, predictions__confidence__lt=high))
    if value:
        said &= Q(**{f"predictions__rank_confidence__{rank}__value__iexact": value})
        implied &= game.rank_q(rank, value, "predictions__taxon__")
    return said | implied


class DrawTests(GameCase):
    def setUp(self):
        super().setUp()
        self.no_species = make_taxon(subfamily="Scolytinae", tribe="Ipini", genus="Ips", species="",
                                     scientific_name="Ips")
        self.beetles = []

        def predict(taxon, confidence, model="m", roi=None, **said):
            roi = roi or self.roi(self.t_affinis, validated=False)
            self.beetles.append(roi)
            ModelPrediction.objects.create(roi=roi, valid_species_id=taxon.valid_species_id if taxon else "x",
                                           taxon=taxon, confidence=confidence, rank_confidence=said,
                                           model_name=model, model_version="1")
            return roi

        predict(self.t_affinis, 0.95, subfamily={"value": "SCOLYTINAE", "confidence": 0.97},
                tribe={"value": "xyleborini", "confidence": 0.96}, genus={"value": "Xyleborus", "confidence": 0.95})
        predict(self.t_plat, 0.3)                                                     # every rank implied
        predict(self.t_ferr, 0.5, genus={"value": "xyleborus", "confidence": 0.7})   # the rest implied
        predict(self.t_affinis, 0.92, genus=None)                                     # no call at genus
        predict(self.t_affinis, 0.8, tribe={"value": "Xyleborini", "confidence": True})
        predict(None, 0.65, subfamily={"value": "Scolytinae", "confidence": 0.65})    # no taxon
        predict(self.no_species, 0.75)
        two = predict(self.t_plat, 0.2, model="a")
        predict(self.t_affinis, 0.91, model="b", roi=two)                             # two models, one beetle
        validated = self.roi(self.t_affinis, validated=True)                          # never an AI beetle
        ModelPrediction.objects.create(roi=validated, valid_species_id=self.t_affinis.valid_species_id,
                                       taxon=self.t_affinis, confidence=0.99, model_name="m", model_version="1")
        game_ai_calls.refresh()

    def test_it_finds_exactly_what_the_old_query_found(self):
        values = {"subfamily": [None, "Scolytinae", "scolytinae", "Platypodinae", "Nope"],
                  "tribe": [None, "Xyleborini", "XYLEBORINI", "Ipini", "Platypodini"],
                  "genus": [None, "Xyleborus", "ips", "Platypus"],
                  "species": [None, "Xyleborus affinis", "xyleborus FERRUGINEUS", "Platypus cylindrus", "Ips "]}
        pool = game.open_rois()
        for rank, names in values.items():
            for value in names:
                for band in BANDS:
                    with self.subTest(rank=rank, value=value, band=band):
                        expected = set(pool.filter(old_query(rank, value, *band)).values_list("id", flat=True))
                        self.assertEqual(set(game_ai_calls.draw(pool, rank, value, *band, 1000)), expected)

    def test_only_beetles_the_grid_may_use_and_never_one_to_avoid(self):
        pool = game.open_rois().exclude(id=self.beetles[0].id)
        drawn = game_ai_calls.draw(pool, "subfamily", None, 0.0, 1.01, 1000, avoid={self.beetles[1].id})
        self.assertTrue(drawn)
        self.assertNotIn(self.beetles[0].id, drawn)
        self.assertNotIn(self.beetles[1].id, drawn)
        self.assertEqual(len(drawn), len(set(drawn)))   # a beetle with two predictions comes once
        self.assertEqual(len(game_ai_calls.draw(game.open_rois(), "subfamily", None, 0.0, 1.01, 2)), 2)

    def test_a_new_prediction_counts_from_the_next_build(self):
        roi = self.roi(self.t_ferr, validated=False)
        ModelPrediction.objects.create(roi=roi, valid_species_id=self.t_ferr.valid_species_id, taxon=self.t_ferr,
                                       confidence=0.99, model_name="m", model_version="1")
        game_ai_calls.refresh()
        self.assertIn(roi.id, game_ai_calls.draw(game.open_rois(), "species", "Xyleborus ferrugineus", 0.9, 1.01, 10))

    def test_one_process_reads_them_and_the_others_take_them_from_the_cache(self):
        game_ai_calls._calls["version"] = None   # another process, which hasn't read them yet
        with mock.patch.object(game_ai_calls, "_load") as load:
            game_ai_calls.refresh()
        load.assert_not_called()
        self.assertTrue(game_ai_calls.draw(game.open_rois(), "subfamily", None, 0.0, 1.01, 1))


class GridQueryTests(GridCase):
    def test_a_build_asks_for_the_predictions_a_fixed_number_of_times_not_per_grid(self):
        for beetle in self.unknown["affinis"] + self.unknown["ferrugineus"]:
            self.predict(beetle, 0.95)
        at(self.user, "select", game_grid_ladder.step_for(9, "genus"))
        game_ai_calls.refresh()
        counts = []
        for n in (1, 3):
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(len(game.build_select_items(self.user, n)), n)
            counts.append(sum("model_prediction" in q["sql"] for q in queries))
        self.assertEqual(counts[0], counts[1])


class AmongTests(GameCase):
    def test_a_long_list_is_one_parameter_and_finds_the_same_beetles(self):
        rois = [self.roi(self.t_affinis) for _ in range(5)]
        many = [r.id for r in rois[:2]] + [f"00000000-0000-0000-0000-{n:012d}" for n in range(game.ARRAY_FROM)]
        for ids in (many, [r.id for r in rois[:2]]):
            left = game.Beetles.objects.exclude(game.among(ids))
            self.assertEqual(set(left.values_list("id", flat=True)), {r.id for r in rois[2:]})
        sql, params = game.Beetles.objects.exclude(game.among(many)).query.sql_with_params()
        self.assertEqual(len(params), 1)
        self.assertIn("unnest", sql)
