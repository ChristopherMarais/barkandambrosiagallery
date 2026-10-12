"""
The session review keeps the fully correct answers in their fold, but as whole cards with their photos (every game),
loaded the first time the fold opens, so a player can still look at the beetles they got right.
"""
from pathlib import Path

from django.test import SimpleTestCase

PAGE = Path(__file__).resolve().parent.parent / "templates" / "beetles" / "game_round_review.html"


class CorrectAnswersKeepTheirPhotosTests(SimpleTestCase):
    def setUp(self):
        self.page = PAGE.read_text(encoding="utf-8")

    def test_a_correct_answer_is_a_whole_card_in_the_fold(self):
        self.assertIn('li.dataset.testid = right ? "item-row-correct" : "item-card";', self.page)
        self.assertIn("(right ? correctList : list).appendChild(li);", self.page)
        self.assertNotIn("if (isCorrect(item)) {", self.page)   # no more one-line rows without photos

    def test_its_photos_load_when_the_fold_opens(self):
        self.assertIn("if (right) correctPhotos.push(() => { img.src = side.url; });", self.page)
        self.assertIn('$("correct-group").addEventListener("toggle", () => { if ($("correct-group").open) loadCorrectPhotos(); });',
                      self.page)
        self.assertIn("if (group.open) loadCorrectPhotos();", self.page)

    def test_its_photos_open_and_report_like_the_others(self):
        self.assertIn('window.onLongPress($("items-correct"), ".review-photo",', self.page)
        self.assertIn('[list, correctList].forEach((ul) => ul.querySelectorAll("[data-report]")', self.page)
