"""
What players are told about how to play well: stop at the rank you're sure of, there is no timer and keys and
references are welcome, and a photo with no useful characters is answered, not reported (issues #358-#360).
"""
from pathlib import Path

from django.conf import settings
from django.urls import reverse

from beetlesgallery.beetles_app.test_game import GameCase


class GuidanceTests(GameCase):
    def test_how_it_works_says_how_to_play_well(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_how")).content.decode()
        self.assertIn("Stop at the rank you're sure of.", page)
        self.assertIn("There is no timer", page)
        self.assertIn("keys, papers or a reference collection", page)
        self.assertIn("isn't bad: answer as far as you can", page)   # a clear photo from an unusual side (#498)

    def test_the_tour_says_it_too(self):
        tour = (Path(settings.BASE_DIR) / "beetlesgallery/static/js/game_tour.js").read_text()
        self.assertIn("a sure tribe beats a wrong genus", tour)
        self.assertIn("No timer: take your time, use keys or references", tour)
