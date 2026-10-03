"""The "More photos of each beetle" unlock: other photos of exactly the same specimen, single-beetle photos only."""
from beetlesgallery.beetles_app import game, game_levels
from beetlesgallery.beetles_app.models import GamePreference, GameRound
from beetlesgallery.beetles_app.game_views import _item_payload
from beetlesgallery.beetles_app.test_game import GameCase
from beetlesgallery.beetles_app.testing import make_beetle, make_image


class SpecimenPhotoTests(GameCase):
    def photo(self, specimen, beetles=1, **fields):
        """A photo with ``beetles`` boxed ROIs; the first shows ``specimen``. Returns that first ROI."""
        image = make_image()
        first = make_beetle(image=image, taxon=self.t_affinis, bbox="validated", depicts_specimen=specimen, **fields)
        for _ in range(beetles - 1):
            make_beetle(image=image, taxon=self.t_affinis, bbox="validated", depicts_specimen="someone else")
        return first

    def test_other_single_beetle_photos_of_the_same_specimen(self):
        dorsal = self.photo("UF-123", aspect="dorsal")
        lateral = self.photo("uf-123 ", aspect="lateral")   # same id, written a little differently
        self.photo("UF-999")
        self.assertEqual(game.specimen_photos(dorsal), [lateral])
        self.assertEqual(game.specimen_photos(lateral), [dorsal])

    def test_photos_of_several_beetles_never_count(self):
        alone = self.photo("UF-1")
        crowded = self.photo("UF-1", beetles=2)
        self.assertEqual(game.specimen_photos(alone), [])     # the other photo has two beetles in it
        self.assertEqual(game.specimen_photos(crowded), [])   # and from it we can't tell which is which

    def test_no_specimen_id_no_photos(self):
        self.assertEqual(game.specimen_photos(self.photo("")), [])
        deleted = self.photo("UF-7")
        other = self.photo("UF-7")
        other.is_deleted = True
        other.save()
        self.assertEqual(game.specimen_photos(deleted), [])

    def test_locked_players_see_how_many_and_unlocked_players_see_them(self):
        main = self.photo("UF-55")
        self.photo("UF-55", aspect="lateral")
        rnd = GameRound.objects.create(player=self.user, mode="classify",
                                       items=[{"a": str(main.id), "b": None, "check": True}])
        image = _item_payload(rnd, 0)["images"][0]
        self.assertEqual(image["more"], 1)
        self.assertNotIn("photos", image)
        self.assertEqual(_item_payload(rnd, 0)["more_level"], game_levels.perk_level(game_levels.SPECIMEN_PHOTOS))
        GamePreference.objects.create(player=self.user, granted_perks=[game_levels.SPECIMEN_PHOTOS])
        image = _item_payload(rnd, 0)["images"][0]
        self.assertEqual([p["aspect"] for p in image["photos"]], ["lateral"])

    def test_it_is_a_level_unlock(self):
        self.assertIn(game_levels.SPECIMEN_PHOTOS, game_levels.PERKS)
        self.assertEqual(game_levels.perk_level(game_levels.SPECIMEN_PHOTOS), 3)   # early: other angles help from the start (#362)
