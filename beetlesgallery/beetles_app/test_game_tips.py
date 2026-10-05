"""Tips on the Image Annotation page: what players agree a beetle is, and what they are confident it is not."""
from django.test import override_settings

from beetlesgallery.beetles_app import game_tips
from beetlesgallery.beetles_app.test_game import AFFINIS
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase


@override_settings(GAME_PROPOSALS_NEED_LEVEL=False)
class TipTests(ScoringCase):
    def pair(self, name, target, partner, answer):
        return self.answer(self.player(name), target, mode="pair", roi_b=partner, pair=answer, check=False)

    def test_family_ties_answers_say_what_a_beetle_is_not(self):
        target = self.roi(validated=False)
        known = self.roi(self.t_affinis)
        self.pair("a", target, known, "tribe")     # same tribe, so not the same genus
        self.pair("b", target, known, "tribe")
        tip = game_tips.tips([target.id])[target.id][0]
        self.assertEqual((tip["kind"], tip["rank"], tip["value"], tip["count"]), ("not", "genus", "Xyleborus", 2))
        self.assertEqual(game_tips.text(tip), "Players are confident it is not in genus Xyleborus.")

    def test_disagreement_cancels_it(self):
        target = self.roi(validated=False)
        known = self.roi(self.t_affinis)
        self.pair("a", target, known, "tribe")
        self.pair("b", target, known, "tribe")
        self.pair("c", target, known, "genus")     # says it IS a Xyleborus
        self.assertNotIn("not", [t["kind"] for t in game_tips.tips([target.id]).get(target.id, [])])

    def test_only_validated_partners_count(self):
        target = self.roi(validated=False)
        unsure = self.roi(self.t_affinis, validated=False)
        self.pair("a", target, unsure, "different")
        self.pair("b", target, unsure, "different")
        self.assertEqual(game_tips.tips([target.id]), {})

    def test_strong_agreement_is_a_tip_and_says_when_it_clashes_with_the_label(self):
        target = self.roi(self.t_ferr, validated=False)
        for name in "abc":
            self.answer(self.player(name), target, AFFINIS, check=False)
        tip = game_tips.tips([target.id])[target.id][0]
        self.assertEqual((tip["kind"], tip["rank"], tip["count"], tip["conflicts"]), ("players", "species", 3, True))
        self.assertIn("The current label says", game_tips.text(tip))

    def test_too_few_players_is_no_tip(self):
        target = self.roi(validated=False)
        self.answer(self.player("a"), target, AFFINIS, check=False)
        self.assertEqual(game_tips.tips([target.id]), {})

    def test_the_annotation_api_carries_the_tips(self):
        target = self.roi(validated=False)
        known = self.roi(self.t_plat)
        self.pair("a", target, known, "different")
        self.pair("b", target, known, "different")
        self.client.force_login(self.staff)
        data = self.client.get("/game/api/proposals/", {"image_asset": target.image_asset_id}).json()
        self.assertEqual(data["tips"][str(target.id)][0]["value"], "Platypodinae")


class TipVoterTests(ScoringCase):
    def test_beginners_do_not_make_tips(self):
        target = self.roi(validated=False)
        known = self.roi(self.t_affinis)
        for name in "ab":
            self.answer(self.player(name), target, mode="pair", roi_b=known, pair="tribe", check=False)
        self.assertEqual(game_tips.tips([target.id]), {})


class HelpAndFeedbackTests(ScoringCase):
    URL = "https://github.com/ChristopherMarais/barkandambrosiagallery/discussions/categories/beetle-id-game"

    def test_the_discussions_link_is_on_home_help_and_the_recap(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        for url in (reverse("game_home"), reverse("game_how"), reverse("game_play", args=["classify"])):
            self.assertIn(self.URL, self.client.get(url).content.decode(), url)

    def test_the_help_page_explains_the_loop_levels_experts_and_leaderboard(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_how")).content.decode()
        for text in ("In 30 seconds", "King of Bark and Ambrosia", "without review", "at least 90% correct", "one taxon at a time", "specialists"):
            self.assertIn(text, page)
