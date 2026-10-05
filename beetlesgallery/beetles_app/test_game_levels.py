"""Levels need points and reliability; perks; whose labels reach curators; experts' labels applied without review."""
from django.test import SimpleTestCase
from django.test import override_settings

from beetlesgallery.beetles_app import game_levels as levels
from beetlesgallery.beetles_app.game_trust import auto_apply_expert_labels, recompute_skills
from beetlesgallery.beetles_app.models import Beetles, LabelReview, PlayerScore
from beetlesgallery.beetles_app.test_game import AFFINIS, FERR
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase


class LevelTests(ScoringCase):
    def test_a_level_needs_both_points_and_reliability(self):
        self.assertEqual(levels.describe(0, 0)["level"], 1)
        self.assertEqual(levels.describe(60, 0)["level"], 2)
        self.assertEqual(levels.describe(200, 0.2)["level"], 2)     # enough points for 3, not reliable enough
        self.assertEqual(levels.describe(200, 0.4)["level"], 3)
        self.assertEqual(levels.describe(20000, 0.99)["name"], "Coleopterist")
        self.assertEqual(levels.describe(30000, 0.9)["name"], "Coleopterist")   # the top needs 92% reliability
        self.assertEqual(levels.describe(30000, 0.95)["name"], "King of Bark and Ambrosia")
        self.assertEqual(levels.describe(30000, 0.95)["level"], 10)

    def test_it_says_what_is_missing_for_the_next_level(self):
        nxt = levels.describe(200, 0.2)["next"]
        self.assertEqual((nxt["level"], nxt["points_needed"], nxt["rating_short"]), (3, 0, True))
        self.assertEqual(levels.describe(100, 0.9)["next"]["points_needed"], 50)

    def test_perks_build_up(self):
        self.assertEqual(levels.describe(0, 0)["perks"], set())
        self.assertEqual(levels.describe(60, 0)["perks"], {"choose_game"})   # level 2: choose your game
        self.assertEqual(levels.describe(200, 0.4)["perks"], {"choose_game", "focus_subfamily", "specimen_photos"})
        self.assertTrue(levels.describe(1600, 0.75)["proposals"])
        self.assertFalse(levels.describe(1600, 0.65)["proposals"])   # reliability matters for it
        self.assertEqual(levels.proposal_level(), 6)

    def test_beginners_get_more_family_ties_and_experts_more_naming(self):
        self.assertAlmostEqual(levels.pair_share(1), 0.7)
        self.assertAlmostEqual(levels.pair_share(10), 0.15)
        self.assertGreater(levels.pair_share(3), levels.pair_share(7))


class SuggestionTests(ScoringCase):
    def test_only_the_labels_of_reliable_players_and_experts_reach_the_curators(self):
        target = self.roi(self.t_affinis, validated=False)
        beginner = self.player("beginner")
        good = self.player("good")
        PlayerScore.objects.create(player=good, score=1600, rating=0.75)
        self.answer(beginner, target, FERR)
        self.answer(good, target, AFFINIS)
        voters = levels.suggestion_voters()
        self.assertIn(good.id, voters)
        self.assertNotIn(beginner.id, voters)
        self.client.force_login(self.staff)
        data = self.client.get("/game/api/proposals/", {"image_asset": target.image_asset_id}).json()["proposals"]
        self.assertEqual(data[str(target.id)]["ranks"]["species"]["value"].lower(), "xyleborus affinis")
        self.assertEqual(data[str(target.id)]["answers"], 1)   # the beginner's answer is not part of it

    @override_settings(GAME_PROPOSALS_NEED_LEVEL=False)
    def test_it_can_be_switched_off(self):
        self.assertIsNone(levels.suggestion_voters())


class ExpertLabelTests(ScoringCase):
    def expert(self, name):
        p = self.strong(name, right=40)
        recompute_skills(p)
        return p

    def test_two_experts_agreeing_name_an_unnamed_beetle_without_review(self):
        target = self.roi(validated=False)       # no taxon
        for name in ("e1", "e2"):
            self.answer(self.expert(name), target, AFFINIS)
        self.assertEqual(auto_apply_expert_labels(), [target.id])
        target.refresh_from_db()
        self.assertEqual(target.taxon, self.t_affinis)
        self.assertFalse(target.bbox_is_validated)   # a curator still confirms it
        review = LabelReview.objects.get(roi=target)
        self.assertEqual((review.decision, review.reviewed_by, review.trusted_rank), ("accepted", None, "species"))

    def test_one_expert_is_not_enough(self):
        target = self.roi(validated=False)
        self.answer(self.expert("e1"), target, AFFINIS)
        self.assertEqual(auto_apply_expert_labels(), [])

    def test_a_named_validated_or_reviewed_beetle_is_left_alone(self):
        named = self.roi(self.t_ferr, validated=False)
        reviewed = self.roi(validated=False)
        LabelReview.objects.create(roi=reviewed, decision="dismissed")
        e1, e2 = self.expert("e1"), self.expert("e2")
        for roi in (named, reviewed):
            for e in (e1, e2):
                self.answer(e, roi, AFFINIS)
        self.assertEqual(auto_apply_expert_labels(), [])
        named.refresh_from_db()
        self.assertEqual(named.taxon, self.t_ferr)

    def test_non_experts_never_name_anything(self):
        target = self.roi(validated=False)
        for name in ("a", "b", "c"):
            self.answer(self.player(name), target, AFFINIS)
        self.assertEqual(auto_apply_expert_labels(), [])
        self.assertIsNone(Beetles.objects.get(pk=target.pk).taxon)

    def test_good_but_not_ninety_percent_sure_is_not_an_expert(self):
        target = self.roi(validated=False)
        for name in ("g1", "g2"):
            p = self.strong(name, right=34, wrong=6)    # 85% right on validated beetles
            recompute_skills(p)
            self.answer(p, target, AFFINIS)
        self.assertEqual(auto_apply_expert_labels(), [])

    def test_the_unlocks_page_states_the_bar(self):
        self.client.force_login(self.user)
        page = self.client.get("/game/unlocks/").content.decode()
        self.assertIn("at least 90% correct", page)
        self.assertIn("every species in it", page)
        self.assertIn("neighbouring taxa is not enough", page)

    def test_experts_proven_only_in_neighbouring_genera_do_not_write_labels(self):
        """
        Two players proven in two other Xyleborini genera are trusted for a genus nobody can be tested on (enough
        for a suggestion to curators), but not proven there themselves, so nothing is written to the database.
        """
        from django.core.cache import cache

        from beetlesgallery.beetles_app import game
        from beetlesgallery.beetles_app.testing import make_taxon
        other = {}
        for genus in ("Ambrosiodmus", "Euwallacea"):
            other[genus] = make_taxon(subfamily="Scolytinae", tribe="Xyleborini", genus=genus, species="one",
                                      scientific_name=f"{genus} one")
        untested = make_taxon(subfamily="Scolytinae", tribe="Xyleborini", genus="Xylosandrus", species="crassiusculus",
                              scientific_name="Xylosandrus crassiusculus")
        fields = {"subfamily": "Scolytinae", "tribe": "Xyleborini", "species": "one"}
        experts = []
        for name in ("s1", "s2"):
            p = self.player(name)
            for genus, taxon in other.items():
                for _ in range(40):
                    self.answer(p, self.roi(taxon), dict(fields, genus=genus))
            recompute_skills(p)
            experts.append(p)
        cache.clear()
        target = self.roi(validated=False)
        for p in experts:
            self.answer(p, target, {"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xylosandrus",
                                    "species": "crassiusculus"})
        entry = game.consensus(roi_ids=[target.id])[0]
        self.assertEqual(entry["trusted_rank"], "species")       # good enough to suggest to a curator ...
        self.assertEqual(auto_apply_expert_labels(), [])          # ... but not to write without review
        self.assertIsNone(Beetles.objects.get(pk=target.pk).taxon)
        self.assertIsNotNone(untested)

    @override_settings(GAME_AUTO_APPLY_EXPERT_LABELS=False)
    def test_it_can_be_switched_off(self):
        target = self.roi(validated=False)
        for name in ("e1", "e2"):
            self.answer(self.expert(name), target, AFFINIS)
        self.assertEqual(auto_apply_expert_labels(), [])


class EliteTests(ScoringCase):
    @override_settings(GAME_EXPERT_MIN_PLAYERS=3, GAME_EXPERT_PERCENTILE=0.3)
    def test_experts_must_also_be_among_the_most_reliable_players(self):
        from django.core.cache import cache
        from beetlesgallery.beetles_app.game_trust import elite_players
        best = self.strong("best", right=40)
        ok = self.strong("ok", right=30, wrong=10)
        meh = self.strong("meh", right=20, wrong=20)
        cache.clear()
        self.assertEqual(elite_players(), {best.id})

    def test_no_limit_while_few_players_are_rated(self):
        from django.core.cache import cache
        from beetlesgallery.beetles_app.game_trust import elite_players
        self.strong("only")
        cache.clear()
        self.assertIsNone(elite_players())


class FocusTests(ScoringCase):
    def level(self, score, rating=0.0, player=None):
        PlayerScore.objects.update_or_create(player=player or self.user, defaults={"score": score, "rating": rating})

    def set_focus(self, rank, value):
        self.client.force_login(self.user)
        return self.client.post("/game/unlocks/", {"focus_rank": rank, "focus_value": value})

    def test_a_locked_focus_is_refused(self):
        self.level(60)   # level 2 chooses the game; focus comes at level 3
        self.assertContains(self.set_focus("subfamily", "Scolytinae"), "not unlocked yet")

    def test_an_unlocked_focus_is_saved_and_narrows_the_feed(self):
        from beetlesgallery.beetles_app import game
        from beetlesgallery.beetles_app.models import GamePreference
        self.level(200, 0.4)
        for _ in range(3):
            self.roi(self.t_plat)
            self.roi(self.t_plat, validated=False)
            self.roi(self.t_affinis)
            self.roi(self.t_affinis, validated=False)
        self.assertRedirects(self.set_focus("subfamily", "platypodinae"), "/game/unlocks/")
        self.assertEqual(GamePreference.objects.get(player=self.user).focus_value, "Platypodinae")
        checks, opens = game.pools(self.user)
        self.assertEqual({r.taxon.subfamily for r in checks}, {"Platypodinae"})
        rnd = game.start_round(self.user, "classify", size=4)
        subfamilies = {Beetles.objects.get(id=i["a"]).taxon.subfamily for i in rnd.items}
        self.assertEqual(subfamilies, {"Platypodinae"})

    def test_a_focus_needs_a_real_group(self):
        self.level(200, 0.4)
        self.assertContains(self.set_focus("subfamily", "Nonsense"), "Choose a subfamily")

    def test_a_focus_lapses_if_the_level_drops_and_with_no_beetles_left_it_shows_everything(self):
        from beetlesgallery.beetles_app import game
        from beetlesgallery.beetles_app.models import GamePreference
        GamePreference.objects.create(player=self.user, focus_rank="genus", focus_value="Xyleborus")
        self.level(900, 0.65)
        self.assertEqual(game.player_focus(self.user), ("genus", "Xyleborus"))
        self.level(900, 0.55)    # reliability fell below the genus-focus level
        self.assertIsNone(game.player_focus(self.user))
        self.level(900, 0.65)
        self.roi(self.t_plat)
        checks, _ = game.pools(self.user)   # no Xyleborus beetles at all: everything instead
        self.assertTrue(checks.exists())

    def test_clearing_it(self):
        from beetlesgallery.beetles_app.models import GamePreference
        self.level(200, 0.4)
        self.set_focus("subfamily", "Scolytinae")
        self.set_focus("", "")
        self.assertEqual(GamePreference.objects.get(player=self.user).focus_rank, "")


class PageTests(ScoringCase):
    def test_the_unlocks_page(self):
        self.client.force_login(self.user)
        page = self.client.get("/game/unlocks/").content.decode()
        for text in ("Levels and unlocks", "Coleopterist", "Focus on a genus", "Your labels go to curators", "proven experts", "expertise tree"):
            self.assertIn(text, page)

    def test_it_says_when_your_labels_go_to_curators(self):
        PlayerScore.objects.create(player=self.user, score=1600, rating=0.75)
        self.client.force_login(self.user)
        self.assertContains(self.client.get("/game/unlocks/"), "Your labels go to the curators as suggestions")
        self.assertContains(self.client.get("/game/"), 'data-testid="proposals-banner"')

    def test_the_expertise_tree_colours_branches_by_accuracy(self):
        expert = self.strong("expert", right=40)
        recompute_skills(expert)
        self.client.force_login(expert)
        page = self.client.get("/game/expertise/").content.decode()
        self.assertIn("st-expert", page)
        self.assertIn("Xyleborini", page)
        self.assertIn("Xyleborus", page)
        self.assertIn("not played yet", page)   # Platypodinae

    def test_anyone_can_look_at_someone_elses_tree(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(f"/game/players/{self.staff.id}/expertise/"), "expertise")


class LevelIconTests(SimpleTestCase):
    def test_every_level_has_its_own_icon_from_egg_to_crown(self):
        from beetlesgallery.beetles_app import game_levels
        icons = [game_levels.level_icon(n) for n in range(1, len(game_levels.LEVELS) + 1)]
        self.assertEqual(len(set(icons)), len(game_levels.LEVELS))
        self.assertEqual((icons[0], icons[-1]), ("fi-rr-egg", "fi-rr-crown"))
        self.assertEqual(game_levels.level_icon(99), "fi-rr-crown")
        self.assertEqual(game_levels.level_icon("x"), "fi-rr-egg")

    def test_the_badge_shows_the_icon(self):
        from django.template.loader import render_to_string
        html = render_to_string("beetles/includes/game_level_badge.html", {"level": 2, "name": "Larva"})
        self.assertIn("fi-rr-worm", html)
        self.assertIn(">2</span>", html)

    def test_a_proven_expert_is_a_glowing_gold_dot_like_the_top_level(self):
        import re
        from pathlib import Path
        from django.conf import settings
        css = (Path(settings.BASE_DIR) / "beetlesgallery/templates/beetles/game_expertise.html").read_text()
        rule = re.search(r"\.st-expert \.tree-dot \{([^}]*)\}", css).group(1)
        self.assertIn("#facc15", rule)        # gold, as the level 10 badge
        self.assertIn("box-shadow", rule)     # glowing


class RarityColourTests(SimpleTestCase):
    """Badges, levels and the expertise tree share one colour scale: grey, green, blue, purple, orange, gold."""

    def test_every_badge_has_exactly_one_tier(self):
        from beetlesgallery.beetles_app import game_rewards
        listed = [k for keys in game_rewards.BADGE_TIERS.values() for k in keys]
        self.assertEqual(sorted(listed), sorted(game_rewards.BADGES))
        self.assertEqual(len(listed), len(set(listed)))
        self.assertEqual(game_rewards.badge_tier("first"), "common")
        self.assertEqual(game_rewards.badge_tier("king"), "mythic")

    def test_an_earned_badge_takes_its_tiers_colour(self):
        from django.template.loader import render_to_string
        easy = render_to_string("beetles/includes/game_badge.html", {"b": {"key": "hundred", "name": "x", "how": "", "icon": "fi-rr-medal", "earned": True, "tier": "uncommon"}})
        self.assertIn("text-green-600", easy)      # the colour of levels 3-4
        top = render_to_string("beetles/includes/game_badge.html", {"b": {"key": "king", "name": "x", "how": "", "icon": "fi-rr-crown", "earned": True, "tier": "mythic"}})
        self.assertIn("box-shadow", top)            # the top badge glows, like the top level

    def test_levels_not_reached_are_grey_and_the_king_glows(self):
        from django.template.loader import render_to_string
        locked = render_to_string("beetles/includes/game_level_badge.html", {"level": 5, "locked": True, "size": "lg"})
        self.assertIn("bg-gray-100", locked)
        self.assertNotIn("bg-blue-600", locked)
        self.assertIn("height: 1.75rem", locked)
        king = render_to_string("beetles/includes/game_level_badge.html", {"level": 10})
        self.assertIn("box-shadow", king)
        self.assertNotIn("box-shadow", render_to_string("beetles/includes/game_level_badge.html", {"level": 9}))

    @override_settings(GAME_TRUST_MIN_ACCURACY=0.9)
    def test_expertise_bands_run_from_50_percent_to_what_an_expert_needs(self):
        from types import SimpleNamespace
        from beetlesgallery.beetles_app import game_trust

        def status(right, judged=100, proven=False):
            return game_trust.node_status(SimpleNamespace(correct=right, judged=judged, proven=proven), 5)
        self.assertEqual([status(n) for n in (49, 50, 60, 70, 80, 95)],
                         ["common", "uncommon", "rare", "epic", "legendary", "legendary"])
        self.assertEqual(status(95, proven=True), "expert")
        self.assertEqual(status(4, judged=4), "unknown")
        self.assertEqual([label for _, label in game_trust.expertise_legend()],
                         ["under 50%", "50\u201360%", "60\u201370%", "70\u201380%", "80%+", "proven expert"])
