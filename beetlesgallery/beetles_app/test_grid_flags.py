"""
Flagging a photo in a grid game (#489): the photo goes to the curators and drops out of the grid, which carries on.
Flagged photos are left out of scoring; half of them flagged, or the odd one, ends the grid unscored.
"""
from pathlib import Path

from django.conf import settings
from django.test import override_settings
from django.urls import reverse

from beetlesgallery.beetles_app import game, game_grid_ladder as ladder
from beetlesgallery.beetles_app.models import AnswerPoints, GameAnswer, GameReport, GameRound
from beetlesgallery.beetles_app.test_grid_builders import GridCase, at


@override_settings(GAME_POINTS_PARTICIPATION=0.0)
class FlagCase(GridCase):
    def start(self, mode, size=9, rank="species"):
        at(self.user, mode, ladder.step_for(size, rank))
        res = self.post("game_start", {"mode": mode})
        self.assertEqual(res.status_code, 200, res.content)
        self.rnd = GameRound.objects.get(id=res.json()["round"])
        self.item = res.json()["item"]
        self.grid = self.rnd.items[self.item["index"]]
        return self.item

    def flag(self, place):
        return self.post("game_report_item", {"round": str(self.rnd.id), "index": self.item["index"], "image": place,
                                              "reason": "bad_image"}).json()

    def answer(self, **body):
        return self.post("game_answer", dict(body, index=self.item["index"]), self.rnd.id)

    def places(self, member=True):
        group = self.grid["group"][self.grid["rank"]].lower()
        out = []
        for i, t in enumerate(self.grid["tiles"]):
            roi = game.Beetles.objects.select_related("taxon").get(id=t)
            if roi.bbox_is_validated and (game.lineage(roi.taxon, self.grid["rank"])[self.grid["rank"]].lower() == group) == member:
                out.append(i)
        return out


class SelectFlagTests(FlagCase):
    def test_a_flagged_member_is_left_out_and_the_grid_carries_on(self):
        self.start("select")
        members = self.places(member=True)
        reply = self.flag(members[0])
        self.assertEqual((reply["flagged"], reply["end"]), ([members[0]], False))
        self.assertTrue(GameReport.objects.filter(reporter=self.user, status="open").exists())
        data = self.answer(picks=members[1:], flagged=[members[0]]).json()
        ans = GameAnswer.objects.get()
        self.assertEqual((ans.flagged, ans.skipped, ans.score_hold, ans.correct_species), ([members[0]], False, False, True))
        self.assertEqual(data["review"]["grid"]["tiles"][members[0]]["state"], "flagged")   # the review card (#488)
        points = AnswerPoints.objects.get(answer=ans)
        self.assertEqual((points.detail["members"], points.detail["missed"]), (len(members) - 1, 0))
        self.assertGreater(points.points, 0)

    def test_a_flagged_photo_cant_be_tapped(self):
        self.start("select")
        member = self.places(member=True)[0]
        self.flag(member)
        self.assertEqual(self.answer(picks=[member], flagged=[member]).status_code, 400)

    def test_a_flag_without_a_report_is_ignored(self):
        self.start("select")
        members = self.places(member=True)
        self.answer(picks=members[1:], flagged=[members[0]])   # no report filed: it counts as missed
        ans = GameAnswer.objects.get()
        self.assertEqual((ans.flagged, ans.correct_species), ([], False))

    def test_bad_flags_are_refused(self):
        self.start("select")
        for bad in ("1", [99], [0, 0], [True]):
            with self.subTest(flagged=bad):
                self.assertEqual(self.answer(picks=[0], flagged=bad).status_code, 400)

    def test_half_the_grid_flagged_ends_it_unscored(self):
        self.start("select", size=4)
        self.assertFalse(self.flag(0)["end"])
        reply = self.flag(1)
        self.assertEqual((reply["flagged"], reply["end"]), ([0, 1], True))
        self.answer(skipped=True, reported=True, flagged=[0, 1])
        ans = GameAnswer.objects.get()
        self.assertEqual((ans.skipped, ans.score_hold, ans.flagged), (True, True, [0, 1]))
        self.assertEqual(AnswerPoints.objects.get(answer=ans).points, 0.0)
        self.assertIsNone(ladder.outcome(ans))

    def test_the_server_ends_it_even_if_the_page_carries_on(self):
        self.start("select", size=4)
        self.flag(0)
        self.flag(1)
        free = [i for i in range(4) if i not in (0, 1)]
        self.answer(picks=free[:1], flagged=[0, 1])
        ans = GameAnswer.objects.get()
        self.assertEqual((ans.skipped, ans.score_hold), (True, True))


class OddFlagTests(FlagCase):
    def test_a_flagged_beetle_of_the_rest_leaves_the_grid_playable(self):
        self.start("odd")
        odd = self.grid["tiles"].index(self.grid["a"])
        other = next(i for i in range(len(self.grid["tiles"])) if i != odd)
        self.assertFalse(self.flag(other)["end"])
        self.assertEqual(self.answer(pick=other, flagged=[other]).status_code, 400)   # can't be picked
        self.answer(pick=odd, flagged=[other])
        ans = GameAnswer.objects.get()
        self.assertEqual((ans.correct_species, ans.flagged, ans.score_hold), (True, [other], False))
        self.assertGreater(ans.points.points, 0)

    def test_the_odd_one_flagged_ends_the_grid_unscored(self):
        self.start("odd")
        odd = self.grid["tiles"].index(self.grid["a"])
        self.assertTrue(self.flag(odd)["end"])
        self.answer(skipped=True, reported=True, flagged=[odd])
        ans = GameAnswer.objects.get()
        self.assertEqual((ans.skipped, ans.score_hold), (True, True))
        self.assertEqual(AnswerPoints.objects.get(answer=ans).points, 0.0)

    def test_a_flagged_photo_says_nothing_about_its_beetle(self):
        self.start("odd")
        odd = self.grid["tiles"].index(self.grid["a"])
        others = [i for i in range(len(self.grid["tiles"])) if i != odd]
        self.flag(others[0])
        self.answer(pick=odd, flagged=[others[0]])
        voted = {str(r) for r, _, _ in game.tap_votes()}
        self.assertNotIn(self.grid["tiles"][others[0]], voted)


class SinglePhotoTests(GridCase):
    def test_reporting_a_single_photo_still_moves_on(self):
        self.level(60)
        res = self.post("game_start", {"mode": "pair"})
        reply = self.post("game_report_item", {"round": res.json()["round"], "index": res.json()["item"]["index"],
                                               "image": 0, "reason": "bad_box"}).json()
        self.assertNotIn("flagged", reply)


class PageTests(GridCase):
    def test_the_play_page_greys_out_a_flagged_photo_and_carries_on(self):
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        self.assertIn("function flagTile(i)", page)
        self.assertIn("if (data.flagged && !data.end)", page)
        self.assertIn('cell.classList.add("flagged")', page)
        self.assertIn("|| flagged.has(i)) return;", page)                  # no picking or tapping a flagged photo
        self.assertIn("flagged: flags", page)                              # the answer carries them
        self.assertIn("repeat(var(--grid-cols, 2), minmax(0, 1fr))", page)  # 2x2, 3x3 or 4x4
        self.assertNotIn("#photos.six", page)
        self.assertIn('" beetles"', page)                                  # "Tap every Xyleborini · 9 beetles"

    def test_how_it_works_says_the_grids_grow(self):
        page = self.client.get(reverse("game_how")).content.decode()
        self.assertIn('data-testid="how-grids"', page)

    def test_the_review_lays_out_bigger_grids(self):
        review = (Path(settings.BASE_DIR) / "beetlesgallery/templates/beetles/game_round_review.html").read_text(encoding="utf-8")
        self.assertIn("grid grid-cols-4 gap-2 items-center", review)
