"""
Points for the Beetle ID game.

Every answer is worth some points (AnswerPoints) and a player's score is the running total, never below zero
(PlayerScore). The rules, all adjustable with GAME_POINTS_* settings:

Identification is the harder and more useful game, so every Name That Beetle answer counts
GAME_POINTS_CLASSIFY_WEIGHT (3) times the points below, gains and losses alike: a perfect identification
earns 45, a perfect similarity answer at most about 6.

Beetles we know the answer to (validated) are scored against the truth. These earn the most.
  Name That Beetle   partial credit: each rank you get right earns its weight (subfamily 1, tribe 2, genus 4,
                     species 8), so naming the exact species is worth the most. Where you go wrong, only the first
                     wrong rank costs anything: a small penalty (GAME_POINTS_OVERREACH, 35% of its weight) when the
                     ranks above it were right, e.g. right genus but wrong species = 7 - 2.8 = 4.2, less than
                     stopping at the genus (7). A wrong subfamily, with nothing right, costs 3/4 of everything you
                     claimed (GAME_POINTS_WRONG_FACTOR).
  Family Ties        the right answer earns more the finer the line you had to draw: different subfamilies 1,
                     same subfamily 2, same tribe 3, same genus 5, same species 5, plus up to a quarter more when
                     the two photos are alike (same photographer, place, magnification...). Partial credit too:
                     a cautious answer that is true as far as it goes ("same tribe" for two beetles of one genus)
                     earns that rung's points; claiming too close a tie ("same genus" for two of one tribe) earns
                     the true rung's points minus the small penalty for every rung too far. Calling related beetles
                     "different subfamilies", or unrelated ones related, loses 1 point per step it is off.
  Odd One Out        picking the beetle that doesn't belong earns GAME_POINTS_ODD_WEIGHT (1.5) times what Family Ties
                     pays for telling apart the odd one and the rest (another subfamily 1, another tribe of the same
                     subfamily 2, another genus of the same tribe 3, another species of the same genus 5). Picking one
                     of the rest costs GAME_POINTS_ODD_WRONG_FACTOR (1.25) times that, so guessing loses on average,
                     and Skip earns a little instead of costing (GAME_POINTS_ODD_SKIP, 0.25). A pick on a beetle nobody
                     has validated yet is scored like a name on it: by agreement that it doesn't belong.
  Select all         every validated member tapped earns a share of GAME_POINTS_SELECT_WEIGHT (2) times the Family
                     Ties points for the grid's rank (another subfamily 1 ... another species of one genus 5), so a
                     perfect grid earns about twice a Similarity answer; every validated non-member tapped costs
                     GAME_POINTS_SELECT_WRONG (1.5) shares, and a member left out costs nothing. Taps on beetles nobody
                     has validated are recorded, never scored. Skip earns GAME_POINTS_ODD_SKIP, as in Odd One Out.
                     Select all is left out of the reliability rating for now: a grid is many judgements at once (#381).
  Seen again         a beetle shown again so you can learn it (a retry) earns half.

Beetles nobody has validated yet are scored by agreement, never more than GAME_POINTS_CONSENSUS_CAP (60%)
of what the same answer would earn on a validated beetle, and never less than zero:
  * only players whose rating is at or above the median of rated players are counted as judges;
  * a judge counts fully when they are a proven expert for that part of the tree, otherwise by how their rating
    compares with yours, like an Elo expectation: agreeing with stronger players earns almost full credit,
    agreeing with weaker ones very little;
  * a stronger player who disagrees cancels out agreement from weaker ones, so siding with many weak players
    against one strong one earns nothing;
  * the more judges agree, the closer it gets to the cap.

Not sure / skip costs a little (GAME_POINTS_UNSURE, 0.25). Every real answer earns a small participation point
(GAME_POINTS_PARTICIPATION, 0.5), so the score grows with play.

When a beetle is validated later, or its label is corrected, every answer on it is re-scored against the truth,
up or down (recompute, run for a player when they leave the game and for everyone every night).
"""
import math
import uuid
from collections import defaultdict
from collections.abc import Mapping

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
PAIR_POINTS = _Points("GAME_PAIR_POINTS", {-1: 1.0, 0: 2.0, 1: 3.0, 2: 5.0, 3: 5.0})
DEPTH_NAME = {-1: "different subfamilies", 0: "same subfamily", 1: "same tribe", 2: "same genus", 3: "same species"}


def setting(name, default):
    return game_setting(name, default)


def classify_weight():
    """How many times more a Name That Beetle answer counts than the base points (GAME_POINTS_CLASSIFY_WEIGHT)."""
    return float(setting("GAME_POINTS_CLASSIFY_WEIGHT", 3.0))


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
    (points, detail) for a Name That Beetle answer on a validated beetle, with partial credit: every right rank
    earns its weight, and only the first wrong rank costs anything (the ranks below it are wrong because of it).
    That costs a small GAME_POINTS_OVERREACH share of its weight after a right rank, or GAME_POINTS_WRONG_FACTOR of
    everything claimed when even the subfamily is wrong.
    """
    given = {r: getattr(answer, r) for r in RANKS}
    results = game.score_classification(given, taxon)
    points, ranks, any_right = 0.0, {}, False
    for r in RANKS:
        ok = results[r]
        if ok is None:
            continue
        if ok:
            p = RANK_POINTS[r]
            any_right = True
        elif any(v.get("right") is False for v in ranks.values()):
            p = 0.0   # already wrong above: this rank couldn't be right
        elif any_right:
            p = -RANK_POINTS[r] * setting("GAME_POINTS_OVERREACH", 0.35)
        else:
            claimed = sum(RANK_POINTS[x] for x in RANKS if results[x] is not None)
            p = -claimed * setting("GAME_POINTS_WRONG_FACTOR", 0.75)
        ranks[r] = {"right": ok, "points": round(p, 2)}
        points += p
    return points, {"ranks": ranks}


def pair_truth(answer, roi_a, roi_b):
    """(points, detail) for a Family Ties answer when both beetles are validated, or None if it can't be told."""
    truth = true_depth(roi_a.taxon, roi_b.taxon)
    given = PAIR_DEPTH.get(answer.pair_answer)
    if truth is None or given is None:
        return None
    sim = photo_similarity(roi_a, roi_b)
    bonus = 1 + setting("GAME_POINTS_SIMILARITY_BONUS", 0.25) * sim
    if given == truth:
        return PAIR_POINTS[truth] * bonus, {"right": True, "truth": DEPTH_NAME[truth], "similarity": sim}
    steps = abs(given - truth)
    if given >= 0 and truth >= 0:
        if given < truth:
            # cautious but true as far as it goes: that rung's points
            return PAIR_POINTS[given] * bonus, {"right": "partial", "truth": DEPTH_NAME[truth], "similarity": sim,
                                               "steps": steps}
        # too close a tie: the true rung's points, less a small penalty per rung too far
        penalty = setting("GAME_POINTS_OVERREACH", 0.35) * PAIR_POINTS[truth + 1] * steps
        return PAIR_POINTS[truth] * bonus - penalty, {"right": "partial", "truth": DEPTH_NAME[truth],
                                                      "similarity": sim, "steps": steps}
    return -setting("GAME_POINTS_PAIR_STEP", 1.0) * steps, {"right": False, "truth": DEPTH_NAME[truth], "steps": steps}


def odd_base(answer):
    """
    What a right Odd One Out pick is worth: GAME_POINTS_ODD_WEIGHT times the Family Ties points for how related the
    round's odd one (``roi_b``) is to the rest. The closer they are, the harder it was to tell.
    """
    odd = answer.roi_b.taxon if answer.roi_b_id and answer.roi_b else None
    depth = true_depth(odd, game.group_taxon(answer.grid_group)) if odd is not None and answer.grid_group else None
    return PAIR_POINTS[depth if depth is not None and depth < 3 else -1] * setting("GAME_POINTS_ODD_WEIGHT", 1.5)


def odd_truth(answer):
    """(points, detail) for an Odd One Out pick on a validated beetle, or None if it can't be told."""
    ok = game.score_odd(answer.roi.taxon, answer.grid_rank, answer.grid_group).get(answer.grid_rank)
    if ok is None:
        return None
    base = odd_base(answer)
    detail = {"right": ok, "rank": answer.grid_rank, "worth": round(base, 2)}
    if ok:
        return base, detail
    return -base * setting("GAME_POINTS_ODD_WRONG_FACTOR", 1.25), detail


def grid_tiles(answer):
    """The Beetles of a grid answer in the order shown (None where one is gone); recompute loads them for all at once."""
    cached = getattr(answer, "_grid_tiles", None)
    if cached is None:
        found = Beetles.objects.select_related("taxon").in_bulk([uuid.UUID(str(t)) for t in answer.tiles or []])
        cached = answer._grid_tiles = [found.get(uuid.UUID(str(t))) for t in answer.tiles or []]
    return cached


def select_truth(answer):
    """(points, detail) for a Select all grid, or None if it holds no validated member to score against."""
    result = game.score_select(grid_tiles(answer), answer.picks, answer.grid_rank, answer.grid_group)
    if not result["members"]:
        return None
    depth = game.RANKS.index(answer.grid_rank) - 1 if answer.grid_rank in game.RANKS else -1
    share = setting("GAME_POINTS_SELECT_WEIGHT", 2.0) * PAIR_POINTS[depth] / result["members"]
    points = share * (result["right"] - setting("GAME_POINTS_SELECT_WRONG", 1.5) * result["wrong"])
    detail = {k: result[k] for k in ("right", "wrong", "missed", "members", "perfect", "tiles")}
    return points, dict(detail, rank=answer.grid_rank, worth=round(share * result["members"], 2))


def odd_consensus(answer, votes, judges, model_refs):
    """
    (points, detail) for an Odd One Out pick on a beetle not validated yet: like a name on it, scored by how far the
    judges' names for it, and what proven experts or a trusted model say, agree that it is not one of the group at
    the round's rank. Never negative.
    """
    rank, group = answer.grid_rank, (answer.grid_group or {}).get(answer.grid_rank, "")
    if not rank or not group:
        return 0.0, {"agreement": {}}
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
    base = odd_base(answer)
    points = setting("GAME_POINTS_CONSENSUS_CAP", 0.6) * base * max(0.0, c)
    detail = {"agreement": {rank: round(c, 3)}}
    reference = game_reference.reference_for(answer, votes, judges, model_refs).get(rank)
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


RATINGS_CACHE = "game:ratings:v1"


def cached_ratings():
    """ratings(), kept for a few minutes: used while playing, where a slightly old table is fine."""
    from django.core.cache import cache
    table = cache.get(RATINGS_CACHE)
    if table is None:
        table = ratings()
        cache.set(RATINGS_CACHE, table, setting("GAME_RATINGS_CACHE_SECONDS", 300))
    return table


def ratings():
    """
    {player_id: (rating, accuracy, judged)} from the first time each player saw each validated beetle, including
    beetles validated after they answered (validated_later).
    The rating is a cautious estimate of their accuracy (the lower end of a Wilson interval), so a few lucky
    answers don't make anyone an authority.
    """
    tallies = defaultdict(lambda: [0, 0])
    first = set()
    rows = (
        GameAnswer.objects.filter(Q(is_check=True) | Q(validated_later=True), is_retry=False, skipped=False,
                                  score_hold=False)
        .exclude(mode="select")   # a grid is many judgements at once: not counted until #381 says how
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


def consensus_points(answer, votes, judges):
    """(points, detail) for an answer on a beetle not validated yet: agreement only, never negative."""
    claims = game.implied_labels(answer)
    if not claims:
        return 0.0, {"agreement": {}}
    cap = setting("GAME_POINTS_CONSENSUS_CAP", 0.6)
    c = agreement(answer.player_id, claims, votes, judges)
    if answer.mode == "classify":
        points = sum(cap * RANK_POINTS[r] * max(0.0, c[r]) for r in claims)
    else:
        # partial credit per rung: agreement on "same tribe" earns the tribe rung even if the genus is disputed
        depth = PAIR_DEPTH[answer.pair_answer]
        points, below = 0.0, 0.0
        for d in range(depth + 1):
            step = PAIR_POINTS[d] - below
            below = PAIR_POINTS[d]
            points += cap * step * max(0.0, c.get(RANKS[d], 0.0))
    return points, {"agreement": {r: round(v, 3) for r, v in c.items()}}


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
    if basis in (AnswerPoints.Basis.TRUTH, AnswerPoints.Basis.CONSENSUS, AnswerPoints.Basis.NONE) and not answer.skipped \
            and not answer.score_hold:
        bonus = setting("GAME_POINTS_PARTICIPATION", 0.5)
        points += bonus
        detail = dict(detail, participation=bonus)
    return points, basis, detail


def _score(answer, votes_for, judges, model_refs):
    if answer.score_hold:
        return 0.0, AnswerPoints.Basis.NONE, {"held": True}
    if answer.skipped and answer.mode in ("odd", "select"):
        # the grid games reward saying you're not sure over guessing (#369, #370)
        return setting("GAME_POINTS_ODD_SKIP", 0.25), AnswerPoints.Basis.UNSURE, {}
    if answer.skipped or (answer.mode == "pair" and answer.pair_answer == "unsure"):
        return -setting("GAME_POINTS_UNSURE", 0.25), AnswerPoints.Basis.UNSURE, {}
    if answer.mode == "select":
        scored = select_truth(answer)
        if scored:
            return scored[0], AnswerPoints.Basis.TRUTH, scored[1]
        return 0.0, AnswerPoints.Basis.NONE, {}
    if answer.mode == "odd":
        if is_truth(answer.roi):
            scored = odd_truth(answer)
            if scored:
                return scored[0], AnswerPoints.Basis.TRUTH, scored[1]
            return 0.0, AnswerPoints.Basis.NONE, {}
        points, detail = odd_consensus(answer, votes_for(answer.roi_id), judges, model_refs)
        return points, AnswerPoints.Basis.CONSENSUS, detail
    retry = setting("GAME_POINTS_RETRY_FACTOR", 0.5) if answer.is_retry else 1.0
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


def _with_reference(answer, reference, detail):
    """
    Agreement points per rank, raised to the reference's where the answer matches what proven experts or a trusted
    model say (game_reference). The best of the two per rank, so nothing is counted twice; never negative.
    """
    claims = game.implied_labels(answer)
    cap = setting("GAME_POINTS_CONSENSUS_CAP", 0.6)
    agreed = {r: cap * RANK_POINTS[r] * max(0.0, detail["agreement"].get(r, 0.0)) for r in claims}
    matched = game_reference.reference_points(claims, reference, RANK_POINTS)
    points = sum(max(agreed.get(r, 0.0), matched.get(r, 0.0)) for r in claims)
    shown = {r: {"name": name, "source": source, "match": r in matched}
             for r, (name, source) in reference.items() if r in claims}
    return points, dict(detail, reference=shown)


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
    # Select all taps count too, a little less than a name (game.tap_votes), unless the player also named it
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
        .filter(Q(roi__bbox_is_validated=True) | Q(validated_later=True))
        .select_related("roi__taxon", "roi_b__taxon")
    )
    if player_ids is not None:
        answers = answers.filter(player_id__in=list(player_ids))
    fields = ["validated_later", "ref_subfamily", "ref_tribe", "ref_genus", "ref_species", *[f"correct_{r}" for r in RANKS]]
    changed = []
    for ans in answers:
        before = [getattr(ans, f) for f in fields]
        results = None
        if is_truth(ans.roi):
            if ans.mode == "classify":
                results = game.score_classification({r: getattr(ans, r) for r in RANKS}, ans.roi.taxon)
            elif ans.mode == "odd":
                results = game.score_odd(ans.roi.taxon, ans.grid_rank, ans.grid_group) if ans.grid_rank else None
            elif ans.roi_b is not None and is_truth(ans.roi_b) and ans.pair_answer in PAIR_DEPTH:
                results = game.score_pair(ans.pair_answer, ans.roi.taxon, ans.roi_b.taxon)
        if results is not None:
            t = ans.roi.taxon
            ans.validated_later = True
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
    from django.core.cache import cache
    sync_late_truth(player_ids)
    table = ratings()
    cache.set(RATINGS_CACHE, table, setting("GAME_RATINGS_CACHE_SECONDS", 300))
    judges = Judges(table)
    answers = GameAnswer.objects.select_related(
        "roi__taxon", "roi__image_asset", "roi_b__taxon", "roi_b__image_asset",
    ).order_by("player_id", "answered_at", "index")
    if player_ids is not None:
        answers = answers.filter(player_id__in=list(player_ids))
    answers = list(answers)
    grids = [a for a in answers if a.mode == "select"]
    if grids:   # every Select all beetle in one query
        found = Beetles.objects.select_related("taxon").in_bulk({uuid.UUID(str(t)) for a in grids for t in a.tiles or []})
        for a in grids:
            a._grid_tiles = [found.get(uuid.UUID(str(t))) for t in a.tiles or []]
    open_rois = {a.roi_id for a in answers if not is_truth(a.roi)}
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
    roi_ids = {answer.roi_id}
    votes = votes_on(roi_ids) if not is_truth(answer.roi) else {}
    judges = Judges(cached_ratings()) if votes else _NoJudges()
    model_refs = game_reference.model_references(roi_ids) if not is_truth(answer.roi) else {}
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
