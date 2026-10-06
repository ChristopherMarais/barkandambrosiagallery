"""
The game home shows this week's top players, never all time (#497). While nobody has played this week it says so in
one line, with last week's top three beneath. Everyone takes part: there are no privacy settings any more, so every
player shows under their own name, and the Unlocks page has no Leaderboards card.
"""
from datetime import timedelta

from django.urls import reverse

from beetlesgallery.beetles_app import game, game_board
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, GamePreference, GameRound, PlayerScore
from beetlesgallery.beetles_app.templatetags.beetle_tags import digit_groups
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase
from beetlesgallery.beetles_app.testing import make_beetle


class BoardCase(ScoringCase):
    def setUp(self):
        super().setUp()
        self.ann, self.bob, self.cy, self.dee = (self.player(n) for n in ("ann", "bob", "cy", "dee"))
        for p, score in ((self.ann, 50000), (self.bob, 100), (self.cy, 100), (self.dee, 100)):   # ann leads all time
            PlayerScore.objects.create(player=p, score=score, rating=0.5, viewed=10)
        self.roi = make_beetle(bbox="unvalidated")
        self.this_week = game.week_start()
        self.last_week = self.this_week - timedelta(days=7)

    def points(self, player, pts, when):
        rnd = GameRound.objects.create(player=player, mode="classify", items=[])
        ans = GameAnswer.objects.create(round=rnd, player=player, mode="classify", index=0, roi=self.roi)
        GameAnswer.objects.filter(pk=ans.pk).update(answered_at=when)
        AnswerPoints.objects.create(answer=ans, points=pts)

    def home(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_home"))


class WeeklyHomeBoardTests(BoardCase):
    def test_the_home_board_counts_this_week_only(self):
        self.points(self.bob, 40, self.this_week + timedelta(hours=1))
        self.points(self.ann, 900, self.this_week - timedelta(days=3))   # last week
        response = self.home()
        self.assertEqual([(r["username"], r["score"]) for r in response.context["board"]], [("bob", 40)])
        page = response.content.decode()
        self.assertIn("Top players this week", page)
        self.assertNotIn("of all time", page)
        self.assertNotIn('data-testid="home-board-empty"', page)
        self.assertNotIn('data-testid="last-week"', page)   # last week's line is only for an empty week

    def test_an_empty_week_says_so_and_shows_last_weeks_top_three(self):
        for player, pts in ((self.ann, 30), (self.bob, 1234), (self.cy, 20), (self.dee, 5)):
            self.points(player, pts, self.last_week + timedelta(days=2))
        self.points(self.dee, 9999, self.last_week - timedelta(days=2))   # the week before: not last week's
        response = self.home()
        self.assertEqual(response.context["board"], [])
        self.assertEqual([(w["position"], w["username"], w["points"]) for w in response.context["last_week"]],
                         [(1, "bob", 1234), (2, "ann", 30), (3, "cy", 20)])
        page = response.content.decode()
        self.assertIn("Top players this week", page)
        self.assertIn('data-testid="home-board-empty"', page)
        self.assertIn("No scores yet this week.", page)
        self.assertIn('data-testid="last-week"', page)
        self.assertIn(f"({digit_groups(1234)})", page)   # grouped in threes
        self.assertIn(f'href="{reverse("game_profile", args=[self.bob.id])}"', page)
        self.assertIn("Leader&shy;board</a>", page)   # the way to the full board is always there
        self.assertIn('data-testid="podium"', page)   # and last week's podium still pops up

    def test_after_a_quiet_week_just_the_line(self):
        self.points(self.ann, 500, self.last_week - timedelta(days=3))   # two weeks ago
        response = self.home()
        self.assertEqual((response.context["board"], response.context["last_week"]), ([], []))
        page = response.content.decode()
        self.assertIn("No scores yet this week.", page)
        self.assertNotIn('data-testid="last-week"', page)
        self.assertIn("Leader&shy;board</a>", page)

    def test_last_weeks_top_matches_the_weekly_winners(self):
        for player, pts in ((self.cy, 40), (self.bob, 40), (self.ann, 60), (self.dee, -5)):
            self.points(player, pts, self.last_week + timedelta(hours=3))
        [week] = game_board.weekly_wins(top=5)
        top = game_board.last_week_top(top=5)
        self.assertEqual([w["player_id"] for w in top], [p for p, _ in week["places"]])
        self.assertEqual([w["username"] for w in top], ["ann", "bob", "cy"])   # a tie by who joined first; no losses

    def test_the_leaderboard_shows_the_same_last_week(self):
        self.points(self.ann, 500, self.last_week - timedelta(days=3))   # two weeks ago is not "last week"
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(reverse("game_leaderboard")), 'data-testid="last-week"')
        self.points(self.bob, 70, self.last_week + timedelta(days=1))
        page = self.client.get(reverse("game_leaderboard")).content.decode()
        self.assertIn('data-testid="last-week"', page)
        self.assertIn(f"bob</a> ({digit_groups(70)})", page)


class EveryoneTakesPartTests(BoardCase):
    def test_every_player_shows_by_name_with_a_link_to_their_profile(self):
        self.points(self.bob, 40, self.this_week + timedelta(hours=1))
        self.points(self.bob, 70, self.last_week + timedelta(days=1))
        profile = reverse("game_profile", args=[self.bob.id])
        self.client.force_login(self.user)
        pages = {name: self.client.get(url).content.decode() for name, url in (
            ("home", reverse("game_home")),
            ("leaderboard", reverse("game_leaderboard")),
            ("search", reverse("game_leaderboard") + "?q=bo"),
            ("profile", profile),
            ("expertise", reverse("game_player_expertise", args=[self.bob.id])),
        )}
        for name, page in pages.items():
            with self.subTest(page=name):
                self.assertIn("bob", page)
                self.assertNotIn("A player", page)
        for name in ("home", "leaderboard", "search"):
            self.assertIn(f'href="{profile}"', pages[name], name)
        self.assertIn("bob&rsquo;s expertise", pages["expertise"])

    def test_the_unlocks_page_has_no_leaderboards_card(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_unlocks")).content.decode()
        self.assertIn('id="focus-form"', page)
        for gone in ('data-testid="board-settings"', "Leaderboards</h2>", 'name="hide_name"', 'name="hide_boards"',
                     "A player"):
            self.assertNotIn(gone, page)

    def test_posting_the_old_leaderboards_card_changes_nothing(self):
        GamePreference.objects.create(player=self.user, focus_rank="genus", focus_value="Xyleborus")
        self.client.force_login(self.user)
        response = self.client.post(reverse("game_unlocks"), {"boards": "1", "hide_name": "on", "hide_boards": "on"})
        self.assertRedirects(response, reverse("game_unlocks"), fetch_redirect_response=False)
        pref = GamePreference.objects.get(player=self.user)
        self.assertEqual((pref.focus_rank, pref.focus_value), ("genus", "Xyleborus"))   # not taken as "no focus"
        self.client.post(reverse("game_unlocks"), {"focus_rank": ""})   # the focus form itself still clears it
        pref.refresh_from_db()
        self.assertEqual((pref.focus_rank, pref.focus_value), ("", ""))
