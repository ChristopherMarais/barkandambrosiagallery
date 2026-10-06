"""
The review after an answer (#488): what the player said, what it earned, and what is known about the beetle.

The feed shows it as soon as an answer is saved (game_views.game_answer), and Back shows it again, also after a reload
(game_views.game_past_review). A validated beetle shows its true name and the points rank by rank, or tile by tile in
the grid games; a beetle nobody has validated yet shows what the other players and IBBI-AI say it is, and how sure
they are. Everything comes from the saved answer and its points (AnswerPoints), through the round review's helpers
(game_feedback), so the card always matches the score.

What it never shows: a name past the grid's rank in the grid games (game.revealed_ids keeps the deeper names scorable),
and any truth for a skipped answer.
"""
import math
import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Q

from . import game, game_feedback, game_scoring
from .game import RANKS, game_setting
from .models import AnswerPoints, Beetles, GameAnswer, ModelPrediction
from .predictions import rank_tips
from .templatetags.beetle_tags import digit_groups_text

# How the points were made, for the player: anything not listed has no points to show yet ("pending")
BASIS = {AnswerPoints.Basis.TRUTH: "truth", AnswerPoints.Basis.CONSENSUS: "agreement"}
RUNG = dict(GameAnswer.PairAnswer.choices)                                        # "genus" -> "Same genus"
DEPTH_RUNG = {depth: RUNG[key] for key, depth in game.PAIR_DEPTH.items()}         # 2 -> "Same genus"
TRUTH_DEPTH = {name: depth for depth, name in game_scoring.DEPTH_NAME.items()}   # "same genus" (in the points) -> 2
VERDICT_LEAD = {"right": "Correct", "partly": "Partly correct", "wrong": "Not quite"}
JUDGED = ("right", "wrong", "missed", "clear")   # the Select all tiles scored against a validated name
SMALLEST_BURST = 0.2


def review(answer, item):
    """
    The review card of one saved answer, ready for JSON. ``item`` is its round item (``round.items[answer.index]``),
    which says how a pair was shown; a grid answer may carry its tiles already (game_scoring.grid_tiles).
    """
    row = _points_row(answer)
    skipped = answer.skipped or (answer.mode == "pair" and answer.pair_answer == "unsure")
    held = answer.score_hold and not skipped   # its name is being checked: not counted for now
    basis = "skip" if skipped else "held" if held else BASIS.get(row.basis if row else None, "pending")
    losses = game_feedback.answer_losses(answer, row) if row is not None and basis == "truth" else None
    out = {
        "mode": answer.mode, "skipped": skipped, "reported": answer.skipped and answer.score_hold, "held": held,
        "again": answer.is_retry, "images": _images(answer, item), "verdict": None,
        "points": _points(row, basis, losses),
    }
    facts = {}
    if not (skipped or held):
        body, facts = BODIES[answer.mode](answer, item, basis, row, losses)
        out.update(body)
    out["headline"] = _headline(out)
    out["celebrate"] = _celebrate(out, facts)
    return out


def past(rnd, index):
    """The review of the answer at ``index`` in the round ``rnd`` (Back, after a reload), or None if there is none."""
    answer = (rnd.answers.select_related("roi__taxon", "roi__image_asset", "roi_b__taxon", "roi_b__image_asset", "points")
              .filter(index=index).first())
    if answer is None:
        return None
    if answer.tiles:   # the grid's beetles at once, with their photos
        found = Beetles.objects.select_related("taxon", "image_asset").in_bulk([uuid.UUID(str(t)) for t in answer.tiles])
        answer._grid_tiles = [found.get(uuid.UUID(str(t))) for t in answer.tiles]
    return review(answer, rnd.items[index] if index < len(rnd.items) else {})


def confetti_size(points):
    """
    How big the confetti is for an answer's points, 0.2 to 1: on a log scale, so a small win still shows and the
    biggest are worth aiming for, full size from GAME_CONFETTI_FULL_POINTS (by default what a fully correct species
    identification earns). The points already grow with how hard the task was.
    """
    full = game_setting("GAME_CONFETTI_FULL_POINTS", None) or (
        sum(game_scoring.RANK_POINTS.values()) * game_scoring.classify_weight())
    if points <= 0 or full <= 0:
        return SMALLEST_BURST
    return round(min(1.0, max(SMALLEST_BURST, math.log1p(points) / math.log1p(full))), 2)


# ---------------------------------------------------------------------------
# Each game
# ---------------------------------------------------------------------------
def _classify(answer, item, basis, row, losses):
    """Rank by rank: the player's names next to the true name and what each rank earned, or next to what the other
    players (and how many proven experts among them) and IBBI-AI say."""
    yours = game_feedback._answer_label(answer)
    if basis == "truth" and losses:
        truth = game_feedback._label(answer.roi.taxon) or {}
        ranks = [{"rank": r, "yours": yours[r], "truth": truth.get(r, ""), **losses["ranks"][r]} for r in RANKS]
        verdict = game_feedback._verdict(answer)
        complete = verdict == "right" and all(c["state"] != "stopped" for c in ranks)
        return {"verdict": verdict, "classify": {"ranks": ranks, "truth": _truth(answer.roi)}}, {"complete": complete}
    mine = game.answer_values({r: getattr(answer, r) for r in RANKS})
    said = _said([answer.roi_id], answer.player_id).get(answer.roi_id)
    votes = said["ranks"] if said else {}
    tips = _ai([answer.roi_id]).get(answer.roi_id, {})
    ranks = [{"rank": r, "yours": yours[r], "players": _players(votes.get(r), mine[r]), "ai": _bot(tips.get(r), mine[r])}
             for r in RANKS]
    return ({"classify": {"ranks": ranks, "truth": None, "players": said["players"] if said else 0}},
            {"ai_agrees": _agrees_with_ai(mine, tips)})


def _pair(answer, item, basis, row, losses):
    """
    The rung the player chose, and either the true relation with both names, or the validated partner's name and
    what the other players and IBBI-AI say the other beetle is, with the rung that puts them on.
    """
    shown = [answer.roi_b, answer.roi] if item.get("flip") else [answer.roi, answer.roi_b]
    mine = game.PAIR_DEPTH.get(answer.pair_answer)
    data = {"said": RUNG.get(answer.pair_answer, ""), "truth": None, "state": None, "sides": []}
    if basis == "truth" and losses:
        data.update(truth=DEPTH_RUNG.get(TRUTH_DEPTH.get(losses["truth"])), state=losses["state"],
                    sides=[dict(_truth(roi), letter=letter, validated=True) for letter, roi in zip("AB", shown)])
        verdict = game_feedback._verdict(answer)
        return {"verdict": verdict, "pair": data}, {"complete": verdict == "right"}
    # On a pair nobody has fully validated, ``roi`` is the open one and ``roi_b`` its validated partner
    partner = answer.roi_b if game_scoring.is_truth(answer.roi_b) else None
    said = _said([answer.roi_id], answer.player_id).get(answer.roi_id)
    votes = {r: v for r, v in (said["ranks"] if said else {}).items() if v}
    tips = _ai([answer.roi_id]).get(answer.roi_id, {})
    least = game_setting("GAME_FEEDBACK_AI_MIN", 0.5)
    sure = {r: t for r, t in tips.items() if t["confidence"] >= least}   # only where IBBI-AI is sure sets its rung
    ai_relation = _relation({r: t["value"] for r, t in sure.items()}, partner)
    for letter, roi in zip("AB", shown):
        if roi is None:
            continue
        if roi.id == answer.roi_id:
            side = {"letter": letter, "validated": False,
                    "players": _pair_view(votes, "support", _relation({r: v["value"] for r, v in votes.items()}, partner),
                                          mine),
                    "ai": _pair_view(sure or tips, "confidence", ai_relation, mine)}
        else:
            side = dict(_truth(roi), letter=letter, validated=True) if partner else {"letter": letter, "validated": False}
        data["sides"].append(side)
    return {"pair": data}, {"ai_agrees": ai_relation is not None and ai_relation == (mine, True)}


def _odd(answer, item, basis, row, losses):
    """
    Odd One Out: every beetle in the order shown, which one was odd, the pick and what it earned; names only to the
    grid's rank, and for the beetles nobody has validated what the other players and IBBI-AI say about them.
    """
    tiles = game_scoring.grid_tiles(answer)
    rank, target = answer.grid_rank, (answer.grid_group or {}).get(answer.grid_rank, "")
    pick, odd = _place(tiles, answer.roi_id), _place(tiles, answer.roi_b_id)
    right = (row.detail or {}).get("right") if basis == "truth" else None
    flagged = set(answer.flagged or [])
    views = _grid_views(tiles, answer.player_id, rank, target)
    cells = []
    for i, tile in enumerate(tiles):
        if tile is None:   # gone since
            cells.append(None)
            continue
        if i in flagged:   # flagged while answering: out of the grid, nothing to say about it
            cells.append({"validated": False, "name": "", "state": "flagged", "points": None})
            continue
        validated = game_scoring.is_truth(tile)
        cell = {"validated": validated, "name": _name_at(tile, rank) if validated else "", "state": "", "points": None}
        if i == odd:
            cell["state"] = "odd"
        if i == pick:   # the answer's points are all the pick's
            cell.update(state="pick" if right is None else "right" if right else "wrong", points=_earned(row))
        if i in views:   # picking a beetle says it isn't one of the group: that pays once it is confirmed it isn't
            cell.update(views[i], pays=None if views[i]["in"] is None else not views[i]["in"])
        cells.append(cell)
    data = {"rank": rank, "target": target, "odd": odd, "pick": pick, "tiles": cells,
            "odd_name": _name_at(tiles[odd], rank) if odd is not None else ""}
    least = game_setting("GAME_FEEDBACK_AI_MIN", 0.5)
    ai = (views.get(pick) or {}).get("ai")
    return ({"verdict": None if right is None else "right" if right else "wrong", "grid": data},
            {"complete": bool(right), "ai_agrees": bool(ai and ai["in"] is False and ai["sure"] >= least * 100)})


def _select(answer, item, basis, row, losses):
    """
    Select all: every beetle in the order shown with its state (right, wrong, missed, clear, or a vote on one nobody
    has validated) and what it earned, names only to the grid's rank, and what the other players and IBBI-AI say about
    the unvalidated ones. The tiles add up to the grid's points (game_scoring.select_tile_points).
    """
    tiles = game_scoring.grid_tiles(answer)
    rank, target = answer.grid_rank, (answer.grid_group or {}).get(answer.grid_rank, "")
    detail = (row.detail or {}) if row else {}
    picked = set(answer.picks or [])
    scored = basis == "truth" and bool(losses) and bool(detail.get("tiles"))
    flagged = set(answer.flagged or [])
    states = detail["tiles"] if scored else ["flagged" if i in flagged else "vote" if i in picked else ""
                                             for i in range(len(tiles))]
    gain, cost = game_scoring.select_tile_points(detail) if scored else (0.0, 0.0)
    views = _grid_views(tiles, answer.player_id, rank, target)
    cells = []
    for i, tile in enumerate(tiles):
        if tile is None:
            cells.append(None)
            continue
        state = states[i] if i < len(states) else ""
        if state == "flagged" or i in flagged:   # flagged while answering: out of the grid
            cells.append({"validated": False, "name": "", "state": "flagged", "points": None})
            continue
        judged = state in JUDGED
        points = gain if state == "right" else cost if state == "wrong" else 0.0 if judged else None
        cell = {"validated": judged, "name": _name_at(tile, rank) if judged else "", "state": state,
                "points": None if points is None else round(points, 2)}
        if i in views:   # tapping a beetle says it is one of the group: that pays once it is confirmed it is
            cell.update(views[i], pays=views[i]["in"])
        cells.append(cell)
    data = {"rank": rank, "target": target, "tiles": cells,
            **{k: detail.get(k, 0) if scored else 0 for k in ("right", "wrong", "missed", "members")}}
    least = game_setting("GAME_FEEDBACK_AI_MIN", 0.5)
    taps = [views[i].get("ai") for i in picked if i in views]
    return ({"verdict": game_feedback.grid_verdict(detail) if scored else None, "grid": data},
            {"complete": scored and bool(detail.get("perfect")),
             "ai_agrees": bool(taps) and all(ai and ai["in"] and ai["sure"] >= least * 100 for ai in taps)})


BODIES = {"classify": _classify, "pair": _pair, "odd": _odd, "select": _select}


# ---------------------------------------------------------------------------
# What others say about a beetle nobody has validated
# ---------------------------------------------------------------------------
def _said(roi_ids, player_id):
    """
    {roi_id: consensus entry}: what the other players say these beetles are (game.consensus: names, Similarity answers
    and Select all taps, weighted by how reliable each player is), without this player's own answers.
    """
    others = set(
        GameAnswer.objects.filter(Q(roi_id__in=roi_ids) | game.showing(roi_ids), skipped=False)
        .exclude(player_id=player_id).values_list("player_id", flat=True).distinct()
    )
    if not others:
        return {}
    return {entry["roi"].id: entry for entry in game.consensus(roi_ids=roi_ids, voters=others)}


def _ai(roi_ids):
    """{roi_id: {rank: {"value", "confidence", ...}}}: IBBI-AI's best guess at each rank, from the newest prediction."""
    newest = {}
    for prediction in ModelPrediction.objects.filter(roi_id__in=roi_ids).order_by("created_at"):
        newest[prediction.roi_id] = prediction
    return {roi_id: rank_tips(prediction) for roi_id, prediction in newest.items()}


def _view(name, share, against, key):
    """
    One opinion about a beetle: the name, how sure as a whole percentage, and under ``key`` whether it is ``against``
    (the player's own name, normalised, or a grid's group), None when there is nothing to compare it with.
    """
    return {"name": name, "sure": round(share * 100), key: (game._norm(name) == against) if against else None}


def _players(vote, against, key="agrees"):
    """The other players' name at one rank (a consensus rank), with their votes and the proven experts backing it."""
    if not vote:
        return None
    return dict(_view(vote["value"], vote["support"], against, key), votes=vote["votes"],
                experts=vote.get("trusted_votes", 0) if vote.get("trusted") else 0)


def _bot(tip, against, key="agrees"):
    """IBBI-AI's best guess at one rank (predictions.rank_tips), with its confidence."""
    return _view(tip["value"], tip["confidence"], against, key) if tip else None


def _agrees_with_ai(mine, tips):
    """True when IBBI-AI is sure (GAME_FEEDBACK_AI_MIN) of a rank the player named, and says the same at each one."""
    least = game_setting("GAME_FEEDBACK_AI_MIN", 0.5)
    sure = [r for r in RANKS if mine.get(r) and r in tips and tips[r]["confidence"] >= least]
    return bool(sure) and all(game._norm(tips[r]["value"]) == mine[r] for r in sure)


def _grid_views(tiles, player_id, rank, target):
    """
    {place: {"players", "ai", "in"}} for a grid's beetles nobody has validated: what the other players and IBBI-AI
    call each at the grid's rank, and whether that puts it in the group ("in" True or False; None when nobody sure
    says, or they disagree). IBBI-AI counts from GAME_FEEDBACK_AI_MIN, the players from half their weighted vote.
    """
    open_tiles = {i: t for i, t in enumerate(tiles) if t is not None and not game_scoring.is_truth(t)}
    if not open_tiles:
        return {}
    ids = [t.id for t in open_tiles.values()]
    said, tips = _said(ids, player_id), _ai(ids)
    group, least = game._norm(target), game_setting("GAME_FEEDBACK_AI_MIN", 0.5)
    out = {}
    for i, tile in open_tiles.items():
        vote = ((said.get(tile.id) or {}).get("ranks") or {}).get(rank)
        tip = tips.get(tile.id, {}).get(rank)
        players, ai = _players(vote, group, key="in"), _bot(tip, group, key="in")
        calls = {view["in"] for view, sure in ((players, vote and vote["support"] >= 0.5),
                                               (ai, tip and tip["confidence"] >= least)) if view and sure}
        out[i] = {"players": players, "ai": ai, "in": calls.pop() if len(calls) == 1 else None}
    return out


def _relation(names, partner):
    """
    The Similarity rung that names for one beetle put it on next to its validated partner: (depth, exact), depth -1
    (different subfamilies) to 3 (same species); exact is False when the names stop while the two still match ("same
    genus or closer"). None when there is nothing to go on.
    """
    if partner is None or not names:
        return None
    shared = game.shared_ranks(game.group_taxon(names), partner.taxon)
    depth = -1
    for i, r in enumerate(RANKS):
        if shared[r] is None:
            return (depth, False) if depth >= 0 else None
        if not shared[r]:
            return depth, True
        depth = i
    return depth, True


def _pair_view(opinions, share, relation, mine):
    """
    What one source (the players, or IBBI-AI) says the open beetle of a pair is: its deepest name and how sure, the rung
    that puts it on next to the partner (``relation``, from _relation), and whether that is the player's rung.
    ``opinions`` is {rank: {"value", share, ...}}.
    """
    if not opinions:
        return None
    deepest = next(r for r in reversed(RANKS) if r in opinions)
    said = opinions[deepest]
    view = {"name": said["value"], "rank": deepest, "sure": round(said[share] * 100)}
    if "votes" in said:   # the players
        view.update(votes=said["votes"], experts=said.get("trusted_votes", 0) if said.get("trusted") else 0)
    if relation is not None:
        depth, exact = relation
        view["rung"] = DEPTH_RUNG[depth] + ("" if exact else " or closer")
        view["agrees"] = None if mine is None else mine == depth if exact else False if mine < depth else None
    return view


# ---------------------------------------------------------------------------
# Small parts
# ---------------------------------------------------------------------------
def _points_row(answer):
    try:
        return answer.points
    except AnswerPoints.DoesNotExist:
        return None


def _earned(row):
    """An answer's points without the small one for taking part, which every real answer gets."""
    if row is None:
        return 0.0
    return _dp(row.points - (row.detail or {}).get("participation", 0.0))


def _points(row, basis, losses):
    """
    What the answer earned (without the point for taking part, given apart), how ("truth", "agreement", "pending",
    "skip" or "held"), and on a validated beetle what it missed.
    """
    return {"earned": _earned(row), "basis": basis, "lost": losses["lost"] if losses else None,
            "participation": _dp((row.detail or {}).get("participation", 0.0)) if row else 0.0}


def _images(answer, item):
    """The photos as they were shown (a pair's A and B, a grid in order; None for one gone since), for Back."""
    if answer.mode in ("odd", "select"):
        rois = game_scoring.grid_tiles(answer)
    elif answer.roi_b_id:
        rois = [answer.roi_b, answer.roi] if item.get("flip") else [answer.roi, answer.roi_b]
    else:
        rois = [answer.roi]
    return [None if r is None else {"url": r.display_url, "box": [r.bbox_x, r.bbox_y, r.bbox_width, r.bbox_height]}
            for r in rois]


def _truth(roi):
    """A validated beetle's name, its rank (genus and species are written in italics) and how reliable the name is."""
    t = roi.taxon
    species = bool(t and t.genus and t.species)
    return {"name": f"{t.genus} {t.species}" if species else (t.genus if t else ""),
            "rank": "species" if species else "genus", "tier": roi.get_label_source_display() or "Verified"}


def _name_at(roi, rank):
    """A grid beetle's name at the grid's rank, never deeper (game_feedback._rank_label)."""
    return (game_feedback._rank_label(roi.taxon, rank) or {}).get(rank, "")


def _place(tiles, roi_id):
    return next((i for i, t in enumerate(tiles) if t is not None and t.id == roi_id), None)


def _dp(value):
    """One decimal place, halves rounded up (0.25 -> 0.3), as the game's pages show points."""
    return float(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _signed(value):
    """Points as players read them: +45, +12.6, −8.4 (a real minus sign), 0; digits grouped in threes."""
    value = _dp(value)
    text = digit_groups_text(abs(value), 0 if value == int(value) else 1)
    return ("+" if value > 0 else "−" if value < 0 else "") + text


def _headline(out):
    """
    One line for the top of the card: "Correct to species · +45 points", "Found 2 of 3 · 1 wrong · +1.7 points",
    "Not checked yet · +3 points so far", "Skipped · −0.3 points".
    """
    points = out["points"]
    amount = f"{_signed(points['earned'])} points"
    if out["skipped"]:
        return "Reported · no points" if out["reported"] else f"Skipped · {amount}"
    if out["held"]:
        return "Not counted while its name is checked"
    if points["basis"] != "truth":
        return "Not checked yet · " + (f"{amount} so far" if points["earned"] > 0 else "no points yet")
    if out["mode"] == "classify":
        deepest = None
        for cell in out["classify"]["ranks"]:   # correct down to the first mistake ("stopped" ranks are blank)
            if cell["state"] == "right":
                deepest = cell["rank"]
            elif cell["state"] in ("wrong", "after"):
                break
        lead = f"Correct to {deepest}" if deepest else "Not quite"
    elif out["mode"] == "select":
        grid = out["grid"]
        lead = f"Found {grid['right']} of {grid['members']}" + (f" · {grid['wrong']} wrong" if grid["wrong"] else "")
    else:
        lead = VERDICT_LEAD.get(out["verdict"], "Checked")
    return f"{lead} · {amount}"


def _celebrate(out, facts):
    """
    The confetti for this answer, which follows its points ({"kind", "size"}, or None): "validated" (brown beetles) for
    a fully correct answer on a validated beetle (the species, the true rung, the odd one, a perfect grid), "partial"
    (grey beetles) for points from a partly correct one, "strong" (paper confetti) for points by agreement on a beetle
    nobody has validated yet, or, with no points yet, for agreeing with IBBI-AI where it is sure (the smallest burst).
    Nothing for no points or a loss. A level-up has its own burst (its toast).
    """
    if out["skipped"] or out["held"]:
        return None
    earned, basis = out["points"]["earned"], out["points"]["basis"]
    if earned > 0:
        kind = ("validated" if facts.get("complete") else "partial") if basis == "truth" else "strong"
        return {"kind": kind, "size": confetti_size(earned)}
    if basis != "truth" and facts.get("ai_agrees"):
        return {"kind": "strong", "size": SMALLEST_BURST}
    return None
