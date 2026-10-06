"""Daily goal, streak, levels, badges and the recap: the things that keep people playing, without showing a live score."""
import json
from datetime import timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game_rewards as rewards
from beetlesgallery.beetles_app.models import GameAnswer, GameRound
from beetlesgallery.beetles_app.test_game import AFFINIS, GameCase


class RewardsCase(GameCase):
    def answers(self, n, days_ago=0, mode="classify", check=False, right=False, skipped=False):
        rnd = GameRound.objects.create(player=self.user, mode=mode, items=[])
        when = timezone.now() - timedelta(days=days_ago)
        made = []
        for i in range(n):
            roi = self.roi(self.t_affinis, validated=check)
            a = GameAnswer.objects.create(
                round=rnd, player=self.user, mode=mode, index=len(made), roi=roi, is_check=check, skipped=skipped,
                correct_species=True if (check and right) else (False if check else None),
                correct_genus=True if (check and right) else (False if check else None),
            )
            made.append(a.id)
        GameAnswer.objects.filter(id__in=made).update(answered_at=when)


class LevelAndGoalTests(RewardsCase):
    def set_score(self, score, rating=0.0):
        from beetlesgallery.beetles_app.models import PlayerScore
        PlayerScore.objects.update_or_create(player=self.user, defaults={"score": score, "rating": rating})

    def test_progress_counts_labelled_beetles_and_not_skips(self):
        self.answers(3)
        self.answers(2, skipped=True)
        state = rewards.progress(self.user)
        # 3 answers x 0.5 participation - 2 skips x 0.25 = 1 point so far
        self.assertEqual((state["total"], state["today"], state["level"], state["to_next"]), (3, 3, 1, 49))

    @override_settings(GAME_DAILY_GOAL=4)
    def test_the_daily_goal(self):
        self.answers(3)
        self.assertFalse(rewards.progress(self.user)["goal_met"])
        self.answers(1)
        self.assertTrue(rewards.progress(self.user)["goal_met"])

    def test_yesterdays_beetles_do_not_count_towards_todays_goal(self):
        self.answers(5, days_ago=1)
        self.assertEqual(rewards.progress(self.user)["today"], 0)


# A day counts towards the streak once that day's goal is reached (#423); with a goal of 1, every day played does
@override_settings(GAME_DAILY_GOAL=1)
class StreakTests(RewardsCase):
    def test_consecutive_days_make_a_streak(self):
        for d in (0, 1, 2):
            self.answers(1, days_ago=d)
        self.assertEqual(rewards.progress(self.user)["streak"], 3)

    def test_a_streak_is_still_alive_if_they_played_yesterday_but_not_yet_today(self):
        for d in (1, 2):
            self.answers(1, days_ago=d)
        self.assertEqual(rewards.progress(self.user)["streak"], 2)

    def test_a_missed_day_breaks_it(self):
        self.answers(1, days_ago=0)
        self.answers(1, days_ago=2)
        self.assertEqual(rewards.progress(self.user)["streak"], 1)

    def test_no_play_no_streak(self):
        self.assertEqual(rewards.progress(self.user)["streak"], 0)

    @override_settings(GAME_DAILY_GOAL=3)
    def test_a_day_below_the_goal_does_not_count(self):
        self.answers(3, days_ago=2)
        self.answers(1, days_ago=1)         # played, but short of the goal: the streak stops here
        self.answers(3, days_ago=0)
        self.assertEqual(rewards.progress(self.user)["streak"], 1)
        self.assertEqual(len(rewards.goal_days(self.user)), 2)


class BadgeTests(RewardsCase):
    def test_a_new_player_has_none_and_the_list_shows_them_all_locked(self):
        cards = rewards.badge_cards(self.user)
        self.assertEqual(len(cards), len(rewards.BADGES))
        self.assertFalse(any(c["earned"] for c in cards))

    def test_count_badges(self):
        self.answers(10)
        have = rewards.earned_badges(self.user)
        self.assertTrue({"first", "ten"} <= have)
        self.assertNotIn("hundred", have)

    @override_settings(GAME_DAILY_GOAL=1)
    def test_streak_badge(self):
        for d in range(3):
            self.answers(1, days_ago=d)
        self.assertIn("streak3", rewards.earned_badges(self.user))
        self.assertNotIn("streak7", rewards.earned_badges(self.user))

    @override_settings(GAME_DAILY_GOAL=3)
    def test_goal_badge(self):
        self.answers(3)
        self.assertIn("goal", rewards.earned_badges(self.user))

    def test_both_games_badge(self):
        self.answers(1, mode="classify")
        self.assertNotIn("both", rewards.earned_badges(self.user))
        self.answers(1, mode="pair")
        self.assertIn("both", rewards.earned_badges(self.user))

    def test_species_badges_need_a_scored_right_answer(self):
        self.answers(2, check=True, right=False)
        self.assertNotIn("species1", rewards.earned_badges(self.user))
        self.answers(1, check=True, right=True)
        self.assertIn("species1", rewards.earned_badges(self.user))

    def test_earned_before_a_moment(self):
        self.answers(10, days_ago=3)
        self.assertIn("ten", rewards.earned_badges(self.user, before=timezone.now() - timedelta(days=1)))
        self.assertNotIn("ten", rewards.earned_badges(self.user, before=timezone.now() - timedelta(days=5)))


class PlayEventsTests(RewardsCase):
    def test_nothing_to_celebrate_midway(self):
        before = rewards.progress(self.user)
        self.answers(1)
        self.assertEqual(rewards.play_events(self.user, before), [])   # the streak waits for the goal (#423)

    @override_settings(GAME_DAILY_GOAL=2)
    def test_reaching_the_goal_grows_the_streak_in_one_toast(self):
        self.answers(2, days_ago=1)
        self.answers(1)
        before = rewards.progress(self.user)
        self.answers(1)
        [event] = rewards.play_events(self.user, before)
        self.assertEqual((event["kind"], event["text"]), ("goal", "2 beetles today. 2-day streak!"))

    def set_score(self, score, rating=0.0):
        from beetlesgallery.beetles_app.models import PlayerScore
        PlayerScore.objects.update_or_create(player=self.user, defaults={"score": score, "rating": rating})

    def test_a_level_up_and_a_milestone(self):
        self.answers(9)
        self.set_score(40)
        before = rewards.progress(self.user)
        self.answers(1)
        self.set_score(55)
        events = rewards.play_events(self.user, before)
        kinds = [e["kind"] for e in events]
        self.assertIn("level", kinds)
        self.assertIn("milestone", kinds)
        self.assertIn("Odd One Out", next(e for e in events if e["kind"] == "level")["text"])   # level 2's new game

    def test_reaching_the_suggestions_level_says_so(self):
        self.set_score(1490, 0.75)
        before = rewards.progress(self.user)
        self.set_score(1510, 0.75)
        events = rewards.play_events(self.user, before)
        self.assertIn("proposals", [e["kind"] for e in events])
        self.assertIn("curators", next(e for e in events if e["kind"] == "proposals")["text"])

    @override_settings(GAME_DAILY_GOAL=2)
    def test_reaching_the_daily_goal_once(self):
        self.answers(1)
        before = rewards.progress(self.user)
        self.answers(1)
        self.assertIn("goal", [e["kind"] for e in rewards.play_events(self.user, before)])
        again = rewards.progress(self.user)
        self.answers(1)
        self.assertNotIn("goal", [e["kind"] for e in rewards.play_events(self.user, again)])

    def test_no_event_says_anything_about_accuracy(self):
        self.answers(9, check=True, right=True)
        before = rewards.progress(self.user)
        self.answers(1, check=True, right=True)
        text = json.dumps(rewards.play_events(self.user, before)).lower()
        for word in ("right", "correct", "wrong", "accura", "score"):
            self.assertNotIn(word, text)


class RecapTests(RewardsCase):
    def test_the_recap_covers_the_sitting_and_shows_the_score_only_now(self):
        self.answers(3, check=True, right=True)
        self.answers(1, check=True, right=False)
        self.answers(2)                                   # open items are not scored
        self.answers(1, skipped=True)
        recap = rewards.recap(self.user, timezone.now() - timedelta(hours=1))
        self.assertEqual((recap["labelled"], recap["skipped"], recap["scored"], recap["right"]), (6, 1, 4, 3))

    def test_it_lists_the_badges_earned_in_the_sitting_only(self):
        self.answers(5, days_ago=3)
        self.answers(5)
        recap = rewards.recap(self.user, timezone.now() - timedelta(hours=1))
        self.assertEqual([b["key"] for b in recap["badges"]], ["ten"])


class FeedAndHomeTests(RewardsCase):
    @override_settings(GAME_ROUND_SIZE=3)
    def test_the_feed_gets_a_chip_and_events_but_no_accuracy(self):
        for _ in range(3):
            self.roi(self.t_affinis, validated=False)
        rnd, item = self.play("classify")
        res = self.post("game_answer", dict(AFFINIS, index=item["index"]), rnd.id).json()
        self.assertEqual({k: res["chip"][k] for k in ("today", "goal", "goal_met", "streak")},
                         {"today": 1, "goal": 20, "goal_met": False, "streak": 0})
        self.assertEqual((res["chip"]["level"], res["chip"]["next_at"]), (1, 50))   # the level bar in the top bar
        self.assertEqual(res["events"], [])   # the streak only grows once the day's goal is reached
        self.assertNotIn("accuracy", json.dumps(res))

    def test_the_start_response_carries_the_chip(self):
        self.roi(self.t_affinis)
        self.client.force_login(self.user)
        data = self.post("game_start", {"mode": "classify"}).json()
        self.assertEqual(data["chip"]["goal"], 20)

    def test_the_home_page_shows_level_goal_streak_and_badges(self):
        self.answers(12)
        from beetlesgallery.beetles_app.models import PlayerScore
        PlayerScore.objects.update_or_create(player=self.user, defaults={"score": 60})
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_home")).content.decode()
        for text in ("Larva", "Level 2", "day streak", "/20 today"):
            self.assertIn(text, page)
        profile = self.client.get(reverse("game_profile", args=[self.user.id])).content.decode()
        for text in ("Warming up", "First steps"):
            self.assertIn(text, profile)

    def test_the_leaderboard_can_be_this_week(self):
        self.answers(2)
        self.answers(5, days_ago=20)
        self.client.force_login(self.user)
        from beetlesgallery.beetles_app import game_scoring
        game_scoring.recompute([self.user.id])
        week = self.client.get(reverse("game_leaderboard") + "?period=week").context["rows"]
        everything = self.client.get(reverse("game_leaderboard") + "?period=all").context["rows"]
        self.assertEqual((week[0]["viewed"], everything[0]["viewed"]), (2, 7))
