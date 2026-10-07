"""
Points for the Beetle ID game.

Every answer is worth some points (AnswerPoints) and a player's score is the running total, never below zero
(PlayerScore). The rules, all adjustable with GAME_POINTS_* settings:

One rule runs through every game (#530): a claim pays on average only when the player is at least
GAME_POINTS_CONFIDENCE (t, 70%) sure of it. Every claim earns its points when right and costs k = t / (1 − t) (2⅓)
times those points when wrong, so at t claiming and not claiming are worth the same and below it stopping earns more.
A name left blank, a Similarity rung more cautious than the truth and a member left out of a Find Them All grid are
never wrong; anything claimed that isn't true is wrong, however much else was right.

Identification is the harder and more useful game, so every Name That Beetle answer counts
GAME_POINTS_CLASSIFY_WEIGHT (3) times the points below, gains and losses alike: a perfect identification
earns 45, a perfect similarity answer at most 15.

Beetles we know the answer to (validated) are scored against the truth. These earn the most.
  Name That Beetle   each rank named earns its points when right (subfamily 1, tribe 2, genus 4, species 8) and
                     costs k times them when wrong, also when it is wrong because a rank above it was: naming the
                     species is a claim about every rank. Right genus but wrong species = 7 − 18.7 = −11.7, against
                     7 for stopping at the genus.
  Family Ties        the right answer earns more the finer the line you had to draw: different subfamilies 1,
                     same subfamily 2, same tribe 4, same genus 7, same species 12 (P), plus up to a quarter more when
                     the two photos are alike (same photographer, place, magnification...). A cautious answer that is
                     true as far as it goes ("same tribe" for two beetles of one genus) earns that rung's points.
                     Claiming too close a tie is wrong: the true rung's points less k times the points of every rung
                     claimed beyond it, P[true] − k × (P[said] − P[true]), and nothing for alike photos. Calling
                     related beetles "different subfamilies" costs k × P[different] per step it is off.
  Odd One Out        picking the beetle that doesn't belong earns GAME_POINTS_ODD_WEIGHT (1.5) times what Family Ties
                     pays for telling apart the odd one and the rest (another subfamily 1, another tribe of the same
                     subfamily 2, another genus of the same tribe 4, another species of the same genus 7). Picking one
                     of the rest costs k times that, and Skip earns a little instead of costing (GAME_POINTS_ODD_SKIP,
                     0.25). A pick on a beetle nobody has validated yet is scored like a name on it: by agreement that
                     it doesn't belong. A grid hiding several odd ones (#540) is worth what one with one odd one is
                     (the average for its odd ones), split over them: each pick is a claim that earns its share when
                     right and costs k shares when wrong, so picking blindly loses at every step of the ladder.
  Select all         every validated member tapped earns a share of GAME_POINTS_SELECT_WEIGHT (1.25) times the Family
                     Ties points for the grid's rank (another subfamily 1 ... another species of one genus 7); every
                     validated non-member tapped costs k shares, and a member left out costs nothing. A grid never
                     loses more than a perfect one earns. Taps on beetles nobody has validated are recorded, never
                     scored. Skip earns GAME_POINTS_ODD_SKIP, as in Odd One Out.
                     In the reliability rating a grid counts once, at its rank: correct only when perfect (#381).
  Grid size          both grid games grow from 4 to 9, 16 and 25 beetles as the player gets better (game_grid_ladder), and
                     every point of a grid built on that ladder, gained or lost, is times GAME_GRID_SIZE_FACTOR for its
                     size (1, 1.5, 2, 2.5). Grids from before the ladder (no grid_step) keep ×1, so a re-score doesn't
                     inflate them. A photo the player flagged as bad before answering counts for nothing (#489).
  Seen again        a beetle shown again so you can learn it (a retry) earns GAME_POINTS_RETRY_FACTOR (half), in every
                     game (#490).

Beetles nobody has validated yet are scored by agreement, never more than GAME_POINTS_CONSENSUS_CAP (60%)
of what the same answer would earn on a validated beetle, and never less than zero:
  * only players whose rating is at or above the median of rated players are counted as judges;
  * a judge counts fully when they are a proven expert for that part of the tree, otherwise by how their rating
    compares with yours, like an Elo expectation: agreeing with stronger players earns almost full credit,
    agreeing with weaker ones very little;
  * a stronger player who disagrees cancels out agreement from weaker ones, so siding with many weak players
    against one strong one earns nothing;
  * the more judges agree, the closer it gets to the cap;
  * a rank the judges (or the experts and trusted model) disagree with costs k times what agreeing would have
    earned, taken off the ranks they agree with: a deep guess on an unvalidated beetle doesn't pay either.

Not sure / skip costs a little (GAME_POINTS_UNSURE, 0.25). Every real answer earns a small participation point
(GAME_POINTS_PARTICIPATION, 0.5), so the score grows with play.

Harder beetles are worth more (#492). An Identification or Similarity answer keeps the beetle's difficulty percentile p
from when it was given (game_difficulty; the anchor of a pair), and its points, gain or loss, skip included, are
scaled by m = 1 + GAME_POINTS_DIFFICULTY_SPREAD × (2p − 1): a gain × m, a loss × (2 − m). So a hard beetle pays more
and costs less when missed, an easy one the reverse. The participation point is not scaled; older answers keep ×1.

When a beetle is validated later, or its label is corrected, every answer on it is re-scored against the truth,
up or down (recompute, run for a player when they leave the game and for everyone every night).
"""
import math
import uuid
from collections import defaultdict
from collections.abc import Mapping
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.models import F, Q, Value
from django.db.models.functions import Greatest
from django.utils import timezone

from . import game, game_reference
from .game import PAIR_DEPTH, RANKS, game_setting
from .models import AnswerPoints, Beetles, GameAnswer, PlayerScore, RetroCredit

class _Points(Mapping):
    """A points table read from its game setting each time (a superuser can tune it on the Scoring page)."""

    def __init__(self, name, default, convert=lambda k: k):
        self.name, self.default, self.convert = name, default, convert

    def _table(self):
        stored = game_setting(self.name, None) or {}
        return {k: float(stored.get(str(k), v)) for k, v in self.default.items()}

    def __getitem__(self, key):
        return self._table()[key]

    def __iter__(self):
        return iter(self.default)

    def __len__(self):
        return len(self.default)


RANK_POINTS = _Points("GAME_POINTS_RANK", {"subfamily": 1.0, "tribe": 2.0, "genus": 4.0, "species": 8.0})
# Family Ties: points for the right answer, by how related the two beetles really are (-1 = different subfamilies)
PAIR_POINTS = _Points("GAME_PAIR_POINTS", {-1: 1.0, 0: 2.0, 1: 4.0, 2: 7.0, 3: 12.0})
DEPTH_NAME = {-1: "different subfamilies", 0: "same subfamily", 1: "same tribe", 2: "same genus", 3: "same species"}
# The grid games: every point of a grid, gained or lost, times this for its number of beetles (#489)
GRID_SIZE_FACTOR = _Points("GAME_GRID_SIZE_FACTOR", {4: 1.0, 9: 1.5, 16: 2.0, 25: 2.5})


def setting(name, default):
    return game_setting(name, default)


def classify_weight():
    """How many times more a Name That Beetle answer counts than the base points (GAME_POINTS_CLASSIFY_WEIGHT)."""
    return float(setting("GAME_POINTS_CLASSIFY_WEIGHT", 3.0))


def confidence():
    """How sure a player should be of a claim before it pays on average (GAME_POINTS_CONFIDENCE, #530)."""
    return min(0.95, max(0.05, float(setting("GAME_POINTS_CONFIDENCE", 0.7))))


def wrong_cost():
    """
    k = t / (1 − t): what a wrong claim costs for every point it earns when right. Then a claim right with chance p is
    worth p − (1 − p) × k on average, which is above zero only when p is above t: below it, stopping earns more.
    """
    t = confidence()
    return t / (1 - t)


def classify_points(results):
    """
    {rank: points} for a Name That Beetle answer from score_classification's {rank: True/False/None}, before the
    Identification weight: a right rank earns its points, a wrong one costs k times them (#530).
    """
    k = wrong_cost()
    return {r: RANK_POINTS[r] if ok else -k * RANK_POINTS[r] for r, ok in results.items() if ok is not None}


def pair_points(given, truth, bonus=1.0):
    """
    A Similarity answer's points and how it went ("right", "cautious", "too_close", "wrong"), from the rung said and
    the true one (-1 different subfamilies ... 3 same species). ``bonus`` (alike photos) raises only what is earned.
    """
    if given == truth:
        return PAIR_POINTS[truth] * bonus, "right"
    k = wrong_cost()
    if given < 0:   # "different subfamilies" for related beetles: k × that rung's points per step off
        return -k * PAIR_POINTS[-1] * (truth + 1), "wrong"
    if truth < 0:   # related, said of beetles that aren't: every rung claimed is wrong
        return -k * PAIR_POINTS[given], "wrong"
    if given < truth:   # cautious but true as far as it goes: that rung's points
        return PAIR_POINTS[given] * bonus, "cautious"
    # too close a tie: the true rung's points, less k times those of every rung claimed beyond it
    return PAIR_POINTS[truth] - k * (PAIR_POINTS[given] - PAIR_POINTS[truth]), "too_close"


# ---------------------------------------------------------------------------
# The truth
# ---------------------------------------------------------------------------
def is_truth(roi):
    """A beetle whose label can be scored against: validated, a complete taxon, not deleted."""
    t = getattr(roi, "taxon", None) if roi is not None else None
    return bool(roi is not None and roi.bbox_is_validated and not roi.is_deleted and t is not None and t.subfamily and t.genus)


def true_depth(taxon_a, taxon_b):
    """How related two taxa are: 3 same species ... -1 different subfamilies. None if it can't be told."""
    shared = game.shared_ranks(taxon_a, taxon_b)
    depth = -1
    for i, r in enumerate(RANKS):
        if shared[r] is True:
            depth = i
        elif shared[r] is False:
            return depth
        else:
            return None   # e.g. one of them is only known to genus: "same genus" or "same species" can't be told
    return depth


SIMILARITY_FIELDS = (
    ("image_asset", "photographer"), ("image_asset", "image_institution"), ("image_asset", "resolution_in_ppmm"),
    (None, "collection_country"), (None, "aspect"),
)


def photo_similarity(roi_a, roi_b):
    """How alike two photos are from what we know about them, 0 to 1 (0 when there is too little to compare)."""
    def value(roi, owner, field):
        holder = getattr(roi, owner, None) if owner else roi
        return getattr(holder, field, None) if holder is not None else None

    same = compared = 0
    for owner, field in SIMILARITY_FIELDS:
        a, b = value(roi_a, owner, field), value(roi_b, owner, field)
        if a in (None, "") or b in (None, ""):
            continue
        compared += 1
        if field == "resolution_in_ppmm":
            same += abs(float(a) - float(b)) <= 0.1 * max(float(a), float(b))
        else:
            same += str(a).strip().lower() == str(b).strip().lower()
    return round(same / compared, 3) if compared >= 2 else 0.0


def classify_truth(answer, taxon):
    """
    (points, detail) for a Name That Beetle answer on a validated beetle: every rank named earns its points when
    right and costs k times them when wrong (classify_points), a rank below a wrong one too: it claimed that as well.
    """
    given = {r: getattr(answer, r) for r in RANKS}
    results = game.score_classification(given, taxon)
    per_rank = classify_points(results)
    ranks = {r: {"right": results[r], "points": round(p, 2)} for r, p in per_rank.items()}
    return sum(per_rank.values()), {"ranks": ranks}


def pair_truth(answer, roi_a, roi_b):
    """
    (points, detail) for a Family Ties answer when both beetles are validated, or None if it can't be told
    (pair_points). ``right`` in the detail is True, "partial" for a cautious answer (true as far as it goes), or False
    for anything claiming more than the truth, closer or further: never partly right (#530).
    """
    truth = true_depth(roi_a.taxon, roi_b.taxon)
    given = PAIR_DEPTH.get(answer.pair_answer)
    if truth is None or given is None:
        return None
    sim = photo_similarity(roi_a, roi_b)
    bonus = 1 + setting("GAME_POINTS_SIMILARITY_BONUS", 0.25) * sim
    points, state = pair_points(given, truth, bonus)
    detail = {"right": {"right": True, "cautious": "partial"}.get(state, False), "truth": DEPTH_NAME[truth],
              "similarity": sim}
    if state != "right":
        detail["steps"] = abs(given - truth)
    return points, detail


def size_factor(tiles):
    """
    What a grid of ``tiles`` beetles is worth against one of four (GAME_GRID_SIZE_FACTOR): more beetles take longer and
    are harder. A grid of another size (six, from before the grids grew) counts as the biggest size it reaches.
    """
    reached = [s for s in GRID_SIZE_FACTOR if s <= tiles] or [min(GRID_SIZE_FACTOR)]
    return GRID_SIZE_FACTOR[max(reached)]


def grid_size_factor(answer):
    """
    size_factor for a grid answer built on the ladder (it recorded its step), 1 for one from before: those were scored
    without it, so a re-score must not inflate them (#530).
    """
    return 1.0 if answer.grid_step is None else size_factor(len(answer.tiles or []))


def _grid_detail(answer):
    """What the review shows of a grid besides its points: how many beetles it had, and the player's step then."""
    return {"size": len(answer.tiles or []), "step": answer.grid_step}


def odd_base(answer):
    """
    What a right Odd One Out pick is worth: GAME_POINTS_ODD_WEIGHT times the Family Ties points for how related the
    round's odd one (``roi_b``) is to the rest, times the grid's size factor. The closer they are, and the more beetles
    to choose from, the harder it was to tell.
    """
    return _odd_points(answer, answer.roi_b.taxon if answer.roi_b_id and answer.roi_b else None)


def _odd_points(answer, odd):
    """What telling the odd one with taxon ``odd`` apart from the grid's group is worth (odd_base)."""
    depth = true_depth(odd, game.group_taxon(answer.grid_group)) if odd is not None and answer.grid_group else None
    weight = setting("GAME_POINTS_ODD_WEIGHT", 1.5) * grid_size_factor(answer)
    return PAIR_POINTS[depth if depth is not None and depth < 3 else -1] * weight


def odd_worth(answer, grid):
    """
    What finding every odd one of a grid is worth (#540): odd_base for each of its odd ones (score_odd_grid's "right"
    and "missed" tiles), on average, so a grid hiding three is worth what one hiding one is, its points split three
    ways. Its first odd one (``roi_b``) when the truth shows none.
    """
    tiles = grid_tiles(answer)
    odd = [tiles[i].taxon for i, state in enumerate(grid["tiles"]) if state in ("right", "missed")]
    return sum(_odd_points(answer, t) for t in odd) / len(odd) if odd else odd_base(answer)


def odd_grid(answer, votes_for, judges, model_refs):
    """
    (points, basis, detail) for an Odd One Out grid answered with picks (#540). Each pick is a claim that the beetle is
    not one of the group, worth a share of the grid (odd_worth over the odd ones asked for): a validated odd one picked
    earns its share, a validated beetle of the rest picked costs k shares (#530), and a pick on a beetle nobody has
    validated is scored like a name on it, by agreement (never below zero). Blind picking loses on average at every
    step of the ladder (game_tuning.odd_guess). Against the truth (basis TRUTH) once any pick is wrong or every pick
    is on a validated beetle, else by agreement. ``share`` and the tiles' states are in the detail for the review.
    """
    grid = game.score_odd_grid(grid_tiles(answer), answer.picks, answer.grid_rank, answer.grid_group, answer.flagged)
    worth = odd_worth(answer, grid)
    share = worth / max(1, len(answer.picks))
    right = game.odd_verdict(grid)
    points = share * (grid["right"] - wrong_cost() * grid["wrong"])
    agreement = {}
    for i, state in enumerate(grid["tiles"]):
        if state == "vote":
            tile = grid_tiles(answer)[i]
            got, said = _odd_agreement(answer, tile.id if tile else None, votes_for, judges, model_refs, share)
            points += got
            agreement[str(i)] = dict(said, points=round(got, 3))
    detail = {"right": right, "rank": answer.grid_rank, "worth": round(worth, 2), "share": round(share, 3),
              "odds": len(answer.picks), "found": grid["right"], "wrong": grid["wrong"], "tiles": grid["tiles"],
              **_grid_detail(answer)}
    if agreement:
        detail["votes"] = agreement
    return points, AnswerPoints.Basis.TRUTH if right is not None else AnswerPoints.Basis.CONSENSUS, detail


def open_picks(answer):
    """The beetles nobody has validated that an Odd One Out answer picked (#540): scored by what others say of them."""
    if answer.mode != "odd" or not answer.picks or answer.skipped:
        return set()
    tiles = grid_tiles(answer)
    return {tiles[i].id for i in answer.picks if 0 <= i < len(tiles) and tiles[i] is not None and not is_truth(tiles[i])}


def odd_tile_points(detail):
    """What one picked tile of a scored Odd One Out grid earned, from its detail (odd_grid): (an odd one, one of the rest)."""
    share = float(detail.get("share", 0.0))
    return share, -share * wrong_cost()


def odd_truth(answer):
    """(points, detail) for an Odd One Out pick on a validated beetle, or None if it can't be told."""
    ok = game.score_odd(answer.roi.taxon, answer.grid_rank, answer.grid_group).get(answer.grid_rank)
    if ok is None:
        return None
    base = odd_base(answer)
    detail = {"right": ok, "rank": answer.grid_rank, "worth": round(base, 2), **_grid_detail(answer)}
    if ok:
        return base, detail
    return -base * wrong_cost(), detail


def grid_tiles(answer):
    """The Beetles of a grid answer in the order shown (None where one is gone); recompute loads them for all at once."""
    cached = getattr(answer, "_grid_tiles", None)
    if cached is None:
        found = Beetles.objects.select_related("taxon").in_bulk([uuid.UUID(str(t)) for t in answer.tiles or []])
        cached = answer._grid_tiles = [found.get(uuid.UUID(str(t))) for t in answer.tiles or []]
    return cached


def select_truth(answer):
    """
    (points, detail) for a Select all grid, or None if it holds no validated member to score against. Photos the player
    flagged count for nothing. ``share`` in the detail is what each member found earns (a wrong tap costs k of it),
    so the review can show each beetle's points.
    """
    result = game.score_select(grid_tiles(answer), answer.picks, answer.grid_rank, answer.grid_group, answer.flagged)
    if not result["members"]:
        return None
    depth = game.RANKS.index(answer.grid_rank) - 1 if answer.grid_rank in game.RANKS else -1
    worth = setting("GAME_POINTS_SELECT_WEIGHT", 1.25) * PAIR_POINTS[depth] * grid_size_factor(answer)
    share = worth / result["members"]
    points = select_points(worth, result["members"], result["right"], result["wrong"])
    detail = {k: result[k] for k in ("right", "wrong", "missed", "members", "perfect", "tiles")}
    return points, dict(detail, rank=answer.grid_rank, worth=round(worth, 2), share=round(share, 3), **_grid_detail(answer))


def select_points(worth, members, right, wrong):
    """
    A Find Them All grid's points: a share of ``worth`` for every member tapped, k shares off for every non-member
    tapped (a tap pays only when the player is at least GAME_POINTS_CONFIDENCE sure of it), and never below −worth,
    so one grid can't lose more than a perfect one earns.
    """
    share = worth / members
    return max(-worth, share * (right - wrong_cost() * wrong))


def select_tile_points(detail):
    """
    What one tile of a scored Select all grid earned, from the grid's stored detail (select_truth): (a member tapped,
    a non-member tapped). A member left out and everything else earn nothing, so the tiles add up to the grid's points
    (unless the grid hit its floor, −worth).
    """
    share = float(detail.get("worth", 0.0)) / detail["members"] if detail.get("members") else 0.0
    return share, -share * wrong_cost()


def odd_consensus(answer, votes, judges, model_refs):
    """
    (points, detail) for an Odd One Out pick on a beetle not validated yet: like a name on it, scored by how far the
    judges' names for it, and what proven experts or a trusted model say, agree that it is not one of the group at
    the round's rank. Never negative.
    """
    return _odd_agreement(answer, answer.roi_id, lambda rid: votes, judges, model_refs, odd_base(answer))


def _odd_agreement(answer, roi_id, votes_for, judges, model_refs, base):
    """
    (points, detail) for picking the unvalidated beetle ``roi_id`` out of an Odd One Out grid: up to
    GAME_POINTS_CONSENSUS_CAP of ``base`` as the judges agree it is not one of the group, at least
    GAME_POINTS_REFERENCE_CAP of it when proven experts or a trusted model say so. Never negative.
    """
    rank, group = answer.grid_rank, (answer.grid_group or {}).get(answer.grid_rank, "")
    if not rank or not group or roi_id is None:
        return 0.0, {"agreement": {}}
    votes = votes_for(roi_id)
    agree = disagree = 0.0
    for judge_id, labels in votes:
        if rank not in labels:
            continue
        w = judges.weight(judge_id, answer.player_id, rank, labels)
        if labels[rank].strip().lower() != group.strip().lower():
            agree += w
        else:
            disagree += w
    c = (agree - disagree) / (agree + disagree + 1.0)
    points = setting("GAME_POINTS_CONSENSUS_CAP", 0.6) * base * max(0.0, c)
    detail = {"agreement": {rank: round(c, 3)}}
    about = SimpleNamespace(roi_id=roi_id, player_id=answer.player_id)   # the beetle picked, not the answer's own roi
    reference = game_reference.reference_for(about, votes, judges, model_refs).get(rank)
    if reference:
        match = reference[0].strip().lower() != group.strip().lower()
        if match:
            points = max(points, setting("GAME_POINTS_REFERENCE_CAP", 0.6) * base)
        detail["reference"] = {rank: {"name": reference[0], "source": reference[1], "match": match}}
    return points, detail


# ---------------------------------------------------------------------------
# Agreement, for beetles not validated yet
# ---------------------------------------------------------------------------
def wilson(ok, n, z=1.0):
    if n == 0:
        return 0.0
    p = ok / n
    z2 = z * z
    return (p + z2 / (2 * n) - z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / (1 + z2 / n)


RATINGS_STORE = "game:ratings:v2"    # {"at": when ratings() was run (epoch seconds), "table": its result}
RATINGS_REFRESH = "game:ratings:refreshing"   # one refresh queued (or running) at a time
RATINGS_KEEP = 60 * 60 * 24


def store_ratings(table):
    """Keep a freshly worked-out table, for the requests that read it (cached_ratings)."""
    import time
    from django.core.cache import cache
    cache.set(RATINGS_STORE, {"at": time.time(), "table": table}, RATINGS_KEEP)


def refresh_ratings():
    """The worker's part (tasks.refresh_game_ratings_task): work the table out over the whole table and store it."""
    from django.core.cache import cache
    try:
        store_ratings(ratings())
    finally:
        cache.delete(RATINGS_REFRESH)


def _queue_refresh():
    from django.core.cache import cache
    from .tasks import refresh_game_ratings_task
    if not cache.add(RATINGS_REFRESH, 1, 300):
        return
    try:
        refresh_game_ratings_task.apply_async(retry=False)
    except Exception:
        cache.delete(RATINGS_REFRESH)   # the next request asks again


def cached_ratings():
    """
    The ratings table, as the worker last stored it (refresh_ratings). A web request never works it out over the
    whole table where game work is on the worker (GAME_RECOMPUTE_IN_BACKGROUND): a table older than
    GAME_RATINGS_CACHE_SECONDS is used as it is and a refresh is queued; none yet means no judges for now (the
    answer's points are settled again by the next recompute). Where game work stays in the request, it is worked out
    as before, and stored.
    """
    import time
    from django.core.cache import cache
    if not setting("GAME_RECOMPUTE_IN_BACKGROUND", False):
        stored = cache.get(RATINGS_STORE)
        if stored is None or time.time() - stored["at"] > setting("GAME_RATINGS_CACHE_SECONDS", 300):
            store_ratings(ratings())
            stored = cache.get(RATINGS_STORE)
        return stored["table"]
    stored = cache.get(RATINGS_STORE)
    if stored is None or time.time() - stored["at"] > setting("GAME_RATINGS_CACHE_SECONDS", 300):
        _queue_refresh()
    return stored["table"] if stored else {}



def ratings():
    """
    {player_id: (rating, accuracy, judged)} from the first time each player saw each validated beetle, including
    beetles validated after they answered (validated_later).
    The rating is a cautious estimate of their accuracy (the lower end of a Wilson interval), so a few lucky
    answers don't make anyone an authority. A Select all grid counts once, at its rank: correct only when perfect
    (#381), like one Odd One Out pick, so a grid player can't swamp it with easy taps.
    """
    tallies = defaultdict(lambda: [0, 0])
    first = set()
    rows = (
        GameAnswer.objects.filter(Q(is_check=True) | Q(validated_later=True), is_retry=False, seen_before=False,
                                  skipped=False, score_hold=False)
        .order_by("answered_at")
        .values_list("player_id", "mode", "roi_id", "roi_b_id", *[f"correct_{r}" for r in RANKS])
    )
    for pid, mode, a, b, *oks in rows:
        key = (pid, mode, a, b)
        if key in first:
            continue
        first.add(key)
        for ok in oks:
            if ok is not None:
                tallies[pid][0] += int(ok)
                tallies[pid][1] += 1
    return {pid: (wilson(ok, n), (ok / n if n else None), n) for pid, (ok, n) in tallies.items()}


class Judges:
    """Who counts as a judge for agreement points, and how much each counts for a given player."""

    def __init__(self, rating_table):
        from .game_trust import TrustContext

        self.rating = {pid: r for pid, (r, _, _) in rating_table.items()}
        min_judged = setting("GAME_RATER_MIN_JUDGED", 10)
        rated = sorted(r for pid, (r, _, n) in rating_table.items() if n >= min_judged)
        median = rated[len(rated) // 2] if rated else 1.0
        self.qualified = {
            pid for pid, (r, _, n) in rating_table.items() if n >= min_judged and r >= median and r > 0
        }
        self.trust = TrustContext(self.qualified)
        self.spread = setting("GAME_RATER_SPREAD", 0.1)

    def weight(self, judge_id, player_id, rank, labels):
        if judge_id not in self.qualified or judge_id == player_id:
            return 0.0
        if self.trust.trusted_through(judge_id, rank, labels):
            return 1.0  # a proven expert for this part of the tree
        diff = self.rating.get(judge_id, 0.0) - self.rating.get(player_id, 0.0)
        return 1.0 / (1.0 + math.exp(-diff / self.spread))


def agreement(player_id, claims, votes, judges):
    """
    {rank: c} for each rank the player claims, c from -1 (strong judges disagree) to +1 (many strong judges agree).
    ``votes`` is [(judge_id, {rank: value})] for the beetle, one per judge.
    """
    out = {}
    for rank, value in claims.items():
        agree = disagree = 0.0
        for judge_id, labels in votes:
            if rank not in labels:
                continue
            w = judges.weight(judge_id, player_id, rank, labels) * getattr(labels, "weight", 1.0)
            if not w:
                continue
            if labels[rank].strip().lower() == value.strip().lower():
                agree += w
            else:
                disagree += w
        out[rank] = (agree - disagree) / (agree + disagree + 1.0)
    return out


def agreed(worth, c, cap):
    """
    One claim on an unvalidated beetle: cap × worth × c while the judges agree (c > 0), and k times that off when they
    disagree, as a wrong claim costs on a validated beetle, so a deep guess doesn't pay here either (#530). The answer
    as a whole is never below zero (consensus_points).
    """
    return cap * worth * (c if c >= 0 else wrong_cost() * c)


def consensus_points(answer, votes, judges):
    """
    (points, detail) for an answer on a beetle not validated yet: agreement rank by rank (agreed), never negative in
    all.
    """
    claims = game.implied_labels(answer)
    if not claims:
        return 0.0, {"agreement": {}}
    cap = setting("GAME_POINTS_CONSENSUS_CAP", 0.6)
    c = agreement(answer.player_id, claims, votes, judges)
    if answer.mode == "classify":
        points = sum(agreed(RANK_POINTS[r], c[r], cap) for r in claims)
    else:
        # rung by rung: agreement on "same tribe" earns the tribe rung, a disputed genus rung costs
        depth = PAIR_DEPTH[answer.pair_answer]
        points, below = 0.0, 0.0
        for d in range(depth + 1):
            step = PAIR_POINTS[d] - below
            below = PAIR_POINTS[d]
            points += agreed(step, c.get(RANKS[d], 0.0), cap)
    return max(0.0, points), {"agreement": {r: round(v, 3) for r, v in c.items()}}


# ---------------------------------------------------------------------------
# One answer
# ---------------------------------------------------------------------------
def score(answer, votes_for, judges, model_refs=None):
    """
    (points, basis, detail). ``votes_for(roi_id)`` gives the judges' votes on an unvalidated beetle; ``model_refs``
    the classifier's trusted names for unvalidated beetles (game_reference.model_references).
    Every real answer also earns a small participation point (GAME_POINTS_PARTICIPATION), so the score grows the
    more you play; accuracy still decides most of it.
    """
    points, basis, detail = _score(answer, votes_for, judges, model_refs or {})
    points, detail = _by_difficulty(answer, points, detail)
    if basis in (AnswerPoints.Basis.TRUTH, AnswerPoints.Basis.CONSENSUS, AnswerPoints.Basis.NONE) and not answer.skipped \
            and not answer.score_hold:
        bonus = setting("GAME_POINTS_PARTICIPATION", 0.5)
        points += bonus
        detail = dict(detail, participation=bonus)
    return points, basis, detail


# ---------------------------------------------------------------------------
# How hard the beetle was (#492)
# ---------------------------------------------------------------------------
# Identification and Similarity only: the grid games are priced by their own size and rank, so scaling them by one
# beetle's difficulty as well would count it twice.
SCALED_MODES = ("classify", "pair")


def difficulty_spread():
    return float(setting("GAME_POINTS_DIFFICULTY_SPREAD", 0.25))


def difficulty_multiplier(percentile):
    """m = 1 + spread × (2p − 1): from 1 − spread on the easiest beetle (p = 0) to 1 + spread on the hardest (p = 1)."""
    return 1 + difficulty_spread() * (2 * percentile - 1)


def by_difficulty(points, m):
    """
    Points scaled for how hard the beetle is: a gain × m, a loss × (2 − m), so a hard beetle pays more and costs less
    when missed. One factor for the whole answer, so a wrong answer never turns into a gain (nor a right one into a
    loss) and every comparison between answers on one beetle (stopping beats overreaching, a skip costs less than a
    mistake) holds at every difficulty.
    """
    return points * m if points >= 0 else points * (2 - m)


def difficulty_factor(detail, earned):
    """What an answer's points were multiplied by for its beetle's difficulty (1 when not), from its detail."""
    m = float((detail or {}).get("multiplier", 1.0))
    return m if earned >= 0 else 2 - m


def note_difficulty(answer):
    """Store how hard the beetle is now on an Identification or Similarity answer about to be saved (the anchor of a pair)."""
    from .game_difficulty import percentile

    if answer.mode in SCALED_MODES:
        answer.difficulty = percentile(answer.roi_id)


def _by_difficulty(answer, points, detail):
    """The one place points follow difficulty, for recompute and score_new_answer alike. Older answers have none: ×1."""
    if answer.mode not in SCALED_MODES or answer.difficulty is None or answer.score_hold:
        return points, detail
    m = difficulty_multiplier(answer.difficulty)
    return by_difficulty(points, m), dict(detail, difficulty=answer.difficulty, multiplier=round(m, 3))


def _score(answer, votes_for, judges, model_refs):
    if answer.score_hold:
        return 0.0, AnswerPoints.Basis.NONE, {"held": True}
    if answer.skipped and answer.mode in ("odd", "select"):
        # the grid games reward saying you're not sure over guessing (#369, #370)
        return setting("GAME_POINTS_ODD_SKIP", 0.25), AnswerPoints.Basis.UNSURE, {}
    if answer.skipped or (answer.mode == "pair" and answer.pair_answer == "unsure"):
        return -setting("GAME_POINTS_UNSURE", 0.25), AnswerPoints.Basis.UNSURE, {}
    retry = setting("GAME_POINTS_RETRY_FACTOR", 0.5) if answer.is_retry else 1.0
    if answer.mode == "select":
        scored = select_truth(answer)
        if scored:
            return scored[0] * retry, AnswerPoints.Basis.TRUTH, _retried(scored[1], answer, retry)
        return 0.0, AnswerPoints.Basis.NONE, {}
    if answer.mode == "odd" and answer.picks:   # picks: every answer since grids hid several odd ones (#540)
        points, basis, detail = odd_grid(answer, votes_for, judges, model_refs)
        if basis == AnswerPoints.Basis.TRUTH:
            return points * retry, basis, _retried(detail, answer, retry)
        return points, basis, detail
    if answer.mode == "odd":
        if is_truth(answer.roi):
            scored = odd_truth(answer)
            if scored:
                return scored[0] * retry, AnswerPoints.Basis.TRUTH, _retried(scored[1], answer, retry)
            return 0.0, AnswerPoints.Basis.NONE, {}
        points, detail = odd_consensus(answer, votes_for(answer.roi_id), judges, model_refs)
        return points, AnswerPoints.Basis.CONSENSUS, detail
    if answer.mode == "classify":
        weight = classify_weight()
        if is_truth(answer.roi):
            points, detail = classify_truth(answer, answer.roi.taxon)
            for rank in detail["ranks"].values():
                rank["points"] = round(rank["points"] * weight, 2)
            return points * weight * retry, AnswerPoints.Basis.TRUTH, dict(detail, retry=answer.is_retry, weight=weight)
        votes = votes_for(answer.roi_id)
        points, detail = consensus_points(answer, votes, judges)
        reference = game_reference.reference_for(answer, votes, judges, model_refs)
        if reference:
            points, detail = _with_reference(answer, reference, detail)
        return points * weight, AnswerPoints.Basis.CONSENSUS, dict(detail, weight=weight)
    a, b = answer.roi, answer.roi_b
    if b is not None and is_truth(a) and is_truth(b):
        scored = pair_truth(answer, a, b)
        if scored:
            return scored[0] * retry, AnswerPoints.Basis.TRUTH, dict(scored[1], retry=answer.is_retry)
        return 0.0, AnswerPoints.Basis.NONE, {}
    if b is not None and is_truth(b):
        points, detail = consensus_points(answer, votes_for(answer.roi_id), judges)
        return points, AnswerPoints.Basis.CONSENSUS, detail
    return 0.0, AnswerPoints.Basis.NONE, {}


def _retried(detail, answer, factor):
    """A grid answer's detail on a retry (#490): marked, with what it was worth scaled like its points."""
    if not answer.is_retry:
        return detail
    scaled = dict(detail, retry=True, worth=round(float(detail.get("worth", 0.0)) * factor, 2))
    if "share" in detail:
        scaled["share"] = round(float(detail["share"]) * factor, 3)
    return scaled


def _with_reference(answer, reference, detail):
    """
    Agreement points per rank, raised to the reference's where the answer matches what proven experts or a trusted
    model say (game_reference), and lowered to k times the reference's points off where it names that rank otherwise.
    The best (or worst) of the two per rank, so nothing is counted twice; never negative in all.
    """
    claims = game.implied_labels(answer)
    cap = setting("GAME_POINTS_CONSENSUS_CAP", 0.6)
    by_agreement = {r: agreed(RANK_POINTS[r], detail["agreement"].get(r, 0.0), cap) for r in claims}
    matched = game_reference.reference_points(claims, reference, RANK_POINTS)
    points = 0.0
    for r in claims:
        if r in matched:
            points += max(by_agreement[r], matched[r])
        elif r in reference:   # the experts or a trusted model say otherwise
            points += min(by_agreement[r], -wrong_cost() * setting("GAME_POINTS_REFERENCE_CAP", 0.6) * RANK_POINTS[r])
        else:
            points += by_agreement[r]
    shown = {r: {"name": name, "source": source, "match": r in matched}
             for r, (name, source) in reference.items() if r in claims}
    return max(0.0, points), dict(detail, reference=shown)


def votes_on(roi_ids):
    """{roi_id: [(player_id, {rank: value})]} from every answer on these beetles, the latest per player."""
    latest = {}
    answers = (
        GameAnswer.objects.filter(roi_id__in=list(roi_ids), skipped=False)
        .select_related("roi_b__taxon").order_by("answered_at")
    )
    for ans in answers:
        labels = game.implied_labels(ans)
        if labels:
            latest[(ans.roi_id, ans.player_id)] = labels
    # The grid games count too (Select all taps, the rest of a solved Odd One Out grid), a little less than a name
    # (game.tap_votes), unless the player also named it
    for roi_id, pid, vote in game.tap_votes(roi_ids):
        latest.setdefault((roi_id, pid), vote)
    out = defaultdict(list)
    for (roi_id, pid), labels in latest.items():
        out[roi_id].append((pid, labels))
    return out


# ---------------------------------------------------------------------------
# Beetles validated after they were answered
# ---------------------------------------------------------------------------
def species_name(taxon):
    if taxon is None:
        return ""
    return taxon.scientific_name or " ".join(x for x in (taxon.genus, taxon.species) if x)


def sync_late_truth(player_ids=None):
    """
    Answers given on beetles nobody had validated, where the beetle has been validated since: fill in what was
    right and wrong (correct_*, ref_*) and mark them ``validated_later``, so they count towards accuracy and
    expertise like any other validated beetle. Undone if the beetle loses its validation. Returns how many changed.
    """
    answers = (
        GameAnswer.objects.filter(is_check=False, skipped=False)
        # an Odd One Out answer with picks (#540) may have any of them validated since, not just its roi
        .filter(Q(roi__bbox_is_validated=True) | Q(validated_later=True) | (Q(mode="odd") & ~Q(picks=[])))
        .select_related("roi__taxon", "roi_b__taxon")
    )
    if player_ids is not None:
        answers = answers.filter(player_id__in=list(player_ids))
    fields = ["validated_later", "ref_subfamily", "ref_tribe", "ref_genus", "ref_species", *[f"correct_{r}" for r in RANKS]]
    answers = list(answers)
    picked = [a for a in answers if a.mode == "odd" and a.picks]
    if picked:   # their grids' beetles in one query
        found = Beetles.objects.select_related("taxon").in_bulk({uuid.UUID(str(t)) for a in picked for t in a.tiles or []})
        for a in picked:
            a._grid_tiles = [found.get(uuid.UUID(str(t))) for t in a.tiles or []]
    changed = []
    for ans in answers:
        before = [getattr(ans, f) for f in fields]
        results = None
        if ans.mode == "odd" and ans.picks:
            tiles = grid_tiles(ans)
            right = game.odd_verdict(game.score_odd_grid(tiles, ans.picks, ans.grid_rank, ans.grid_group, ans.flagged))
            if right is not None:
                results = {ans.grid_rank: right}
                # the name of a validated beetle picked, as on an answer checked straight away
                taxon = next((tiles[i].taxon for i in ans.picks if 0 <= i < len(tiles) and is_truth(tiles[i])), None)
                ans.ref_subfamily, ans.ref_tribe, ans.ref_genus, ans.ref_species = (
                    (taxon.subfamily or "", taxon.tribe or "", taxon.genus or "", taxon.species or "") if taxon
                    else ("", "", "", ""))
        elif is_truth(ans.roi):
            if ans.mode == "classify":
                results = game.score_classification({r: getattr(ans, r) for r in RANKS}, ans.roi.taxon)
            elif ans.mode == "odd":
                results = game.score_odd(ans.roi.taxon, ans.grid_rank, ans.grid_group) if ans.grid_rank else None
            elif ans.roi_b is not None and is_truth(ans.roi_b) and ans.pair_answer in PAIR_DEPTH:
                results = game.score_pair(ans.pair_answer, ans.roi.taxon, ans.roi_b.taxon)
        if results is not None:
            ans.validated_later = True
            if not (ans.mode == "odd" and ans.picks):
                t = ans.roi.taxon
                ans.ref_subfamily, ans.ref_tribe, ans.ref_genus, ans.ref_species = t.subfamily or "", t.tribe or "", t.genus or "", t.species or ""
            for r in RANKS:
                setattr(ans, f"correct_{r}", results.get(r))
        elif ans.validated_later:
            ans.validated_later = False
            ans.ref_subfamily = ans.ref_tribe = ans.ref_genus = ans.ref_species = ""
            for r in RANKS:
                setattr(ans, f"correct_{r}", None)
        if [getattr(ans, f) for f in fields] != before:
            changed.append(ans)
    GameAnswer.objects.bulk_update(changed, fields, batch_size=500)
    return len(changed)


def players_sharing_beetles(player_id, roi_ids, limit=200):
    """Other players who answered these (unvalidated) beetles: their agreement points move with a new answer."""
    return list(
        GameAnswer.objects.filter(roi_id__in=list(roi_ids)).exclude(player_id=player_id)
        .values_list("player_id", flat=True).distinct()[:limit]
    )


# ---------------------------------------------------------------------------
# Recomputing
# ---------------------------------------------------------------------------
LOCK_KEY = 0x6A6D5C01   # any fixed number: the advisory lock that serialises score writes


def _write_lock():
    """
    Many players finish at the same time in production (several web workers and the background worker), and each
    finish re-scores the players it touches. Their writes take turns behind one Postgres advisory lock, held until
    the transaction ends, so two recomputes never delete and re-create the same rows at once.
    """
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [LOCK_KEY])

def recompute(player_ids=None):
    """
    Re-score every answer of these players (everyone when None) and refresh their totals. Safe to run any time;
    this is how validations, label corrections and other players' later answers reach a score.
    Returns the number of players updated.
    """
    sync_late_truth(player_ids)
    table = ratings()
    store_ratings(table)
    judges = Judges(table)
    answers = GameAnswer.objects.select_related(
        "roi__taxon", "roi__image_asset", "roi_b__taxon", "roi_b__image_asset",
    ).order_by("player_id", "answered_at", "index")
    if player_ids is not None:
        answers = answers.filter(player_id__in=list(player_ids))
    answers = list(answers)
    grids = [a for a in answers if a.mode == "select" or a.mode == "odd" and a.picks]
    if grids:   # every Select all beetle, and every Odd One Out one picked since #540, in one query
        found = Beetles.objects.select_related("taxon").in_bulk({uuid.UUID(str(t)) for a in grids for t in a.tiles or []})
        for a in grids:
            a._grid_tiles = [found.get(uuid.UUID(str(t))) for t in a.tiles or []]
    open_rois = {a.roi_id for a in answers if not is_truth(a.roi)}
    for a in grids:
        open_rois |= open_picks(a)
    votes = votes_on(open_rois)
    model_refs = game_reference.model_references(open_rois)

    old = {
        aid: (p, b) for aid, p, b in AnswerPoints.objects.filter(answer__in=[a.id for a in answers if a.validated_later])
        .values_list("answer_id", "points", "basis")
    }
    credited = set(RetroCredit.objects.filter(answer__in=list(old)).values_list("answer_id", flat=True))
    rows, by_player, credits = [], defaultdict(list), []
    for ans in answers:
        points, basis, detail = score(ans, lambda rid: votes.get(rid, []), judges, model_refs)
        rows.append(AnswerPoints(answer=ans, points=round(points, 3), basis=basis, detail=detail))
        by_player[ans.player_id].append((ans, points))
        if ans.validated_later and basis == AnswerPoints.Basis.TRUTH and ans.id not in credited:
            before, old_basis = old.get(ans.id, (0.0, None))
            if old_basis != AnswerPoints.Basis.TRUTH:
                credits.append(RetroCredit(
                    answer=ans, player_id=ans.player_id, points_before=round(before, 3), points_after=round(points, 3),
                    validated_name=species_name(ans.roi.taxon), validated_at=ans.roi.bbox_validated_at,
                ))

    players = list(by_player) if player_ids is None else list(player_ids)
    existing = set(get_user_model().objects.filter(id__in=players).values_list("id", flat=True))
    with transaction.atomic():
        _write_lock()
        AnswerPoints.objects.filter(answer__player_id__in=players).delete()
        AnswerPoints.objects.bulk_create(rows, batch_size=1000)
        RetroCredit.objects.bulk_create(credits, ignore_conflicts=True)
        for pid in players:
            total = 0.0
            for _, p in by_player.get(pid, []):
                total = max(0.0, total + p)  # a bad start never leaves anyone in debt
            rating, accuracy, judged = table.get(pid, (0.0, None, 0))
            entries = by_player.get(pid, [])
            if pid not in existing:
                continue
            PlayerScore.objects.update_or_create(player_id=pid, defaults=dict(
                score=round(total, 2), rating=round(rating, 4), accuracy=accuracy, judged=judged,
                viewed=len(entries), labelled=sum(1 for a, _ in entries if not a.skipped),
            ))
    return len(players)


def score_new_answer(answer):
    """
    Points for an answer just given, straight away, and the player's total moved with it. The full recompute
    (when they leave, and every night) tidies this up with everything that has changed since.
    """
    roi_ids = set() if is_truth(answer.roi) else {answer.roi_id}
    roi_ids |= open_picks(answer)
    votes = votes_on(roi_ids) if roi_ids else {}
    judges = Judges(cached_ratings()) if votes else _NoJudges()
    model_refs = game_reference.model_references(roi_ids) if roi_ids else {}
    points, basis, detail = score(answer, lambda rid: votes.get(rid, []), judges, model_refs)
    for attempt in range(2):   # a recompute may have re-created the row in between: try once more
        try:
            with transaction.atomic():
                AnswerPoints.objects.update_or_create(
                    answer=answer, defaults=dict(points=round(points, 3), basis=basis, detail=detail))
            break
        except IntegrityError:
            if attempt:
                raise
    PlayerScore.objects.get_or_create(player_id=answer.player_id)
    # one UPDATE, so two answers saved at the same moment can't overwrite each other's total
    PlayerScore.objects.filter(player_id=answer.player_id).update(
        score=Greatest(Value(0.0), F("score") + round(points, 2)),
        viewed=F("viewed") + 1,
        labelled=F("labelled") + (0 if answer.skipped else 1),
        updated_at=timezone.now(),
    )
    return points, basis


class _NoJudges:
    qualified = set()

    def weight(self, *args):
        return 0.0


def score_for(player):
    """The player's PlayerScore, created (and computed) the first time it is asked for."""
    found = PlayerScore.objects.filter(player=player).first()
    if found is None and GameAnswer.objects.filter(player=player).exists():
        recompute([player.id])
        found = PlayerScore.objects.filter(player=player).first()
    return found or PlayerScore(player=player)
