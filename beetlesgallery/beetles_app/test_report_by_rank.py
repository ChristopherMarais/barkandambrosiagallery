"""
The report's "Accuracy by rank" (#605): Naming and Distinction, counted as the expertise tree counts them. Naming is
Naming and Find Them All (the player's skills), Distinction is Similarity and Odd One Out (apart_counts), each added up
over every branch at the rank.
"""
from types import SimpleNamespace
from unittest import mock

from django.urls import reverse

from beetlesgallery.beetles_app import game_trust
from beetlesgallery.beetles_app.models import PlayerSkill
from beetlesgallery.beetles_app.test_game import GameCase


def skill(rank, branch, correct, judged):
    return SimpleNamespace(rank=rank, branch=branch, correct=correct, judged=judged)


class AccuracyByRankTests(GameCase):
    def test_every_branch_at_a_rank_is_added_up(self):
        skills = [skill("species", "Xyleborus", 3, 4), skill("species", "Platypus", 1, 4), skill("genus", "Xyleborini", 2, 2)]
        apart = {("species", "xyleborus"): [1, 2, "Xyleborus", {}], ("species", "platypus"): [1, 2, "Platypus", {}],
                 ("tribe", "scolytinae"): [0, 1, "Scolytinae", {}]}
        rows = {r["rank"]: r for r in game_trust.accuracy_by_rank(skills, apart)}
        self.assertEqual(list(rows), ["subfamily", "tribe", "genus", "species"])
        self.assertEqual(rows["species"]["naming"], {"ok": 4, "n": 8, "accuracy": 0.5})
        self.assertEqual(rows["species"]["distinction"], {"ok": 2, "n": 4, "accuracy": 0.5})
        self.assertEqual(rows["genus"]["naming"]["accuracy"], 1.0)
        self.assertEqual(rows["genus"]["distinction"], {"ok": 0, "n": 0, "accuracy": None})
        self.assertEqual(rows["tribe"]["distinction"]["n"], 1)

    def test_the_report_uses_the_trees_counts(self):
        PlayerSkill.objects.create(player=self.user, rank="genus", branch="Xyleborini", correct=3, judged=4)
        with mock.patch.object(game_trust, "apart_counts", return_value={("genus", "xyleborini"): [1, 4, "Xyleborini", {}]}):
            rows = {r["rank"]: r for r in game_trust.player_report(self.user)["by_rank"]}
        self.assertEqual((rows["genus"]["naming"]["accuracy"], rows["genus"]["distinction"]["accuracy"]), (0.75, 0.25))

    def test_the_table_says_naming_and_distinction(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_report")).content.decode()
        table = page[page.index("Accuracy by rank"):page.index("<!-- Trend -->")]
        self.assertIn(">Naming<", table)
        self.assertIn(">Distinction<", table)
        self.assertNotIn("Similarity", table)
