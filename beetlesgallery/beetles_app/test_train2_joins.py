"""
Where the staging-feedback batch 2 PRs meet (the train, #544-#553): several odd ones (#540) relearn every miss and are
all "seen" in the review (#541); Find Them All counting toward naming (#543) skips beetles seen before (#541), and so
does every game's accuracy on the leaderboard; batches built ahead (#542) leave out other photos of beetles named
since; and the help reads right for several odd ones, in the new names (#538).
"""
import html
import time
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from django.utils.html import strip_tags

from beetlesgallery.beetles_app import game, game_board, game_grid_ladder as ladder, game_levels, game_relearn
from beetlesgallery.beetles_app import game_warm
from beetlesgallery.beetles_app.models import Beetles, GameAnswer, GameRound
from beetlesgallery.beetles_app.test_find_them_all_naming import FindThemAllNamingCase
from beetlesgallery.beetles_app.test_grid_builders import GridCase, at


def words(parts):
    return "".join(p if isinstance(p, str) else p.get("name") or p.get("say") or "" for p in parts)


class SeveralOddCase(GridCase):
    def start(self, size=9, odds=2, rank="species"):
        at(self.user, "odd", ladder.step_for(size, rank, odds))
        res = self.post("game_start", {"mode": "odd"})
        self.assertEqual(res.status_code, 200, res.content)
        self.rnd, self.item = GameRound.objects.get(id=res.json()["round"]), res.json()["item"]
        self.grid = self.rnd.items[self.item["index"]]
        self.odds = list(self.grid["odds"])
        self.odd_places = [self.grid["tiles"].index(t) for t in self.odds]
        self.rest = [i for i, t in enumerate(self.grid["tiles"])
                     if t not in self.odds and Beetles.objects.filter(id=t, bbox_is_validated=True).exists()]

    def answer(self, picks):
        res = self.post("game_answer", {"index": self.item["index"], "picks": picks}, self.rnd.id)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()


class SeveralOddRelearnTests(SeveralOddCase):
    def test_every_odd_one_missed_and_every_wrong_pick_comes_back(self):
        self.start()
        self.answer([self.odd_places[0], self.rest[0]])   # one odd one found, one of the rest picked instead
        mistakes = {str(k) for k in game_relearn.open_mistakes(self.user)}
        self.assertIn(self.odds[1], mistakes)                       # the odd one left out
        self.assertIn(self.grid["tiles"][self.rest[0]], mistakes)   # the beetle wrongly picked
        self.assertNotIn(self.odds[0], mistakes)                    # found: nothing to learn

    def test_a_grid_found_in_full_leaves_nothing(self):
        self.start()
        self.answer(self.odd_places)
        self.assertEqual(game_relearn.open_mistakes(self.user), {})


class SeveralOddReviewTests(SeveralOddCase):
    def test_the_card_names_every_odd_one_in_one_line(self):
        self.start()
        grid = self.answer([self.odd_places[0], self.rest[0]])["review"]["grid"]
        note = words(grid["note"])   # one short line (#569)
        self.assertTrue(note.startswith("Odd ones: "), note)
        for place in self.odd_places:
            self.assertIn(f"{place + 1} · ", note)
        self.assertNotIn("lines", grid)
        self.assertEqual((grid["found"], grid["wrong"], grid["count"]), (1, 1, 2))
        self.assertEqual({grid["tiles"][p]["state"] for p in self.odd_places}, {"right", "odd"})

    def test_one_odd_one_still_says_the_odd_one(self):
        self.start(odds=1)
        note = words(self.answer(self.odd_places)["review"]["grid"]["note"])
        self.assertTrue(note.startswith("Odd one: "), note)
        self.assertNotIn("Odd ones", note)

    def test_a_grid_whose_second_odd_one_was_shown_before_is_seen_before(self):
        self.start()
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=0, is_check=True,
                                  roi_id=self.odds[1])   # its names were shown after that answer
        self.answer(self.odd_places)
        self.assertTrue(GameAnswer.objects.get(mode="odd").seen_before)


class SeenBeforeOutOfNamingTests(FindThemAllNamingCase):
    def test_a_find_them_all_grid_seen_before_counts_nothing_toward_naming(self):
        self.grid(self.known["affinis"][:3], [self.known["typographus"][0]], picks=[0, 1, 2], seen_before=True)
        self.assertEqual(self.counts(), {})
        self.grid(self.known["affinis"][3:6], [self.known["typographus"][1]], picks=[0, 1, 2])
        self.assertEqual(self.counts(), {("genus", "xyleborini"): [1, 1],   # and the ranks above it (#640)
                                         ("tribe", "scolytinae"): [1, 1], ("subfamily", ""): [1, 1]})

    def test_the_boards_game_accuracy_leaves_beetles_seen_before_out(self):
        for i in range(10):
            self.grid(self.known["affinis"][i:i + 1], [], picks=[0], correct_genus=True, seen_before=i < 4)
        stats = game_board.mode_stats([self.user.id])[self.user.id]["select"]
        self.assertEqual((stats["correct"], stats["judged"]), (6, 6))


class AheadLeavesOutSpecimenMatesTests(GridCase):
    def test_same_specimen_adds_the_other_photos(self):
        a, b, c = self.known["affinis"][:3]
        Beetles.objects.filter(pk__in=[a.pk, b.pk]).update(depicts_specimen="SPEC-9")
        self.assertEqual(game.same_specimen([a.id]), {a.id, b.id})
        self.assertEqual(game.same_specimen([str(c.id)]), {c.id})
        self.assertEqual(game.same_specimen([]), set())

    def test_a_batch_built_for_another_game_drops_a_photo_of_a_beetle_named_since(self):
        dorsal, lateral = self.known["affinis"][:2]
        other = self.known["ferrugineus"][0]
        Beetles.objects.filter(pk__in=[dorsal.pk, lateral.pk]).update(depicts_specimen="SPEC-9")
        info = game_levels.for_player(self.user)
        choice = game.play_mode(self.user, info)
        items = [{"a": str(lateral.id), "check": True, "mode": "classify"},
                 {"a": str(other.id), "check": True, "mode": "classify"}]
        cache.set(game_warm.KEY.format(self.user.pk, choice),
                  {"items": items, "notice": "", "sig": game_warm._signature(info, game.player_focus(self.user)),
                   "at": time.time() - 30}, 60)
        rnd = GameRound.objects.create(player=self.user, mode="mixed", items=[])
        GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=0, roi=dorsal, is_check=True)
        taken = game_warm.take(self.user, "mixed")
        self.assertEqual([i["a"] for i in taken.items], [str(other.id)])


class HelpTests(GridCase):
    def test_how_it_works_says_bigger_grids_hide_more_odd_ones(self):
        how = " ".join(strip_tags(self.client.get(reverse("game_how")).content.decode()).split()).replace("’", "'")
        self.assertIn("bigger grids hide one to four odd ones, a different number each time, and you pick that many", how)
        self.assertIn("(in Odd One Out, then with two, three and four odd ones)", how)
        self.assertIn("in Similarity and Odd One Out, makes you a Distinction expert", how)
        self.assertNotIn("Imposter Picker", how)
        self.assertNotIn("Identification expert", how)

    def test_the_play_page_help_and_tour_count_the_odd_ones(self):
        page = Path(settings.BASE_DIR, "beetlesgallery", "templates", "beetles", "game_play.html").read_text(
            encoding="utf-8")
        self.assertIn("All but one share a name (bigger grids hide up to four). Select the ones that don't", page)
        self.assertIn('"Odd One Out: all but " + (oddWant > 1 ? oddWant : "one")', page)
        self.assertIn("oddPrompt(item.rank, oddWant)", page)
        self.assertNotIn("odd_count", page)

    def test_the_expertise_page_puts_find_them_all_under_naming(self):
        page = " ".join(html.unescape(strip_tags(self.client.get(reverse("game_expertise")).content.decode())).split())
        self.assertIn("Naming expert: names a taxon’s beetles in Naming and Find Them All.", page)
        self.assertIn("Distinction expert: tells them apart in Similarity and Odd One Out.", page)
        self.assertNotIn("Identification expert", page)
