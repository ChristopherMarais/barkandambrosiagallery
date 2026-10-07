"""History by game and by day (#574): the game home's game cards, streak and "today" cards open History filtered."""
import zoneinfo
from datetime import datetime, time, timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game_levels
from beetlesgallery.beetles_app.middleware import TIMEZONE_COOKIE
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, GameRound
from beetlesgallery.beetles_app.test_game import GameCase


class HistoryFilterCase(GameCase):
    def setUp(self):
        super().setUp()
        self.a = self.roi(self.t_affinis)
        self.b = self.roi(self.t_ferr)
        self.client.force_login(self.user)

    def session(self, modes, when=None, points=1.0):
        """A finished session of these games (one answer each, ``points`` apiece), played ``when`` (default now)."""
        when = when or timezone.now()
        rnd = GameRound.objects.create(player=self.user, mode=modes[0] if len(set(modes)) == 1 else "mixed",
                                       items=[{} for _ in modes], finished_at=when)
        for i, mode in enumerate(modes):
            ans = GameAnswer.objects.create(round=rnd, player=self.user, mode=mode, index=i, roi=self.a,
                                            roi_b=self.b if mode == "pair" else None, is_check=False,
                                            pair_answer="genus" if mode == "pair" else "")
            AnswerPoints.objects.create(answer=ans, points=points)
        GameAnswer.objects.filter(round=rnd).update(answered_at=when)
        return rnd

    def history(self, query=""):
        res = self.client.get(reverse("game_history") + query)
        self.assertEqual(res.status_code, 200)
        return res


class HomeLinksTests(HistoryFilterCase):
    def test_each_game_card_opens_its_history(self):
        page = self.client.get(reverse("game_home")).content.decode()
        for key in game_levels.GAMES:
            with self.subTest(key=key):
                self.assertIn(f'href="{reverse("game_history")}?game={key}"', page)
                self.assertIn(f'data-testid="game-card-{key}"', page)
                self.assertIn(game_levels.GAME_NAMES[key], page)
        self.assertIn("focus-visible:outline", page)   # a clear focus ring on the cards

    def test_the_streak_and_today_cards_open_their_days(self):
        page = self.client.get(reverse("game_home")).content.decode()
        self.assertIn(f'href="{reverse("game_history")}?day=streak"', page)
        self.assertIn(f'href="{reverse("game_history")}?day=today"', page)

    def test_the_profile_cards_stay_plain(self):
        page = self.client.get(reverse("game_profile", args=[self.user.id])).content.decode()
        self.assertIn('data-testid="game-split"', page)
        self.assertNotIn('data-testid="game-card-pair"', page)


class GameFilterTests(HistoryFilterCase):
    def test_one_game_lists_its_sessions_and_only_its_answers(self):
        mixed = self.session(["classify", "classify", "pair"], points=2)
        pairs = self.session(["pair"], points=5)
        res = self.history("?game=classify")
        sessions = list(res.context["sessions"])
        self.assertEqual([r.id for r in sessions], [mixed.id])
        self.assertEqual((sessions[0].labelled, sessions[0].points), (2, 4))   # its two Naming answers only
        self.assertEqual(sessions[0].games_label, "Naming")
        page = res.content.decode()
        self.assertIn(f'{reverse("game_round_review", args=[mixed.id])}?game=classify', page)
        self.assertEqual((res.context["summary"]["beetles"], res.context["summary"]["points"]), (2, 4))
        self.assertIn('data-testid="game-summary"', page)   # the game's own accuracy and points
        res = self.history("?game=pair")
        self.assertEqual({r.id for r in res.context["sessions"]}, {mixed.id, pairs.id})
        self.assertEqual(res.context["summary"]["beetles"], 2)

    def test_all_games_is_unchanged(self):
        mixed = self.session(["classify", "pair"])
        res = self.history()
        self.assertEqual(list(res.context["sessions"])[0].games_label, "2 game types")
        self.assertIsNone(res.context["summary"])
        self.assertNotIn('data-testid="history-summary"', res.content.decode())
        self.assertIn(f'href="{reverse("game_round_review", args=[mixed.id])}"', res.content.decode())

    def test_a_skip_counts_no_beetle_but_keeps_its_points(self):
        rnd = self.session(["odd"], points=1)
        skip = GameAnswer.objects.create(round=rnd, player=self.user, mode="odd", index=1, roi=self.a, skipped=True)
        AnswerPoints.objects.create(answer=skip, points=0.25)
        for query in ("", "?game=odd"):
            with self.subTest(query=query):
                row = list(self.history(query).context["sessions"])[0]
                self.assertEqual((row.labelled, row.points), (1, 1.25))
        self.assertEqual(self.history("?game=odd").context["summary"]["points"], 1.25)

    def test_an_unknown_game_shows_everything(self):
        self.session(["classify"])
        self.assertEqual(self.history("?game=nope").context["game_key"], "")

    def test_the_filter_row_names_every_game(self):
        page = self.history("?game=odd").content.decode()
        self.assertIn('data-testid="game-filter"', page)
        for key in game_levels.GAMES:
            self.assertIn(f'href="?game={key}"', page)
            self.assertIn(f">{game_levels.GAME_NAMES[key]}</a>", page)
        self.assertIn('aria-current="true" class', page)

    def test_an_empty_game_says_so(self):
        self.session(["pair"])
        for key in ("classify", "odd", "select"):
            with self.subTest(key=key):
                page = self.history(f"?game={key}").content.decode()
                self.assertIn('data-testid="filtered-empty"', page)
                self.assertIn(f"Nothing here yet for {game_levels.GAME_NAMES[key]}", page)

    def test_paging_keeps_the_filter(self):
        for i in range(21):
            self.session(["select"], when=timezone.now() - timedelta(minutes=i))
        self.session(["pair"])
        page = self.history("?game=select").content.decode()
        self.assertIn("game=select&amp;page=2", page)
        self.assertIn("?tab=sessions&amp;game=select", page)   # the Sessions tab keeps it too
        second = self.history("?game=select&page=2")
        self.assertEqual(len(second.context["sessions"]), 1)
        self.assertIn("game=select&amp;page=1", second.content.decode())

    def test_the_session_review_shows_that_game_only(self):
        mixed = self.session(["classify", "pair", "classify"])
        url = reverse("game_round_review", args=[mixed.id])
        only = self.client.get(url + "?game=pair")
        self.assertEqual([i["mode"] for i in only.context["feedback"]["items"]], ["pair"])
        self.assertContains(only, 'data-testid="only-game"')
        everything = self.client.get(url)
        self.assertEqual(len(everything.context["feedback"]["items"]), 3)
        self.assertNotContains(everything, 'data-testid="only-game"')


class DayFilterTests(HistoryFilterCase):
    def test_today_lists_todays_sessions(self):
        today = self.session(["classify"])
        self.session(["classify"], when=timezone.now() - timedelta(days=3))
        res = self.history("?day=today")
        self.assertEqual([r.id for r in res.context["sessions"]], [today.id])
        self.assertIn(">Today</h2>", res.content.decode())

    def test_a_date_and_a_game_combine(self):
        day = timezone.localdate() - timedelta(days=2)
        when = timezone.make_aware(datetime.combine(day, time(12)))
        both = self.session(["classify", "pair"], when=when)
        self.session(["classify"], when=when)
        self.session(["pair"])
        res = self.history(f"?game=pair&day={day.isoformat()}")
        self.assertEqual([r.id for r in res.context["sessions"]], [both.id])
        page = res.content.decode()
        self.assertIn(f"?game=classify&amp;day={day.isoformat()}", page)   # the game chips keep the day
        self.assertIn("?game=pair&amp;day=today", page)                   # and the day chips keep the game

    def test_nonsense_and_future_days_show_every_day(self):
        self.session(["classify"])
        for day in ("soon", (timezone.localdate() + timedelta(days=1)).isoformat()):
            self.assertIsNone(self.history(f"?day={day}").context["window"])

    @override_settings(GAME_DAILY_GOAL=2)
    def test_the_streak_covers_its_days(self):
        now = timezone.now()
        inside = [self.session(["classify", "pair"], when=now - timedelta(days=d)) for d in (0, 1, 2)]
        self.session(["classify", "classify"], when=now - timedelta(days=5))   # an older streak
        res = self.history("?day=streak")
        self.assertEqual([r.id for r in res.context["sessions"]], [r.id for r in inside])   # newest first
        self.assertIn("Your 3-day streak", res.content.decode())

    def test_no_streak_says_so(self):
        self.assertIn("No streak yet", self.history("?day=streak").content.decode())

    def test_today_is_the_players_own_day(self):
        tz = zoneinfo.ZoneInfo("Pacific/Kiritimati")   # UTC+14: their today is mostly the server's tomorrow
        self.client.cookies[TIMEZONE_COOKIE] = "Pacific/Kiritimati"
        midnight = datetime.combine(timezone.now().astimezone(tz).date(), time.min, tzinfo=tz)
        yesterday = self.session(["classify"], when=midnight - timedelta(minutes=1))
        today = self.session(["classify"], when=midnight + timedelta(minutes=1))
        res = self.history("?day=today")
        self.assertEqual([r.id for r in res.context["sessions"]], [today.id])
        self.assertNotIn(yesterday.id, [r.id for r in res.context["sessions"]])
