"""
Leaving the game shows how the session went, on a phone as on a computer (#578): Exit opens the recap at once,
the back gesture shows it too, and a page closed or put away first closes the session with a beacon, so the game
home shows that recap once.
"""
import re

from django.test import Client
from django.urls import reverse

from beetlesgallery.beetles_app.game_views import LAST_SESSION
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
        self.beacon(self.client, round=str(rnd.id))   # put away, then back, then Exit
        self.assertIn(LAST_SESSION, self.client.session)
        self.assertEqual(self.post("game_exit", {"round": str(rnd.id)}).json()["recap"]["labelled"], 1)
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

    def test_closing_or_putting_away_the_page_sends_a_beacon_to_exit(self):
        page = self.page()
        self.assertIn("navigator.sendBeacon(root.dataset.exitUrl, form)", page)
        self.assertIn('form.append("csrfmiddlewaretoken", csrf)', page)
        self.assertIn('window.addEventListener("pagehide", sendGoodbye)', page)
        self.assertIn('if (document.visibilityState === "hidden") sendGoodbye();', page)
        self.assertIn("if (recapSeen || goodbyeSent || answered === answeredAtOpen", page)   # once, and only if needed

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
