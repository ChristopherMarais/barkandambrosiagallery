"""
Find Them All counts toward naming, not telling apart (#543): the grid names a group and the player has to recognise
its members, so a member tapped is a right name for its taxon at the grid's rank, a wrong tap or a member left out a
wrong one. A grid counts once per taxon it showed, each beetle once per rank, so big grids can't be farmed. And the
leaderboard shows every game's accuracy.
"""
import html

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.html import strip_tags

from beetlesgallery.beetles_app import game, game_board, game_trust
from beetlesgallery.beetles_app.models import GameAnswer, GameRound, PlayerScore, PlayerSkill
from beetlesgallery.beetles_app.test_grid_builders import GridCase


class FindThemAllNamingCase(GridCase):
    def grid(self, members, others, picks, rank="genus", group="affinis", **fields):
        """A Find Them All grid of ``members`` then ``others`` (Beetles), tapping the places in ``picks``."""
        tiles = [*members, *others]
        rnd = GameRound.objects.create(player=self.user, mode="select", items=[])
        return GameAnswer.objects.create(
            round=rnd, player=self.user, mode="select", index=0, roi=members[0], is_check=True, picks=list(picks),
            tiles=[str(t.id) for t in tiles], grid_rank=rank, grid_group=game.lineage(self.taxa[group], rank),
            **fields)

    def counts(self):
        return {key: row[:2] for key, row in game_trust.skill_counts(self.user).items()}


class NamingTests(FindThemAllNamingCase):
    def test_members_found_are_right_and_a_wrong_tap_is_wrong_in_the_tapped_beetles_own_taxon(self):
        xyleborus = self.known["affinis"][:3] + self.known["ferrugineus"][:1]
        others = [self.known["typographus"][0], self.known["crassiusculus"][0]]
        self.grid(xyleborus, others, picks=[0, 1, 2, 3, 4])   # every Xyleborus, and an Ips by mistake
        self.assertEqual(self.counts(), {
            ("genus", "xyleborini"): [1, 1],   # Xyleborus, once for its four beetles
            ("genus", "ipini"): [0, 1],        # "this Ips is a Xyleborus"
        })                                     # the Xylosandrus left out says nothing about naming
        shown = game_trust.skill_counts(self.user)[("genus", "xyleborini")][3]
        self.assertEqual(dict(shown), {"xyleborus": 1})

    def test_a_member_left_out_was_not_recognised(self):
        self.grid(self.known["affinis"][:3], [self.known["typographus"][0]], picks=[0, 1])
        self.assertEqual(self.counts(), {("genus", "xyleborini"): [0, 1]})

    def test_a_species_grid_counts_within_its_genus(self):
        members = self.known["affinis"][:2]
        self.grid(members, [self.known["ferrugineus"][0]], picks=[0, 1, 2], rank="species")
        self.assertEqual(self.counts(), {("species", "xyleborus"): [1, 2]})   # affinis right, ferrugineus wrong
        shown = game_trust.skill_counts(self.user)[("species", "xyleborus")][3]
        self.assertEqual(dict(shown), {"xyleborus affinis": 1, "xyleborus ferrugineus": 1})

    def test_each_beetle_counts_once_per_rank_across_grids_and_identification(self):
        members = self.known["affinis"][:4]
        self.grid(members, [], picks=[0, 1, 2, 3])
        self.grid(members, [], picks=[0, 1, 2, 3])                          # the same beetles again: nothing new
        self.assertEqual(self.counts(), {("genus", "xyleborini"): [1, 1]})
        self.grid(members[:1] + self.known["affinis"][4:5], [], picks=[0])   # only the new beetle counts: missed
        self.assertEqual(self.counts(), {("genus", "xyleborini"): [1, 2]})

    def test_a_beetle_named_in_identification_first_does_not_count_again_in_a_grid(self):
        from beetlesgallery.beetles_app.test_game import AFFINIS
        from beetlesgallery.beetles_app.test_game_scoring import ScoringCase

        roi = self.known["affinis"][0]
        ScoringCase.answer(self, self.user, roi, AFFINIS)
        before = self.counts()[("genus", "xyleborini")]
        self.grid([roi], [], picks=[])                                       # left out, but already named
        self.assertEqual(self.counts()[("genus", "xyleborini")], before)

    def test_unvalidated_and_flagged_beetles_and_unscored_grids_count_for_nothing(self):
        members = self.known["affinis"][:2]
        self.grid(members, [self.unknown["typographus"][0]], picks=[0, 2], flagged=[1])   # flagged member not missed
        self.assertEqual(self.counts(), {("genus", "xyleborini"): [1, 1]})
        fresh = self.known["affinis"][5:7]
        for fields in ({"skipped": True}, {"is_retry": True}, {"score_hold": True}):
            self.grid(fresh, [], picks=[], **fields)
        self.assertEqual(self.counts(), {("genus", "xyleborini"): [1, 1]})

    def test_find_them_all_no_longer_counts_as_telling_apart(self):
        self.grid(self.known["affinis"][:2], [self.known["typographus"][0]], picks=[0, 1])
        self.assertEqual(dict(game_trust.apart_counts(self.user)), {})

    def test_it_builds_naming_skill_toward_identification_expert(self):
        for i in range(0, 12, 2):
            self.grid(self.known["affinis"][i:i + 2], [self.known["typographus"][i]], picks=[0, 1])
        game_trust.recompute_skills(self.user)
        skill = PlayerSkill.objects.get(player=self.user, rank="genus", branch="Xyleborini")
        self.assertEqual((skill.correct, skill.judged), (6, 6))
        tree = game_trust.expertise_tree(self.user)
        tribe = next(t for s in tree["subfamilies"] for t in s["tribes"] if t["name"] == "Xyleborini")
        self.assertEqual((tribe["judged"], tribe["apart_judged"]), (6, 0))


class PagesTests(FindThemAllNamingCase):
    def test_the_expertise_page_lists_find_them_all_under_naming(self):
        page = " ".join(html.unescape(strip_tags(self.client.get(reverse("game_expertise")).content.decode())).split())
        # the "What it takes" box (#539) says which games count where
        self.assertIn("Naming expert: names a taxon’s beetles in Naming and Find Them All.", page)
        self.assertIn("Distinction expert: tells them apart in Similarity and Odd One Out.", page)

    def test_how_it_works_and_scoring_say_the_same(self):
        how = " ".join(strip_tags(self.client.get(reverse("game_how")).content.decode()).split())
        self.assertIn("in Similarity and Odd One Out, makes you a Distinction expert", how.replace("’", "'"))
        self.client.force_login(self.superuser)   # the scoring page is for superusers
        scoring = " ".join(strip_tags(self.client.get(reverse("game_scoring")).content.decode()).split())
        self.assertIn("Naming counts Naming answers and Find Them All grids", scoring)


class LeaderboardTests(FindThemAllNamingCase):
    def setUp(self):
        super().setUp()
        self.ann = get_user_model().objects.create_user("ann", password="pw")
        PlayerScore.objects.create(player=self.ann, score=10, viewed=5)
        PlayerScore.objects.filter(player=self.user).update(viewed=5)

    def stats(self, **acc):
        return {m: {"accuracy": acc.get(m)} for m in game_board.GAMES}

    def test_every_game_has_its_accuracy_and_its_sort(self):
        from collections import defaultdict
        from unittest import mock

        by_game = defaultdict(lambda: self.stats(), {
            self.user.id: self.stats(classify=0.5, pair=0.6, odd=0.7, select=0.4),
            self.ann.id: self.stats(odd=0.5, select=0.9)})
        with mock.patch.object(game_board, "mode_stats", return_value=by_game):
            rows = {r["username"]: r for r in game_board.board(period="all")}
            me = rows[self.user.username]
            self.assertEqual((me["id_accuracy"], me["sim_accuracy"], me["odd_accuracy"], me["select_accuracy"]),
                             (0.5, 0.6, 0.7, 0.4))
            self.assertEqual([r["username"] for r in game_board.board(sort="odd", period="all")][0], self.user.username)
            self.assertEqual([r["username"] for r in game_board.board(sort="select", period="all")][0], "ann")

    def test_the_page_shows_four_game_columns_and_sorts(self):
        page = self.client.get(reverse("game_leaderboard"), {"period": "all", "sort": "select"}).content.decode()
        for testid in ("id-accuracy", "sim-accuracy", "odd-accuracy", "select-accuracy"):
            self.assertIn(f'data-testid="{testid}"', page)
        self.assertIn('value="select" selected', page)
        self.assertIn("Odd One Out accuracy", page)
        for short in (">Name<", ">Sim.<", ">Odd<", ">Find<"):   # short headings on phones (names from #538)
            self.assertIn(short, page)

    def test_find_them_all_accuracy_on_the_board_is_perfect_grids(self):
        for i in range(10):
            self.grid(self.known["affinis"][i:i + 1], [], picks=[0], correct_genus=i < 8)
        stats = game_board.mode_stats([self.user.id])[self.user.id]
        self.assertEqual((stats["select"]["correct"], stats["select"]["judged"]), (8, 10))
        self.assertEqual(stats["select"]["accuracy"], 0.8)

    def test_the_profile_shows_every_game(self):
        page = self.client.get(reverse("game_profile", args=[self.ann.id])).content.decode()
        self.assertIn('data-testid="game-split"', page)
        for name in ("Naming", "Similarity", "Odd One Out", "Find Them All"):
            self.assertIn(name, page)
