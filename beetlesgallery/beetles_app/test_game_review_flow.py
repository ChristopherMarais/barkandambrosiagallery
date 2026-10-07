"""
The answer flow with a review (#488): Submit locks the answer in and shows the review, Next brings the next beetle;
a slim bar for dead time; Back shows the last review again, from memory or (after a reload) from the past-review
endpoint, which serves only the player's own answers. The "Last beetle" bar and the timed grid reveal are gone.
"""
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app.test_game import AFFINIS, GameCase


@override_settings(GAME_ROUND_SIZE=2)
class PastReviewTests(GameCase):
    def answered(self):
        for _ in range(2):
            self.roi(self.t_affinis)
        rnd, item = self.play("classify")
        live = self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id).json()["review"]
        return rnd, item["index"], live

    def test_your_own_answer_comes_back_the_same_as_after_answering(self):
        rnd, index, live = self.answered()
        res = self.client.get(reverse("game_past_review", args=[rnd.id, index]))
        self.assertEqual(res.status_code, 200)
        past = res.json()["review"]
        self.assertEqual((past["headline"], past["classify"]), (live["headline"], live["classify"]))
        self.assertEqual(len(past["images"]), 1)

    def test_nobody_elses_answer_and_no_answer_that_isnt_there(self):
        rnd, index, _ = self.answered()
        self.assertEqual(self.client.get(reverse("game_past_review", args=[rnd.id, index + 1])).status_code, 404)
        self.client.force_login(get_user_model().objects.create_user("someone", password="pw"))
        self.assertEqual(self.client.get(reverse("game_past_review", args=[rnd.id, index])).status_code, 404)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("game_past_review", args=[rnd.id, index])).status_code, 404)
        self.client.logout()
        self.assertRedirectsToLogin(self.client.get(reverse("game_past_review", args=[rnd.id, index])))

    def test_the_page_knows_where_back_finds_your_latest_answer(self):
        self.client.force_login(self.user)
        self.assertIn('data-last-review-url=""', self.client.get(reverse("game_play", args=["classify"])).content.decode())
        rnd, index, _ = self.answered()
        page = self.client.get(reverse("game_play", args=["classify"])).content.decode()
        self.assertIn(f'data-last-review-url="{reverse("game_past_review", args=[rnd.id, index])}"', page)


class PageTests(GameCase):
    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=["mixed"])).content.decode()

    def test_submit_then_next(self):
        page = self.page()
        self.assertIn('id="submit" disabled><span id="submit-text">Submit</span>', page)
        self.assertIn('$("submit-text").textContent = onReview ? "Next" : "Submit";', page)
        self.assertIn('if (phase === "review") { if (performance.now() >= readyAt) next(); }', page)
        self.assertIn("readyAt = performance.now() + MASH_GUARD_MS;   // Enter pressed three times fast never skips the review", page)
        self.assertIn("then <strong>Submit</strong>", page)

    def test_the_answer_is_locked_in_after_submit(self):
        page = self.page()
        self.assertIn('root.classList.toggle("answer-locked", busy);', page)
        self.assertIn("if (busy || index < 0", page)                                  # the Similarity ladder
        self.assertIn("if (busy) { select.value = pick[rank]; return; }", page)      # the name lists
        self.assertIn('setPhase("answer");   // unlocked again', page)                # an error unlocks it

    def test_a_slim_bar_shows_while_the_game_waits(self):
        page = self.page()
        self.assertIn('id="wait-bar" class="hidden" role="progressbar"', page)
        self.assertIn('waiting(p === "send" || p === "next");', page)

    def test_the_review_replaces_the_last_beetle_bar_and_the_timed_reveal(self):
        page = self.page()
        self.assertIn('id="review"', page)
        self.assertIn("markTiles(cells, review);", page)   # a grid tile by tile since #602 (test_grid_lay_and_reveal)
        self.assertIn("confetti(review.celebrate.kind, review.celebrate.size, review.celebrate.colour)", page)   # round 5: colour too
        for gone in ('id="community"', "showCommunity", "Last beetle", "revealOdd", "revealSelect", "celebrate_size"):
            self.assertNotIn(gone, page)

    def test_back_shows_the_same_card_and_fetches_it_after_a_reload(self):
        page = self.page()
        self.assertIn('let previousUrl = root.dataset.lastReviewUrl || "";', page)
        self.assertIn("previous = (await api(previousUrl)).review;", page)
        self.assertIn('renderReview(previous, $("previous-review"));', page)
        self.assertIn("if (busy) return false;   // never over a review in progress", page)
        self.assertIn('class="btn-primary w-full h-12 text-base rounded-xl">Current beetle</button>', page)

    def test_new_text_calls_the_ai_ibbi_ai(self):
        page = self.page()
        self.assertIn('"IBBI-AI"', page)
        self.assertNotIn("classifier leans", page)
