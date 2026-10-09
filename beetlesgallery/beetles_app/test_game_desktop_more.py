"""
More of the game on a computer (lg, 64rem and up), after #649: the grid games' question sits at the top of the answer
column so the grid has the full height, and History, the round review and the leaderboard use the width (two
columns). Below lg every page is the phone's: the new rules live only inside each page's one lg block, and the new
wrappers are plain blocks that keep the phone's order.
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

from beetlesgallery.beetles_app.test_game_desktop_layout import LG, block
from beetlesgallery.beetles_app.test_r7_review import js_function

TEMPLATES = Path(__file__).resolve().parent.parent / "templates" / "beetles"


def read(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def style(page):
    return page[page.index("<style>"):page.index("</style>")]


def outside_lg(css):
    """The CSS outside the lg block, without its comments."""
    return re.sub(r"/\*.*?\*/", "", css.replace(block(css, LG), ""), flags=re.S)


class GridQuestionTests(SimpleTestCase):
    PAGE = read("game_play.html")

    def setUp(self):
        self.css = style(self.PAGE)
        self.lg = block(self.css, LG)

    def test_the_question_moves_to_the_answer_column_on_a_computer_only(self):
        place = js_function(self.PAGE, "placePrompts")
        self.assertIn('if (WIDE_SCREEN.matches) $("panel").prepend(...prompts);', place)
        self.assertIn('else $("photo-area").prepend(...prompts);', place)
        self.assertIn('WIDE_SCREEN.addEventListener("change", placePrompts);', self.PAGE)
        # placed once at the start, after the screen-size query it reads exists
        self.assertLess(self.PAGE.index('const WIDE_SCREEN = window.matchMedia("(min-width: 64rem)");'), self.PAGE.index("  placePrompts();"))

    def test_in_the_column_it_is_a_heading_not_a_band_and_the_grid_keeps_no_room_for_it(self):
        self.assertIn('#game:is([data-mode="odd"], [data-mode="select"]) #photos { --prompt: 0px; }', self.lg)
        self.assertIn("#panel > :is(#odd, #select) { position: static; height: auto;", self.lg)
        self.assertIn("#panel > :is(#odd, #select) p { white-space: normal;", self.lg)

    def test_the_phone_keeps_its_band_above_the_photos(self):
        # the markup still puts the question in the photo area; the band and its room are as before
        self.assertLess(self.PAGE.index('<div id="photo-area"'), self.PAGE.index('<div id="odd" class="hidden" data-testid="odd-panel">'))
        self.assertLess(self.PAGE.index('<div id="select" class="hidden" data-testid="select-panel">'), self.PAGE.index('<div id="panel">'))
        self.assertIn("--prompt: 2.25rem;", self.css)
        self.assertNotIn("#panel > :is(#odd, #select)", outside_lg(self.css))
        self.assertEqual(self.css.count(LG), 1)


class TwoColumnPageTests(SimpleTestCase):
    """History, the round review and the leaderboard: two columns from lg, one below it in the same order."""

    def check(self, name, root, cols, side, main, side_first=True):
        page = read(name)
        css = style(page)
        self.assertEqual(css.count(LG), 1)
        lg = block(css, LG)
        self.assertIn(f"{root} {{ max-width:", lg)
        self.assertIn(f"{root} .{cols} {{ display: grid;", lg)
        rest = outside_lg(css)
        for cls in (cols, side, main):
            self.assertNotIn(cls, rest)
        self.assertLess(page.index(f'<div class="{cols}">'), page.index(f'<div class="{side if side_first else main}">'))
        first, second = (side, main) if side_first else (main, side)
        self.assertLess(page.index(f'<div class="{first}">'), page.index(f'<div class="{second}">'))
        return page, lg

    def test_history(self):
        page, lg = self.check("game_history.html", "#game-history", "his-cols", "his-side", "his-main")
        self.assertIn('<div id="game-history" class="max-w-xl mx-auto">', page)
        # the filters and the summary on the left, the sessions on the right
        side, main = page.index('<div class="his-side">'), page.index('<div class="his-main">')
        for left in ('data-testid="game-filter-select"', 'data-testid="game-filter"', 'data-testid="day-filter"', 'data-testid="history-summary"'):
            self.assertTrue(side < page.index(left) < main, left)
        self.assertLess(main, page.index('data-testid="sessions"'))
        # the curators' reviews two to a row
        self.assertIn("#game-history .his-main .checked-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));", lg)
        self.assertIn('<ul class="checked-list space-y-2">', read("includes/game_checked_list.html"))

    def test_round_review(self):
        page, lg = self.check("game_round_review.html", "#review", "rv-cols", "rv-side", "rv-main")
        side, main = page.index('<div class="rv-side">'), page.index('<div class="rv-main">')
        self.assertTrue(side < page.index('data-testid="review-summary"') < main)
        for right in ('data-testid="review-toc"', '<ul id="items"', 'data-testid="correct-group"', ">Keep playing</a>"):
            self.assertLess(main, page.index(right), right)
        self.assertIn("#review :is(#items, #items-correct) { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));", lg)
        # in a half-width card the buttons keep one line
        self.assertIn('#review #items :is([data-report], [data-testid="open-in-annotator"]) { white-space: nowrap;', lg)

    def test_leaderboard(self):
        page, lg = self.check("game_leaderboard.html", "#game-board", "lb-cols", "lb-side", "lb-main", side_first=False)
        self.assertIn("#game-board .lb-cols { display: grid; grid-template-columns: minmax(0, 1fr) 24rem;", lg)
        main, side = page.index('<div class="lb-main">'), page.index('<div class="lb-side">')
        self.assertTrue(main < page.index('data-testid="board-table"') < side)
        self.assertLess(side, page.index(">Specialists</h2>"))
        self.assertLess(side, page.index('id="branch-form"'))
