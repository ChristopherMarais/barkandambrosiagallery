"""The Beetle ID game as one continuous feed: no rounds to the player, checks dropped in now and then, confetti, exit."""
import json
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game
from beetlesgallery.beetles_app.models import GameAnswer, GameRound
from beetlesgallery.beetles_app.test_game import AFFINIS, GameCase

FERR = {"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus", "species": "ferrugineus"}


class FeedTests(GameCase):
    @override_settings(GAME_ROUND_SIZE=2)
    def test_the_feed_carries_on_into_a_new_batch_without_a_break(self):
        for _ in range(4):
            self.roi(self.t_affinis, validated=False)
        rnd, item = self.play("classify")
        seen = set()
        for _ in range(2):
            data = self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id).json()
            seen.add(str(rnd.id))
            if "round" in data:
                rnd = GameRound.objects.get(id=data["round"])
            item = data["item"]
        self.assertEqual(len(seen), 1)   # the first batch ended after two answers...
        self.assertNotIn("done", data)   # ...and the second answer already returned the next beetle
        self.assertIsNotNone(GameRound.objects.filter(finished_at__isnull=False).first())
        self.assertIsNone(GameRound.objects.get(id=data["round"]).finished_at)   # (the one after may be built ahead, #494)

    @override_settings(GAME_ROUND_SIZE=2)
    def test_the_feed_ends_only_when_there_is_nothing_new_left(self):
        self.roi(self.t_affinis, validated=False)
        self.roi(self.t_ferr, validated=False)
        rnd, item = self.play("classify")
        data = {}
        for _ in range(2):
            data = self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id).json()
            item = data.get("item")
        self.assertTrue(data["done"])
        self.assertIn("review_url", data)

    def test_the_item_payload_still_hides_what_is_scored(self):
        self.roi(self.t_affinis)
        rnd, item = self.play("classify")
        self.assertEqual(set(item), {"index", "mode", "position", "total", "images", "prefetch"})


class ConfettiTests(GameCase):
    @override_settings(GAME_ROUND_SIZE=1)
    def answer(self, truth_taxon, answer, validated=True, mode="classify", **extra):
        self.roi(truth_taxon, validated=validated)
        if mode == "pair":
            self.roi(self.t_ferr)
        rnd, item = self.play(mode)
        body = answer if mode == "classify" else {"pair_answer": answer}
        return self.post("game_answer", dict(body, index=item["index"], **extra), rnd.id).json()

    def test_a_right_species_on_a_scored_beetle_gets_confetti(self):
        self.assertTrue(self.answer(self.t_affinis, AFFINIS)["celebrate"])

    def test_a_wrong_species_with_the_right_genus_gets_the_small_partial_kind(self):
        self.assertEqual(self.answer(self.t_affinis, FERR)["celebrate"], "partial")

    def test_a_genus_only_answer_gets_the_partial_kind(self):
        self.assertEqual(self.answer(self.t_affinis, {"subfamily": "Scolytinae", "genus": "Xyleborus"})["celebrate"], "partial")

    def test_no_confetti_on_a_beetle_we_do_not_know_the_answer_to(self):
        self.assertFalse(self.answer(self.t_affinis, AFFINIS, validated=False)["celebrate"])

    def test_no_confetti_for_a_skip(self):
        self.assertFalse(self.answer(self.t_affinis, {}, skipped=True)["celebrate"])

    def test_a_pair_answered_right_on_every_judged_claim_gets_confetti(self):
        # affinis and ferrugineus: the same genus, so "genus" is right everywhere...
        self.assertTrue(self.answer(self.t_affinis, "genus", mode="pair")["celebrate"])

    def test_a_pair_answered_wrong_or_unsure_does_not(self):
        self.assertFalse(self.answer(self.t_affinis, "different", mode="pair")["celebrate"])

    def test_a_pair_marked_not_sure_does_not(self):
        self.assertFalse(self.answer(self.t_affinis, "unsure", mode="pair")["celebrate"])


class ExitTests(GameCase):
    def test_exiting_closes_the_batch_so_the_answers_count(self):
        self.roi(self.t_affinis)
        rnd, item = self.play("classify")
        self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id)
        res = self.post("game_exit", {"round": str(rnd.id)})
        self.assertEqual(res.json()["url"], reverse("game_home"))
        self.assertEqual(res.json()["recap"]["labelled"], 1)
        rnd.refresh_from_db()
        self.assertIsNotNone(rnd.finished_at)

    def test_exit_ignores_someone_elses_or_an_unknown_batch(self):
        self.roi(self.t_affinis)
        rnd, _ = self.play("classify")
        self.client.force_login(self.staff)
        self.assertEqual(self.post("game_exit", {"round": str(rnd.id)}).status_code, 200)
        self.assertEqual(self.post("game_exit", {"round": "nope"}).status_code, 200)
        rnd.refresh_from_db()
        self.assertIsNone(rnd.finished_at)

    def test_exit_needs_login(self):
        self.client.logout()
        self.assertRedirectsToLogin(self.client.post(reverse("game_exit"), "{}", content_type="application/json"))

    @override_settings(GAME_ROUND_SIZE=3)
    def test_a_batch_left_open_is_closed_when_they_next_visit_the_game_home(self):
        for _ in range(3):
            self.roi(self.t_affinis, validated=False)
        rnd, item = self.play("classify")
        self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id)
        GameRound.objects.filter(id=rnd.id).update(started_at=timezone.now() - timedelta(hours=1))
        GameAnswer.objects.filter(round=rnd).update(answered_at=timezone.now() - timedelta(minutes=30))
        self.client.get(reverse("game_home"))
        rnd.refresh_from_db()
        self.assertIsNotNone(rnd.finished_at)

    @override_settings(GAME_ROUND_SIZE=3)
    def test_a_batch_in_play_is_not_closed_by_a_visit(self):
        for _ in range(3):
            self.roi(self.t_affinis, validated=False)
        rnd, item = self.play("classify")
        self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id)
        GameRound.objects.filter(id=rnd.id).update(started_at=timezone.now() - timedelta(hours=1))
        self.client.get(reverse("game_home"))        # they answered a moment ago
        rnd.refresh_from_db()
        self.assertIsNone(rnd.finished_at)


class SpreadTests(GameCase):
    def items(self, checks, opens):
        return [{"a": str(i), "b": None, "check": True} for i in range(checks)] + \
               [{"a": str(100 + i), "b": None, "check": False} for i in range(opens)]

    def test_nothing_is_lost_or_added(self):
        items = self.items(3, 7)
        self.assertEqual(sorted(i["a"] for i in game.spread(items)), sorted(i["a"] for i in items))

    def test_checks_are_spread_out_rather_than_clumped(self):
        for _ in range(200):
            feed = game.spread(self.items(2, 8))
            positions = [n for n, i in enumerate(feed) if i["check"]]
            self.assertEqual(len(positions), 2)
            self.assertGreaterEqual(positions[1] - positions[0], 4, positions)

    def test_the_first_check_is_not_always_in_the_same_place(self):
        firsts = {next(n for n, i in enumerate(game.spread(self.items(2, 8))) if i["check"]) for _ in range(200)}
        self.assertGreater(len(firsts), 2)

    def test_all_checks_or_all_open(self):
        self.assertEqual(len(game.spread(self.items(3, 0))), 3)
        self.assertEqual(len(game.spread(self.items(0, 3))), 3)
        self.assertEqual(game.spread([]), [])

    def test_a_short_batch_with_many_checks_still_places_every_one(self):
        feed = game.spread(self.items(5, 1))
        self.assertEqual(sum(1 for i in feed if i["check"]), 5)


class PlayPageTests(GameCase):
    def page(self, mode):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=[mode])).content.decode()

    def test_the_games_have_catchy_names(self):
        self.assertIn("Name That Beetle", self.page("classify"))
        self.assertIn("Family Ties", self.page("pair"))
        mixed = self.page("mixed")
        self.assertIn("Name That Beetle", mixed)
        self.assertIn("Family Ties", mixed)
        self.assertNotIn("Spot the relatives", mixed + self.page("pair"))

    def test_the_home_page_has_one_play_button_for_the_mixed_game(self):
        self.client.force_login(self.user)
        home = self.client.get(reverse("game_home")).content.decode()
        self.assertEqual(home.count('data-testid="play"'), 1)
        self.assertIn(reverse("game_play", args=["mixed"]), home)

    def test_there_is_an_exit_and_no_progress_or_live_score(self):
        for mode in ("classify", "pair"):
            page = self.page(mode)
            self.assertIn('id="exit"', page)
            self.assertNotIn("progress-bar", page)
            self.assertNotIn("progress-text", page)
            self.assertNotIn("Round complete", page)

    def test_name_that_beetle_stacks_the_four_ranks_broad_to_specific(self):
        page = self.page("classify")
        order = [page.index(f'data-rank="{r}"') for r in ("subfamily", "tribe", "genus", "species")]
        self.assertEqual(order, sorted(order))
        self.assertIn('id="skip"><span id="skip-text">Skip</span>', page)
        self.assertIn('id="submit"', page)


    def test_keyboard_hints_and_back_button_sit_with_the_answer_buttons(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["classify"])).content.decode()
        actions = page[page.index('<div id="actions">'):]
        actions = actions[:actions.index("</div>")]
        for part in ('id="skip"', 'id="submit"', '<kbd class="kbd">Enter</kbd>'):
            self.assertIn(part, actions)
        self.assertNotIn('id="open-search"', page)
        self.assertIn('(hover: hover) and (pointer: fine)', page)   # shortcuts only shown on a computer
        self.assertIn('addEventListener("popstate"', page)          # the phone's back button acts like Exit
        self.assertIn('id="community"', page)                        # what others said stays until closed

    def test_each_rank_list_has_its_own_search(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["classify"])).content.decode()
        for rank in ("subfamily", "tribe", "genus", "species"):
            self.assertIn(f'data-open-find="{rank}"', page)
            self.assertIn(f'data-find="{rank}"', page)
            self.assertIn(f'id="find-{rank}"', page)

    def test_family_ties_is_a_ladder_from_strangers_to_the_same_species_with_a_not_sure_button(self):
        page = self.page("pair")
        order = [page.index(f'data-choice="{c}"') for c in ("different", "subfamily", "tribe", "genus", "species")]
        self.assertEqual(order, sorted(order))
        self.assertNotIn('data-choice="unsure"', page)        # "Not sure" is the fixed button next to Next
        self.assertIn('$("skip-text").textContent = "Skip";', page)   # "Skip" in every game now (it still sends "unsure" here)

    def test_the_game_keeps_its_colours_to_a_small_palette(self):
        # The game may be more colourful than the rest of the site, but from one palette: RPG rarity colours
        # (grey, green, blue, purple, orange, gold) for levels and streaks, plus badge accents and beta.
        from django.conf import settings
        root = settings.BASE_DIR / "beetlesgallery" / "templates" / "beetles"
        source = "".join((root / n).read_text() for n in ("game_play.html", "game_home.html"))
        for colour in ("indigo", "emerald", "pink", "fuchsia", "rose", "cyan"):
            self.assertNotIn(colour + "-", source)


class OnboardingTests(GameCase):
    """New players get a walkthrough the first time and, for a few days, how to report a bad photo."""

    def page(self, query=""):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=["mixed"]) + query).content.decode()

    def answer_once(self, days_ago=0):
        from datetime import timedelta
        from django.utils import timezone
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        ans = GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=0,
                                        roi=self.roi(self.t_affinis), is_check=True, genus="Xyleborus")
        GameAnswer.objects.filter(pk=ans.pk).update(answered_at=timezone.now() - timedelta(days=days_ago))

    def test_a_first_game_starts_with_the_tour(self):
        page = self.page()
        self.assertIn('data-first="1"', page)
        self.assertIn("game_tour.js", page)
        self.assertIn('id="report-tip"', page)

    def test_after_the_first_answer_the_tour_only_comes_back_when_asked(self):
        self.answer_once()
        self.assertIn('data-first="0"', self.page())
        self.assertIn('data-first="1"', self.page("?tour=1"))
        self.assertIn("?tour=1", self.client.get(reverse("game_how")).content.decode())

    def test_photos_can_be_made_brighter(self):
        page = self.page()
        self.assertIn('id="light-btn"', page)
        self.assertIn('id="light-brightness"', page)
        self.assertIn('id="light-contrast"', page)
        self.assertIn("#photos canvas, #lightbox-img { filter: var(--photo-filter, none); }", page)   # crops and whole photo alike
        self.assertIn("resetLight();", page)                                                         # back to normal for the next beetle

    def test_the_report_tip_is_for_the_first_few_days_only(self):
        self.answer_once()
        self.assertIn('data-report-tip="1"', self.page())
        for days_ago in (1, 2, 3):
            self.answer_once(days_ago)
        self.assertIn('data-report-tip="0"', self.page())

    def test_box_shows_on_any_photo_and_the_crop_shows_where_it_sits(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["classify"])).content.decode()
        self.assertIn("#lightbox-box { border-radius: 0.5rem; border: 2px solid #fff; box-shadow: 0 0 0 1px rgba(0,0,0,.7)", page)   # white with a dark edge
        self.assertIn("const CROP_PAD = 0.25;", page)        # some of the photo around the beetle (#421)
        self.assertNotIn("photo-edge", page)                 # past the photo's edge is plain grey: no label needed

    def test_the_report_button_is_labelled(self):
        self.assertIn("<span>Report</span>", self.page())

    def test_every_photo_has_a_report_button_and_the_help_says_where(self):
        from pathlib import Path
        from django.conf import settings
        page = self.page()
        self.assertIn('report.className = "report-chip"', page)          # drawn on each photo in play
        self.assertIn("top right of the photo", page)                    # the report tip card
        tour = (Path(settings.BASE_DIR) / "beetlesgallery/static/js/game_tour.js").read_text()
        self.assertIn('el: "report-chip-0"', tour)                        # the tour spotlights the button itself
        self.assertIn("top right of the photo", tour)
