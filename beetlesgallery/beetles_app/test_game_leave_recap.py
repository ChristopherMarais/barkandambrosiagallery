"""
Leaving the game shows how the session went, on a phone as on a computer (#578): Exit opens the recap at once,
the back gesture shows it too, and a page closed or put away first closes the session with a beacon, so the game
home shows that recap once.
"""
import re
from datetime import timedelta

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game
from beetlesgallery.beetles_app.game_views import LAST_SESSION, _sitting_start
from beetlesgallery.beetles_app.models import GameAnswer, GameRound
from beetlesgallery.beetles_app.test_game import AFFINIS, GameCase


class BeaconTests(GameCase):
    def answered_round(self):
        self.roi(self.t_affinis)
        rnd, item = self.play("classify")
        self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id)
        return rnd

    def beacon(self, client, **fields):
        """What navigator.sendBeacon sends: a form post, the CSRF token in the form (a beacon can't set headers)."""
        return client.post(reverse("game_exit"), dict({"beacon": "1"}, **fields))

    def test_the_beacon_closes_the_session_and_the_home_shows_its_recap_once(self):
        rnd = self.answered_round()
        since = int((rnd.started_at.timestamp() - 60) * 1000)
        res = self.beacon(self.client, round=str(rnd.id), since=str(since))
        self.assertEqual(res.status_code, 204)
        rnd.refresh_from_db()
        self.assertIsNotNone(rnd.finished_at)   # the answers count, as with Exit
        home = self.client.get(reverse("game_home")).content.decode()
        self.assertIn('data-testid="last-session"', home)
        self.assertIn("Your last session", home)
        self.assertRegex(home, r'1</span> beetle labelled')
        self.assertNotIn('data-testid="last-session"', self.client.get(reverse("game_home")).content.decode())

    def test_the_beacon_passes_the_csrf_check_with_the_token_in_the_form(self):
        rnd = self.answered_round()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        page = client.get(reverse("game_play", args=["mixed"])).content.decode()
        token = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
        self.assertEqual(self.beacon(client, round=str(rnd.id), csrfmiddlewaretoken=token).status_code, 204)
        self.assertEqual(self.beacon(client, round=str(rnd.id)).status_code, 403)   # and it is still checked

    def test_a_recap_seen_on_the_page_is_not_shown_again_on_the_home(self):
        rnd = self.answered_round()
        since = int((rnd.started_at - timedelta(minutes=1)).timestamp() * 1000)
        self.beacon(self.client, round=str(rnd.id), since=str(since))   # left, back from the cache, then Exit
        self.assertIn(LAST_SESSION, self.client.session)
        self.assertEqual(self.post("game_exit", {"round": str(rnd.id), "since": since}).json()["recap"]["labelled"], 1)
        self.assertNotIn(LAST_SESSION, self.client.session)
        self.assertNotIn('data-testid="last-session"', self.client.get(reverse("game_home")).content.decode())

    def test_nothing_labelled_means_no_last_session_on_the_home(self):
        self.roi(self.t_affinis)
        rnd, _ = self.play("classify")
        self.beacon(self.client, round=str(rnd.id))
        self.assertNotIn('data-testid="last-session"', self.client.get(reverse("game_home")).content.decode())

    def test_a_bad_stamp_in_the_session_is_ignored(self):
        self.client.force_login(self.user)
        session = self.client.session
        session[LAST_SESSION] = "not a time"
        session.save()
        res = self.client.get(reverse("game_home"))
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('data-testid="last-session"', res.content.decode())

    def test_a_long_hide_ends_its_sitting_and_a_later_exit_keeps_that_recap_for_the_home(self):
        rnd = self.answered_round()
        answered_at = GameAnswer.objects.get(round=rnd).answered_at
        since = int((answered_at - timedelta(minutes=1)).timestamp() * 1000)
        until = int((answered_at + timedelta(minutes=1)).timestamp() * 1000)
        self.beacon(self.client, round=str(rnd.id), since=str(since), until=str(until))
        # the new sitting: a later answer, then Exit on the page (it began after the hidden one)
        later = int((answered_at + timedelta(minutes=30)).timestamp() * 1000)
        self.post("game_exit", {"since": later})
        self.assertIn(LAST_SESSION, self.client.session)   # still there for the home
        self.assertIn('data-testid="last-session"', self.client.get(reverse("game_home")).content.decode())

    def test_the_home_recaps_a_sitting_the_idle_rule_closed(self):
        """A phone that killed the page without a beacon: the home closes the idle batch and shows its recap once."""
        rnd = self.answered_round()
        old = timezone.now() - timedelta(minutes=game.IDLE_MINUTES + 5)
        GameRound.objects.filter(id=rnd.id).update(started_at=old - timedelta(minutes=2), finished_at=None)   # still open
        GameAnswer.objects.filter(round=rnd).update(answered_at=old)
        home = self.client.get(reverse("game_home")).content.decode()
        rnd.refresh_from_db()
        self.assertIsNotNone(rnd.finished_at)
        self.assertIn('data-testid="last-session"', home)
        self.assertRegex(home, r'1</span> beetle labelled')
        self.assertNotIn('data-testid="last-session"', self.client.get(reverse("game_home")).content.decode())

    def test_the_idle_rule_never_repeats_a_recap_seen_on_the_page(self):
        rnd = self.answered_round()
        self.post("game_exit", {})   # the recap on the page, without closing this batch (no round given)
        old = timezone.now() - timedelta(minutes=game.IDLE_MINUTES + 5)
        GameRound.objects.filter(id=rnd.id).update(started_at=old - timedelta(minutes=2), finished_at=None)   # still open
        GameAnswer.objects.filter(round=rnd).update(answered_at=old)
        self.assertNotIn('data-testid="last-session"', self.client.get(reverse("game_home")).content.decode())

    def test_a_sitting_reaches_back_over_short_gaps_only(self):
        self.client.force_login(self.user)
        last = timezone.now()
        roi = self.roi(self.t_affinis)
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        for i, minutes in enumerate((0, 3, 8, 40)):   # 40: a gap longer than the idle limit before it
            answer = GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=i, roi=roi)
            GameAnswer.objects.filter(pk=answer.pk).update(answered_at=last - timedelta(minutes=minutes))
        self.assertEqual(_sitting_start(self.user, last, None), last - timedelta(minutes=8))
        self.assertEqual(_sitting_start(self.user, last, last - timedelta(minutes=5)), last - timedelta(minutes=5))

    def test_the_beacon_needs_login(self):
        self.client.logout()
        self.assertRedirectsToLogin(self.beacon(self.client))


class LeavePageTests(GameCase):
    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=["mixed"])).content.decode()

    def test_exit_opens_the_recap_at_once_and_never_sends_the_player_away_first(self):
        page = self.page()
        start = page.index('$("exit").addEventListener("click"')
        handler = page[start:page.index('$("recap-more")', start)]
        self.assertLess(handler.index('$("recap").classList.remove("hidden")'), handler.index("await leaving"))
        self.assertNotIn("location.href", handler)
        self.assertNotIn("setTimeout", handler)   # no race that gives up and leaves without the recap
        self.assertIn("Saving your answers", handler)

    def test_the_back_gesture_closes_what_is_open_or_shows_the_recap(self):
        page = self.page()
        start = page.index('window.addEventListener("popstate"')
        handler = page[start:page.index("});", start)]
        self.assertIn("closeOverlays()", handler)
        self.assertIn('$("exit").click()', handler)
        self.assertNotIn("showPrevious", handler)   # Back no longer opens the last beetle's review instead
        # the entry that catches Back is added on the first tap or key: Chrome skips one added on load
        self.assertIn('["click", "keydown"].forEach((type) => document.addEventListener(type, catchBack, true))', page)
        self.assertNotIn('history.pushState(BACK_STATE, "");\n  window.addEventListener("popstate"', page)

    def test_closing_the_page_sends_a_beacon_to_exit(self):
        page = self.page()
        self.assertIn("navigator.sendBeacon(root.dataset.exitUrl, form)", page)
        self.assertIn('form.append("csrfmiddlewaretoken", csrf)', page)
        self.assertIn('window.addEventListener("pagehide", () => sendGoodbye());', page)
        self.assertIn("if (recapSeen || goodbyeSent || answered === answeredAtOpen", page)   # once, and only if needed

    def test_a_quick_switch_away_keeps_the_beetle_and_a_long_one_starts_afresh(self):
        page = self.page()
        self.assertIn(f'data-idle-minutes="{game.IDLE_MINUTES}"', page)
        start = page.index('document.addEventListener("visibilitychange"')
        handler = page[start:page.index("});", start)]
        self.assertIn('if (document.visibilityState === "hidden") { hiddenAt = Date.now(); return; }', handler)
        self.assertIn("if (away < AWAY_MS", handler)   # back soon: nothing happens, the same beetle waits
        self.assertEqual(page.count("const AWAY_MS"), 1)   # one script: a name declared twice stops it all
        self.assertLess(handler.index("if (away < AWAY_MS"), handler.index("sendGoodbye(left)"))
        self.assertLess(handler.index("sendGoodbye(left)"), handler.index("startFeed()"))

    def test_links_to_the_game_home_go_through_the_recap(self):
        page = self.page()
        self.assertIn('if (!link || link.closest("#recap")', page)

    def test_the_recap_fits_a_small_phone_and_scrolls(self):
        page = self.page()
        recap = page[page.index('<div id="recap"'):]
        recap = recap[:recap.index(">")]
        for part in ("fixed inset-0", "overflow-y-auto", "overscroll-contain", 'role="dialog"'):
            self.assertIn(part, recap)
        self.assertIn('id="recap-stats"', page)
        self.assertIn('id="recap-title"', page)
