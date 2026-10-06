"""
The grid games' builders (#489): every grid is built at the player's step on the ladder (4, 9 or 16 beetles, from
subfamily to species), and when the beetles for that are short they fall back to an easier grid instead of giving up.
"""
import random
from unittest import mock

from django.test import override_settings

from beetlesgallery.beetles_app import game, game_grid_ladder
from beetlesgallery.beetles_app.models import GamePreference, GridStep, ModelPrediction, PlayerScore
from beetlesgallery.beetles_app.test_game import GameCase
from beetlesgallery.beetles_app.testing import make_beetle, make_image, make_taxon

# A small tree, two subfamilies deep enough for every rank: (subfamily, tribe, genus, species)
TREE = [
    ("Scolytinae", "Xyleborini", "Xyleborus", "affinis"),
    ("Scolytinae", "Xyleborini", "Xyleborus", "ferrugineus"),
    ("Scolytinae", "Xyleborini", "Xylosandrus", "crassiusculus"),
    ("Scolytinae", "Ipini", "Ips", "typographus"),
    ("Scolytinae", "Ipini", "Pityogenes", "chalcographus"),
    ("Platypodinae", "Platypodini", "Platypus", "cylindrus"),
    ("Platypodinae", "Platypodini", "Euplatypus", "parallelus"),
]


class GridCase(GameCase):
    """A seeded tree with enough validated beetles of every species, each on its own photo, for a grid of 16."""

    PER_SPECIES = 16
    OPEN_PER_SPECIES = 2

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.taxa, cls.known, cls.unknown = {}, {}, {}
        for sub, tribe, genus, species in TREE:
            taxon = make_taxon(subfamily=sub, tribe=tribe, genus=genus, species=species,
                               scientific_name=f"{genus} {species}")
            cls.taxa[species] = taxon
            cls.known[species] = [cls.beetle(taxon, True) for _ in range(cls.PER_SPECIES)]
            cls.unknown[species] = [cls.beetle(taxon, False) for _ in range(cls.OPEN_PER_SPECIES)]

    @classmethod
    def beetle(cls, taxon, validated):
        return make_beetle(image=make_image(image_file="originals/aa/bb/test.jpg"), taxon=taxon,
                           bbox="validated" if validated else "unvalidated")

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.level(200, 0.4)   # level 3: both grid games, every rank open

    def level(self, score, rating=0.0):
        PlayerScore.objects.update_or_create(player=self.user, defaults={"score": score, "rating": rating})

    def predict(self, roi, confidence, taxon=None, **ranks):
        taxon = taxon or roi.taxon
        return ModelPrediction.objects.create(
            roi=roi, valid_species_id=taxon.valid_species_id, taxon=taxon, confidence=confidence,
            model_name="IBBI", model_version="1", rank_confidence=ranks)


class StagingBugTests(GridCase):
    """
    Staging has production's IBBI-AI predictions, and every grid game said "There are no images ready for this game
    yet": with predictions present a grid had to hold an AI-sure and an AI-unsure beetle, and none qualified.
    """

    def setUp(self):
        super().setUp()
        # plenty of predictions, but none sure (>= 0.9) or unsure (< 0.6) about any group: all in between
        for beetles in self.unknown.values():
            for roi in beetles:
                self.predict(roi, 0.75)

    def test_both_grid_games_still_build(self):
        self.assertTrue(game.build_odd_items(self.user, 3))
        self.assertTrue(game.build_select_items(self.user, 3))

    def test_the_feed_starts(self):
        for mode in ("odd", "select"):
            with self.subTest(mode=mode):
                res = self.post("game_start", {"mode": mode})
                self.assertEqual(res.status_code, 200, res.content)
                self.assertEqual(res.json()["item"]["mode"], mode)

    @override_settings(GAME_AI_SURE_FROM=0.99, GAME_AI_UNSURE_BELOW=0.01)
    def test_also_when_the_bands_are_tuned_out_of_reach(self):
        self.assertTrue(game.build_odd_items(self.user, 1))
        self.assertTrue(game.build_select_items(self.user, 1))

    def test_at_the_top_step_too(self):
        for key in ("odd", "select"):
            at(self.user, key, len(game_grid_ladder.steps(key)))
            item = (game.build_odd_items if key == "odd" else game.build_select_items)(self.user, 1)[0]
            self.assertEqual((item["size"], item["rank"]), (16, "species"))


def at(player, game_key, step):
    GridStep.objects.update_or_create(player=player, game=game_key, defaults={"step": step})


def every_anchor(case):
    """
    Try every beetle as a grid's heart instead of GRID_ANCHORS of them at random, and seed the builders' random picks,
    so a test that needs one particular group never depends on the sample (these were flaky, about 1 run in 70).
    """
    patcher = mock.patch.object(game, "GRID_ANCHORS", 10_000)
    patcher.start()
    case.addCleanup(patcher.stop)
    state = random.getstate()
    random.seed(489)
    case.addCleanup(random.setstate, state)


class LadderStepTests(GridCase):
    """Every step of the ladder builds its own size and rank, with the composition each game promises."""

    def name_at(self, roi_id, rank):
        roi = game.Beetles.objects.select_related("taxon").get(id=roi_id)
        return game.lineage(roi.taxon, rank)[rank].lower()

    def check_common(self, item, size, rank, step):
        self.assertEqual((item["size"], len(item["tiles"]), len(set(item["tiles"])), item["rank"], item["step"]),
                         (size, size, size, rank, step))
        photos = game.Beetles.objects.filter(id__in=item["tiles"]).values_list("image_asset_id", flat=True)
        self.assertEqual(len(set(photos)), size)   # no two from one photo

    def test_odd_one_out_at_every_step(self):
        for step, (size, rank, odds) in enumerate(game_grid_ladder.ODD_LADDER, start=1):
            with self.subTest(step=step):
                at(self.user, "odd", step)
                item = game.build_odd_items(self.user, 1)[0]
                self.check_common(item, size, rank, step)
                group = item["group"][rank].lower()
                outside = [t for t in item["tiles"] if self.name_at(t, rank) != group]
                self.assertEqual(sorted(outside), sorted(item["odds"]))   # exactly the odd ones (#540), all validated
                self.assertEqual((len(item["odds"]), item["a"]), (odds, item["odds"][0]))
                self.assertEqual(game.Beetles.objects.filter(id__in=item["odds"], bbox_is_validated=True).count(), odds)
                rest = game.Beetles.objects.filter(id__in=item["tiles"], bbox_is_validated=True).exclude(id__in=item["odds"])
                self.assertGreaterEqual(rest.count(), 1)

    def test_select_all_at_every_step(self):
        for step, (size, rank) in enumerate(game_grid_ladder.LADDER, start=1):
            with self.subTest(step=step):
                at(self.user, "select", step)
                item = game.build_select_items(self.user, 1)[0]
                self.check_common(item, size, rank, step)
                group = item["group"][rank].lower()
                known = game.Beetles.objects.filter(id__in=item["tiles"], bbox_is_validated=True)
                members = sum(self.name_at(b.id, rank) == group for b in known)
                low, high = game.SELECT_MEMBERS[size]
                self.assertTrue(low <= members <= high, (size, members))
                self.assertGreaterEqual(known.count() - members, members)   # tapping everything never pays
                self.assertLessEqual(size - known.count(), game.SELECT_AI[size][1])

    def test_ai_beetles_of_both_kinds_when_predictions_allow(self):
        at(self.user, "odd", game_grid_ladder.step_for(9, "species"))
        sure, unsure = self.unknown["affinis"]
        self.predict(sure, 0.95)
        self.predict(unsure, 0.3)
        every_anchor(self)   # so the one group with both is always found
        item = game.build_odd_items(self.user, 1)[0]   # a grid of affinis, the only group with both, holds them
        self.assertEqual(item["group"]["species"], "Xyleborus affinis")
        self.assertTrue({str(sure.id), str(unsure.id)} <= set(item["tiles"]))


class FallbackTests(GridCase):
    def setUp(self):
        super().setUp()
        every_anchor(self)   # which rank a grid falls back to must not depend on the beetles sampled

    def test_a_rank_with_too_few_beetles_gives_a_smaller_grid(self):
        for beetles in self.known.values():   # three of each species left: not enough for 16 or 9 at species
            for roi in beetles[3:]:
                roi.delete()
        at(self.user, "odd", game_grid_ladder.step_for(16, "species"))
        item = game.build_odd_items(self.user, 1)[0]
        self.assertEqual((item["rank"], item["size"]), ("species", 4))

    def test_a_rank_with_no_grid_falls_back_to_the_nearest_rank_shallower_first(self):
        for beetles in self.known.values():   # two per species: too few for a grid at species, enough in Xyleborus
            for roi in beetles[2:]:
                roi.delete()
        for beetles in self.unknown.values():
            for roi in beetles:
                roi.delete()
        at(self.user, "odd", game_grid_ladder.step_for(4, "species"))
        self.assertEqual(game.build_odd_items(self.user, 1)[0]["rank"], "genus")

    def test_nothing_at_all_gives_no_item(self):
        game.Beetles.objects.all().delete()
        self.assertEqual(game.build_odd_items(self.user, 1), [])
        self.assertEqual(game.build_select_items(self.user, 1), [])

    def test_a_focus_keeps_grids_inside_it_and_below_its_rank(self):
        self.level(450, 0.55)   # level 4: focus on a tribe
        GamePreference.objects.create(player=self.user, focus_rank="tribe", focus_value="Xyleborini")
        at(self.user, "odd", 1)   # 4 at subfamily: at or above the focus, so the nearest rank below it
        for _ in range(3):
            item = game.build_odd_items(self.user, 1)[0]
            self.assertEqual(item["rank"], "genus")
            tribes = set(game.Beetles.objects.filter(id__in=item["tiles"]).values_list("taxon__tribe", flat=True))
            self.assertEqual(tribes, {"Xyleborini"})
