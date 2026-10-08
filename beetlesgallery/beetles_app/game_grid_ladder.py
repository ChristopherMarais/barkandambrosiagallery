"""
The grid games' ladder (#489): Odd One Out and Select all get harder as a player gets better, and easier again when
they struggle, each game on its own. The grids grow from 4 to 9, 16 and 25 beetles: 5×5 is the most a phone shows
comfortably. Select all has sixteen steps, the grid's size first and then its rank (the owner's choice):

    step   1    2    3    4    5 ... 8     9 ... 12    13 ... 16
    size   4    9    16   25   as 1-4      as 1-4      as 1-4
    rank   subfamily           tribe       genus       species

Odd One Out has 40 (#540): at each rank the grid grows first, then hides more odd ones, then the next rank comes. The
odd ones stay at most about a sixth of the grid (three in 16, four in 25):

    step   1    2    3    4    5    6    7    8    9    10     11 ... 20   21 ... 30   31 ... 40
    size   4    9    16   25   9    16   25   16   25   25     as 1-10     as 1-10     as 1-10
    odd    1    1    1    1    2    2    2    3    3    4
    rank   subfamily                                          tribe       genus       species

A player starts on GAME_GRID_START_STEP, goes up a step after GAME_GRID_UP_AFTER good grids in a row and down one
after a poor grid (outcome says which is which; skips and grids ended by flags are neither). No step goes deeper than
the ranks the player has open (game_levels.rank_unlock). game.py builds each grid at the player's step, or the nearest
easier grid the beetles allow (plan); restep() builds a batch's later grids again when the step moves, so the change
shows from the grid after the next one rather than the next batch. Not the next one itself: its photos are loaded
while the grid before it is played, and building it again made the player wait for new ones.
"""
from django.db import IntegrityError, transaction

from .game import GRID_SIZES, RANKS, game_setting

GRID_GAMES = ("odd", "select")
LADDER = [(size, rank) for rank in RANKS for size in GRID_SIZES]   # Select all
# Odd One Out (#540): (beetles, odd ones) at each rank, in turn
ODD_SHAPES = ((4, 1), (9, 1), (16, 1), (25, 1), (9, 2), (16, 2), (25, 2), (16, 3), (25, 3), (25, 4))
ODD_LADDER = [(size, rank, odds) for rank in RANKS for size, odds in ODD_SHAPES]
GOOD, POOR = "good", "poor"


def steps(game_key):
    """One game's ladder, [(size, rank, odd ones)]: Select all's steps all have one (where it means nothing)."""
    return ODD_LADDER if game_key == "odd" else [(size, rank, 1) for size, rank in LADDER]


def most_odds(size):
    """The most odd ones an Odd One Out grid of ``size`` beetles hides (ODD_SHAPES): one in 4, two in 9, three in 16, four in
    25."""
    return max([odds for s, odds in ODD_SHAPES if s <= size] or [1])


def step_for(size, rank, odds=1, game_key="odd"):
    """The ladder step (from 1) of a grid of ``size`` beetles at ``rank`` with ``odds`` odd ones, in one game."""
    return steps(game_key).index((size, rank, odds if game_key == "odd" else 1)) + 1


def start_step(game_key="odd"):
    return max(1, min(len(steps(game_key)), int(game_setting("GAME_GRID_START_STEP", 1))))


def up_after():
    return max(1, int(game_setting("GAME_GRID_UP_AFTER", 2)))


def good_share():
    return float(game_setting("GAME_GRID_GOOD_SHARE", 0.75))


def top_step(open_rank, game_key="odd"):
    """The highest step whose rank the player has open: a grid never asks for a rank they can't name yet."""
    return max(i for i, (_, rank, _) in enumerate(steps(game_key), start=1)
               if RANKS.index(rank) <= RANKS.index(open_rank))


def current(player, game_key):
    """The player's step in one grid game (GAME_GRID_START_STEP until they have played it)."""
    from .models import GridStep

    found = GridStep.objects.filter(player=player, game=game_key).values_list("step", flat=True).first()
    return min(found, len(steps(game_key))) if found else start_step(game_key)


def plan(player, game_key, open_rank, focus=None):
    """
    What the player's next grid should be: {"step", "size", "rank", "odds", "ranks"}. ``odds`` is how many odd ones an
    Odd One Out grid hides (1 in Select all). ``ranks`` are the ranks to build at, in turn while the beetles for one
    are short: the step's rank, then the nearest others (the shallower first). Only ranks the player has open and,
    with a focus (a chosen subfamily, tribe or genus), only ranks below it: every beetle shown is in it, so nothing at
    or above it can tell them apart.
    """
    step = current(player, game_key)
    size, rank, odds = steps(game_key)[min(step, top_step(open_rank, game_key)) - 1]
    deepest = RANKS.index(open_rank)
    above = RANKS.index(focus[0]) if focus and focus[0] in RANKS else -1
    allowed = [r for r in RANKS if above < RANKS.index(r) <= deepest] or list(RANKS[: deepest + 1])
    at = RANKS.index(rank)
    return {"step": step, "size": size, "rank": rank, "odds": odds,
            "ranks": sorted(allowed, key=lambda r: (abs(RANKS.index(r) - at), RANKS.index(r)))}


def outcome(answer):
    """
    GOOD, POOR or None (neither) for one grid answer. Odd One Out: the odd ones picked are good, and like Select all it
    is poor when its wrong picks (validated beetles of the rest) cost more than its right ones earned (#530, #540); a
    pick on a beetle nobody has validated says nothing yet. Select all: good with no wrong tap and at least
    GAME_GRID_GOOD_SHARE of the validated members found; poor when it lost points (its wrong taps cost more than its
    right ones earned, #530), or none right. So a good grid always scores and a poor one never does.
    Skips, held answers, grids ended by flags and retries (a small grid at the rank of a mistake, #490) are neither.
    """
    from . import game
    from .game_scoring import grid_tiles, wrong_cost

    if (answer.mode not in GRID_GAMES or answer.skipped or answer.score_hold or answer.is_retry
            or answer.grid_rank not in RANKS):
        return None
    if answer.mode == "odd" and not answer.picks:   # one pick, saved before grids hid several odd ones (#540)
        right = getattr(answer, f"correct_{answer.grid_rank}")
        return None if right is None else GOOD if right else POOR
    if answer.mode == "odd":
        grid = game.score_odd_grid(grid_tiles(answer), answer.picks, answer.grid_rank, answer.grid_group, answer.flagged)
        if grid["wrong"] and grid["right"] < wrong_cost() * grid["wrong"]:
            return POOR
        if not grid["wrong"] and grid["odds"] and grid["right"] >= good_share() * grid["odds"]:
            return GOOD
        return None
    grid = game.score_select(grid_tiles(answer), answer.picks, answer.grid_rank, answer.grid_group, answer.flagged)
    if not grid["members"]:
        return None
    if not grid["right"] or grid["right"] < wrong_cost() * grid["wrong"]:
        return POOR
    if not grid["wrong"] and grid["right"] >= good_share() * grid["members"]:
        return GOOD
    return None


def update(answer):
    """
    Move the player's step for one grid answer: up after a run of good grids (never past their open ranks), down after
    a poor one (never below 1). An answer moves it once at most, however often this is called for it. Returns the step,
    or None for an answer that is neither good nor poor.
    """
    from .game_levels import rank_for
    from .models import GridStep

    result = outcome(answer)
    if result is None:
        return None
    for attempt in range(2):   # two first answers at once: the second finds the row the first made
        try:
            with transaction.atomic():
                row, _ = GridStep.objects.select_for_update().get_or_create(
                    player_id=answer.player_id, game=answer.mode, defaults={"step": start_step(answer.mode)})
                if row.last_answer_id == answer.id:
                    return row.step
                row.step = min(row.step, len(steps(answer.mode)))
                if result == POOR:
                    row.step, row.good_run = max(1, row.step - 1), 0
                else:
                    row.good_run += 1
                    if row.good_run >= up_after():
                        row.good_run = 0
                        if row.step < top_step(rank_for(answer.player)["rank"], answer.mode):
                            row.step += 1
                row.last_answer = answer
                row.save()
                return row.step
        except IntegrityError:
            if attempt:
                raise
    return None


def restep(rnd, index):
    """
    The grid at ``index`` of a batch, and the one after it (whose photos load in the background meanwhile), built again
    when the player's step has moved since the batch was picked. A grid that can't be built at the new step stays as it
    was. Returns whether anything changed.
    """
    from . import game

    from .models import GameRound

    steps, changed = {}, {}
    for i in (index, index + 1):
        item = rnd.items[i] if 0 <= i < len(rnd.items) else None
        key = (item or {}).get("mode") or rnd.mode
        if item is None or key not in GRID_GAMES or "step" not in item:
            continue
        if key not in steps:
            steps[key] = current(rnd.player, key)
        if item["step"] == steps[key]:
            continue
        others = {t for j, it in enumerate(rnd.items) if j != i for t in game._item_ids(it)}
        fresh = game.build_grid_items(key, rnd.player, 1, avoid=others)
        if fresh:
            rnd.items[i] = changed[i] = dict(fresh[0], mode=key)
    if changed:
        # only these places, on the batch as it is now: a batch that grows (game_grow, #575) may have new items since
        with transaction.atomic():
            items = list(GameRound.objects.select_for_update().filter(id=rnd.id).values_list("items", flat=True)
                         .first() or rnd.items)
            for i, item in changed.items():
                items[i] = item
            GameRound.objects.filter(id=rnd.id).update(items=items)
        rnd.items = items
    return bool(changed)
