"""
The games' names (#496, owner's choice): Odd One Out is Imposter Picker, Select all is Find Them All, and the choice
that mixes them (Mix) is All modes, which looks unlike the single games in the toolbar. Identification is one name
everywhere (no more Name That Beetle). Only what players see changed: the stored modes and URLs stay.
"""
import re
from pathlib import Path

from django.conf import settings
from django.urls import reverse

from beetlesgallery.beetles_app import game_levels, game_tuning, game_views
from beetlesgallery.beetles_app.models import GamePreference, GameRound
from beetlesgallery.beetles_app.test_game import GameCase

TEMPLATES = Path(settings.BASE_DIR) / "beetlesgallery" / "templates" / "beetles"
GAME_TEMPLATES = sorted(TEMPLATES.glob("game*.html")) + sorted((TEMPLATES / "includes").glob("game*.html"))
OLD = re.compile(r"[Oo]dd [Oo]nes? [Oo]ut|Select all|Name That Beetle|\bMix\b|>Mixed<")   # "to mix them" is fine


def without_comments(text):
    """The text a player can see or be shown: template, HTML, CSS and JS comments taken out."""
    text = re.sub(r"\{#.*?#\}|<!--.*?-->|/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"(?<![:\"'\\])//[^\n]*", "", text)


class NamesInTemplatesTests(GameCase):
    def test_no_old_name_is_left_in_the_game_pages(self):
        for path in GAME_TEMPLATES:
            with self.subTest(template=path.name):
                self.assertEqual(OLD.findall(without_comments(path.read_text(encoding="utf-8"))), [])

    def test_the_bulk_select_buttons_elsewhere_keep_their_name(self):
        # "Select All" on the image browser selects images: not a game, so not renamed
        self.assertIn("Select All", (TEMPLATES / "image_browser.html").read_text(encoding="utf-8"))

    def test_the_feed_shows_the_new_names(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        for name in ("Imposter Picker", "Find Them All", "All modes", "Identification", "Similarity"):
            self.assertIn(name, page)
        self.assertIn('const GAME_NAMES = { classify: "Identification", pair: "Similarity", odd: "Imposter Picker", '
                      'select: "Find Them All" };', page)
        self.assertIn("New game: Imposter Picker.", page)
        self.assertIn("New game: Find Them All.", page)

    def test_the_how_to_play_page_uses_the_new_names(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_how")).content.decode()
        self.assertIn('data-testid="how-odd">Imposter Picker<', page)
        self.assertIn('data-testid="how-select">Find Them All<', page)


class AllModesLooksDifferentTests(GameCase):
    def toolbar(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        return page, page[page.index('id="toolbar"'):page.index('id="focus-btn"')]

    def test_all_modes_is_its_own_pill_before_the_four_games(self):
        _, bar = self.toolbar()
        all_modes = re.search(r'<button[^>]*data-play="both"[^>]*>', bar).group(0)
        self.assertIn('data-long="All modes"', all_modes)
        self.assertIn('data-short="All"', all_modes)
        self.assertIn("play-all", all_modes)
        self.assertIn("border-dashed", all_modes)
        self.assertIn('role="radio"', all_modes)
        # the single games share one grey track; All modes sits outside it, first
        track = bar[bar.index("bg-gray-100 rounded-lg"):]
        self.assertNotIn('data-play="both"', track)
        for key in ("pair", "odd", "select", "classify"):
            self.assertIn(f'data-play="{key}"', track)
        self.assertLess(bar.index('data-play="both"'), bar.index('data-play="pair"'))
        self.assertIn('role="radiogroup"', bar[:bar.index('data-play="both"')])   # still one choice of five

    def test_short_names_fit_a_phone(self):
        _, bar = self.toolbar()
        shorts = re.findall(r'data-short="([^"]+)"', bar)
        self.assertEqual(shorts, ["All", "Similar", "Imposter", "Find all", "ID"])
        self.assertTrue(all(len(s) <= 8 for s in shorts))

    def test_the_feed_keeps_it_distinct_when_it_redraws_the_toolbar(self):
        page, _ = self.toolbar()
        render = page[page.index("function renderPrefs"):page.index("async function savePrefs")]
        self.assertIn('b.dataset.play === "both"', render)
        self.assertIn("fi-rr-shuffle", render)            # a mixing icon on the pill
        self.assertIn("border-dashed", render)            # dashed until it is the choice
        self.assertIn('setAttribute("aria-checked"', render)


class NamesInCodeTests(GameCase):
    def test_one_name_per_game(self):
        expected = {"classify": "Identification", "pair": "Similarity", "odd": "Imposter Picker",
                    "select": "Find Them All"}
        self.assertEqual({k: game_views.GAME_NAMES[k] for k in expected}, expected)
        self.assertEqual(game_levels.GAME_NAMES, expected)
        self.assertEqual(dict(GamePreference.PlayMode.choices), {"both": "All modes", **expected})
        self.assertEqual((GameRound.Mode.ODD.label, GameRound.Mode.SELECT.label, GameRound.Mode.MIXED.label),
                         ("Imposter Picker", "Find Them All", "All modes"))
        self.assertEqual(GamePreference.PlayMode.BOTH.value, "both")   # stored values unchanged
        self.assertEqual(GameRound.Mode.ODD.value, "odd")

    def test_perks_and_tuning_labels_use_the_new_names(self):
        self.assertEqual(game_levels.PERKS[game_levels.ODD_ONE_OUT][0], "Imposter Picker")
        self.assertEqual(game_levels.PERKS[game_levels.SELECT_ALL][0], "Find Them All")
        self.assertIn("All modes", game_levels.PERKS[game_levels.CHOOSE_GAME][1])
        text = repr([(t["label"], t["help"]) for t in game_tuning.TUNABLES.values()])
        text += repr(game_tuning.examples()) + repr(game_tuning.checks())
        self.assertEqual(OLD.findall(text), [])
        self.assertIn("Imposter Picker", text)
        self.assertIn("Find Them All", text)

    def test_the_level_toast_keeps_a_games_capitals(self):
        from beetlesgallery.beetles_app import game_rewards
        from beetlesgallery.beetles_app.models import PlayerScore

        before = game_rewards.progress(self.user)
        PlayerScore.objects.update_or_create(player=self.user, defaults={"score": 55, "rating": 0.0})
        level = next(e for e in game_rewards.play_events(self.user, before) if e["kind"] == "level")
        self.assertIn("Imposter Picker", level["text"])
        self.assertIn("choose your game", level["text"])   # other perks still read as part of the sentence

    def test_a_locked_game_is_named_in_its_error(self):
        self.client.force_login(self.user)
        res = self.client.post(reverse("game_prefs"), {"play_mode": "odd"}, content_type="application/json")
        self.assertEqual((res.status_code, res.json()["error"]), (403, "Imposter Picker unlocks at level 2."))
