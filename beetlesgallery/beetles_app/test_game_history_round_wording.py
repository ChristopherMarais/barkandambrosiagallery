"""
History's wording and per-round layout (round 5): a session counts its rounds (one beetle each) and its game types,
and each round reads like the card after an answer (the review's .rv-head, .rv-verdict and .rv-pill).
"""
import re

from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game, game_levels
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, GameRound
from beetlesgallery.beetles_app.test_game_history_filters import HistoryFilterCase


def words(html):
    """The text of some HTML, tags gone and whitespace squeezed (numbers are wrapped in tags for digit groups)."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


class RoundWordingCase(HistoryFilterCase):
    def judged_session(self, modes, points=2.0, correct=True, judged=True):
        """A finished session, one answer per game in ``modes``, each judged against the truth (``judged``)."""
        when = timezone.now()
        rnd = GameRound.objects.create(player=self.user, mode=modes[0] if len(set(modes)) == 1 else "mixed",
                                       items=[{} for _ in modes], finished_at=when)
        for i, mode in enumerate(modes):
            fields = {f"correct_{r}": correct for r in game.RANKS} if mode == "classify" else {}
            ans = GameAnswer.objects.create(round=rnd, player=self.user, mode=mode, index=i, roi=self.a,
                                            roi_b=self.b if mode == "pair" else None, is_check=judged,
                                            pair_answer="genus" if mode == "pair" else "", **fields)
            AnswerPoints.objects.create(answer=ans, points=points,
                                        basis=AnswerPoints.Basis.TRUTH if judged else AnswerPoints.Basis.NONE)
        GameAnswer.objects.filter(round=rnd).update(answered_at=when)
        return rnd

    def sessions_html(self, query=""):
        """The History page's session list, the part that counts."""
        page = self.history(query).content.decode()
        return page[page.index('data-testid="sessions"'):page.index("Open a session to see")]


class WordingTests(RoundWordingCase):
    def test_a_session_counts_rounds_not_beetles(self):
        self.judged_session(["classify", "classify"])
        text = words(self.sessions_html())
        self.assertIn("2 rounds", text)
        self.assertNotIn("beetle", text)

    def test_one_round_is_singular(self):
        self.judged_session(["pair"])
        text = words(self.sessions_html())
        self.assertIn("1 round", text)
        self.assertNotIn("1 rounds", text)

    def test_a_mixed_session_counts_game_types(self):
        mixed = self.judged_session(["classify", "pair", "odd"])
        self.assertEqual(list(self.history().context["sessions"])[0].games_label, "3 game types")
        html = self.sessions_html()
        text = words(html)
        self.assertIn("3 game types", text)
        self.assertNotIn("3 games", text)
        self.assertIn(f'href="{reverse("game_round_review", args=[mixed.id])}"', html)

    def test_a_single_game_session_names_the_game(self):
        self.judged_session(["classify"])
        text = words(self.sessions_html())
        self.assertIn(game_levels.GAME_NAMES["classify"], text)
        self.assertNotIn("game types", text)

    def test_the_summary_counts_rounds_too(self):
        self.judged_session(["classify", "classify"])
        page = self.history("?game=classify").content.decode()
        summary = words(page[page.index('data-testid="history-summary"'):page.index('data-testid="sessions"')])
        self.assertIn("2 rounds", summary)
        self.assertNotIn("beetle", summary)


class RoundLayoutTests(RoundWordingCase):
    def test_each_round_renders_the_review_line(self):
        self.judged_session(["classify", "classify", "classify"])
        html = self.sessions_html()
        self.assertEqual(html.count('data-testid="round-row"'), 3)
        self.assertEqual(html.count('class="rv-head"'), 3)
        self.assertEqual(html.count('class="rv-verdict right"'), 3)
        self.assertIn("Correct to species · +2", html)   # the card's headline: deepest rank right, then the points

    def test_the_verdict_dot_follows_the_review_colours(self):
        self.judged_session(["classify"], correct=False)
        html = self.sessions_html()
        self.assertIn('class="rv-verdict wrong"', html)
        self.assertIn("Not quite · +2", html)

    def test_an_unchecked_round_is_grey(self):
        self.judged_session(["classify"], judged=False)
        html = self.sessions_html()
        self.assertIn('class="rv-verdict none"', html)
        self.assertIn("Not checked yet · +2 so far", html)

    def test_a_mixed_session_names_each_rounds_game(self):
        self.judged_session(["classify", "pair"])
        html = self.sessions_html()
        self.assertEqual(html.count('class="rv-pill"'), 2)
        self.assertIn(f'<span class="rv-pill">{game_levels.GAME_NAMES["classify"]}</span>', html)
        self.assertIn(f'<span class="rv-pill">{game_levels.GAME_NAMES["pair"]}</span>', html)

    def test_a_filtered_game_drops_the_game_names_but_keeps_the_rounds(self):
        self.judged_session(["classify", "pair"])
        html = self.sessions_html("?game=classify")
        self.assertNotIn("rv-pill", html)
        self.assertEqual(html.count('data-testid="round-row"'), 1)

    def test_long_sessions_show_ten_rounds_then_say_how_many_more(self):
        self.judged_session(["classify"] * 12)
        html = self.sessions_html()
        self.assertEqual(html.count('data-testid="round-row"'), 10)
        self.assertIn("more rounds in this session", words(html))
        self.assertIn("+2 more rounds", words(html).replace("+ ", "+"))

    def test_the_day_filter_keeps_the_rounds(self):
        self.judged_session(["classify", "classify"])
        html = self.sessions_html("?day=today")
        self.assertEqual(html.count('data-testid="round-row"'), 2)
        self.assertIn("2 rounds", words(html))

    def test_a_session_is_still_one_link_to_its_review(self):
        rnd = self.judged_session(["classify"])
        html = self.sessions_html()
        self.assertIn(f'href="{reverse("game_round_review", args=[rnd.id])}"', html)
        self.assertTrue(re.search(r'<li>\s*<a href="[^"]+" class="flex items-center', html))
