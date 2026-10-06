"""
The review after an answer (#488): what the player said, what it earned, and what is known about the beetle.

The feed shows it as soon as an answer is saved (game_views.game_answer), and Back shows it again, also after a reload
(game_views.game_past_review). A validated beetle shows its true name and the points rank by rank, or tile by tile in
the grid games; a beetle nobody has validated yet shows what the other players and IBBI-AI say it is, and how sure
they are. Everything comes from the saved answer and its points (AnswerPoints), through the round review's helpers
(game_feedback), so the card always matches the score.

Every beetle on screen also gets its names at all four ranks (#541), shown on its photo: the true names of a validated
beetle, or, for one nobody has validated, the most likely name at each rank from the other players or IBBI-AI with how
sure they are. That gives every grid beetle away, so each one waits a while before it is scored for this player again
(game.held_back_ids), and then it no longer counts towards their accuracy or expertise (GameAnswer.seen_before).
The text under the photos stays short (#569): a headline and at most one line of what matters, such as the odd one or
the beetles missed; the names, rings and each tile's points are on the photos.

What it never shows: any name for a skipped answer, or for one held while its name is checked.
"""
import math
import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Q

from . import game, game_feedback, game_scoring
from .game import RANKS, game_setting
from .models import AnswerPoints, Beetles, GameAnswer, ModelPrediction
from .predictions import best_predictions, rank_tips
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
        shown = _shown(answer, item)
        opinions = Opinions(answer.player_id, [r.id for r in shown if r is not None and not game_scoring.is_truth(r)])
        body, facts = BODIES[answer.mode](answer, item, basis, row, losses, opinions)
        out.update(body)
        out["beetles"] = _beetles(answer, shown, opinions)
        facts.update(_agreed(answer, row, basis, facts))
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
def _classify(answer, item, basis, row, losses, opinions):
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
    said = opinions.said(answer.roi_id)
    votes = said["ranks"] if said else {}
    tips = opinions.tips(answer.roi_id)
    ranks = [{"rank": r, "yours": yours[r], "players": _players(votes.get(r), mine[r]), "ai": _bot(tips.get(r), mine[r])}
             for r in RANKS]
    return ({"classify": {"ranks": ranks, "truth": None, "players": said["players"] if said else 0}},
            {"ai_agrees": _agrees_with_ai(mine, tips)})


def _pair(answer, item, basis, row, losses, opinions):
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
    said = opinions.said(answer.roi_id)
    votes = {r: v for r, v in (said["ranks"] if said else {}).items() if v}
    tips = opinions.tips(answer.roi_id)
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


def _odd(answer, item, basis, row, losses, opinions):
    """
    Odd One Out: every beetle in the order shown, which ones were odd, the picks and what each earned, its name at the
    grid's rank, and for the beetles nobody has validated what the other players and IBBI-AI say about them, with a
    line naming the odd ones (_explain). Each tile carries its own state ("odd" for an odd one not picked, "right", "wrong", or "pick" on a beetle
    nobody has validated) and points, so a grid hiding several odd ones (#540) reads tile by tile like Select all; "odd"
    and "pick" are the first of "odds" and "picks".
    """
    tiles = game_scoring.grid_tiles(answer)
    rank, target = answer.grid_rank, (answer.grid_group or {}).get(answer.grid_rank, "")
    detail = (row.detail or {}) if row else {}
    picks = game.odd_picks(answer)
    right = detail.get("right") if basis == "truth" else None
    if answer.picks:   # every pick judged on its own beetle, each worth a share of the grid (game_scoring.odd_grid)
        grid = game.score_odd_grid(tiles, answer.picks, rank, answer.grid_group, answer.flagged)
        odds = [i for i, state in enumerate(grid["tiles"]) if state in ("right", "missed")]
        gain, cost = game_scoring.odd_tile_points(detail) if row else (None, None)
        votes = detail.get("votes") or {}
        states = {i: {"right": ("right", gain), "wrong": ("wrong", cost)}.get(
            grid["tiles"][i], ("pick", (votes.get(str(i)) or {}).get("points") if row else None)) for i in picks}
    else:   # one pick, from before (#540): the answer's points are all the pick's
        odds = [o for o in [_place(tiles, answer.roi_b_id)] if o is not None]
        states = {i: ("pick" if right is None else "right" if right else "wrong", _earned(row)) for i in picks}
    flagged = set(answer.flagged or [])
    views = _grid_views(tiles, opinions, rank, target)
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
        if i in odds:
            cell["state"] = "odd"
        if i in states:
            state, points = states[i]
            cell.update(state=state, points=None if points is None else round(points, 2))
        if i in views:   # picking a beetle says it isn't one of the group: that pays once it is confirmed it isn't
            cell.update(views[i], pays=None if views[i]["in"] is None else not views[i]["in"])
        cells.append(cell)
    found = sum(1 for i in picks if i in odds)
    wrong = sum(1 for state, _ in states.values() if state == "wrong")
    first_odd = next((o for o in [_place(tiles, answer.roi_b_id)] if o in odds), odds[0] if odds else None)
    data = {"rank": rank, "target": target, "odd": first_odd, "pick": picks[0] if picks else None, "tiles": cells,
            "odd_name": _name_at(tiles[first_odd], rank) if first_odd is not None else "",
            "odds": odds, "picks": picks, "odd_names": [_name_at(tiles[o], rank) for o in odds],
            "found": found, "wrong": wrong, "count": len(picks),
            **_explain("odd", tiles, cells, set(picks) - flagged, rank, target)}
    least = game_setting("GAME_FEEDBACK_AI_MIN", 0.5)
    ais = [(views.get(i) or {}).get("ai") for i in picks if states[i][0] == "pick"]
    return ({"verdict": None if right is None else "right" if right else "wrong", "grid": data},
            {"complete": bool(right),
             "ai_agrees": bool(ais) and all(ai and ai["in"] is False and ai["sure"] >= least * 100 for ai in ais)})


def _select(answer, item, basis, row, losses, opinions):
    """
    Select all: every beetle in the order shown with its state (right, wrong, missed, clear, or a vote on one nobody
    has validated) and what it earned, its name at the grid's rank, and what the other players and IBBI-AI say about
    the unvalidated ones, with a line naming the beetles missed (_explain). The tiles add up to the grid's points (game_scoring.select_tile_points).
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
    views = _grid_views(tiles, opinions, rank, target)
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
            **{k: detail.get(k, 0) if scored else 0 for k in ("right", "wrong", "missed", "members")},
            **_explain("select", tiles, cells, picked - flagged, rank, target)}
    least = game_setting("GAME_FEEDBACK_AI_MIN", 0.5)
    taps = [views[i].get("ai") for i in picked if i in views]
    return ({"verdict": game_feedback.grid_verdict(detail) if scored else None, "grid": data},
            {"complete": scored and bool(detail.get("perfect")),
             "ai_agrees": bool(taps) and all(ai and ai["in"] and ai["sure"] >= least * 100 for ai in taps)})


BODIES = {"classify": _classify, "pair": _pair, "odd": _odd, "select": _select}


# ---------------------------------------------------------------------------
# A grid, beetle by beetle (#541)
# ---------------------------------------------------------------------------
def _explain(mode, tiles, cells, chosen, rank, target):
    """
    Adds "belongs" (to the grid's group: True or False on a validated beetle, None while nobody knows) and "chosen" to
    each tile, and returns {"note"}: at most one short line of what is worth learning, as a list of parts (text, and
    {"name", "rank"} for a name): Odd One Out names the odd ones ("Odd one: 1 · Platypodinae"), Find Them All the
    beetles of the group left out ("Missed 4 · Ipini"). Everything else is on the photos: every name, the rings and
    each tile's points.
    """
    group = game._norm(target)
    odd_ones, missed = [], []
    for i, (tile, cell) in enumerate(zip(tiles, cells)):
        if tile is None or cell["state"] == "flagged":
            continue
        chose = i in chosen
        belongs = (game._norm(_name_at(tile, rank)) == group) if game_scoring.is_truth(tile) and group else None
        cell.update(belongs=belongs, chosen=chose)
        if belongs is False:
            odd_ones.append(i)
        elif belongs and not chose:   # one of the group, left out: missed in Find Them All
            missed.append(i)
    note = []
    if mode == "odd" and odd_ones:
        note = ["Odd ones: " if len(odd_ones) > 1 else "Odd one: "]   # several since #540
        for n, i in enumerate(odd_ones):
            note += [", " if n else "", f"{i + 1} · ", _part(_name_at(tiles[i], rank), rank)]
    elif mode == "select" and missed:
        note = ["Missed " + ", ".join(str(i + 1) for i in missed) + " · ", _part(target, rank)]
    return {"note": note}


def _part(name, rank):
    return {"name": name, "rank": rank}


# ---------------------------------------------------------------------------
# Every beetle's names on its photo (#541)
# ---------------------------------------------------------------------------
def _beetles(answer, shown, opinions):
    """
    For each photo in the order shown (None for one gone since or flagged): {"validated", "tier", "ranks"}, ranks
    being [{"rank", "name", "source", "sure", "votes", "expert"}] for all four ranks. A validated beetle gives its true
    names (source "truth"); one nobody has validated the most likely name at each rank, from the other players
    ("players", with how many named it, and "expert" when a proven Naming expert backs it, game_trust) or
    IBBI-AI ("ai"), whichever is surer (the players on a tie); "" where nobody says. The page marks each source with a
    coloured dot (#569).
    """
    flagged = set(answer.flagged or []) if answer.mode in ("odd", "select") else set()
    out = []
    for i, roi in enumerate(shown):
        if roi is None or i in flagged:
            out.append(None)
        elif game_scoring.is_truth(roi):
            label = game_feedback._label(roi.taxon)
            out.append({"validated": True, "tier": roi.get_label_source_display() or "Verified",
                        "ranks": [{"rank": r, "name": label[r], "source": "truth"} for r in RANKS]})
        else:
            said = (opinions.said(roi.id) or {}).get("ranks") or {}
            tips = opinions.tips(roi.id)
            out.append({"validated": False, "tier": None,
                        "ranks": [_likeliest(r, said.get(r), tips.get(r)) for r in RANKS]})
    return out


def _likeliest(rank, vote, tip):
    """The surer of the other players' name (a consensus rank) and IBBI-AI's best guess at one rank."""
    players = ((vote["support"], 1, {"name": vote["value"], "source": "players", "votes": vote["votes"],
                                     "expert": bool(vote.get("trusted"))})
               if vote else None)
    ai = (tip["confidence"], 0, {"name": tip["value"], "source": "ai"}) if tip else None
    best = max((c for c in (players, ai) if c), key=lambda c: c[:2], default=None)
    if best is None:
        return {"rank": rank, "name": "", "source": "", "sure": None}
    return dict(best[2], rank=rank, sure=round(best[0] * 100))


# ---------------------------------------------------------------------------
# What others say about a beetle nobody has validated
# ---------------------------------------------------------------------------
class Opinions:
    """
    What the other players (_said) and IBBI-AI (_ai) say about the beetles on screen nobody has validated, loaded for
    them all at once; a beetle asked about later is loaded then.
    """

    def __init__(self, player_id, roi_ids):
        self.player_id, self._said, self._tips, self._loaded = player_id, {}, {}, set()
        self.load(roi_ids)

    def load(self, roi_ids):
        ids = [i for i in dict.fromkeys(roi_ids) if i not in self._loaded]
        if ids:
            self._said.update(_said(ids, self.player_id))
            self._tips.update(_ai(ids))
            self._loaded.update(ids)

    def said(self, roi_id):
        self.load([roi_id])
        return self._said.get(roi_id)

    def tips(self, roi_id):
        self.load([roi_id])
        return self._tips.get(roi_id, {})


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
    """{roi_id: {rank: {"value", "confidence", ...}}}: IBBI-AI's best guess at each rank, from the prediction people
    see (the best model's, predictions.best_predictions)."""
    shown = best_predictions(ModelPrediction.objects.filter(roi_id__in=roi_ids))
    return {roi_id: rank_tips(prediction) for roi_id, prediction in shown.items()}


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


def _grid_views(tiles, opinions, rank, target):
    """
    {place: {"players", "ai", "in"}} for a grid's beetles nobody has validated: what the other players and IBBI-AI
    call each at the grid's rank, and whether that puts it in the group ("in" True or False; None when nobody sure
    says, or they disagree). IBBI-AI counts from GAME_FEEDBACK_AI_MIN, the players from half their weighted vote.
    """
    open_tiles = {i: t for i, t in enumerate(tiles) if t is not None and not game_scoring.is_truth(t)}
    if not open_tiles:
        return {}
    opinions.load([t.id for t in open_tiles.values()])
    group, least = game._norm(target), game_setting("GAME_FEEDBACK_AI_MIN", 0.5)
    out = {}
    for i, tile in open_tiles.items():
        vote = ((opinions.said(tile.id) or {}).get("ranks") or {}).get(rank)
        tip = opinions.tips(tile.id).get(rank)
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


def _shown(answer, item):
    """The beetles in the order shown: a pair's A and B, a grid in order (None for one gone since)."""
    if answer.mode in ("odd", "select"):
        return game_scoring.grid_tiles(answer)
    if answer.roi_b_id:
        return [answer.roi_b, answer.roi] if item.get("flip") else [answer.roi, answer.roi_b]
    return [answer.roi]


def _images(answer, item):
    """The photos as they were shown, for Back."""
    return [None if r is None else {"url": r.display_url, "box": [r.bbox_x, r.bbox_y, r.bbox_width, r.bbox_height]}
            for r in _shown(answer, item)]


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
    One short line for the top of the card (#569): "Correct to species · +45", "Correct · Same tribe · +3", "Not quite ·
    You said Same genus · It's Same tribe · −3", "Found 2 of 3 · 1 wrong · +1.7" (Select all, or Odd One Out with
    several odd ones), "Not checked yet · +3 so far", "Skipped · −0.3".
    """
    points = out["points"]
    amount = _signed(points["earned"])
    if out["skipped"]:
        return "Reported · no points" if out["reported"] else f"Skipped · {amount}"
    if out["held"]:
        return "Not counted while its name is checked"
    if points["basis"] != "truth":
        return "Not checked yet" + (f" · {amount} so far" if points["earned"] > 0 else "")
    if out["mode"] == "classify":
        deepest = None
        for cell in out["classify"]["ranks"]:   # correct down to the first mistake ("stopped" ranks are blank)
            if cell["state"] == "right":
                deepest = cell["rank"]
            elif cell["state"] in ("wrong", "after"):
                break
        lead = f"Correct to {deepest}" if deepest else "Not quite"
    elif out["mode"] == "pair" and out["pair"]["truth"]:   # said once (#569): the result and the true rung
        pair, lead = out["pair"], VERDICT_LEAD.get(out["verdict"], "Checked")
        if out["verdict"] == "right":
            return f"{lead} · {pair['truth']} · {amount}"
        return f"{lead} · You said {pair['said']} · It's {pair['truth']} · {amount}"
    elif out["mode"] == "select":
        grid = out["grid"]
        lead = f"Found {grid['right']} of {grid['members']}" + (f" · {grid['wrong']} wrong" if grid["wrong"] else "")
    elif out["mode"] == "odd" and out["grid"]["count"] > 1:   # several odd ones (#540)
        grid = out["grid"]
        lead = f"Found {grid['found']} of {grid['count']}" + (f" · {grid['wrong']} wrong" if grid["wrong"] else "")
    else:
        lead = VERDICT_LEAD.get(out["verdict"], "Checked")
    return f"{lead} · {amount}"


# The confetti's colours say who agreed (#572): green for correct on a validated beetle, blue for IBBI-AI, purple for
# the players, glowing purple for a Naming expert among them, grey for the rest (static/js/beetle_confetti.js).
POP_SIZE = 0.3   # at or under this size (very few points), a partly correct or unexplained win is just a small grey pop


def _celebrate(out, facts):
    """
    The confetti for this answer, which follows its points ({"kind", "size"}, or None). On a validated beetle:
    "validated" (green beetles) for a fully correct answer (the species, the true rung, the odd one, a perfect grid),
    "validated_agreed" when IBBI-AI and the other players had said the same, "partial" (grey) for points from a partly
    correct one. On a beetle nobody has validated, by who it agrees with: "expert" (a Naming expert's name), "ai_players",
    "ai" or "players"; with no points yet, agreeing with a sure IBBI-AI still gets the smallest burst. Very few points
    with nobody named are a "pop". Nothing for no points or a loss. A level-up has its own burst (its pop-up).
    """
    if out["skipped"] or out["held"]:
        return None
    earned, basis = out["points"]["earned"], out["points"]["basis"]
    ai, players = facts.get("ai_agrees"), facts.get("players_agree")
    if earned <= 0 and not (basis != "truth" and ai):
        return None
    size = confetti_size(earned) if earned > 0 else SMALLEST_BURST
    if basis == "truth":
        if facts.get("complete"):
            kind = "validated_agreed" if ai and players else "validated"
        else:
            kind = "pop" if size <= POP_SIZE else "partial"
    elif facts.get("expert_agrees"):
        kind = "expert"
    elif ai or players:
        kind = "ai_players" if ai and players else "ai" if ai else "players"
    else:   # points on a beetle nobody has validated come from the players' vote
        kind = "pop" if size <= POP_SIZE else "players"
    return {"kind": kind, "size": size}


def _agreed(answer, row, basis, facts):
    """
    Who said the same as the player, for the confetti: {"ai_agrees", "players_agree", "expert_agrees"}. On a beetle
    nobody has validated it is in the points (agreement with the players' vote, and a reference by proven experts or a
    trusted model, game_scoring); a fully correct name on a validated beetle is compared with IBBI-AI and with the
    other players' names for it.
    """
    if basis == "agreement" and row is not None:
        detail = row.detail or {}
        parts = [detail] + [v for v in (detail.get("votes") or {}).values() if isinstance(v, dict)]
        players = any(isinstance(c, (int, float)) and c > 0 for p in parts for c in (p.get("agreement") or {}).values())
        refs = [r for p in parts for r in (p.get("reference") or {}).values() if isinstance(r, dict) and r.get("match")]
        return {"players_agree": players, "expert_agrees": any(r.get("source") == "expert" for r in refs),
                "ai_agrees": bool(facts.get("ai_agrees")) or any(r.get("source") == "model" for r in refs)}
    if basis == "truth" and facts.get("complete") and answer.mode == "classify":
        mine = game.answer_values({r: getattr(answer, r) for r in RANKS})
        deepest = next((r for r in reversed(RANKS) if mine.get(r)), None)
        others = [game.answer_values(row)[deepest] for row in
                  GameAnswer.objects.filter(roi_id=answer.roi_id, mode="classify", skipped=False)
                  .exclude(player_id=answer.player_id).values(*RANKS)] if deepest else []
        others = [name for name in others if name]   # only those who named that rank
        same = sum(1 for name in others if name == mine[deepest])
        return {"ai_agrees": _agrees_with_ai(mine, _ai([answer.roi_id]).get(answer.roi_id, {})),
                "players_agree": bool(others) and same * 2 > len(others)}
    return {}
