"""
The game page hides its waits (#602): the next beetle's tiles are laid at once and each photo lands in its own; after
Submit a grid's tiles are numbered one by one while the answer is on its way, then revealed tile by tile; the review
is prepared on the server while the player chooses and the next beetle fetched while the review is read; tiles nobody
has checked are ringed blue (IBBI-AI) or purple (the other players). No motion with prefers-reduced-motion.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

PAGE = (Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles" / "game_play.html").read_text(encoding="utf-8")


def js_function(name):
    start = PAGE.index(f"function {name}(")
    return PAGE[start:PAGE.index("\n  }\n", start)]


def css_rule(selector):
    return re.search(re.escape(selector) + r" \{([^}]*)\}", PAGE).group(1)


class LayTheTilesTests(SimpleTestCase):
    def test_photos_load_before_the_tiles_are_laid_and_land_one_by_one(self):
        show = js_function("showItem")
        self.assertLess(show.index("const loads = item.images.map((im) => loadCrop(im)"), show.index('holder.innerHTML = "";'))
        self.assertIn('cell.className = "cell slot";', show)
        self.assertIn('cell.classList.add("landed");', show)
        # the answer opens once every photo is in, and a newer beetle wins
        self.assertLess(show.index('show("stage");'), show.index("await Promise.all(loads)"))
        self.assertIn("if (item !== next) return false;", show)
        self.assertIn("if (!await showItem(next)) return;", js_function("present"))

    def test_the_laying_is_short_and_only_where_motion_is_welcome(self):
        self.assertIn("const LAY_MS = 150;", PAGE)
        self.assertIn("animation: tile-lay 0.16s ease-out both", css_rule("#photos .cell.slot"))
        motion = PAGE.index("@media (prefers-reduced-motion: no-preference) {\n    #photos .cell.slot")
        self.assertGreater(motion, 0)
        self.assertIn("background: #e5e7eb", css_rule("#photos .cell.slot:not(.landed)"))   # an empty tile, grey

    def test_a_tile_being_laid_is_not_faded_as_locked(self):
        self.assertIn("#photos:not(.revealing):not(.laying) .cell:not(.picked):not(.tapped) canvas { opacity: 0.6; }", PAGE)


class TileByTileTests(SimpleTestCase):
    def test_numbers_from_submit_then_each_tile_in_turn(self):
        send = js_function("send")
        self.assertIn('if (!skipping && (MODE === "odd" || MODE === "select")) sweepStart(', send)
        self.assertLess(send.index("sweepStart("), send.index("await api("))
        self.assertIn("sweepStop(true);", send)   # the answer didn't go: the numbers come off again
        self.assertIn("if (review.grid) sweepReview(cells, review);", js_function("showReview"))

    def test_quick_steps_whole_grid_in_about_a_second(self):
        self.assertIn("Math.max(40, Math.min(80, Math.round(1000 / Math.max(1, n))))", PAGE)
        tick = js_function("sweepTick")
        self.assertIn("s.done < s.at ? 2 : 1", tick)   # the tiles numbered already catch up two at a time

    def test_all_at_once_with_reduced_motion(self):
        self.assertIn("if (reduceMotion) return;", js_function("sweepStart"))
        self.assertIn("while (sweep) sweepTick();", js_function("sweepReview"))

    def test_measured(self):
        self.assertIn('performance.mark("game:submit")', js_function("send"))
        self.assertIn('"game:first-tile"', js_function("revealTile"))
        self.assertIn('"game:review"', js_function("sweepTick"))
        self.assertIn('"game:tiles"', js_function("showItem"))


class PreloadTests(SimpleTestCase):
    def test_the_review_is_prepared_while_the_player_chooses(self):
        self.assertIn("prepareReview(item);", js_function("showItem"))
        self.assertIn("root.dataset.prepareUrl", js_function("prepareReview"))
        self.assertIn("data-prepare-url=", PAGE)

    def test_the_next_beetle_comes_while_the_review_is_read(self):
        send = js_function("send")
        self.assertIn("skipping ? {} : { item_later: true }", send)
        self.assertIn("data.itemLoad = fetchItem(data);", send)
        nxt = js_function("next")
        self.assertIn("if (!data.item && data.itemLoad) await data.itemLoad;", nxt)
        self.assertIn("if (!data.item) { await startFeed(); return; }", nxt)
        self.assertIn("upcoming.itemLoad.then(loadNext)", js_function("showReview"))

    def test_room_for_two_grids_of_25_decoded(self):
        self.assertIn("const DECODED_KEEP = 160;", PAGE)


class CallColourTests(SimpleTestCase):
    def test_unchecked_tiles_take_the_colour_of_who_calls_them(self):
        self.assertIn("--ring: #2563eb", css_rule(".cell:is(.ring-open, .ring-unknown).by-ai"))
        self.assertIn("--ring: #9333ea", css_rule(".cell:is(.ring-open, .ring-unknown).by-players"))
        # the same colours as the dots of the names
        self.assertIn("background: #2563eb", css_rule(".rv-dot.ai"))
        self.assertIn("background: #9333ea", css_rule(".rv-dot.players, .rv-dot.expert"))

    def test_only_unchecked_tiles(self):
        mark = js_function("markTile")
        self.assertIn('const by = ring === "ring-open" || ring === "ring-unknown" ? callBy(t) : null;', mark)
        call = js_function("callBy")
        self.assertIn('return p && (!a || p.sure >= a.sure) ? "players" : "ai";', call)   # the players on a tie
