"""
Round 5, the game feels fast: the play page answers every press at once, keeps something moving while it waits (a
thin bar after a short stretch of nothing, the tiles' pulse, the checking dot), fills the loading screen with light
lines and beetle facts from a fixed list (no server calls), keeps Next on screen while the next beetle loads, and
never says the game is slow. Browser side only; the server's replies keep their shape.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import reverse

from beetlesgallery.beetles_app.test_game import GameCase

PAGE = (Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles" / "game_play.html").read_text(encoding="utf-8")


def between(start, end):
    return PAGE[PAGE.index(start):PAGE.index(end, PAGE.index(start))]


class PressedTests(SimpleTestCase):
    """Instant feedback: the browser's own :active, so a press shows in the frame of the touch, before any reply."""

    def test_every_button_and_rank_row_dims_when_pressed(self):
        self.assertIn(":is(#game, #lightbox, #recap) :is(button:not(:disabled), .rank-row):active", PAGE)   # the browser's :active
        self.assertIn(":is(#game, #lightbox, #recap) :is(button, .rank-row).pressed", PAGE)   # the class, at pointerdown
        self.assertIn("{ filter: brightness(0.9); }", PAGE)

    def test_the_press_class_goes_on_at_pointerdown_and_off_at_pointerup(self):
        self.assertIn('document.addEventListener("pointerdown", (e) => {', PAGE)
        self.assertIn('el.classList.add("pressed");', PAGE)
        self.assertIn('["pointerup", "pointercancel", "lostpointercapture"]', PAGE)
        self.assertIn('document.querySelectorAll(".pressed").forEach((el) => el.classList.remove("pressed"));', PAGE)

    def test_busy_buttons_stay_tappable_so_a_tap_is_never_silent(self):
        self.assertNotIn('id="back" disabled', PAGE)
        self.assertIn('id="back" aria-disabled="true"', PAGE)
        self.assertIn('setOff($("back"), busy || !(previous || previousUrl));', PAGE)
        self.assertIn('setOff($("skip"), busy);', PAGE)
        self.assertIn("function setOff(btn, off)", PAGE)

    def test_the_tiles_press_too(self):   # a photo's tile is a button (.frame), so the same rule holds for it
        self.assertIn('frame.className = "frame";', PAGE)
        self.assertIn("button:not(:disabled), .rank-row):active", PAGE)


class OptimisticTests(SimpleTestCase):
    """After Submit the review's card shows at once, with the answer; the server's review replaces it."""

    def test_the_checking_card_shows_straight_after_submit(self):
        self.assertIn("if (!skipping) showPending();", PAGE)
        self.assertIn('node("span", "", "Checking your answer…")', PAGE)
        self.assertIn("function yourAnswer()", PAGE)

    def test_a_failed_answer_takes_the_card_away_again(self):
        catch = between("    } catch (e) {\n      sweepStop(true);", 'setPhase("answer");   // unlocked again')
        self.assertIn("endReview();", catch)

    def test_a_reply_without_a_review_takes_the_card_away_too(self):
        self.assertIn("if (!skipping && !data.review) endReview();", PAGE)


class NextTests(SimpleTestCase):
    """Next is visible from Submit to the next beetle; while that beetle loads it is busy, never gone."""

    def test_next_is_on_the_review_and_while_waiting(self):
        self.assertIn('const waitingNext = phase === "send" || phase === "next";', PAGE)
        self.assertIn('const onReview = phase === "review" || waitingNext;', PAGE)
        self.assertIn('$("submit-text").textContent = onReview ? "Next" : "Submit";', PAGE)

    def test_busy_next_is_tappable_with_a_sheen_and_a_spinner(self):
        self.assertIn('$("submit").disabled = !onReview && (busy || !ready);', PAGE)
        self.assertIn('setOff($("submit"), waitingNext);', PAGE)
        self.assertIn('$("submit").classList.toggle("next-busy", waitingNext);', PAGE)
        self.assertIn('$("submit").setAttribute("aria-busy", waitingNext ? "true" : "false");', PAGE)
        self.assertIn('id="submit-spin" class="fi fi-rr-spinner hidden"', PAGE)
        self.assertIn("#submit.next-busy {", PAGE)

    def test_the_busy_next_still_responds_to_taps_and_keys(self):   # the guard inside send() and next()
        self.assertIn('if (phase === "review") { if (performance.now() >= readyAt) next(); }', PAGE)
        self.assertIn("if (busy || performance.now() < readyAt) return;", PAGE)


class WatchdogTests(SimpleTestCase):
    """If nothing new has happened for about 1.5 s, the thin bar moves (loading screen, and the wait after a tap)."""

    def test_the_stuck_limit_and_the_check(self):
        self.assertIn("const STUCK_MS = 1500;", PAGE)
        self.assertIn("setInterval(syncBars, 150);", PAGE)
        self.assertIn("const stuck = (loading || waitingOn) && performance.now() - movedAt >= STUCK_MS;", PAGE)

    def test_the_bars_follow_the_check(self):
        self.assertIn('$("loading-bar").classList.toggle("hidden", !(loading && stuck));', PAGE)
        self.assertIn('$("wait-bar").classList.toggle("hidden", !(!loading && waitingOn && stuck));', PAGE)
        self.assertIn('id="wait-bar" class="hidden" role="progressbar"', PAGE)   # the old markup, unchanged
        self.assertIn('id="loading-bar" class="hidden"', PAGE)

    def test_a_reply_or_a_photo_in_its_tile_counts_as_movement(self):
        self.assertIn("moved();   // a reply is something new", PAGE)
        self.assertIn('cell.classList.add("landed");\n        moved();', PAGE)
        self.assertIn("function waiting(on) {", PAGE)
        self.assertIn('waiting(p === "send" || p === "next");', PAGE)   # the wait after Submit or Next

    def test_the_old_150_ms_timer_is_gone(self):
        self.assertNotIn("waitTimer", PAGE)


class LoadingTests(SimpleTestCase):
    """The loading screen: one message at a time (not a short line and a beetle fact stacked and both animating at
    once, #play-loading-double), light, no word about speed, and facts from a fixed list with no server calls."""

    def loading_block(self):   # the constants and loadingLines(); warmOthers() after them does call the server
        return between("const LOADING_LINES", "// ---------- the feed")

    def test_no_line_says_the_game_is_slow(self):
        self.assertNotIn("taking a while", PAGE)
        self.assertNotIn("frass", PAGE)
        self.assertNotIn("waiting for the fungus garden to grow", PAGE)   # the old slow line (fact 2 is about fungus gardens)
        self.assertNotIn("slow", self.loading_block().lower())

    def test_the_lines_are_short_and_fun(self):
        lines = re.findall(r'"([^"]+)"', between("const LOADING_LINES", "const LOADING_FACTS"))
        self.assertGreaterEqual(len(lines), 3)
        self.assertEqual(lines[:2], ["Finding beetles…", "Building your gallery…"])   # the first two stay
        for line in lines:
            self.assertLessEqual(len(line), 30, line)

    def test_the_facts_are_a_fixed_list_of_beetle_facts(self):
        facts = re.findall(r'"([^"]+)"', between("const LOADING_FACTS", "const LOADING_BASE_MS"))
        self.assertEqual(len(facts), 8)   # the owner's seven, in this order, then the hand-drawn art
        self.assertEqual(len(set(facts)), len(facts))
        self.assertTrue(facts[0].startswith("In a Colorado study, forests killed by spruce beetles had 62% more flowers"))
        self.assertIn("Ambrosia beetles grow their own fungus inside the wood, and they eat it.", facts)
        self.assertNotIn("preprint", facts[5].lower())   # labelled in the code comment only, not on the page
        self.assertIn("LOADING_FACTS[(turn / 2 - 1) % LOADING_FACTS.length]", PAGE)

    def test_the_lines_and_facts_share_one_slot(self):
        # one element, not a separate #loading-fact stacked underneath it (the regression the owner flagged)
        self.assertNotIn('id="loading-fact"', PAGE)
        self.assertNotIn('data-testid="loading-fact"', PAGE)
        self.assertIn('line.textContent = LOADING_LINES[0];', PAGE)
        self.assertIn('turn % 2 === 1 ? LOADING_LINES[((turn - 1) / 2) % LOADING_LINES.length]', PAGE)

    def test_the_facts_and_lines_do_not_call_the_server(self):
        self.assertNotIn("api(", self.loading_block())
        self.assertNotIn("fetch(", self.loading_block())

    def test_reduced_motion_keeps_the_first_line_and_fact(self):
        self.assertIn("if (still) return;   // reduced motion: the first line stays put", PAGE)


class SwitchTests(SimpleTestCase):
    """Switching game: the old game fades out at once, and the new one lays its tiles."""

    def test_the_old_game_fades_at_once(self):
        self.assertIn("#stage.switching #photo-area { opacity: 0.3; }", PAGE)
        self.assertIn("async function savePrefs(body) {\n    switching(true);", PAGE)
        self.assertIn("function switching(on) { $(\"stage\").classList.toggle(\"switching\", on); }", PAGE)

    def test_a_failed_switch_brings_the_game_back(self):
        self.assertIn("      switching(false);\n      toast({ kind: \"lock\"", PAGE)
        self.assertIn("      if (keep) switching(false);\n      if (keep) setPhase(before);", PAGE)


class MotionTests(SimpleTestCase):
    """Every new effect is short, and the moving ones only run when the reader allows motion."""

    def test_the_moving_effects_sit_under_no_preference(self):
        block = between("@media (prefers-reduced-motion: no-preference) {\n    #submit.next-busy", "@keyframes next-sheen")
        self.assertIn("#loading-line.new", block)
        self.assertIn("tile-wait", block)
        self.assertIn("rv-pulse", PAGE)
        self.assertIn("@keyframes tile-wait", PAGE)
        self.assertIn("#loading-bar span { animation: none; left: 0; width: 100%; opacity: 0.5; }", PAGE)

    def test_the_tiles_pulse_only_while_they_lay(self):   # a failed photo keeps its plain grey tile
        self.assertIn('#photos.laying .cell.slot:not(.landed)::before', PAGE)


class RenderedPageTests(GameCase):
    def test_the_play_page_carries_the_new_parts(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        self.assertIn('data-testid="loading-line"', page)
        self.assertNotIn('id="loading-fact"', page)   # one message, not two stacked (#play-loading-double)
        self.assertIn('id="loading-bar"', page)
        self.assertIn('id="submit-spin"', page)
        self.assertIn('id="back" aria-disabled="true"', page)
        self.assertNotIn("taking a while", page)
