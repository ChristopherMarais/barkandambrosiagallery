"""
The game home on a computer (lg, 64rem and up) is a play-first dashboard: a top band of the last session, the big
Play button and level / streak / goal; the four games in a row; the four accuracy plots in a row; then this week's
top players (wide) beside the shortcuts, Invite and feedback. Grid areas inside the one lg block place each piece,
so the DOM, and with it the phone's page, keeps its order and look.
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

from beetlesgallery.beetles_app.test_game_desktop_layout import LG, block

TEMPLATES = Path(__file__).resolve().parent.parent / "templates" / "beetles"
HOME = (TEMPLATES / "game_home.html").read_text(encoding="utf-8")
RANKS = (TEMPLATES / "includes" / "game_accuracy_ranks.html").read_text(encoding="utf-8")
STYLE = HOME[HOME.index("<style>"):HOME.index("</style>")]


def areas(rule):
    """The rows of a grid-template-areas value, each as a list of area names."""
    value = rule[rule.index("grid-template-areas:") + len("grid-template-areas:"):]
    value = value[:value.index(";")]
    return [row.split() for row in re.findall(r'"([^"]+)"', value)]


class DashboardLayoutTests(SimpleTestCase):
    def setUp(self):
        self.lg = block(STYLE, LG)

    def test_one_lg_block_and_nothing_outside_it(self):
        self.assertEqual(STYLE.count(LG), 1)
        outside = re.sub(r"/\*.*?\*/", "", STYLE.replace(self.lg, ""), flags=re.S)
        self.assertNotIn("gh-", outside)

    def test_the_rows_the_owner_chose(self):
        grid = self.lg[self.lg.index("#game-home .gh-grid {"):]
        rows = areas(grid[:grid.index("}")])
        self.assertEqual(rows[0], ["recap", "recap", "play", "play", "stats", "stats"])
        self.assertEqual(rows[1], ["modes"] * 6)
        self.assertEqual(rows[2], ["ranks"] * 6)
        self.assertEqual(rows[-2], ["board"] * 4 + ["nav"] * 2)
        self.assertEqual(rows[-1], ["board"] * 4 + ["more"] * 2)
        # without a last session, Play takes the recap's place: no empty corner
        bare = self.lg[self.lg.index("#game-home.gh-no-recap .gh-grid {"):]
        self.assertEqual(areas(bare[:bare.index("}")])[0], ["play"] * 4 + ["stats"] * 2)

    def test_every_area_has_its_piece(self):
        for cls, area in (("gh-recap", "recap"), ("gh-play", "play"), ("gh-stats", "stats"), ("gh-modes", "modes"), ("gh-ranks", "ranks"),
                          ("gh-checked", "checked"), ("gh-news", "news"), ("gh-nav", "nav"), ("gh-board", "board"), ("gh-more", "more")):
            self.assertRegex(self.lg, rf"#game-home \.{cls} {{ grid-area: {area};")
            self.assertRegex(HOME, rf'class="{cls}[ "]')

    def test_rows_that_are_not_there_leave_no_gap(self):
        # the gap under each row is the piece's own margin, never row-gap, so an empty row (no recap, nothing
        # checked, no notice) takes no room
        self.assertNotIn("row-gap", self.lg)
        self.assertNotIn(" gap: 1.75rem", self.lg)
        self.assertIn("#game-home .gh-grid > * { margin: 0 0 1.75rem; }", self.lg)

    def test_the_four_plots_in_a_row(self):
        # from 1280 wide; between lg and that the tick labels would touch, so they stay two by two
        self.assertIn("@media (min-width: 80rem) { #game-home .gh-ranks .acc-ranks { grid-template-columns: repeat(4, minmax(0, 1fr)); } }", self.lg)
        self.assertIn('<div class="acc-ranks grid grid-cols-1 sm:grid-cols-2 gap-2">', RANKS)

    def test_the_five_shortcuts_leave_no_hole(self):
        self.assertIn("#game-home .gh-nav { grid-area: nav; grid-template-columns: repeat(6, minmax(0, 1fr));", self.lg)
        self.assertIn("#game-home .gh-nav > a { grid-column: span 2; }", self.lg)
        self.assertIn("#game-home .gh-nav > a:nth-child(n+4) { grid-column: span 3; }", self.lg)
        self.assertEqual(HOME[HOME.index('aria-label="More"'):HOME.index("</nav>")].count("<a "), 5)

    def test_play_fills_its_column(self):
        self.assertIn("#game-home .gh-play { grid-area: play; display: flex; flex-direction: column; }", self.lg)
        self.assertIn("#game-home .gh-play > a { flex: 1 1 auto;", self.lg)


class DashboardMarkupTests(SimpleTestCase):
    def test_play_and_the_tour_link_share_the_play_column(self):
        play = HOME[HOME.index('<div class="gh-play">'):]
        play = play[:play.index("</div>")]
        self.assertIn('data-testid="play"', play)
        self.assertIn('data-testid="home-tour"', play)

    def test_level_streak_and_goal_stack_in_the_stats_column(self):
        stats = HOME[HOME.index('<div class="gh-stats">'):HOME.index('<div class="gh-modes')]
        for marker in ("game_level_badge.html", 'data-testid="streak-card"', 'data-testid="daily-goal"'):
            self.assertIn(marker, stats)

    def test_invite_feedback_and_settings_sit_under_the_shortcuts(self):
        more = HOME[HOME.index('<div class="gh-more">'):HOME.index("{% if discoveries %}")]
        for marker in ('data-testid="share-qr"', "game_discussions.html", 'data-testid="game-settings-link"'):
            self.assertIn(marker, more)

    def test_the_discovery_pop_up_is_outside_the_grid(self):
        # it is fixed over the page; outside the grid no area rule or margin can touch it
        self.assertLess(HOME.index('<div class="gh-more">'), HOME.index("{% if discoveries %}"))
        self.assertNotIn("discovery", HOME[HOME.index('<div class="gh-grid">'):HOME.index("{% if discoveries %}")])
