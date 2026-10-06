"""
What a click-through of the train branch in a browser found: the review's photos stay at full strength, the Imposter
Picker card says what "in the group" means there, IBBI-AI's and the players' names in the Identification card are in
italics like the rest.
"""
from django.urls import reverse

from beetlesgallery.beetles_app.test_game import GameCase


class GamePageTests(GameCase):
    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=["mixed"])).content.decode()

    def test_the_photos_fade_while_waiting_but_not_on_the_review(self):
        page = self.page()
        self.assertIn("root.dataset.phase = p;", page)
        self.assertIn('#game.answer-locked:not([data-phase="review"]) #photos:not(.revealing)', page)
        self.assertNotIn("#game.answer-locked #photos:not(.revealing)", page)

    def test_imposter_picker_says_one_of_the_rest_not_in_it(self):
        self.assertIn('(odd ? "says it\'s one of the rest" : "in it")', self.page())

    def test_names_in_the_identification_card_are_set_like_names(self):
        page = self.page()
        self.assertIn("return [taxonName(v.name, rank), ", page)
        self.assertIn("cell(opinion(r.players, true, r.rank))", page)
        self.assertIn("cell(opinion(r.ai, false, r.rank))", page)

