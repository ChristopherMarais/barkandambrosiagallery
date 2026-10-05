"""Game wording ("correct", "Reviewed by curators"), celebration tiers and rarity colours after a session."""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from beetlesgallery.beetles_app.game_rewards import rarity_tier
from beetlesgallery.beetles_app.test_game import GameCase

TEMPLATES = Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles"


class WordingTests(SimpleTestCase):
    def test_players_read_correct_not_right(self):
        old = ["% right", "species right", "fully right", "Partly right", "> Right", ">Right<", "known beetles right",
               "right answer", "get right", "you're right", "were right", "right genus", "A right tribe", "it right then"]
        for path in list(TEMPLATES.glob("game*.html")) + list((TEMPLATES / "includes").glob("game*.html")):
            text = path.read_text()
            for phrase in old:
                with self.subTest(template=path.name, phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_reviewed_by_curators(self):
        for name in ("game_home.html", "game_history.html"):
            text = (TEMPLATES / name).read_text()
            self.assertIn("Reviewed by curators", text)
            self.assertNotIn("Checked later", text)
            self.assertNotIn("Checked since you played", text)

    def test_rarity_tiers(self):
        self.assertEqual([rarity_tier(v) for v in (None, 0.1, 0.4, 0.6, 0.8, 0.9, 0.97)],
                         ["common", "common", "uncommon", "rare", "epic", "legendary", "mythic"])

    def test_the_confetti_uses_the_logo_and_has_tiers(self):
        js = (Path(settings.BASE_DIR) / "beetlesgallery" / "static" / "js" / "beetle_confetti.js").read_text()
        for kind in ("partial", "validated", "level", "plain"):
            self.assertIn(f'"{kind}"', js)
        self.assertIn("BEETLE_LOGO_URL", js)


@override_settings(GAME_ROUND_SIZE=1)
class CelebrationTierTests(GameCase):
    def answer(self, answer):
        self.roi(self.t_affinis, validated=True)
        rnd, item = self.play("classify")
        return self.post("game_answer", dict(answer, index=item["index"]), rnd.id).json()

    def test_partial_answers_get_a_smaller_celebration(self):
        data = self.answer({"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus", "species": "ferrugineus"})
        self.assertEqual(data["celebrate"], "partial")
        self.assertEqual(data["celebrate_size"], 0.75)

    def test_a_full_answer_is_full_size(self):
        data = self.answer({"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus", "species": "affinis"})
        self.assertEqual((data["celebrate"], data["celebrate_size"]), ("validated", 1.0))

    def test_the_game_page_loads_the_logo_and_has_the_level_burst(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["classify"])).content.decode()
        self.assertIn("window.BEETLE_LOGO_URL", page)
        self.assertIn('confetti("level")', page)
        self.assertIn('id="recap-challenge"', page)
