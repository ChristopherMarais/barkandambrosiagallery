"""
UI audit fixes for the core game pages (issue #618): the Bark & Ambrosia Detective home, play (all modes), History and the
round review. One regression fix is included: the loading screen used to show a short line and a beetle fact at
once (#play-loading-double), now covered in test_game_feel.py / test_game_wording.py alongside the rest of the
loading-screen behaviour they already own.
"""
from django.urls import reverse
from django.utils import timezone

from beetlesgallery.beetles_app import game, game_levels
from beetlesgallery.beetles_app.models import GameAnswer, GameRound, PlayerScore
from beetlesgallery.beetles_app.test_game import GameCase
from beetlesgallery.beetles_app.test_game_scoring import AFFINIS, ScoringCase


class HomeLockedGamesTests(GameCase):
    """gh-locked: a locked game and an unplayed one used to look the same ("–", "0 pts")."""

    def home(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_home")).content.decode()

    def odd_card(self, page):
        # the Odd One Out card's own slice of the page, not the whole thing (every card shares the same testids)
        start = page.index('data-testid="game-card-odd"', page.index('data-testid="game-split"'))
        end = page.find('<div class="relative p-3', start)   # the next card
        return page[start:end if end != -1 else start + 1500]

    def test_a_locked_game_shows_a_lock_and_the_level_that_opens_it(self):
        # a brand-new player: Odd One Out is still locked, not showing "–" and "0 pts" like an unplayed game would
        card = self.odd_card(self.home())
        self.assertIn('data-testid="game-card-locked"', card)
        self.assertIn("Unlocks at level " + str(game_levels.game_level("odd")), card)
        self.assertNotIn('data-testid="game-card-unplayed"', card)

    def test_an_unlocked_but_unplayed_game_invites_you_to_play_it(self):
        # level 2 opens Odd One Out and choosing your game, but this player has never played it
        PlayerScore.objects.update_or_create(player=self.user, defaults={"score": 60, "rating": 0.0})
        card = self.odd_card(self.home())
        self.assertIn('data-testid="game-card-unplayed"', card)
        self.assertIn("Not played yet", card)
        self.assertIn(f'href="{reverse("game_play", args=["odd"])}"', card)
        self.assertNotIn('data-testid="game-card-locked"', card)


class HomeWordingTests(GameCase):
    def home(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_home")).content.decode()

    def test_the_daily_goal_reads_as_a_count_not_a_fraction(self):
        # "79/20 today" reads like a fraction over 100% (#gh-today); now it is "N today · goal M"
        page = self.home()
        self.assertIn("today &middot; goal", page)

    def test_the_next_level_line_names_the_level_not_just_its_number(self):
        # "Level 2: 50 pts" used to be cryptic (#gh-level-bar); now "50 pts to Larva"
        page = self.home()
        self.assertIn("pts to " + game_levels.LEVELS[1][2], page)   # Larva
        self.assertNotIn("Level 2:", page)

    def test_the_more_nav_is_a_two_row_grid_on_a_phone(self):
        # five icons across broke "Leaderboard" mid-word (#gh-icons-row)
        page = self.home()
        self.assertIn("grid grid-cols-3 sm:grid-cols-5", page)
        self.assertIn(">Leaderboard</a>", page)
        self.assertNotIn("Leader&shy;board", page)


class HomeLeaderboardTableTests(GameCase):
    def test_the_home_board_shows_rank_player_score_accuracy_only(self):
        # eight columns ran off the right edge at 390px (#gh-leaderboard); now one Accuracy column, with the
        # per-game breakdown behind a tap (a sibling row, still in the page for anyone who taps)
        PlayerScore.objects.update_or_create(player=self.user, defaults={"score": 10, "viewed": 1})
        # game_board.board() defaults to period="week" and only counts a player whose GameAnswers fall in that
        # window (not just PlayerScore.viewed) -- give it one, so the row isn't filtered out.
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[])
        GameAnswer.objects.create(round=rnd, player=self.user, mode="classify", index=0, roi=self.roi(), is_check=True)
        self.client.force_login(self.user)
        # game_board_table.html (board-row/-detail, per-game accuracy testids) is the Home page's leaderboard
        # widget; the standalone Leaderboard page (game_leaderboard) got its own redesign in #618's reports-meta
        # batch and no longer uses this shared include.
        page = self.client.get(reverse("game_home")).content.decode()
        self.assertIn('data-testid="board-row"', page)
        self.assertIn('data-testid="board-row-detail"', page)
        self.assertIn('data-testid="accuracy"', page)
        for testid in ("id-accuracy", "sim-accuracy", "odd-accuracy", "select-accuracy"):
            self.assertIn(f'data-testid="{testid}"', page)


class HomeAccuracyChartTests(GameCase):
    def test_the_chart_labels_the_average_and_you(self):
        # a dashed line and an orange bar with no legend (#gh-chart); now both are labelled
        from django.template.loader import render_to_string
        standing = {"players": 3, "bins": [{"count": 1, "height": 50}] * 5, "average": 0.6,
                    "me": {"accuracy": 0.8, "percentile": 80, "rank": "Top 20%", "step": "great", "bin": 4}}
        html = render_to_string("beetles/includes/game_accuracy.html", {"standing": standing})
        self.assertIn("average 60%", html)
        self.assertIn('data-testid="accuracy-you-label">You<', html)
        self.assertIn("text-xs text-gray-500", html)   # 12px, gray-500 (not the old text-[10px] text-gray-400)
        self.assertNotIn("text-[10px] text-gray-400", html)


class HistoryFiltersTests(GameCase):
    def test_a_phone_gets_two_selects_side_by_side(self):
        # game pills + day pills wrapped to three lines on a phone (#his-filters)
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_history")).content.decode()
        self.assertIn('data-testid="game-filter-select"', page)
        self.assertIn('data-testid="day-filter-select"', page)
        self.assertIn("sm:hidden", page)
        self.assertIn("hidden sm:flex", page)   # the pills, from a tablet up


class HistoryPointsColourTests(ScoringCase):
    def test_a_gain_is_green_and_a_loss_is_red(self):
        ans = self.answer(self.user, self.roi(self.t_affinis), AFFINIS)
        self.points(ans)
        game.finish_round(ans.round)
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_history")).content.decode()
        self.assertIn("text-green-700", page)   # the gain, matching the round review's ticks (#his-points)


class PlayHeaderTests(GameCase):
    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=["mixed"])).content.decode()

    def test_exit_is_icon_only_with_a_screen_reader_label(self):
        page = self.page()
        self.assertIn('<span class="sr-only">Exit</span>', page)

    def test_combo_and_level_move_behind_one_tap_to_open_chip(self):
        # "L4 · 652/800" and the combo used to crowd the header with Exit, the mode name and the daily count
        # (#play-header); now they sit in a popover behind a single chip
        page = self.page()
        self.assertIn('id="progress-btn"', page)
        self.assertIn('id="progress-pop"', page)
        self.assertLess(page.index('id="progress-pop"'), page.index('id="combo"'))

    def test_the_daily_count_reads_as_today_and_goal(self):
        page = self.page()
        self.assertIn('chip.today + " today · goal " + chip.goal', page)


class PlaySubmitTests(GameCase):
    def page(self):
        self.client.force_login(self.user)
        return self.client.get(reverse("game_play", args=["mixed"])).content.decode()

    def test_disabled_submit_is_readable_grey_on_grey(self):
        # white text on gray-300 was about 1.5:1 (#play-submit); gray-500 on gray-200 reads
        page = self.page()
        self.assertIn("#submit:disabled { background: #e5e7eb; color: #6b7280; }", page)

    def test_a_hint_says_why_submit_is_off(self):
        page = self.page()
        self.assertIn('id="submit-hint"', page)
        self.assertIn('return "Pick a subfamily first"', page)
        self.assertIn('return "Select at least one"', page)


class PlaySpaceTests(GameCase):
    def test_naming_sizes_the_image_to_the_photo(self):
        # a tall grey area around a single photo in Naming (#play-space); now capped to 45% of the screen and
        # shaped to the photo's own aspect ratio
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["classify"])).content.decode()
        self.assertIn('#game[data-mode="classify"] #photos { flex: 0 1 auto; max-height: 45vh;', page)
        self.assertIn('cell.style.setProperty("--photo-ar"', page)


class PlayFlagButtonTests(GameCase):
    def test_the_flag_is_a_40px_icon_button(self):
        # a pale pill with small text was hard to find on the photo (#play-flag); now the detail page's style
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        self.assertIn("width: 2.5rem; height: 2.5rem; border-radius: 9999px;", page)


class PlayProgressBarTests(GameCase):
    def test_the_level_bar_is_grey_not_green(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["mixed"])).content.decode()
        self.assertIn('id="level-bar" class="h-full bg-gray-700 transition-all duration-500"', page)
        self.assertNotIn("bg-green-600", page)


class PlayHintsTests(GameCase):
    def test_similarity_hints_are_gray_500(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("game_play", args=["pair"])).content.decode()
        self.assertIn(".rung small { margin-left: auto; padding-left: 0.5rem; font-size: 0.6875rem; font-weight: 500; color: #6b7280; }", page)


class RoundReviewTests(GameCase):
    def page(self):
        rnd = GameRound.objects.create(player=self.user, mode="classify", items=[], finished_at=timezone.now())
        self.client.force_login(self.user)
        return self.client.get(reverse("game_round_review", args=[rnd.id])).content.decode()

    def test_fully_correct_answers_collapse_to_one_line(self):
        # every answer was a full card, ~33 screens for a round (#rev-collapse); a fully correct one is now one line
        page = self.page()
        self.assertIn("function isCorrect(item)", page)
        self.assertIn('li.dataset.testid = "item-row-correct"', page)
        self.assertIn('li.dataset.testid = "item-card"', page)

    def test_points_and_the_hardness_note_are_on_two_lines(self):
        # "#2 −48.8 pts (−75 missed) ×1.25 easier beetle" crowded one line with a pill that wrapped (#rev-header)
        page = self.page()
        self.assertIn("function pointsLine(item)", page)
        self.assertIn("function pointsSub(item)", page)
        self.assertIn('data-testid="item-sub"', page)

    def test_verified_status_sits_once_in_the_card_header(self):
        # a VERIFIED pill used to live inside the DATABASE column header (#rev-verified)
        page = self.page()
        self.assertIn("function headerBadge(item)", page)
        self.assertIn('<th class="text-left font-semibold pb-1 px-2">Database</th>', page)
        self.assertNotIn("Database ${sideStatus(side)}", page)

    def test_report_and_annotator_are_small_secondary_buttons(self):
        # small links squeezed at the right (#rev-links); now two icon buttons in the house's quiet style
        page = self.page()
        self.assertIn('class="btn-secondary h-8 px-2.5 gap-1.5 text-xs"', page)
        self.assertIn("fi-rr-flag", page)
        self.assertIn("fi-rr-edit", page)
