"""
The rounds under each History session (#574, round 5): one line per round in the same words and colours as the card
after an answer (game_answer_review.review), so a session reads like the review it leads to. The verdict dot is the
review's (.rv-verdict), the headline is its short line, and the points are its points.

It is built from the saved answers and their points only: no photos and no other players' votes, so a page of
sessions stays quick. The round review page still has the full cards. Similarity and grid rounds get the plain verdict
words ("Correct", "Not quite") rather than the rung and the count the card shows.
"""
from . import game, game_answer_review, game_feedback, game_levels
from .game_answer_review import BASIS, VERDICT_LEAD

ROUNDS_SHOWN = 10   # per session on the History page; the rest open from the session's review


def rows_by_round(answers):
    """{round id: [row, ...]} for the answers given (in the order given), one row per answer."""
    out = {}
    for answer in answers:
        out.setdefault(answer.round_id, []).append(_row(answer))
    return out


def _row(answer):
    row = game_answer_review._points_row(answer)
    skipped = answer.skipped or (answer.mode == "pair" and answer.pair_answer == "unsure")
    held = answer.score_hold and not skipped
    basis = "skip" if skipped else "held" if held else BASIS.get(row.basis if row else None, "pending")
    earned = game_answer_review._earned(row)
    amount = game_answer_review._signed(earned)
    if skipped:
        headline = "Reported · no points" if answer.skipped and answer.score_hold else f"Skipped · {amount}"
        verdict = None
    elif held:
        headline, verdict = "Not counted while its name is checked", None
    elif basis != "truth":
        headline = "Not checked yet" + (f" · {amount} so far" if earned > 0 else "")
        verdict = None
    else:
        verdict = game_feedback._verdict(answer)
        if answer.mode == "classify":
            deepest = _deepest_right(answer)
            lead = f"Correct to {deepest}" if deepest else "Not quite"
        else:
            lead = VERDICT_LEAD.get(verdict, "Checked")
        headline = f"{lead} · {amount}"
    return {"verdict": verdict or "none", "headline": headline, "game": game_levels.GAME_NAMES.get(answer.mode, "")}


def _deepest_right(answer):
    """The deepest rank a Naming answer got right before its first mistake (None for none)."""
    deepest = None
    for rank in game.RANKS:
        if getattr(answer, f"correct_{rank}") is not True:
            break
        deepest = rank
    return deepest
