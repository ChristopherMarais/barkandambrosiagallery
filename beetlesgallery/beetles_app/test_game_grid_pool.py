"""
A batch's grids pick their validated beetles from one read of the pool (game._Pool) instead of a query for every
question: hundreds for a batch of big grids, most of the time it took. These pin that the answers are the very ones the
queries gave (a group at a rank in any case, the other groups, a parent, nothing to avoid), found by the same random
walk, and that IBBI-AI's beetles checked against the open pool read once come out as they did from the query.
"""
import random
import uuid
from unittest import mock

from beetlesgallery.beetles_app import game, game_ai_calls
from beetlesgallery.beetles_app.test_grid_builders import TREE, GridCase, at
from beetlesgallery.beetles_app.testing import make_beetle, make_image, make_taxon

ALL = 10 ** 6   # more than there are: every beetle that passes


def ids(qs):
    return set(qs.values_list("id", flat=True))


class SameBeetlesTests(GridCase):
    def setUp(self):
        super().setUp()
        self.pool = game._Pool(game.check_rois())

    def groups(self):
        for sub, tribe, genus, species in TREE:
            yield from (("subfamily", sub), ("tribe", tribe), ("genus", genus), ("species", f"{genus} {species}"))

    def test_a_group_at_a_rank(self):
        for rank, value in set(self.groups()):
            with self.subTest(rank=rank, value=value):
                self.assertEqual(set(self.pool.random_ids(game._in_group(rank, value), ALL)),
                                 ids(game.check_rois().filter(game.rank_q(rank, value))))

    def test_in_any_case_as_the_database_s_iexact(self):
        upper = self.pool.random_ids(game._in_group("genus", "XYLEBORUS"), ALL)
        self.assertEqual(set(upper), ids(game.check_rois().filter(game.rank_q("genus", "XYLEBORUS"))))
        self.assertEqual(len(upper), 2 * self.PER_SPECIES)
        self.assertEqual(set(self.pool.random_ids(game._in_group("species", "xyleborus AFFINIS"), ALL)),
                         {b.id for b in self.known["affinis"]})

    def test_a_name_with_a_stray_space_is_another_name_there_too(self):
        odd = make_taxon(subfamily="Scolytinae", tribe="Ipini", genus="Ips ", species="typographus",
                         scientific_name="Ips typographus")
        stray = make_beetle(image=make_image(image_file="originals/aa/bb/test.jpg"), taxon=odd, bbox="validated")
        pool = game._Pool(game.check_rois())
        for rank, value in (("genus", "Ips"), ("species", "Ips typographus"), ("genus", "Ips ")):
            with self.subTest(value=value):
                got = set(pool.random_ids(game._in_group(rank, value), ALL))
                self.assertEqual(got, ids(game.check_rois().filter(game.rank_q(rank, value))))
        self.assertIn(stray.id, ids(game.check_rois().filter(game.rank_q("genus", "Ips "))))

    def test_named_at_a_rank_and_the_other_groups(self):
        no_tribe = make_taxon(subfamily="Scolytinae", tribe="", genus="Ambrosiophilus", species="atratus",
                              scientific_name="Ambrosiophilus atratus")
        make_beetle(image=make_image(image_file="originals/aa/bb/test.jpg"), taxon=no_tribe, bbox="validated")
        pool = game._Pool(game.check_rois())
        for rank, value in set(self.groups()):
            with self.subTest(rank=rank, value=value):
                same, named = game._in_group(rank, value), game._named_test(rank)
                self.assertEqual(set(pool.random_ids(named, ALL)), ids(game.check_rois().filter(game._named_at(rank))))
                self.assertEqual(set(pool.random_ids(lambda t: named(t) and not same(t), ALL)),
                                 ids(game.check_rois().filter(game._named_at(rank)).exclude(game.rank_q(rank, value))))

    def test_never_a_beetle_to_avoid(self):
        avoid = {b.id for b in self.known["affinis"][:5]}
        taken = {str(b.id) for b in self.known["affinis"][5:8]}   # a growing batch's beetles (game.avoiding)
        token = game.avoiding.set(frozenset(taken))
        try:
            got = set(self.pool.random_ids(game._in_group("species", "Xyleborus affinis"), ALL, avoid))
        finally:
            game.avoiding.reset(token)
        self.assertEqual(got, {b.id for b in self.known["affinis"]} - avoid - {uuid.UUID(t) for t in taken})

    def test_the_same_walk_from_the_same_place(self):
        qs = game.check_rois().filter(game.rank_q("subfamily", "Scolytinae"))
        test = game._in_group("subfamily", "Scolytinae")
        for _ in range(5):
            pivot = uuid.uuid4()
            with mock.patch.object(game.uuid, "uuid4", return_value=pivot):
                self.assertEqual(set(self.pool.random_ids(test, 7)), set(game._random_ids(qs, 7)))

    def test_a_batch_of_grids_asks_no_query_per_question(self):
        at(self.user, "select", 14)
        with mock.patch.object(game, "_random_ids", side_effect=AssertionError("a query per question")):
            items = game.build_grid_items("select", self.user, 3)
        self.assertEqual(len(items), 3)
        at(self.user, "odd", 4)
        with mock.patch.object(game, "_random_ids", side_effect=AssertionError("a query per question")):
            self.assertTrue(game.build_grid_items("odd", self.user, 3))


class AiDrawTests(GridCase):
    def test_checked_against_the_pool_read_once_the_same_beetles_come_out(self):
        for b in self.unknown["affinis"] + self.unknown["typographus"] + self.unknown["cylindrus"]:
            self.predict(b, random.random())
        game_ai_calls.refresh()
        pool = game.open_rois().exclude(id=self.unknown["affinis"][0].id)
        for rank, value in (("genus", "Xyleborus"), ("subfamily", "Scolytinae"), ("genus", None)):
            with self.subTest(rank=rank, value=value):
                random.seed(7)
                by_query = game_ai_calls.draw(pool, rank, value, 0.0, 1.01, 10)
                random.seed(7)
                read_once = game_ai_calls.draw(frozenset(ids(pool)), rank, value, 0.0, 1.01, 10)
                self.assertEqual(read_once, by_query)
                self.assertNotIn(self.unknown["affinis"][0].id, read_once)
