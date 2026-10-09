"""
Round 7 page fixes: the accuracy plot (5% bins, full-height You/average lines, an axis title and ticks, only "Top N%"
in the chip; one small plot per rank since, so ticks every 25% and the card's title for the axis), the settings
page's open reports sorted and paged by the database, the expertise key always shown, the invite QR code behind a
button, the home page's footer at the bottom and its beetle growing on hover, the annotator's old box colours with the
floating delete button, centred pills, and Interactions last in the menu.
"""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.template.loader import render_to_string
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from beetlesgallery.beetles_app import game, game_board
from beetlesgallery.beetles_app.models import GameReport, PlayerSkill
from beetlesgallery.beetles_app.test_game import GameCase
from beetlesgallery.beetles_app.test_game_scoring import ScoringCase

TEMPLATES = Path(settings.BASE_DIR) / "beetlesgallery" / "templates"


def read(*parts):
    return (TEMPLATES.joinpath(*parts)).read_text(encoding="utf-8")


def standing_html(me=True):
    """The card with one plot per rank (accuracy_standing_by_rank); with ``me``, the player is rated at each."""
    bins = [{"from": i / 20, "count": 1, "height": 50} for i in range(20)]
    mine = {"accuracy": 0.85, "percentile": 70, "rank": "Top 30%", "step": "excellent", "bin": 17} if me else None
    standing = {"players": 3, "me": me, "needed": 4, "ranks": [{"rank": rank, "players": 3, "average": 0.6, "bins": bins,
                                                                 "me": mine} for rank in game.RANKS]}
    return render_to_string("beetles/includes/game_accuracy.html", {"standing": standing})


def skill(player, correct, judged=100, rank="species"):
    return PlayerSkill.objects.create(player=player, rank=rank, branch="Xyleborus", correct=correct, judged=judged)


class AccuracyPlotTests(ScoringCase):
    """D1 and E12."""

    def test_twenty_bins_of_five_percent(self):
        for i, ok in enumerate([2, 7, 33, 34, 99, 100]):
            skill(self.player(f"p{i}"), ok)
        skill(self.user, 50)
        s = game_board.accuracy_standing_by_rank(self.user)["ranks"][3]   # species
        counts = [b["count"] for b in s["bins"]]
        self.assertEqual(len(counts), 20)
        self.assertEqual((counts[0], counts[1], counts[6], counts[10], counts[19]), (1, 1, 2, 1, 2))   # 1.0 joins the top bin
        self.assertEqual(sum(counts), 7)
        self.assertEqual(s["me"]["bin"], 10)
        self.assertEqual(s["players"], 7)

    def test_the_counting_is_done_by_the_database_in_a_few_queries(self):
        # the database adds up each player's skills per rank, in one grouped query; the bins are counted from it
        for i in range(30):
            skill(self.player(f"q{i}"), i, 30)
        skill(self.user, 15, 30)
        with CaptureQueriesContext(connection) as queries:
            game_board.accuracy_standing_by_rank(self.user)
        self.assertLessEqual(len(queries), 8)   # never one per player

    def test_the_lines_run_the_full_height_of_the_plot(self):
        html = standing_html()
        plot = html[html.index('data-testid="accuracy-plot"'):html.index('data-testid="accuracy-ticks"')]
        for testid in ("accuracy-you-line", "accuracy-average-line"):
            line = re.search(rf'<div[^>]*data-testid="{testid}"[^>]*>', plot).group(0)
            self.assertIn("inset-y-0", line)
            self.assertNotIn("top-5", line)
        self.assertEqual(plot.count("flex-1 rounded-t-sm"), 20)

    def test_an_axis_title_and_ticks_every_20_percent(self):
        # four small plots now: ticks every 25%, labelled at 0, 50 and 100%, and the card's title names the axis
        html = standing_html()
        ticks = html[html.index('data-testid="accuracy-ticks"'):]
        ticks = ticks[:ticks.index("</div>")]
        for pct in ("0%", "50%", "100%"):
            self.assertIn(f">{pct}<", ticks)
        for left in ("25%", "50%", "75%"):
            self.assertIn(f'border-l border-gray-500 h-1" style="left: {left}"', ticks)
        self.assertIn(">Accuracy by rank<", html)

    def test_the_chip_says_only_top_n_and_the_average_stays(self):
        # one plot per rank: the rank is grey text beside the players, and the average each dashed line's
        html = standing_html()
        self.assertNotIn("Better than", html)
        self.assertEqual(html.count('data-testid="accuracy-rank">Top 30%<'), 4)
        self.assertEqual(html.count('title="Average 60%"'), 4)
        self.assertIn(">Average</span>", html)


class SettingsPagingTests(GameCase):
    """D2: the open reports are sorted and paged by the database; every list has its own pager."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.superuser)

    def test_only_one_page_of_reports_is_loaded(self):
        roi = self.roi(self.t_affinis)
        User = get_user_model()
        GameReport.objects.bulk_create([GameReport(roi=roi, reporter=User.objects.create_user(f"z{n}", password="pw"),
                                                   reason="bad_image") for n in range(60)])
        with CaptureQueriesContext(connection) as queries:
            page = self.client.get(reverse("game_settings") + "?reports_sort=-reporter").content.decode()
        table = f'FROM "{GameReport._meta.db_table}"'
        sorted_selects = [q["sql"] for q in queries.captured_queries if table in q["sql"] and "LOWER(" in q["sql"].upper()]
        self.assertTrue(sorted_selects)   # sorted by the database, case aside
        self.assertTrue(all("LIMIT 25" in sql for sql in sorted_selects), sorted_selects)
        self.assertEqual(page.count("Open in annotator"), 25)

    def test_each_list_pages_on_its_own_parameter(self):
        source = read("beetles", "game_settings.html")
        for param in ("reports_page", "players_page", "labels_page", "unlocks_page"):
            self.assertIn(f'param="{param}"', source)


class ExpertiseKeyTests(ScoringCase):
    """D3: the key is always shown, not folded behind "Key"."""

    def test_no_details_around_the_key(self):
        source = read("beetles", "game_expertise.html")
        legend = source[source.index('data-testid="expertise-legend"'):source.index('data-testid="expertise-key"') + 400]
        self.assertNotIn("<details", legend)
        self.assertNotIn(">Key<", legend)


class InviteTests(ScoringCase):
    """D4: the QR code opens from a button that says "Invite others to play"."""

    def test_the_qr_code_is_behind_a_button(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_home")).content.decode()
        self.assertNotIn("Play with a friend", page)
        box = page[page.index('data-testid="share-qr"'):page.index('<div id="share-qr"')]
        self.assertIn("<details", box)
        self.assertIn("Invite others to play</summary>", box)
        self.assertNotIn("<details class=\"group\" data-testid=\"share-qr-toggle\" open", box)


class HomePageTests(GameCase):
    """D5 and D6."""

    def test_the_footer_sits_at_the_bottom_on_large_screens(self):
        source = read("landing.html")
        self.assertIn("@media (min-width: 1024px) { #landing-section { min-height: calc(100vh - 7.25rem); } }", source)
        self.assertIn('<section id="landing-section" class="relative flex flex-col', source)
        footer = re.search(r'<footer[^>]*data-testid="home-sponsors"[^>]*>', source).group(0)
        self.assertIn("mt-auto", footer)

    def test_the_beetle_grows_on_hover(self):
        page = self.client.get(reverse("image_browser")).content.decode()
        img = re.search(r'<img[^>]*data-testid="home-beetle"[^>]*>', page).group(0)
        self.assertIn("group-hover:scale-105", img)
        self.assertIn("transition-transform", img)


class AnnotatorBoxTests(GameCase):
    """E3, E4 and E5."""

    def setUp(self):
        super().setUp()
        self.source = read("beetles", "tool_annotate.html")
        self.draw = self.source[self.source.index("function drawCanvas"):self.source.index("function fitToScreen")]

    def test_green_amber_and_the_selected_box_blue(self):
        self.assertIn("'#10b981'", self.draw)
        self.assertIn("'#f59e0b'", self.draw)
        self.assertIn("'#3b82f6'", self.draw)
        key = self.source[self.source.index('data-testid="box-key"'):]
        key = key[:key.index("</span></span>\n")]
        for colour in ("#10b981", "#f59e0b", "#3b82f6"):
            self.assertIn(colour, key)

    def test_the_status_pill_text_is_centred(self):
        pill = re.search(r'<span id="canvas-status-pill"[^>]*>', self.source).group(0)
        for centred in ("inline-flex", "items-center", "justify-center", "text-center", "leading-none"):
            self.assertIn(centred, pill)

    def test_the_floating_delete_button_is_back(self):
        self.assertIn("function deleteButtonAt", self.source)
        self.assertIn("handle: 'delete_x'", self.source)
        self.assertIn("if (hit.handle === 'delete_x') {\n        deleteBoxUi(hit.idx);", self.source.replace("\r\n", "\n"))
        self.assertIn("ctx.fillText('X', del.x", self.draw)
        self.assertIn("coarse ? 14 : 9", self.source)   # 28px across on touch


class MenuOrderTests(GameCase):
    """E1: Interactions is last of the pages in the rail and the drawer."""

    def test_interactions_comes_after_data_management(self):
        source = read("base.html")
        for start in (source.index('id="mobile-menu"') if 'id="mobile-menu"' in source else 0, source.index('id="sidenav"')):
            interactions = source.index("{% url 'interactions_preview' %}", start)
            data = source.index("{% url 'data_management' %}", start)
            self.assertLess(data, interactions)
        for pill in re.findall(r'<span class="[^"]*bg-amber-100[^"]*">Preview</span>', source):
            self.assertIn("justify-center", pill)
