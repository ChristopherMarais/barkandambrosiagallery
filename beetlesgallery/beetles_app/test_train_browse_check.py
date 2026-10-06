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

    def test_the_grid_card_names_the_group_instead_of_vague_phrases(self):
        page = self.page()   # the lines come from game_answer_review._explain (#541)
        self.assertIn('node("p", "rv-tile", node("b", "", l.tiles.join(", ") + "."), " ", ...parts(l.parts))', page)
        for vague in ("one of the rest", '"in it"'):
            self.assertNotIn(vague, page)

    def test_names_in_the_identification_card_are_set_like_names(self):
        page = self.page()
        self.assertIn("return [taxonName(v.name, rank), ", page)
        self.assertIn("cell(opinion(r.players, true, r.rank))", page)
        self.assertIn("cell(opinion(r.ai, false, r.rank))", page)



class AnnotatePageTests(GameCase):
    """The annotation page's subtitle said "Please select an image to begin annotating" with an image open."""

    def test_the_subtitle_is_cleared_when_an_image_opens(self):
        self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        page = self.client.get(reverse("tool_annotate")).content.decode()
        self.assertIn("document.getElementById('canvas-subtitle').textContent = '';", page)
