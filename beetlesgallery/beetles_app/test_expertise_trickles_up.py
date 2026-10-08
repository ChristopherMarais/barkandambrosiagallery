"""
What a player recognises at a rank they recognise at the ranks above it (owner): someone good at a tribe's genera is
good at that tribe and its subfamily too. A Find Them All tap counts at every rank of the group above the grid's own,
right where the tapped beetle shares the group's name there, and a right name in Identification counts at the ranks
above it that the answer didn't give. A member left out, or a wrong name, says nothing about the ranks above.
"""
from beetlesgallery.beetles_app import game_trust
from beetlesgallery.beetles_app.models import PlayerSkill
from beetlesgallery.beetles_app.test_find_them_all_naming import FindThemAllNamingCase
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase
from beetlesgallery.beetles_app.testing import make_beetle, make_image, make_taxon


class GridTests(FindThemAllNamingCase):
    def test_a_genus_grid_counts_at_its_tribe_and_subfamily(self):
        for i in range(0, 12, 2):
            self.grid(self.known["affinis"][i:i + 2], [self.known["typographus"][i]], picks=[0, 1])
        game_trust.recompute_skills(self.user)
        skills = {(s.rank, s.branch): (s.correct, s.judged) for s in PlayerSkill.objects.filter(player=self.user)}
        self.assertEqual(skills[("genus", "Xyleborini")], (6, 6))
        self.assertEqual(skills[("tribe", "Scolytinae")], (6, 6))   # they are Xyleborini
        self.assertEqual(skills[("subfamily", "")], (6, 6))         # and Scolytinae

    def test_a_wrong_tap_in_the_groups_tribe_is_right_at_the_tribe(self):
        members, xylosandrus = self.known["affinis"][:2], self.known["crassiusculus"][0]
        self.grid(members, [xylosandrus], picks=[2])   # "this Xylosandrus is a Xyleborus"; both Xyleborus left out
        self.assertEqual(self.counts(), {
            ("genus", "xyleborini"): [0, 2],    # the Xylosandrus tapped, the Xyleborus missed
            ("tribe", "scolytinae"): [1, 1],    # but the Xylosandrus is a Xyleborini, as the tap said
            ("subfamily", ""): [1, 1],
        })

    def test_a_wrong_tap_in_another_tribe_is_wrong_there_too(self):
        self.grid(self.known["affinis"][:1], [self.known["typographus"][0]], picks=[1])
        counts = self.counts()
        self.assertEqual(counts[("tribe", "scolytinae")], [0, 1])   # an Ips is no Xyleborini
        self.assertEqual(counts[("subfamily", "")], [1, 1])         # but it is a Scolytinae

    def test_members_left_out_say_nothing_above_the_grids_rank(self):
        self.grid(self.known["affinis"][:3], [self.known["typographus"][0]], picks=[])
        self.assertEqual(self.counts(), {("genus", "xyleborini"): [0, 1]})

    def test_the_report_may_show_the_groups_higher_names(self):
        for i in range(0, 12, 2):
            self.grid(self.known["affinis"][i:i + 2], [], picks=[0, 1])
        game_trust.recompute_skills(self.user)
        progressing = {(s.rank, s.branch) for s in game_trust.player_report(self.user)["progressing"]}
        self.assertIn(("tribe", "Scolytinae"), progressing)   # the grids named Scolytinae to the player


class IdentificationTests(FindThemAllNamingCase):
    def name(self, roi, **fields):
        return ScoringCase.answer(self, self.user, roi, fields)

    def test_a_right_genus_alone_counts_at_the_tribe_and_subfamily(self):
        self.name(self.known["affinis"][0], genus="Xyleborus")
        self.assertEqual(self.counts(), {("genus", "xyleborini"): [1, 1], ("tribe", "scolytinae"): [1, 1],
                                         ("subfamily", ""): [1, 1]})

    def test_a_wrong_name_implies_nothing_above_it(self):
        self.name(self.known["affinis"][0], genus="Ips")
        self.assertEqual(self.counts(), {("genus", "xyleborini"): [0, 1]})

    def test_nor_at_a_rank_the_beetle_has_no_name_at(self):
        no_tribe = make_taxon(subfamily="Scolytinae", tribe="", genus="Ambrosiophilus", species="atratus",
                              scientific_name="Ambrosiophilus atratus")
        roi = make_beetle(image=make_image(image_file="originals/aa/bb/test.jpg"), taxon=no_tribe, bbox="validated")
        self.name(roi, genus="Ambrosiophilus", species="atratus")
        counts = self.counts()
        self.assertEqual(counts[("subfamily", "")], [1, 1])
        self.assertFalse(any(rank == "tribe" for rank, _ in counts))
