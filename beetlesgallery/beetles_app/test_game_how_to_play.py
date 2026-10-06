"""Game guidance (#360, #361): the rules in short in every game, when to report a photo, and "Why?" links."""
from django.contrib.staticfiles import finders
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import game_tuning, game_views
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase


class GuidanceTests(ScoringCase):
    def setUp(self):
        super().setUp()
        game_tuning.forget()   # no Scoring page overrides left over from another test

    def page(self, name, *args):
        self.client.force_login(self.user)
        return self.client.get(reverse(name, args=args)).content.decode()

    def test_the_rules_are_one_tap_away_in_every_game(self):
        page = self.page("game_play", "mixed")
        self.assertIn('id="open-help" class="icon-btn"', page)   # no longer hidden outside Similarity
        for mode in ("classify", "pair", "odd", "select"):
            self.assertIn(f'data-help-mode="{mode}"', page)
        for text in ("a wrong guess costs more than stopping early", "Flagging a bad photo costs nothing",
                     "isn't bad: name it as far as you can", "No timer", "strong players agree with you"):
            self.assertIn(text, page)
        self.assertIn(f'href="{reverse("game_how")}"', page)

    def test_what_skipping_costs_follows_the_game_on_screen(self):
        page = self.page("game_play", "mixed")
        self.assertIn('data-help-mode="classify pair">costs very little<', page)
        self.assertIn('data-help-mode="odd select">earns a little<', page)

    def test_the_report_menu_says_what_each_reason_means(self):
        page = self.page("game_play", "mixed")
        self.assertIn("Misses the beetle or frames the label", page)
        self.assertIn("Blurry, dark, too little of the beetle, or not a beetle", page)
        self.assertNotIn('data-reason="wrong_label"', page)
        # the reasons the server accepts are unchanged
        self.assertEqual([value for value, _ in game_views.FEED_REPORT_REASONS], ["bad_box", "bad_image", "other"])

    def test_the_report_tip_drops_wrong_name_and_says_too_little_of_the_beetle_is_bad(self):
        page = self.page("game_play", "mixed")
        start = page.index('id="report-tip"')
        tip = page[start:page.index('id="report-tip-show"', start)]
        self.assertNotIn("wrong name", tip.lower())   # not offered before answering (it would hint at the answer)
        self.assertIn("Too little of the beetle (a leg, a fragment) is a bad photo", tip)   # #498
        self.assertIn("from an unusual side isn't bad", tip)

    def test_the_walkthrough_shows_the_rules_button_and_when_to_report(self):
        with open(finders.find("js/game_tour.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn('el: "open-help"', js)
        report_step = next(line for line in js.splitlines() if 'el: "report-chip-0"' in line)
        self.assertNotIn("wrong name", report_step)
        self.assertIn("isn't bad", report_step)

    def test_why_links_lead_to_scoring_and_to_what_experts_do(self):
        how = reverse("game_how")
        self.assertIn(f'href="{how}#scoring"', self.page("game_play", "mixed"))   # next to the points in the recap
        self.assertIn(f'href="{how}#labels"', self.page("game_expertise"))
        page = self.page("game_how")
        for anchor in ('id="scoring"', 'id="labels"', 'id="unchecked"', 'id="faq"'):
            self.assertIn(anchor, page)

    @override_settings(GAME_POINTS_UNSURE=0.5, GAME_POINTS_ODD_SKIP=0.3)
    def test_how_it_works_answers_the_play_test_questions(self):
        page = self.page("game_how")
        faq = page[page.index('id="faq"'):]
        for text in ("Do I lose points if I flag a photo?", "Is there a time limit?", "What does skipping cost?",
                     "shows too little to name it", "Do other players affect my score?", "What is an expert?"):
            self.assertIn(text, faq)
        self.assertIn("A tiny 0.5 points", faq)   # the current settings, not fixed numbers
        self.assertIn("skipping earns 0.3", faq)
