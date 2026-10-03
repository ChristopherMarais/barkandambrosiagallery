"""The quick loop: what others said after each answer, beetles others named, participation points."""
import json

from django.test import override_settings

from beetlesgallery.beetles_app import game, game_scoring as scoring
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, PlayerScore
from beetlesgallery.beetles_app.test_game import AFFINIS, FERR
from beetlesgallery.beetles_app.test_game_scoring import PLAT, ScoringCase


class CommunityTests(ScoringCase):
    """After each answer: how far players ranked above you agree with you, rank by rank (#357)."""

    def answer_in_feed(self, fields):
        self.client.force_login(self.user)
        rnd, item = self.play("classify")
        return item, self.post("game_answer", dict(fields, index=item["index"]), rnd.id).json()

    def ahead(self, name, score=1000):
        player = self.player(name)
        PlayerScore.objects.update_or_create(player=player, defaults={"score": score})
        return player

    @override_settings(GAME_ROUND_SIZE=1)
    def test_players_ahead_agreeing_all_the_way(self):
        target = self.roi(self.t_affinis, validated=False)
        for name in ("a", "b"):
            self.answer(self.ahead(name), target, AFFINIS)
        item, res = self.answer_in_feed(AFFINIS)
        self.assertEqual(item["others"], 2)          # before answering: only how many
        c = res["community"]
        self.assertEqual((c["players"], c["ahead"], c["agree"]), (2, 2, True))
        self.assertEqual(c["text"], "Players ahead of you agree with you to species.")

    @override_settings(GAME_ROUND_SIZE=1)
    def test_it_says_how_far_they_agree_and_where_they_part(self):
        target = self.roi(self.t_affinis, validated=False)
        for name, fields in (("a", FERR), ("b", FERR), ("c", AFFINIS)):
            self.answer(self.ahead(name), target, fields)
        _, res = self.answer_in_feed(AFFINIS)
        c = res["community"]
        self.assertEqual(c["text"], "Players ahead of you agree with you to genus; on species, 2 of 3 said Xyleborus ferrugineus.")
        self.assertIs(c["agree"], False)

    @override_settings(GAME_ROUND_SIZE=1)
    def test_a_split_is_called_a_split_not_a_disagreement(self):
        target = self.roi(self.t_affinis, validated=False)
        for name, fields in (("a", FERR), ("b", AFFINIS)):
            self.answer(self.ahead(name), target, fields)
        _, res = self.answer_in_feed(dict(AFFINIS, species=""))
        self.assertEqual(res["community"]["text"], "Players ahead of you agree with you to genus; they're split on species.")

    @override_settings(GAME_ROUND_SIZE=1)
    def test_it_tells_you_how_far_they_went_beyond_you(self):
        target = self.roi(self.t_affinis, validated=False)
        for name in ("a", "b"):
            self.answer(self.ahead(name), target, AFFINIS)
        _, res = self.answer_in_feed(dict(AFFINIS, species=""))
        self.assertEqual(res["community"]["text"],
                         "Players ahead of you agree with you to genus; 2 of 2 went on to species Xyleborus affinis.")

    @override_settings(GAME_ROUND_SIZE=1)
    def test_only_players_ranked_above_you_count(self):
        PlayerScore.objects.update_or_create(player=self.user, defaults={"score": 500})
        target = self.roi(self.t_affinis, validated=False)
        self.answer(self.ahead("low", score=100), target, PLAT)       # below you: ignored
        self.answer(self.ahead("high", score=900), target, AFFINIS)
        _, res = self.answer_in_feed(AFFINIS)
        c = res["community"]
        self.assertEqual((c["players"], c["ahead"], c["text"]), (2, 1, "Players ahead of you agree with you to species."))

    @override_settings(GAME_ROUND_SIZE=1, GAME_MIN_JUDGED_FOR_ACCURACY=10)
    def test_a_higher_accuracy_also_counts_as_ahead(self):
        PlayerScore.objects.update_or_create(player=self.user, defaults={"score": 500, "accuracy": 0.6, "judged": 20})
        target = self.roi(self.t_affinis, validated=False)
        sharp = self.ahead("sharp", score=100)
        PlayerScore.objects.filter(player=sharp).update(accuracy=0.9, judged=20)
        self.answer(sharp, target, AFFINIS)
        _, res = self.answer_in_feed(AFFINIS)
        self.assertEqual(res["community"]["ahead"], 1)

    @override_settings(GAME_ROUND_SIZE=1)
    def test_nobody_ahead_yet(self):
        PlayerScore.objects.update_or_create(player=self.user, defaults={"score": 500})
        target = self.roi(self.t_affinis, validated=False)
        self.answer(self.ahead("low", score=10), target, FERR)
        _, res = self.answer_in_feed(AFFINIS)
        self.assertEqual(res["community"]["text"], "1 other player named it, none of them ranked above you yet.")

    @override_settings(GAME_ROUND_SIZE=1)
    def test_the_first_to_name_a_beetle_is_told_so(self):
        self.roi(self.t_affinis, validated=False)
        item, res = self.answer_in_feed(AFFINIS)
        self.assertNotIn("others", item)
        self.assertEqual(res["community"], {"players": 0})

    @override_settings(GAME_ROUND_SIZE=1)
    def test_it_never_carries_the_truth(self):
        target = self.roi(self.t_affinis)   # validated
        self.answer(self.ahead("a"), target, FERR)
        _, res = self.answer_in_feed(AFFINIS)
        text = json.dumps(res["community"]).lower()
        self.assertIn("ferrugineus", text)    # what the other player said, even though it is wrong
        for word in ("correct", "truth", "valid"):
            self.assertNotIn(word, text)


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
    def test_the_feed_has_the_combo_and_the_reveal(self):
        self.client.force_login(self.user)
        page = self.client.get("/game/play/classify/").content.decode()
        for text in ('id="combo"', "showCommunity", "in a row", "Named by", "Last beetle"):
            self.assertIn(text, page)
