"""Hard beetles nobody has validated go through the easier games first: Similarity leans to them, Identification waits (#490)."""
from django.contrib.auth import get_user_model
from django.test import override_settings

from beetlesgallery.beetles_app import game, game_relearn
from beetlesgallery.beetles_app.models import GameAnswer, GameRound, ModelPrediction, RoiDifficulty
from beetlesgallery.beetles_app.test_game import AFFINIS, GameCase


class RoutingCase(GameCase):
    def setUp(self):
        super().setUp()
        self.other = get_user_model().objects.create_user("other", password="pw")
        self.rnd = GameRound.objects.create(player=self.other, mode="classify", items=[])
        self.n = 0

    def said(self, roi, mode="classify", player=None, **fields):
        self.n += 1
        rnd = self.rnd if player is None else GameRound.objects.create(player=player, mode=mode, items=[])
        return GameAnswer.objects.create(round=rnd, player=player or self.other, mode=mode, index=self.n, roi=roi,
                                         is_check=False, **fields)

    def sure(self, roi, confidence=0.95):
        ModelPrediction.objects.create(roi=roi, valid_species_id=self.t_affinis.valid_species_id, taxon=self.t_affinis,
                                       confidence=confidence, model_name="IBBI")
        RoiDifficulty.objects.update_or_create(roi=roi, defaults={"model_difficulty": 1 - confidence})
        return roi

    def agreed(self, roi):
        """Named to species by another player, who agree: not hard, and not placed by an easier game either."""
        self.said(roi, **AFFINIS)
        RoiDifficulty.objects.update_or_create(roi=roi, defaults={"game_difficulty": 0.1, "game_answers": 1})
        return roi

    def open(self):
        return self.roi(self.t_affinis, validated=False)

    def hard(self):
        return set(game.open_rois().filter(game_relearn.hard_q()).values_list("id", flat=True))


class HardTests(RoutingCase):
    def test_what_makes_a_beetle_hard(self):
        disputed = self.open()
        self.said(disputed, subfamily="Scolytinae", genus="Xyleborus", species="affinis")
        RoiDifficulty.objects.create(roi=disputed, game_difficulty=0.6, game_answers=2)
        unsure = self.sure(self.open(), confidence=0.3)
        stuck = self.open()
        self.said(stuck, subfamily="Scolytinae", genus="Xyleborus")   # nobody reached species
        untouched = self.open()   # never answered, no IBBI-AI call
        self.assertEqual(self.hard(), {disputed.id, unsure.id, stuck.id, untouched.id})

    def test_what_does_not(self):
        self.sure(self.open())   # never answered, but IBBI-AI is confident
        self.agreed(self.open())
        self.assertEqual(self.hard(), set())

    @override_settings(GAME_HARD_FROM=0.8)
    def test_how_hard_is_a_setting(self):
        disputed = self.agreed(self.open())
        RoiDifficulty.objects.filter(roi=disputed).update(game_difficulty=0.6)
        self.assertEqual(self.hard(), set())

    def test_similarity_leaves_out_hard_beetles_this_player_has_compared(self):
        hard = self.open()
        self.assertEqual(list(game_relearn.hard_rois(self.user, game.open_rois())), [hard])
        self.said(hard, mode="pair", player=self.user, roi_b=self.roi(self.t_ferr), pair_answer="genus")
        self.assertEqual(list(game_relearn.hard_rois(self.user, game.open_rois())), [])

    @override_settings(GAME_STUCK_SHARE=1.0)
    def test_similarity_shows_hard_beetles_first(self):
        for t in (self.t_affinis, self.t_ferr, self.t_plat) * 3:
            self.roi(t)
        hard = {self.open().id for _ in range(6)}
        easy = {self.sure(self.open()).id for _ in range(6)}
        items = game.build_pair_items(self.user, 10)
        anchors = {i["a"] for i in items if not i["check"]}
        self.assertTrue(anchors)
        self.assertTrue(anchors <= {str(i) for i in hard})
        self.assertFalse(anchors & {str(i) for i in easy})


class IdentificationTests(RoutingCase):
    def pick(self, n, **kw):
        return set(game_relearn.identification_open(game.open_rois(), n, 0.5, None, **kw))

    def test_it_prefers_beetles_the_easier_games_have_placed(self):
        validated = self.roi(self.t_ferr)
        by_similarity = {self.open() for _ in range(2)}
        for roi in by_similarity:
            self.said(roi, mode="pair", roi_b=validated, pair_answer="tribe")
        by_ai = {self.sure(self.open()) for _ in range(2)}
        unplaced = {self.agreed(self.open()) for _ in range(4)}
        with override_settings(GAME_ID_PLACED_SHARE=1.0):
            self.assertEqual(self.pick(4), {r.id for r in by_similarity | by_ai})
        with override_settings(GAME_ID_PLACED_SHARE=0.5):
            picks = self.pick(4)
            self.assertEqual(len(picks), 4)
            self.assertGreaterEqual(len(picks & {r.id for r in by_similarity | by_ai}), 2)   # at least half placed
            self.assertTrue(picks <= {r.id for r in by_similarity | by_ai | unplaced})

    def test_hard_unplaced_beetles_wait_while_there_are_others(self):
        hard = {self.open().id for _ in range(5)}
        others = {self.agreed(self.open()).id for _ in range(3)}
        self.assertEqual(self.pick(3), others)
        self.assertFalse(self.pick(3) & hard)

    def test_the_feed_never_runs_dry(self):
        hard = {self.open().id for _ in range(4)}
        self.assertEqual(self.pick(4), hard)

    @override_settings(GAME_PEER_SHARE=0, GAME_ID_PLACED_SHARE=1.0)
    def test_identification_rounds_use_it(self):
        for t in (self.t_affinis, self.t_ferr, self.t_plat) * 2:
            self.roi(t)
        placed = {str(self.sure(self.open()).id) for _ in range(6)}
        held_back = {str(self.open().id) for _ in range(6)}
        opens = {i["a"] for i in game.build_classify_items(self.user, 10) if not i["check"]}
        self.assertTrue(opens)
        self.assertTrue(opens <= placed)
        self.assertFalse(opens & held_back)
