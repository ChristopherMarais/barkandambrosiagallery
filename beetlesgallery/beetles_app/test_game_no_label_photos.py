"""A photo of a specimen's label (aspect "label") gives the name away: the game never shows one, as a beetle to answer
or among a beetle's other photos, even with a box drawn on it."""
from django.test import override_settings

from beetlesgallery.beetles_app import game
from beetlesgallery.beetles_app.models import GameRound
from beetlesgallery.beetles_app.test_game import GameCase
from beetlesgallery.beetles_app.testing import make_beetle, make_image


class LabelPhotoTests(GameCase):
    def photo(self, aspect, specimen="", validated=True):
        image = make_image(image_file="originals/aa/bb/test.jpg")
        return make_beetle(image=image, taxon=self.t_affinis, bbox="validated" if validated else "unvalidated",
                           aspect=aspect, depicts_specimen=specimen)

    def test_no_label_photo_is_playable_whatever_it_is_called(self):
        labels = [self.photo(a) for a in ("label", "Label", "labels", "dorsal label")]
        beetles = [self.photo(a) for a in ("dorsal", "lateral", "", None)]
        playable = set(game.playable_rois().values_list("id", flat=True))
        self.assertTrue(playable.isdisjoint(r.id for r in labels))
        self.assertTrue({r.id for r in beetles} <= playable)
        self.assertFalse(game.check_rois().filter(id__in=[r.id for r in labels]).exists())
        self.assertFalse(game.open_rois().filter(id__in=[r.id for r in labels]).exists())

    @override_settings(GAME_ROUND_SIZE=10)
    def test_the_feed_never_shows_one(self):
        labels = {str(self.photo("label").id) for _ in range(5)} | {str(self.photo("label", validated=False).id)
                                                                     for _ in range(5)}
        for _ in range(3):
            self.photo("dorsal")
            self.photo("dorsal", validated=False)
        rnd, _ = self.play("classify")
        shown = {i for item in GameRound.objects.get(id=rnd.id).items for i in game._item_ids(item)}
        self.assertTrue(shown)
        self.assertTrue(labels.isdisjoint(str(i) for i in shown))

    def test_never_among_a_beetles_other_photos(self):
        dorsal = self.photo("dorsal", specimen="UF-8")
        lateral = self.photo("lateral", specimen="UF-8")
        self.photo("label", specimen="UF-8")
        self.assertEqual(game.specimen_photos(dorsal), [lateral])
