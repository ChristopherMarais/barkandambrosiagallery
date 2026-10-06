"""The quick loop: what others said after each answer, beetles others named, participation points."""
import json

from django.test import override_settings

from beetlesgallery.beetles_app import game, game_scoring as scoring
from beetlesgallery.beetles_app.models import AnswerPoints
from beetlesgallery.beetles_app.test_game import AFFINIS, FERR
from beetlesgallery.beetles_app.test_game_scoring import PLAT, ScoringCase


class CommunityTests(ScoringCase):
    """After each answer on a beetle nobody has validated, the review says what the other players said, rank by rank,
    and how far you agree (#357; since #488 the reliability-weighted consensus, in the review card)."""

    def answer_in_feed(self, fields):
        self.client.force_login(self.user)
        rnd, item = self.play("classify")
        return item, self.post("game_answer", dict(fields, index=item["index"]), rnd.id).json()["review"]

    def players(self, review):
        return {r["rank"]: r["players"] for r in review["classify"]["ranks"]}

    @override_settings(GAME_ROUND_SIZE=1)
    def test_players_agreeing_all_the_way(self):
        target = self.roi(self.t_affinis, validated=False)
        for name in ("a", "b"):
            self.answer(self.player(name), target, AFFINIS)
        item, review = self.answer_in_feed(AFFINIS)
        self.assertEqual(item["others"], 2)          # before answering: only how many
        said = self.players(review)
        self.assertEqual(review["classify"]["players"], 2)
        self.assertTrue(all(said[r]["agrees"] for r in said))
        self.assertEqual(said["species"]["votes"], 2)

    @override_settings(GAME_ROUND_SIZE=1)
    def test_it_says_where_they_part(self):
        target = self.roi(self.t_affinis, validated=False)
        for name, fields in (("a", FERR), ("b", FERR), ("c", AFFINIS)):
            self.answer(self.player(name), target, fields)
        said = self.players(self.answer_in_feed(AFFINIS)[1])
        self.assertIs(said["genus"]["agrees"], True)
        self.assertEqual((said["species"]["name"], said["species"]["votes"], said["species"]["agrees"]),
                         ("Xyleborus ferrugineus", 2, False))

    @override_settings(GAME_ROUND_SIZE=1)
    def test_it_tells_you_how_far_they_went_beyond_you(self):
        target = self.roi(self.t_affinis, validated=False)
        self.answer(self.player("a"), target, AFFINIS)
        said = self.players(self.answer_in_feed(dict(AFFINIS, species=""))[1])
        self.assertEqual((said["species"]["name"], said["species"]["agrees"]), ("Xyleborus affinis", None))

    @override_settings(GAME_ROUND_SIZE=1)
    def test_the_first_to_name_a_beetle_is_told_so(self):
        self.roi(self.t_affinis, validated=False)
        item, review = self.answer_in_feed(AFFINIS)
        self.assertNotIn("others", item)
        self.assertEqual(review["classify"]["players"], 0)

    @override_settings(GAME_ROUND_SIZE=1)
    def test_a_validated_beetle_shows_the_truth_not_the_players(self):   # the owner's call (#488)
        target = self.roi(self.t_affinis)
        self.answer(self.player("a"), target, FERR)
        review = self.answer_in_feed(AFFINIS)[1]
        self.assertEqual(review["classify"]["truth"]["name"], "Xyleborus affinis")
        self.assertNotIn("ferrugineus", json.dumps(review))


class PeerBeetleTests(ScoringCase):
    @override_settings(GAME_PEER_SHARE=1.0, GAME_ROUND_SIZE=5, GAME_CHECK_RATIO_NEW=0.2)
    def test_beetles_others_named_come_first(self):
        named = [self.roi(self.t_affinis, validated=False) for _ in range(3)]
        for roi in named:
            self.answer(self.player(f"p{roi.id.hex[:6]}"), roi, AFFINIS)
        for _ in range(10):
            self.roi(self.t_affinis, validated=False)
        self.roi(self.t_affinis)
        rnd = game.start_round(self.user, "classify")
        opens = {i["a"] for i in rnd.items if not i["check"]}
        self.assertTrue({str(r.id) for r in named} <= opens)

    @override_settings(GAME_PEER_MAX_OTHERS=2)
    def test_beetles_with_plenty_of_opinions_already_are_not_pushed(self):
        crowded = self.roi(self.t_affinis, validated=False)
        for name in ("a", "b", "c"):
            self.answer(self.player(name), crowded, AFFINIS)
        self.assertFalse(game.peer_rois(self.user, game.open_rois()).filter(id=crowded.id).exists())

    def test_not_ones_you_answered_yourself(self):
        roi = self.roi(self.t_affinis, validated=False)
        self.answer(self.player("a"), roi, AFFINIS)
        self.answer(self.user, roi, AFFINIS)
        self.assertFalse(game.peer_rois(self.user, game.open_rois()).exists())


@override_settings(GAME_POINTS_PARTICIPATION=0.5)
class ParticipationTests(ScoringCase):
    def test_every_real_answer_earns_a_little_but_skips_do_not(self):
        right = self.answer(self.user, self.roi(self.t_affinis), AFFINIS)
        unknown = self.answer(self.user, self.roi(self.t_affinis, validated=False), AFFINIS)
        skip = self.answer(self.user, self.roi(self.t_affinis), skipped=True)
        scoring.recompute([self.user.id])
        got = {a.pk: AnswerPoints.objects.get(answer=a).points for a in (right, unknown, skip)}
        self.assertEqual((got[right.pk], got[unknown.pk], got[skip.pk]), (15.5, 0.5, -0.25))

    def test_wrong_answers_still_cost_overall(self):
        wrong = self.answer(self.user, self.roi(self.t_affinis), PLAT)
        scoring.recompute([self.user.id])
        self.assertEqual(AnswerPoints.objects.get(answer=wrong).points, -10.75)


class PageTests(ScoringCase):
    def test_the_feed_has_the_combo_and_the_review(self):
        self.client.force_login(self.user)
        page = self.client.get("/game/play/classify/").content.decode()
        for text in ('id="combo"', 'id="review"', "renderReview", "in a row", "Named by"):
            self.assertIn(text, page)
        self.assertNotIn("Last beetle", page)
