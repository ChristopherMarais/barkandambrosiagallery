"""The names on a reviewed photo start each row with its rank, short (owner): SF, T, G, S. The whole photo spells
the rank out."""
from pathlib import Path

from django.test import SimpleTestCase

GAME_PLAY = Path(__file__).resolve().parent.parent / "templates" / "beetles" / "game_play.html"


class ReviewRankTagTests(SimpleTestCase):
    def setUp(self):
        self.html = GAME_PLAY.read_text(encoding="utf-8")

    def test_the_short_rank_tags(self):
        self.assertIn('const RANK_ABBR = { subfamily: "SF", tribe: "T", genus: "G", species: "S" };', self.html)

    def test_each_row_gets_its_tag_and_the_whole_photo_the_full_rank(self):
        self.assertIn('const rk = full ? node("span", "rk", r.rank) : node("span", "rka", RANK_ABBR[r.rank] || "");', self.html)
        self.assertIn(".rv-names .rka {", self.html)
