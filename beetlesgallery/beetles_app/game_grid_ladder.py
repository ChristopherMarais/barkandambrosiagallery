"""
The grid games' ladder (#489): Odd One Out and Select all get harder as a player gets better, and easier again when
they struggle, each game on its own. Twelve steps, the grid's size first and then its rank (the owner's choice):

    step   1    2    3    4    5    6    7    8    9    10   11   12
    size   4    9    16   4    9    16   4    9    16   4    9    16
    rank   subfamily      tribe          genus          species

A player starts on GAME_GRID_START_STEP, goes up a step after GAME_GRID_UP_AFTER good grids in a row and down one
after a poor grid (outcome says which is which; skips and grids ended by flags are neither). No step goes deeper than
the ranks the player has open (game_levels.rank_unlock). game.py builds each grid at the player's step, or the nearest
easier grid the beetles allow (plan); restep() builds a batch's later grids again when the step moves, so the change
shows from the next grid rather than the next batch.
"""
from django.db import IntegrityError, transaction

from .game import GRID_SIZES, RANKS, game_setting

GRID_GAMES = ("odd", "select")
LADDER = [(size, rank) for rank in RANKS for size in GRID_SIZES]
GOOD, POOR = "good", "poor"


def step_for(size, rank):
    """The ladder step (1 to 12) of a grid of ``size`` beetles at ``rank``."""
    return LADDER.index((size, rank)) + 1


def start_step():
    return max(1, min(len(LADDER), int(game_setting("GAME_GRID_START_STEP", 1))))


def up_after():
    return max(1, int(game_setting("GAME_GRID_UP_AFTER", 2)))


def good_share():
    return float(game_setting("GAME_GRID_GOOD_SHARE", 0.75))


def top_step(open_rank):
    """The highest step whose rank the player has open: a grid never asks for a rank they can't name yet."""
    return max(i for i, (_, rank) in enumerate(LADDER, start=1) if RANKS.index(rank) <= RANKS.index(open_rank))


def current(player, game_key):
    """The player's step in one grid game (GAME_GRID_START_STEP until they have played it)."""
    from .models import GridStep

    found = GridStep.objects.filter(player=player, game=game_key).values_list("step", flat=True).first()
    return found or start_step()


def plan(player, game_key, open_rank, focus=None):
    """
    What the player's next grid should be: {"step", "size", "rank", "ranks"}. ``ranks`` are the ranks to build at, in
    turn while the beetles for one are short: the step's rank, then the nearest others (the shallower first). Only
    ranks the player has open and, with a focus (a chosen subfamily, tribe or genus), only ranks below it: every beetle
    shown is in it, so nothing at or above it can tell them apart.
    """
    step = current(player, game_key)
    size, rank = LADDER[min(step, top_step(open_rank)) - 1]
    deepest = RANKS.index(open_rank)
    above = RANKS.index(focus[0]) if focus and focus[0] in RANKS else -1
    allowed = [r for r in RANKS if above < RANKS.index(r) <= deepest] or list(RANKS[: deepest + 1])
    at = RANKS.index(rank)
    return {"step": step, "size": size, "rank": rank,
            "ranks": sorted(allowed, key=lambda r: (abs(RANKS.index(r) - at), RANKS.index(r)))}


def outcome(answer):
    """
    GOOD, POOR or None (neither) for one grid answer. Odd One Out: the odd one picked is good, a validated beetle of the
    rest poor; a pick on a beetle nobody has validated says nothing yet. Select all: good with no wrong tap and at least
    GAME_GRID_GOOD_SHARE of the validated members found; poor when it lost points (its wrong taps cost more than its
    right ones earned, #530), or none right. So a good grid always scores and a poor one never does.
    Skips, held answers, grids ended by flags and retries (a small grid at the rank of a mistake, #490) are neither.
    """
    from . import game
    from .game_scoring import grid_tiles, wrong_cost

    if (answer.mode not in GRID_GAMES or answer.skipped or answer.score_hold or answer.is_retry
            or answer.grid_rank not in RANKS):
        return None
    if answer.mode == "odd":
        right = getattr(answer, f"correct_{answer.grid_rank}")
        return None if right is None else GOOD if right else POOR
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
                    player_id=answer.player_id, game=answer.mode, defaults={"step": start_step()})
                if row.last_answer_id == answer.id:
                    return row.step
                if result == POOR:
                    row.step, row.good_run = max(1, row.step - 1), 0
                else:
                    row.good_run += 1
                    if row.good_run >= up_after():
                        row.good_run = 0
                        if row.step < top_step(rank_for(answer.player)["rank"]):
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

    steps, changed = {}, False
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
            rnd.items[i] = dict(fresh[0], mode=key)
            changed = True
    if changed:
        rnd.save(update_fields=["items"])
    return changed
