"""
The game's wording for the full release (#538): Naming (was Identification) and Odd One Out (was Imposter Picker),
with Naming experts beside Distinction experts; "Select every X"; Odd One Out asks which one is a different rank
(#569); no "Seen before" tag on the photo; the photo rule in one sentence; the recap leads to the game's home; the
loading screen takes turns between short lines; and no Beta pill anywhere.
"""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import get_template
from django.template import TemplateDoesNotExist
from django.urls import reverse

from beetlesgallery.beetles_app import game_board, game_levels, game_tuning, game_views
from beetlesgallery.beetles_app.models import GamePreference, GameRound, GridStep
from beetlesgallery.beetles_app.test_game import GameCase
from beetlesgallery.beetles_app.test_game_names import without_comments

APP = Path(settings.BASE_DIR) / "beetlesgallery"
TEMPLATES = APP / "templates"


def tour_js():
    with open(finders.find("js/game_tour.js"), encoding="utf-8") as f:
        return f.read()


class PlayPageCase(GameCase):
    def play_page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=["mixed"])).content.decode()


class NamesTests(PlayPageCase):
    def test_naming_and_odd_one_out_in_code_and_choices(self):
        names = {"classify": "Naming", "pair": "Similarity", "odd": "Odd One Out", "select": "Find Them All"}
        self.assertEqual(game_levels.GAME_NAMES, names)
        self.assertEqual({k: game_views.GAME_NAMES[k] for k in names}, names)
        self.assertEqual(dict(GamePreference.PlayMode.choices), {"both": "All modes", **names})
        self.assertEqual(GameRound.Mode.ODD.label, "Odd One Out")
        self.assertEqual(dict(GridStep._meta.get_field("game").choices)["odd"], "Odd One Out")
        self.assertEqual(game_levels.PERKS[game_levels.IDENTIFY][0], "Naming")
        self.assertEqual(game_board.SORTS["identification"], "Naming accuracy")
        self.assertIn(("classify", "Naming"), game_tuning.GAMES)
        # stored values, URLs and perk keys stay
        self.assertEqual((GamePreference.PlayMode.CLASSIFY.value, GameRound.Mode.ODD.value), ("classify", "odd"))
        self.assertEqual(game_levels.IDENTIFY, "identification")

    def test_the_toolbar_names_fit_a_phone(self):
        page = self.play_page()
        bar = page[page.index('id="toolbar"'):page.index('id="focus-btn"')]
        self.assertIn('data-play="odd" data-short="Odd one" data-long="Odd One Out"', bar)
        self.assertIn('data-play="classify" data-short="Name" data-long="Naming"', bar)
        self.assertIn(">Naming</button>", bar)
        self.assertIn("New game: Naming.", page)

    def test_the_experts_are_naming_and_distinction_experts(self):
        self.client.force_login(self.user)
        how = self.client.get(reverse("game_how")).content.decode()
        self.assertIn("A <strong>Naming expert</strong> names its beetles reliably", how)
        self.assertIn("Distinction expert", how)
        self.assertIn("Only Naming experts skip review.", self.client.get(reverse("game_unlocks")).content.decode())

    def test_no_old_name_is_left_where_players_see_it(self):
        old = re.compile(r"Imposter|Identification expert|Identification game")
        paths = list(TEMPLATES.rglob("*.html")) + [APP / "static" / "js" / "game_tour.js"]
        paths += [p for p in (APP / "beetles_app").glob("*.py") if not p.name.startswith("test_")]
        for path in paths:
            with self.subTest(path=path.name):
                self.assertEqual(old.findall(without_comments(path.read_text(encoding="utf-8"))), [])
        tuning = repr([(t["label"], t["help"]) for t in game_tuning.TUNABLES.values()])
        tuning += repr(game_tuning.examples()) + repr(game_tuning.checks())
        self.assertNotIn("Identification", tuning)
        self.assertIn("Naming weight", tuning)

    def test_the_profile_counts_beetles_named_and_odd_ones_spotted(self):
        profile = (TEMPLATES / "beetles" / "game_profile.html").read_text(encoding="utf-8")
        self.assertIn("named &middot;", profile)
        self.assertIn("odd ones spotted", profile)


class PromptTests(PlayPageCase):
    def test_find_them_all_says_select_every(self):
        page = self.play_page()
        self.assertIn('$("select-prompt").replaceChildren("Select every ", name, size);', page)
        self.assertIn('data-help-mode="select">Select every beetle of the group', page)
        self.assertNotIn("Tap every", without_comments(page))
        self.assertNotIn("tap every", without_comments(page))
        self.assertEqual(game_views.GAME_TAGLINES["select"], "Nine beetles. Select every one of a group.")
        self.assertIn("select every beetle of one group", game_levels.PERKS[game_levels.SELECT_ALL][1])

    def test_odd_one_out_asks_for_the_one_that_doesnt_share_the_rank(self):
        page = self.play_page()
        self.assertIn('"Which one is a different " + rank + "?"', page)   # shorter since #569
        self.assertIn('"Which " + n + " are a different " + rank + "?"', page)   # several odd ones
        self.assertIn("oddPrompt(item.rank, oddWant)", page)   # oddWant: item.odds, how many odd ones (#540)
        self.assertNotIn("Which one doesn't?", page)

    def test_the_seen_before_tag_is_gone_from_the_photo_but_the_server_still_marks_it(self):
        page = self.play_page()
        self.assertNotIn("Seen before: have another go", page)
        self.assertNotIn('r.again ? node("span", "rv-pill", "Seen before")', page)   # no headline pill: a mark on the photo (#615)
        self.assertIn("function seenMark(testid)", page)
        source = (APP / "beetles_app" / "game_views.py").read_text(encoding="utf-8")
        self.assertIn('payload["again"] = True', source)


class PhotoRuleTests(PlayPageCase):
    RULE = "A photo must show a good part of the beetle."

    def test_the_help_the_tip_and_the_tour_say_just_the_rule(self):
        page = self.play_page()
        self.assertIn(f"<li>{self.RULE}</li>", page)                                           # How to play
        self.assertIn(f'data-testid="report-tip-note">{self.RULE}</p>', page)                 # the report tip
        report_step = next(line for line in tour_js().splitlines() if 'el: "report-chip-0"' in line)
        self.assertIn(self.RULE, report_step)
        for text in (page, report_step):
            self.assertNotIn("a leg", text)

    def test_the_how_to_page_quick_start_says_just_the_rule(self):
        self.client.force_login(self.user)
        how = self.client.get(reverse("game_how")).content.decode()
        start = how[how.index('data-testid="quick-start"'):how.index("</ol>")]
        self.assertIn(f"<li>{self.RULE}</li>", start)
        self.assertNotIn("leg", how[how.index('id="faq"'):])

    def test_the_report_menu_hint_has_no_legs(self):
        self.assertNotIn("leg", " ".join(game_views.FEED_REPORT_HINTS.values()))


class RecapTests(PlayPageCase):
    def test_the_recap_leads_to_the_games_home(self):
        page = self.play_page()
        self.assertIn(f'<a href="{reverse("game_home")}" class="px-6 py-3.5 text-base font-medium text-gray-700 border '
                      f'border-gray-300 rounded-xl" data-testid="recap-home">{settings.GAME_DISPLAY_NAME}</a>', page)
        self.assertNotIn("See my stats and the leaderboard", page)


class LoadingTests(PlayPageCase):
    def test_the_loading_screen_starts_with_finding_beetles(self):
        page = self.play_page()
        loading = page[page.index('id="loading"'):page.index("<!-- The feed -->")]
        self.assertIn('<p id="loading-line" class="mt-2 text-sm" data-testid="loading-line">Finding beetles&hellip;</p>',
                      loading)

    def test_the_lines_take_turns_then_say_it_is_slow_and_stop_once_loaded(self):
        page = self.play_page()
        self.assertIn('const LOADING_LINES = ["Finding beetles…", "Building your gallery…"];', page)
        self.assertIn('const LOADING_SLOW = ["This is taking a while: probably making frass…",\n'
                      '                        "This is taking a while: waiting for the fungus garden to grow…"];',
                      page.replace("\r\n", "\n"))
        # each first line three times (about 15 s), then the slow ones in turn (#569)
        self.assertIn("const LOADING_LINE_MS = 2500, LOADING_TURNS = LOADING_LINES.length * 3;", page)
        lines = page[page.index("function loadingLines()"):page.index("async function startFeed")]
        self.assertIn('window.matchMedia("(prefers-reduced-motion: reduce)").matches', lines)
        self.assertIn("if (turn === LOADING_TURNS) line.textContent = LOADING_SLOW[0];", lines)   # reduced motion: one change
        self.assertIn('$("loading").classList.contains("hidden")', lines)    # stops once the beetles are in
        self.assertIn("clearInterval(loadingTimer)", lines)
        feed = page[page.index("async function startFeed"):]
        self.assertTrue(feed.index('show("loading");') < feed.index("loadingLines();") < feed.index("await api("))


class OutOfBetaTests(PlayPageCase):
    def test_no_beta_pill_anywhere(self):
        with self.assertRaises(TemplateDoesNotExist):
            get_template("beetles/includes/game_beta.html")
        for path in TEMPLATES.rglob("*.html"):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(">Beta<", text)
                self.assertNotIn("game_beta", text)
        self.client.force_login(self.user)
        for url in ("/", reverse("game_home"), reverse("game_how"), reverse("game_play", args=["mixed"])):
            with self.subTest(url=url):
                page = self.client.get(url).content.decode()
                self.assertNotIn(">Beta</span>", page)
                self.assertNotIn("in beta", page)
