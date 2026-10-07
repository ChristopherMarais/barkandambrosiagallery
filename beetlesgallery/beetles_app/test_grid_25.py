"""
The grid games grow to 25 beetles (5×5), the most a phone shows comfortably: the ladders, the move of every saved step
to the same grid on them, the builders at 25 and when they fall back, the points and balance checks, and the play page
(square tiles, the full width of a phone at 25, one faint whole-photo button per tile with the Flag inside the photo).
"""
import importlib

from django.apps import apps
from django.urls import reverse

from beetlesgallery.beetles_app import game, game_grid_ladder as ladder, game_scoring, game_tuning
from beetlesgallery.beetles_app.models import GameTuning, GridStep
from beetlesgallery.beetles_app.test_grid_builders import GridCase, at, every_anchor

MIGRATION = importlib.import_module("beetlesgallery.beetles_app.migrations.0054_grid_ladder_25")


class LadderTests(GridCase):
    def test_find_them_all_grows_to_25_before_each_rank(self):
        self.assertEqual(game.GRID_SIZES, (4, 9, 16, 25))
        self.assertEqual(ladder.steps("select"), [(size, rank, 1) for rank in game.RANKS for size in (4, 9, 16, 25)])

    def test_odd_one_out_grows_first_then_hides_more_odd_ones(self):
        self.assertEqual(ladder.ODD_SHAPES, ((4, 1), (9, 1), (16, 1), (25, 1), (9, 2), (16, 2), (25, 2), (16, 3),
                                             (25, 3), (25, 4)))
        self.assertEqual(len(ladder.steps("odd")), 40)
        for size, odds in ladder.ODD_SHAPES:
            self.assertLessEqual(odds, max(1, round(size / 6)))   # at most about a sixth of the grid odd
        self.assertEqual(ladder.steps("odd")[9], (25, "subfamily", 4))   # the last subfamily step
        self.assertEqual(ladder.steps("odd")[10], (4, "tribe", 1))

    def test_the_top_step_is_25_at_species(self):
        self.assertEqual(ladder.steps("odd")[-1], (25, "species", 4))
        self.assertEqual(ladder.steps("select")[-1], (25, "species", 1))
        self.assertEqual(ladder.top_step("species", "select"), 16)
        self.assertEqual(ladder.top_step("subfamily", "odd"), 10)


class MigrationTests(GridCase):
    """A player's saved step moves to the same grid: the same size, rank and number of odd ones."""

    def test_every_old_step_keeps_its_grid(self):
        for game_key in ("odd", "select"):
            for step, shape in enumerate(MIGRATION.OLD[game_key], start=1):
                with self.subTest(game=game_key, step=step):
                    new = MIGRATION.old_to_new(game_key, step)
                    self.assertEqual(ladder.steps(game_key)[new - 1], shape)
                    self.assertEqual(MIGRATION.new_to_old(game_key, new), step)

    def test_the_ladders_written_out_are_the_code_s(self):
        self.assertEqual(MIGRATION.NEW["odd"], ladder.steps("odd"))
        self.assertEqual(MIGRATION.NEW["select"], ladder.steps("select"))

    def test_the_rows_move_and_move_back(self):
        GridStep.objects.create(player=self.user, game="odd", step=18)      # was 16 beetles at genus, three odd
        GridStep.objects.create(player=self.superuser, game="select", step=12)   # was 16 at species, the top
        MIGRATION.forwards(apps, None)
        odd, select = GridStep.objects.get(game="odd"), GridStep.objects.get(game="select")
        self.assertEqual(ladder.steps("odd")[odd.step - 1], (16, "genus", 3))
        self.assertEqual(ladder.steps("select")[select.step - 1], (16, "species", 1))
        self.assertEqual((odd.step, select.step), (28, 15))
        MIGRATION.backwards(apps, None)
        self.assertEqual(sorted(GridStep.objects.values_list("step", flat=True)), [12, 18])

    def test_back_a_grid_of_25_becomes_one_of_16(self):
        self.assertEqual(MIGRATION.OLD["odd"][MIGRATION.new_to_old("odd", ladder.step_for(25, "genus", 4)) - 1],
                         (16, "genus", 3))
        self.assertEqual(MIGRATION.OLD["odd"][MIGRATION.new_to_old("odd", ladder.step_for(25, "tribe", 1)) - 1],
                         (16, "tribe", 1))
        self.assertEqual(MIGRATION.new_to_old("select", ladder.step_for(25, "species", game_key="select")), 12)

    def test_a_step_out_of_range_is_read_as_the_nearest(self):
        self.assertEqual(MIGRATION.old_to_new("select", 99), 15)   # past the old top: 16 at species
        self.assertEqual(MIGRATION.old_to_new("odd", 0), 1)


class BuilderTests(GridCase):
    def test_odd_one_out_at_25_with_four_odd_ones(self):
        at(self.user, "odd", ladder.step_for(25, "species", 4))
        item = game.build_odd_items(self.user, 1)[0]
        self.assertEqual((item["size"], len(set(item["tiles"])), len(item["odds"]), item["rank"]), (25, 25, 4, "species"))
        found = game.Beetles.objects.select_related("taxon").filter(id__in=item["tiles"])
        group = item["group"]["species"]
        outside = {str(b.id) for b in found if f"{b.taxon.genus} {b.taxon.species}" != group}
        self.assertEqual(outside, set(item["odds"]))
        self.assertEqual(len({b.image_asset_id for b in found}), 25)   # every beetle on its own photo

    def test_find_them_all_at_25(self):
        at(self.user, "select", ladder.step_for(25, "genus", game_key="select"))
        item = game.build_select_items(self.user, 1)[0]
        self.assertEqual((item["size"], len(set(item["tiles"])), item["rank"]), (25, 25, "genus"))
        known = game.Beetles.objects.select_related("taxon").filter(id__in=item["tiles"], bbox_is_validated=True)
        members = sum(b.taxon.genus == item["group"]["genus"] for b in known)
        self.assertTrue(7 <= members <= 10, members)                      # about a quarter to under half
        self.assertGreaterEqual(known.count() - members, members)         # tapping everything never pays
        self.assertLessEqual(25 - known.count(), game.SELECT_AI[25][1])

    def test_short_of_beetles_a_25_falls_back_to_16(self):
        every_anchor(self)
        for beetles in self.known.values():   # 18 of each species: enough for 16 at species, not for 25
            for roi in beetles[18:]:
                roi.delete()
        for beetles in self.unknown.values():
            for roi in beetles:
                roi.delete()
        at(self.user, "odd", ladder.step_for(25, "species"))
        item = game.build_odd_items(self.user, 1)[0]
        self.assertEqual((item["size"], item["rank"]), (16, "species"))


class PointsTests(GridCase):
    def setUp(self):
        super().setUp()
        game_tuning.forget()
        self.addCleanup(game_tuning.forget)

    def test_a_grid_of_25_is_worth_the_most(self):
        self.assertEqual([game_scoring.size_factor(n) for n in (4, 9, 16, 25)], [1.0, 1.5, 2.0, 2.5])

    def test_a_tuning_saved_before_25_gets_its_default(self):
        GameTuning.objects.create(key="GAME_GRID_SIZE_FACTOR", value={"4": 1.0, "9": 2.0, "16": 3.0})
        game_tuning.forget()
        self.assertEqual(game_tuning.current("GAME_GRID_SIZE_FACTOR"), {"4": 1.0, "9": 2.0, "16": 3.0, "25": 2.5})
        self.assertEqual(game_scoring.size_factor(25), 2.5)
        self.assertIn("Bigger grids are worth at least as much",
                      [rule for rule, ok, _ in game_tuning.checks() if not ok])   # 3 for 16 > 2.5 for 25: flagged

    def test_blind_play_loses_at_every_shape_and_every_check_holds(self):
        for size, odds in ladder.ODD_SHAPES:
            self.assertLess(game_tuning.odd_guess(size, odds), 0, (size, odds))
        self.assertLess(game_tuning.select_tap_all(25), 0)
        self.assertTrue(all(ok for _, ok, _ in game_tuning.checks()), game_tuning.checks())

    def test_the_scoring_page_tunes_25(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse("game_scoring")).content.decode()
        self.assertIn('name="GAME_GRID_SIZE_FACTOR.25"', page)
        self.assertIn("Odd One Out has 40", page)
        self.assertIn("25: 7–10", page)


class PageTests(GridCase):
    def page(self):
        return self.client.get(reverse("game_play", args=["mixed"])).content.decode()

    def test_square_tiles_and_the_full_width_of_a_phone_at_25(self):
        page = self.page()
        self.assertIn("#photo-area { container-type: size; }", page)
        self.assertIn('<div id="photo-area"', page)
        # the prompt now takes a reserved band at the top too (#play-prompt), alongside Lighting's at the bottom
        self.assertIn("--tile: calc((min(100cqw, 100cqh - var(--light) - var(--prompt))", page)
        self.assertIn("grid-auto-rows: var(--tile);", page)
        self.assertIn('#game #photos[data-size="25"] { --gap: 0.125rem; --pad: 0px; }', page)

    def test_a_tile_carries_one_faint_button_and_the_flag_is_in_the_whole_photo(self):
        page = self.page()
        self.assertIn('#game:is([data-mode="odd"], [data-mode="select"]) #photos .zoom-chip { opacity: 0.5;', page)
        self.assertIn("#game #photos .cell:is(.chips-on, .key-cursor) .zoom-chip", page)
        self.assertIn("function openTile(i)", page)
        self.assertIn("function showChips(i)", page)
        self.assertIn("if (im.more && !grid)", page)            # no "+2 photos" on a tile
        self.assertIn('if (!grid) holder.querySelectorAll(".cell")', page)   # no Flag on a tile
        self.assertIn("if (MODE === \"odd\" || MODE === \"select\") openTile(at);", page)   # Q and R too

    def test_names_on_a_grid_of_25_show_on_a_tap(self):
        page = self.page()
        self.assertIn(':is(#photos, #previous-photos)[data-size="25"] .cell.names-off .rv-names { opacity: 1; }', page)
        self.assertIn('[data-size="25"] .flag-badge { font-size: 0;', page)

    def test_the_round_review_lays_out_25_in_five_columns(self):
        from pathlib import Path

        from django.conf import settings
        review = (Path(settings.BASE_DIR) / "beetlesgallery/templates/beetles/game_round_review.html").read_text(encoding="utf-8")
        self.assertIn('item.sides.length > 16 ? "grid grid-cols-5 gap-1 items-center"', review)
