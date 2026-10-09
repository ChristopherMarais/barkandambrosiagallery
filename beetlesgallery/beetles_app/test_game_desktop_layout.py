"""
The game on a computer (lg, 64rem and up) uses the screen: the photos fill the left and the answer is a column on the
right, its buttons at its foot, so nothing the answer or the review puts in the column moves the photos. Similarity
shows two wide photos one above the other when that shows them bigger. Below lg the phone layout is exactly as it was:
every new rule sits inside the one lg block.
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

from beetlesgallery.beetles_app.test_r7_review import js_function

PAGE = (Path(__file__).resolve().parent.parent / "templates" / "beetles" / "game_play.html").read_text(encoding="utf-8")
STYLE = PAGE[PAGE.index("<style>"):PAGE.index("</style>")]
LG = "@media (min-width: 64rem) {"


def block(css, opening):
    """The rules inside the ``opening { ... }`` block, by its braces."""
    start = css.index(opening) + len(opening)
    depth, i = 1, start
    while depth:
        depth += {"{": 1, "}": -1}.get(css[i], 0)
        i += 1
    return css[start:i - 1]


class DesktopLayoutTests(SimpleTestCase):
    def setUp(self):
        self.lg = block(STYLE, LG)

    def test_one_lg_block(self):
        self.assertEqual(STYLE.count(LG), 1)

    def test_photos_left_and_the_answer_a_column_on_the_right(self):
        self.assertIn("#game { --side: clamp(24rem, 30vw, 30rem); }", self.lg)
        self.assertIn("#stage { flex-direction: row; }", self.lg)
        self.assertIn("#photo-area { flex: 1 1 0; min-width: 0; }", self.lg)
        panel = self.lg[self.lg.index("#panel, #game #stage:has(#photos) #panel {"):]
        panel = panel[:panel.index("}")]
        for rule in ("flex: 0 0 var(--side)", "width: var(--side)", "max-height: none", "flex-direction: column", "border-left: 1px solid #e5e7eb"):
            self.assertIn(rule, panel)

    def test_the_buttons_stay_at_the_foot_of_the_column(self):
        # the tip keeps its line while answering (#642) and the buttons sit under it; on the review the tip is gone
        # and the buttons take its place at the foot, so they never move
        self.assertIn("#submit-hint { margin-top: auto; padding-top: 0.75rem; }", self.lg)
        self.assertIn("#submit-hint.hidden + #actions { margin-top: auto; }", self.lg)

    def test_naming_s_photo_fills_the_room_and_keeps_its_own_size(self):
        self.assertIn('#game[data-mode="classify"] #photos { flex: 1 1 0; max-height: none; height: 100%; }', self.lg)
        # worked out from the item's shape (--ar), never from the canvas, as on a phone (#642)
        self.assertIn('#game[data-mode="classify"] #photos .cell { width: min(100%, calc((100cqh - 2 * var(--pad) - var(--light)) * var(--ar, 1.3333))); }', self.lg)

    def test_similarity_stacks_two_wide_photos(self):
        self.assertIn('#game[data-mode="pair"] #photos[data-stack] { flex-direction: column; }', self.lg)
        stack = js_function(PAGE, "pairStack")
        self.assertIn('delete holder.dataset.stack;', stack)
        self.assertIn('if (MODE !== "pair" || !WIDE_SCREEN.matches', stack)
        self.assertIn('if (stacked > side) holder.dataset.stack = "1";', stack)
        self.assertIn('const WIDE_SCREEN = window.matchMedia("(min-width: 64rem)");', PAGE)
        # chosen with the beetle, before its photos land
        show = js_function(PAGE, "showItem")
        self.assertLess(show.index("pairStack(holder, item.images.map((im) => im.ar));"), show.index("item.images.forEach((im, i) => {"))

    def test_similarity_s_tiles_take_their_photo_s_shape(self):
        # so the letter, Flag and chips sit on the photo; the shape comes with the item, as Naming's (#642)
        self.assertIn('#game[data-mode="pair"] #photos .cell { flex: 0 1 auto; height: auto; aspect-ratio: var(--photo-ar, 4 / 3);', self.lg)
        self.assertIn('if (MODE === "pair" && im.ar) photoShape(cell, im.ar);', PAGE)
        self.assertIn('if ((MODE === "classify" || MODE === "pair") && (!im.ar || Math.abs(ar - im.ar) > 0.02 * im.ar)) photoShape(cell, ar);', PAGE)

    def test_back_the_sheets_and_the_recap_use_the_width(self):
        self.assertIn("#previous:not(.hidden) { display: grid; grid-template-columns: minmax(0, 1fr) var(--side);", self.lg)
        self.assertIn("#previous #previous-side { max-height: none;", self.lg)
        self.assertIn('id="previous-side" class="p-4 space-y-3 overflow-y-auto max-h-[60%]"', PAGE)
        self.assertIn("#recap-stats:not(.hidden) { grid-template-columns: repeat(4, minmax(0, 1fr)); }", self.lg)


class PhoneLayoutUntouchedTests(SimpleTestCase):
    """Below lg nothing changed: the phone's rules are as they were, and the new ones live only in the lg block."""

    def test_the_phone_rules_are_as_they_were(self):
        for rule in (
            "#panel { flex: 0 1 auto; max-height: 68%; overflow-y: auto; overscroll-behavior: contain; padding: 0.625rem 0.75rem 0; border-top: 1px solid #e5e7eb; background: #fff; }",
            '#game[data-mode="classify"] #photos { flex: 0 1 auto; max-height: 45vh; width: 100%; background: none; }',
            "width: min(100%, calc((min(45vh, 100cqh) - 1rem) * var(--ar, 1.3333))); aspect-ratio: var(--photo-ar, 4 / 3); }",
            "#actions { position: sticky; bottom: 0; z-index: 2; display: grid; grid-template-columns: 1fr 1fr 2fr;",
            "@media (min-width: 700px) { #panel { padding-left: calc((100% - 38rem) / 2); padding-right: calc((100% - 38rem) / 2); } }",
            '@media (max-width: 639px) { #game[data-mode="pair"] #photos { flex-direction: column; }',
            "#stage:has(#photos:is([data-size=\"16\"], [data-size=\"25\"])) #panel { max-height: 45%; }",
            "#previous-photos { flex: 1 1 0; min-height: 0; display: grid;",
        ):
            self.assertIn(rule, STYLE)

    def test_the_new_rules_only_apply_from_lg(self):
        outside = STYLE.replace(block(STYLE, LG), "")
        outside = re.sub(r"/\*.*?\*/", "", outside, flags=re.S)
        for new in ("--side", "data-stack", "#previous-side", "flex-direction: row", "#recap-stats"):
            self.assertNotIn(new, outside)


class GameHomeTests(SimpleTestCase):
    """The game home on a computer: two columns, playing on the left and the rest on the right; one column below lg,
    in the same order as before."""

    HOME = (Path(__file__).resolve().parent.parent / "templates" / "beetles" / "game_home.html").read_text(encoding="utf-8")

    def test_two_columns_from_lg_only(self):
        style = self.HOME[self.HOME.index("<style>"):self.HOME.index("</style>")]
        lg = block(style, LG)
        self.assertIn("#game-home { max-width: 72rem; }", lg)
        self.assertIn("#game-home .gh-cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);", lg)
        self.assertNotIn("gh-cols", style.replace(lg, ""))
        self.assertIn('<div id="game-home" class="max-w-xl mx-auto">', self.HOME)

    def test_playing_left_and_the_rest_right_in_the_phone_s_order(self):
        home = self.HOME
        main, side = home.index('<div class="gh-main">'), home.index('<div class="gh-side">')
        self.assertLess(home.index('<div class="gh-cols">'), main)
        for left in ('data-testid="last-session"', 'data-testid="play"', 'data-testid="daily-goal"', "game_split.html"):
            self.assertTrue(main < home.index(left) < side, left)
        for right in ("game_accuracy_ranks.html", 'data-testid="checked-recap"', 'aria-label="More"', 'data-testid="home-board"', 'data-testid="share-qr"'):
            self.assertLess(side, home.index(right), right)
