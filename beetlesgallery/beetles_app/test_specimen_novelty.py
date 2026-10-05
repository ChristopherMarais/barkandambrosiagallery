"""
Once a validated beetle's name has been shown to a player, none of that specimen's photos is scored for them again
(#386): they would test memory, not skill. Specimens are matched by their id (depicts_specimen), ignoring case and
spaces; a beetle without one stands alone.
"""
from beetlesgallery.beetles_app import game
from beetlesgallery.beetles_app.models import Beetles
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase


class SpecimenNoveltyTests(ScoringCase):
    def photo(self, specimen):
        roi = self.roi(self.t_affinis)
        Beetles.objects.filter(pk=roi.pk).update(depicts_specimen=specimen)
        return roi

    def test_other_photos_of_a_revealed_specimen_are_revealed_too(self):
        dorsal, lateral, other = self.photo("SPEC-4411"), self.photo(" spec-4411 "), self.photo("SPEC-9")
        self.answer(self.user, dorsal, AFFINIS)
        revealed = game.revealed_ids(self.user)
        self.assertIn(lateral.id, revealed)
        self.assertNotIn(other.id, revealed)

    def test_beetles_without_a_specimen_id_stand_alone(self):
        seen, unrelated = self.photo(""), self.photo(None)
        self.answer(self.user, seen, AFFINIS)
        self.assertEqual(game.revealed_ids(self.user), {seen.id})
        self.assertNotIn(unrelated.id, game.revealed_ids(self.user))

    def test_a_round_never_scores_another_photo_of_a_specimen_already_named(self):
        dorsal = self.photo("SPEC-4411")
        siblings = {self.photo("SPEC-4411").id for _ in range(5)}
        self.answer(self.user, dorsal, AFFINIS)
        for _ in range(5):
            self.roi(self.t_ferr)   # fresh beetles to fill the round
        rnd = game.start_round(self.user, "classify", size=4)
        served = {i["a"] for i in rnd.items if i.get("check") and not i.get("retry")}
        self.assertFalse(served & {str(s) for s in siblings})
