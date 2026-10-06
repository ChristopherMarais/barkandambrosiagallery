"""
The expertise tree shows "tells apart" next to naming (#381): Similarity, Odd One Out and Select all answers on
validated beetles, counted in the taxon whose children they tell apart. Only naming makes a Naming expert.
"""
from django.urls import reverse
from django.utils.html import strip_tags

from beetlesgallery.beetles_app.game_trust import apart_counts, expertise_tree
from beetlesgallery.beetles_app.models import GameAnswer, GameRound
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase

XYLEBORUS = {"subfamily": "Scolytinae", "tribe": "Xyleborini", "genus": "Xyleborus"}


class TellsApartTests(ScoringCase):
    def setUp(self):
        super().setUp()
        self.p = self.player("p")

    def grid(self, mode, rank, group, ok):
        rnd = GameRound.objects.create(player=self.p, mode=mode, items=[])
        return GameAnswer.objects.create(round=rnd, player=self.p, mode=mode, index=0, roi=self.roi(self.t_affinis),
                                         is_check=True, grid_rank=rank, grid_group=group, **{f"correct_{rank}": ok})

    def test_a_similarity_answer_judges_every_taxon_the_two_beetles_share(self):
        # two Xyleborus species, rightly called "same genus"
        self.answer(self.p, self.roi(self.t_affinis), mode="pair", roi_b=self.roi(self.t_ferr), pair="genus")
        counts = apart_counts(self.p)
        for key in (("subfamily", ""), ("tribe", "scolytinae"), ("genus", "xyleborini"), ("species", "xyleborus")):
            self.assertEqual(counts[key][:2], [1, 1], key)

    def test_beetles_from_different_subfamilies_only_judge_the_subfamilies(self):
        self.answer(self.p, self.roi(self.t_affinis), mode="pair", roi_b=self.roi(self.t_plat), pair="tribe")   # wrong
        self.assertEqual({key: row[:2] for key, row in apart_counts(self.p).items()}, {("subfamily", ""): [0, 1]})

    def test_grids_judge_their_own_rank_within_their_group(self):
        self.grid("odd", "genus", XYLEBORUS, True)                                   # picked a beetle outside Xyleborus
        self.grid("select", "species", dict(XYLEBORUS, species="Xyleborus affinis"), False)   # not a perfect grid
        counts = apart_counts(self.p)
        self.assertEqual(counts[("genus", "xyleborini")][:2], [1, 1])
        self.assertEqual(counts[("species", "xyleborus")][:2], [0, 1])

    def test_unscored_answers_do_not_count(self):
        self.answer(self.p, self.roi(self.t_affinis), mode="pair", roi_b=self.roi(self.t_ferr), pair="genus", skipped=True)
        self.grid("odd", "genus", XYLEBORUS, None)   # picked a beetle nobody has validated
        self.assertEqual(dict(apart_counts(self.p)), {})

    def test_the_tree_shows_both_and_telling_apart_never_makes_an_identification_expert(self):
        for _ in range(12):
            self.answer(self.p, self.roi(self.t_affinis), mode="pair", roi_b=self.roi(self.t_ferr), pair="genus")
        tree = expertise_tree(self.p)
        self.assertEqual((tree["root"]["apart_judged"], tree["root"]["apart_status"]), (12, "expert"))   # Distinction
        self.assertEqual(tree["experts"], 0)
        self.client.force_login(self.p)
        page = self.client.get(reverse("game_expertise")).content.decode()
        self.assertIn('data-testid="expertise-legend"', page)
        self.assertIn("apart 12/12", strip_tags(page))
