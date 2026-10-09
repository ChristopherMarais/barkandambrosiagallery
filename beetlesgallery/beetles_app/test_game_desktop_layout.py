"""
The game on a computer's screen (owner: "the PC web one should look right for the PC, and the mobile one be for the
mobile"). From the site's lg (1024px) on a screen wider than tall, the answer panel and its review card become a column
on the right of the photos, with Skip, Back and Submit at its foot; the photos take the full height (Naming's photo,
the grids' tiles up to 16:9, Similarity side by side or one above the other by the photos' shape); Back shows its card
in the same column; How to play and Focus keep a readable width; the recap spreads out; the game home has two columns.
All of it sits in media queries of its own: the phone layout (and a tablet held upright) is exactly as before.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

TEMPLATES = Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles"
PAGE = (TEMPLATES / "game_play.html").read_text(encoding="utf-8")
HOME = (TEMPLATES / "game_home.html").read_text(encoding="utf-8")
DESKTOP = "@media (min-width: 1024px) and (orientation: landscape) {"


def style_of(page):
    return page[page.index("<style>"):page.index("</style>")]


def block(css, opening):
    """The body of the CSS block that starts with ``opening`` (braces matched)."""
    start = css.index(opening) + len(opening)
    depth = 1
    for i in range(start, len(css)):
        depth += {"{": 1, "}": -1}.get(css[i], 0)
        if depth == 0:
            return css[start:i]
    raise AssertionError("unclosed block: " + opening)


STYLE = style_of(PAGE)
WIDE = block(STYLE, DESKTOP)
OUTSIDE = STYLE.replace(WIDE, "")


class DesktopBreakpointTests(SimpleTestCase):
    def test_one_desktop_block_at_the_sites_lg_and_only_on_a_landscape_screen(self):
        self.assertEqual(STYLE.count(DESKTOP), 1)
        self.assertEqual(STYLE.count("min-width: 1024px"), 1)   # nothing else on the page keys off a computer's width

    def test_the_answer_panel_is_a_column_on_the_right(self):
        self.assertIn("#game { --side: clamp(22.5rem, 27vw, 27rem); }", WIDE)
        self.assertIn("#stage { flex-direction: row; }", WIDE)
        self.assertIn("#panel { flex: 0 0 var(--side); width: var(--side); max-height: none; display: flex; flex-direction: column;", WIDE)
        self.assertIn("border-top: 0; border-left: 1px solid #e5e7eb; }", WIDE)
        # a grid of 16 or 25 no longer squeezes the panel: it has the full height beside the photos
        self.assertIn('#stage:has(#photos:is([data-size="16"], [data-size="25"])) #panel { max-height: none; }', WIDE)

    def test_skip_back_and_submit_stay_at_the_foot_of_the_column(self):
        self.assertIn("#actions { margin-top: auto; padding-top: 0.5rem; grid-template-columns: 1fr 1fr 1.5fr; }", WIDE)
        self.assertIn("#panel > #submit-hint:not(.hidden) { margin-top: auto; }", WIDE)   # the hint sits on the buttons

    def test_naming_takes_the_full_height_in_its_own_shape(self):
        self.assertIn('#game[data-mode="classify"] #photos { flex: 1 1 0; max-height: none; padding-bottom: 2.75rem; }', WIDE)
        self.assertIn('#game[data-mode="classify"] #photos .cell { width: min(100%, (100cqh - 3.25rem) * (var(--photo-ar, 4 / 3)));', WIDE)

    def test_similarity_stacks_wide_photos_and_puts_tall_ones_side_by_side(self):
        self.assertIn('#game[data-mode="pair"] #photos { flex-wrap: wrap; align-content: stretch; }', WIDE)
        self.assertIn('flex: 1 1 calc((100cqh - 1.5rem) / 2 * (var(--photo-ar, 1)))', WIDE)
        # each photo's shape, set as it lands, as for Naming
        self.assertIn('loads[i].then((got) => { if (got && canvas.isConnected) cell.style.setProperty("--photo-ar", canvas.width + " / " + canvas.height); });', PAGE)

    def test_grid_tiles_grow_wider_than_square_where_the_width_allows(self):
        self.assertIn("--tile-w: calc((100cqw - 2 * var(--pad) - (var(--grid-cols, 2) - 1) * var(--gap)) / var(--grid-cols, 2));", WIDE)
        self.assertIn("minmax(0, min(var(--tile-w), var(--tile) * 16 / 9))", WIDE)

    def test_back_shows_its_card_in_the_same_column(self):
        self.assertIn("#previous:not(.hidden) { display: grid; grid-template-columns: minmax(0, 1fr) var(--side);", WIDE)
        self.assertIn("#previous > :has(#previous-review) { display: flex; flex-direction: column; max-height: none;", WIDE)
        self.assertIn("#previous-done { margin-top: auto; }", WIDE)

    def test_long_lines_are_capped(self):
        self.assertIn("#help-sheet > :last-child, #focus-sheet > :last-child { width: 100%; max-width: 40rem;", WIDE)
        self.assertIn("#recap > div { max-width: 48rem; }", WIDE)
        self.assertIn("#recap-stats { grid-template-columns: repeat(4, minmax(0, 1fr)); }", WIDE)

    def test_no_new_colours(self):
        # grey only: the house's border grey, nothing that would carry a meaning
        self.assertEqual(set(re.findall(r"#[0-9a-fA-F]{3,6}\b", WIDE)), {"#e5e7eb"})


class PhoneLayoutUntouchedTests(SimpleTestCase):
    """Every rule the phone uses is still there, word for word, outside the desktop block."""

    PHONE_RULES = (
        "#game { position: fixed; inset: 0; z-index: 60; display: flex; flex-direction: column; background: #fff;",
        "#panel { flex: 0 1 auto; max-height: 68%; overflow-y: auto; overscroll-behavior: contain; padding: 0.625rem 0.75rem 0; border-top: 1px solid #e5e7eb; background: #fff; }",
        "#actions { position: sticky; bottom: 0; z-index: 2; display: grid; grid-template-columns: 1fr 1fr 2fr; gap: 0.5rem; margin-top: 0.5rem;",
        '#game[data-mode="classify"] #photos { flex: 0 1 auto; max-height: 45vh; width: 100%; background: none; }',
        '#game[data-mode="classify"] #photo-area { justify-content: center; }',
        '#stage:has(#photos:is([data-size="16"], [data-size="25"])) #panel { max-height: 45%; }',
        "--tile: calc((min(100cqw, 100cqh - var(--light) - var(--prompt)) - 2 * var(--pad) - (var(--grid-cols, 2) - 1) * var(--gap)) / var(--grid-cols, 2));",
        "display: grid; grid-template-columns: repeat(var(--grid-cols, 2), minmax(0, var(--tile))); grid-auto-rows: var(--tile);",
        '@media (max-width: 639px) { #game[data-mode="pair"] #photos { flex-direction: column; } #game[data-mode="pair"] #photos .cell { height: auto; width: 100%; } }',
        "@media (min-width: 700px) { #panel { padding-left: calc((100% - 38rem) / 2); padding-right: calc((100% - 38rem) / 2); } }",
        "#previous-photos { flex: 1 1 0; min-height: 0; display: grid; gap: 0.375rem; padding: 0.5rem; background: #f3f4f6;",
    )

    def test_the_phone_rules_are_unchanged(self):
        for rule in self.PHONE_RULES:
            self.assertIn(rule, OUTSIDE, rule)

    def test_the_new_rules_live_only_in_the_desktop_block(self):
        for rule in ("#stage { flex-direction: row; }", "flex-wrap: wrap; align-content: stretch;", "--tile-w:",
                     "#previous:not(.hidden)", "#recap-badges {", "--side"):
            self.assertNotIn(rule, OUTSIDE, rule)
        # Similarity reads each photo's shape only on a computer's screen; on a phone its photos stack as before
        pair_rules = [line for line in OUTSIDE.splitlines() if 'data-mode="pair"' in line]
        self.assertTrue(pair_rules)
        self.assertFalse([line for line in pair_rules if "--photo-ar" in line])

    def test_the_markup_the_phone_layout_hangs_on_is_unchanged(self):
        self.assertIn('<div id="stage" class="hidden flex-1 min-h-0 flex flex-col">', PAGE)
        self.assertIn('<div id="photo-area" class="relative flex-1 min-h-0 flex flex-col">', PAGE)
        self.assertIn('<div id="panel">', PAGE)
        self.assertIn('<div class="p-4 space-y-3 overflow-y-auto max-h-[60%]">', PAGE)   # Back's card
        self.assertIn('<div class="max-w-md mx-auto px-4 sm:px-6 pt-6 sm:pt-10 text-center"', PAGE)   # the recap


class GameHomeTests(SimpleTestCase):
    def test_two_columns_from_the_sites_lg(self):
        css = style_of(HOME)
        wide = block(css, "@media (min-width: 1024px) {")
        self.assertIn(".game-home { max-width: 64rem; }", wide)
        self.assertIn(".game-home-cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);", wide)
        self.assertEqual(css.replace(wide, "").count(".game-home"), 0)   # a phone sees no new rule
        self.assertIn('<div class="max-w-xl mx-auto game-home">', HOME)   # its one column, as before

    def test_playing_and_progress_left_the_rest_right_in_the_phones_order(self):
        cols = HOME.index('data-testid="home-cols"')
        split = "\n  </div>\n  <div>\n  {% if checked %}"   # after the accuracy chart the first column ends
        self.assertEqual(HOME.count(split), 1)
        order = [HOME.index(marker) for marker in (
            'data-testid="last-session"', 'data-testid="home-cols"', 'data-testid="play"', 'data-testid="streak-card"',
            "game_accuracy", split, 'data-testid="checked-recap"', 'aria-label="More"',
            'data-testid="home-board"', 'data-testid="share-qr"', "game_discussions.html", 'id="discovery"')]
        self.assertEqual(order, sorted(order))
        self.assertLess(HOME.index('data-testid="last-session"'), cols)   # the last session spans both columns
