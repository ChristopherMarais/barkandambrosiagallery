"""
Beetle ID game: picking items, scoring answers, player statistics and label consensus.

Four modes:
  classify  one region of interest (ROI) is shown; the player names its subfamily,
            tribe, genus and species, stopping at any rank.
  pair      two ROIs are shown; the player says the deepest rank they share.
  odd       four or six ROIs are shown; all but one share a name at some rank, and the
            player picks the one that doesn't belong (Odd One Out).
  select    nine ROIs are shown; the player taps every one of a named group (Select all).

Some items in every round are "checks": items with a validated answer
(``Beetles.bbox_is_validated``). Only checks are scored, and the player is never told
which items they were. Answers on the other items are the labels we collect.

Items are matched to players by difficulty (RoiDifficulty): newer or weaker players
get easier images, and the target rises as they play. Some checks are aimed at the
branches a player has been labelling, so they get the chance to prove themselves
there (see game_trust.py for how expertise and trusted labels work).

The views live in game_views.py; this module has no request handling.
"""
import math
import random
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Exists, F, OuterRef, Q
from django.utils import timezone

from .models import Beetles, GameAnswer, GameRound, RoiDifficulty

RANKS = ("subfamily", "tribe", "genus", "species")

# Pair answers ordered by depth: the index is the deepest shared rank (-1 = none shared).
PAIR_DEPTH = {"different": -1, "subfamily": 0, "tribe": 1, "genus": 2, "species": 3}


def game_setting(name, default):
    """A game setting: a superuser's override from the Scoring page (game_tuning), else settings.py, else ``default``."""
    from .game_tuning import overrides
    found = overrides()
    if name in found:
        return found[name]
    return getattr(settings, name, default)


# ---------------------------------------------------------------------------
# Item pools
# ---------------------------------------------------------------------------
# A usable reference taxon has at least subfamily and genus. This also drops the
# handful of malformed rows in the species list whose columns are shifted (their
# "subfamily" is a species epithet and genus is blank).
COMPLETE_TAXON = ~Q(subfamily__isnull=True) & ~Q(subfamily="") & ~Q(genus__isnull=True) & ~Q(genus="")


def playable_rois():
    """ROIs that can be shown: a live bounding box on a live image with a file."""
    return (
        Beetles.objects.filter(
            is_deleted=False,
            bbox_x__isnull=False,
            bbox_y__isnull=False,
            bbox_width__gt=0,
            bbox_height__gt=0,
            image_asset__isnull=False,
            image_asset__is_deleted=False,
        )
        .exclude(image_asset__image_file="")
        .exclude(image_asset__image_file__isnull=True)
    )


def reported():
    """
    An Exists() for "a player reported this beetle and no curator has dealt with it yet": the report is open and
    the beetle hasn't been validated since. Such beetles stay out of the game until then.
    """
    from .models import GameReport

    return Exists(
        GameReport.objects.filter(roi_id=OuterRef("pk"), status=GameReport.Status.OPEN)
        .filter(Q(roi__bbox_validated_at__isnull=True) | Q(roi__bbox_validated_at__lt=F("created_at")))
    )


def check_rois():
    """
    ROIs with a validated, well-formed label to score against. ROIs with an open
    player report are left out until staff have looked at them.
    """
    return playable_rois().filter(bbox_is_validated=True, taxon__isnull=False).exclude(
        Q(taxon__subfamily="") | Q(taxon__subfamily__isnull=True)
        | Q(taxon__genus="") | Q(taxon__genus__isnull=True)
    ).filter(~reported())


def open_rois():
    """ROIs whose label has not been validated: the ones we collect labels for. Reported ones wait for a curator."""
    return playable_rois().filter(bbox_is_validated=False).filter(~reported())


def _random_ids(qs, n):
    """
    Up to n ids from qs in random order.

    Starts at a random UUID and walks the primary key index, wrapping around, instead
    of ORDER BY random(), which has to sort the whole table. Beetles ids are random
    UUIDs, so the window is a random sample.
    """
    if n <= 0:
        return []
    pivot = uuid.uuid4()
    ids = list(qs.filter(id__gte=pivot).order_by("id").values_list("id", flat=True)[:n])
    if len(ids) < n:
        ids += list(qs.filter(id__lt=pivot).order_by("id").values_list("id", flat=True)[: n - len(ids)])
    random.shuffle(ids)
    return ids


def _seen(player, mode, is_check):
    """Subquery of ROIs this player already answered in this mode."""
    return GameAnswer.objects.filter(player=player, mode=mode, is_check=is_check).values("roi_id")


def revealed_ids(player):
    """
    Validated ROIs whose answer this player has been shown in round feedback: every
    scored item, and every validated partner in a pair, with every other photo of the same specimen. They are never
    scored for this player again, so feedback can't be memorised into a better score.
    """
    ids = set(GameAnswer.objects.filter(player=player, is_check=True).values_list("roi_id", flat=True))
    # the validated partner of a pair, and the odd one an Odd One Out round was built around
    ids |= set(
        GameAnswer.objects.filter(player=player, mode__in=["pair", "odd"], roi_b__isnull=False)
        .values_list("roi_b_id", flat=True)
    )
    # a grid at species names the rest's species too ("the rest: Xyleborus affinis", "tap every Xyleborus affinis")
    for tiles in (GameAnswer.objects.filter(player=player, mode__in=["odd", "select"], grid_rank="species")
                  .values_list("tiles", flat=True)):
        ids |= {uuid.UUID(str(t)) for t in tiles or []}
    # every other photo of the same specimen (#386): once its name was shown, any photo of it tests memory, not skill
    from django.db.models.functions import Lower, Trim

    by_specimen = Beetles.objects.annotate(specimen=Lower(Trim("depicts_specimen")))
    specimens = set(by_specimen.filter(id__in=ids).exclude(specimen="").values_list("specimen", flat=True)) - {None}
    if specimens:
        ids |= set(by_specimen.filter(specimen__in=specimens).values_list("id", flat=True))
    return ids


# ---------------------------------------------------------------------------
# Difficulty
# ---------------------------------------------------------------------------
UNKNOWN_DIFFICULTY = 0.5


def target_difficulty(player):
    """
    The difficulty this player's next items should sit around, from 0 (easy) to 1.

    Mostly their reliability (PlayerScore.rating, a cautious estimate of how often they are right), so experts get
    hard beetles and novices easy ones, plus a little for every finished round so it keeps creeping up.
    How hard a beetle is comes from how often other players get it right (update_difficulty); how hard a
    Family Ties pair is also depends on how close the two beetles are (RELATION_DIFFICULTY). GAME_DIFFICULTY_*.
    """
    from .models import PlayerScore

    rounds = GameRound.objects.filter(player=player, finished_at__isnull=False).count()
    skill = PlayerScore.objects.filter(player=player).values_list("rating", flat=True).first() or 0.0
    target = (
        game_setting("GAME_DIFFICULTY_START", 0.2)
        + game_setting("GAME_DIFFICULTY_PER_ROUND", 0.02) * rounds
        + game_setting("GAME_DIFFICULTY_SKILL_WEIGHT", 0.3) * skill
    )
    return min(game_setting("GAME_DIFFICULTY_MAX", 0.9), target)


def _difficulties(ids):
    known = {
        d.roi_id: d.value for d in RoiDifficulty.objects.filter(roi_id__in=ids)
    }
    return {i: (known.get(i) if known.get(i) is not None else UNKNOWN_DIFFICULTY) for i in ids}


def _pick_near(candidates, n, target):
    """Pick n of the candidate ids, favouring those whose difficulty is near target."""
    if len(candidates) <= n:
        return list(candidates)
    diff = _difficulties(candidates)
    pool = list(candidates)
    chosen = []
    for _ in range(n):
        weights = [math.exp(-((diff[i] - target) / 0.2) ** 2) + 1e-3 for i in pool]
        pick = random.choices(pool, weights=weights)[0]
        pool.remove(pick)
        chosen.append(pick)
    return chosen


def _sample(qs, n, target, seen=None, exclude=(), allow_seen=True):
    """
    n ids from qs near the target difficulty, preferring ones the player hasn't seen.
    Falls back to seen items when the pool is too small to fill the round, unless
    ``allow_seen`` is off (scored items are never repeated).
    """
    if n <= 0:
        return []
    oversample = game_setting("GAME_CANDIDATE_OVERSAMPLE", 6)
    fresh = qs.exclude(id__in=list(exclude))
    if seen is not None:
        fresh = fresh.exclude(id__in=seen)
    ids = _pick_near(_random_ids(fresh, n * oversample), n, target)
    if len(ids) < n and allow_seen:
        rest = qs.exclude(id__in=list(exclude) + ids)
        ids += _pick_near(_random_ids(rest, (n - len(ids)) * oversample), n - len(ids), target)
    return ids


def update_difficulty(roi_ids):
    """Recompute game_difficulty for these ROIs from all answers on them."""
    roi_ids = list(set(roi_ids))
    rows = defaultdict(lambda: {"ok": 0, "n": 0, "answers": 0, "genus": defaultdict(int)})
    for ans in GameAnswer.objects.filter(roi_id__in=roi_ids, skipped=False, mode="classify"):
        row = rows[ans.roi_id]
        row["answers"] += 1
        for r in RANKS:
            ok = getattr(ans, f"correct_{r}")
            if ok is not None:
                row["n"] += 1
                row["ok"] += int(ok)
        if ans.genus:
            row["genus"][ans.genus.lower()] += 1
    for roi_id in roi_ids:
        row = rows.get(roi_id)
        if not row or not row["answers"]:
            continue
        if row["n"]:
            # Scored item: smoothed error rate across judged ranks.
            value = 1 - (row["ok"] + 1) / (row["n"] + 2)
        elif row["genus"]:
            # Unvalidated item: how much players disagree on the genus.
            value = 1 - max(row["genus"].values()) / sum(row["genus"].values())
        else:
            continue
        RoiDifficulty.objects.update_or_create(
            roi_id=roi_id, defaults={"game_difficulty": round(value, 4), "game_answers": row["answers"]}
        )


# ---------------------------------------------------------------------------
# Round building
# ---------------------------------------------------------------------------
def check_ratio(player, mode):
    """
    Share of check items for this player's next round.

    New players get more checks until their reliability can be estimated, then fewer
    so most of their effort goes into new labels.
    """
    scored = GameAnswer.objects.filter(
        player=player, mode=mode, is_check=True, skipped=False, is_retry=False
    ).count()
    if scored < game_setting("GAME_CALIBRATION_CHECKS", 20):
        return game_setting("GAME_CHECK_RATIO_NEW", 0.6)
    return game_setting("GAME_CHECK_RATIO_KNOWN", 0.2)


def _split_round(player, mode, size):
    """How many check and open items go into a round of ``size``."""
    n_checks = max(1, round(size * check_ratio(player, mode)))
    return n_checks, size - n_checks


def _fill(n_checks, n_open, pick_checks, pick_open):
    """Pick checks and open items, topping up from the other pool when one runs short."""
    checks = pick_checks(n_checks)
    opens = pick_open(n_open + (n_checks - len(checks)))
    if len(opens) < n_open:
        checks += pick_checks(n_open - len(opens), exclude=checks)
    return checks, opens


def focus_filter(player):
    """
    Checks aimed at the branches this player has been labelling but hasn't proven
    themselves in yet, so their labels there can become trusted. None if there are none.
    """
    from .game_trust import skills_for

    recent = (
        GameAnswer.objects.filter(player=player, mode="classify", is_check=False, skipped=False)
        .order_by("-answered_at").values_list("tribe", "genus")[:100]
    )
    proven = {(s.rank, s.branch.lower()) for s in skills_for(player) if s.proven}
    genera = {g for _, g in recent if g and ("species", g.lower()) not in proven}
    tribes = {t for t, _ in recent if t and ("genus", t.lower()) not in proven}
    if not genera and not tribes:
        return None
    return Q(taxon__genus__in=genera) | Q(taxon__tribe__in=tribes)


def player_focus(player):
    """
    (rank, value) when the player has chosen to see only one subfamily, tribe or genus and their level still
    allows it (levels can go down), else None.
    """
    from .game_levels import FOCUS_PERK, for_player
    from .models import GamePreference

    pref = GamePreference.objects.filter(player=player).first()
    if pref is None or not pref.focus_rank or not pref.focus_value:
        return None
    if FOCUS_PERK[pref.focus_rank] not in for_player(player)["perks"]:
        return None
    return pref.focus_rank, pref.focus_value


def _focused(qs, focus):
    return qs.filter(**{f"taxon__{focus[0]}__iexact": focus[1]}) if focus else qs


def pools(player):
    """
    The beetles to choose from: (validated, not validated). With a focus, only that part of the tree, as long as it
    has beetles left in both pools; otherwise everything, so the feed never runs dry because of a focus.
    """
    focus = player_focus(player)
    checks, opens = _focused(check_rois(), focus), _focused(open_rois(), focus)
    if focus and not (checks.exists() and opens.exists()):
        return check_rois(), open_rois()
    return checks, opens


def peer_rois(player, pool):
    """
    Beetles in ``pool`` that a few other players have already named (1 to GAME_PEER_MAX_OTHERS of them) and this
    player has not: showing them to more people is how a name gets agreed on.
    """
    counts = (
        GameAnswer.objects.filter(skipped=False, roi__in=pool).exclude(player=player).exclude(mode__in=["odd", "select"])
        .values("roi_id").annotate(n=Count("player", distinct=True))
        .filter(n__lte=game_setting("GAME_PEER_MAX_OTHERS", 4)).values("roi_id")
    )
    return pool.filter(id__in=counts).exclude(id__in=GameAnswer.objects.filter(player=player).values("roi_id"))


def build_classify_items(player, size, fresh_only=False):
    n_checks, n_open = _split_round(player, "classify", size)
    check_pool, open_pool = pools(player)
    target = target_difficulty(player)
    revealed = list(revealed_ids(player))
    seen_open = _seen(player, "classify", False)
    focus = focus_filter(player)

    def pick_checks(n, exclude=()):
        exclude = [c["a"] for c in exclude] + revealed
        ids = []
        n_focus = n // 2 if focus is not None else 0
        if n_focus:
            ids = _sample(check_pool.filter(focus), n_focus, target, exclude=exclude, allow_seen=False)
        ids += _sample(check_pool, n - len(ids), target, exclude=exclude + ids, allow_seen=False)
        return [{"a": str(i), "b": None, "check": True} for i in ids]

    def pick_open(n):
        # about half of them beetles others have named, so names get a second and third opinion
        n_peer = round(n * game_setting("GAME_PEER_SHARE", 0.5))
        ids = _sample(peer_rois(player, open_pool), n_peer, target, allow_seen=False) if n_peer else []
        ids += _sample(open_pool, n - len(ids), target, seen_open, exclude=ids, allow_seen=not fresh_only)
        return [{"a": str(i), "b": None, "check": False} for i in ids]

    checks, opens = _fill(n_checks, n_open, pick_checks, pick_open)
    # Beetles they got wrong before come back now and then, so they can learn them (see retry_ids)
    retries = [{"a": str(i), "b": None, "check": True, "retry": True} for i in retry_ids(player, len(checks))]
    if retries:
        checks = retries + checks[len(retries):] if len(checks) > len(retries) else retries
    return checks + opens


def retry_ids(player, room):
    """
    Validated beetles this player got wrong, ready to be shown again: last seen at least GAME_RETRY_AFTER_DAYS
    ago, not yet answered right since, and shown again at most GAME_RETRY_MAX times. At most
    GAME_RETRY_PER_BATCH of them, never more than ``room``.
    """
    want = min(game_setting("GAME_RETRY_PER_BATCH", 1), room)
    if want <= 0:
        return []
    cutoff = timezone.now() - timedelta(days=game_setting("GAME_RETRY_AFTER_DAYS", 2))
    last, retries = {}, defaultdict(int)
    rows = (
        GameAnswer.objects.filter(player=player, mode="classify", is_check=True, skipped=False, score_hold=False)
        .order_by("answered_at").values("roi_id", "answered_at", "is_retry", *[f"correct_{r}" for r in RANKS])
    )
    for row in rows:   # the latest answer on each beetle wins
        last[row["roi_id"]] = (row["answered_at"], any(row[f"correct_{r}"] is False for r in RANKS))
        retries[row["roi_id"]] += int(row["is_retry"])
    candidates = [
        roi_id for roi_id, (when, wrong) in last.items()
        if wrong and when <= cutoff and retries[roi_id] < game_setting("GAME_RETRY_MAX", 3)
    ]
    if not candidates:
        return []
    usable = list(check_rois().filter(id__in=candidates).values_list("id", flat=True))
    random.shuffle(usable)
    return usable[:want]


# How hard a Family Ties pair is by how closely related the two beetles are: telling apart two species of one genus
# is much harder than two subfamilies.
RELATION_DIFFICULTY = {"different": 0.1, "subfamily": 0.35, "tribe": 0.55, "genus": 0.75, "species": 0.85}


def _relation_order(target, table=RELATION_DIFFICULTY):
    """The relations (or ranks) to try, the ones nearest the player's target difficulty most likely first."""
    order, pool = [], dict(table)
    while pool:
        names = list(pool)
        weights = [math.exp(-((pool[n] - target) / 0.25) ** 2) + 0.05 for n in names]
        pick = random.choices(names, weights=weights)[0]
        order.append(pick)
        del pool[pick]
    return order


def _partner_for(anchor, target, exclude=()):
    """
    A validated ROI to pair with ``anchor``, at a relation (same species / genus / tribe / subfamily / different)
    chosen at random but leaning towards the player's difficulty: close relatives for experts, distant ones for
    novices (RELATION_DIFFICULTY).

    For an unvalidated anchor its current (unchecked) label is only used to aim the
    pairing; the answer is what we record.
    """
    pool = check_rois().exclude(id=anchor.id).exclude(id__in=list(exclude))
    if anchor.image_asset_id:
        pool = pool.exclude(image_asset_id=anchor.image_asset_id)
    taxon = anchor.taxon

    def one(qs):
        ids = _sample(qs, 1, target)
        return ids[0] if ids else None

    if taxon is None:
        return one(pool)

    # relation: (filter, whether the anchor has the ranks the filter needs)
    relations = {
        "species": (Q(taxon_id=taxon.id), True),
        "genus": (Q(taxon__genus=taxon.genus) & ~Q(taxon_id=taxon.id), bool(taxon.genus)),
        "tribe": (Q(taxon__tribe=taxon.tribe) & ~Q(taxon__genus=taxon.genus),
                  bool(taxon.tribe and taxon.genus)),
        "subfamily": (Q(taxon__subfamily=taxon.subfamily) & ~Q(taxon__tribe=taxon.tribe),
                      bool(taxon.subfamily and taxon.tribe)),
        "different": (~Q(taxon__subfamily=taxon.subfamily), bool(taxon.subfamily)),
    }
    for rel in _relation_order(target):
        condition, usable = relations[rel]
        if usable:
            partner = one(pool.filter(condition))
            if partner:
                return partner
    return one(pool)


def stuck_rois(player, pool):
    """
    Beetles in ``pool`` that players tried to name but nobody could take to species (they stopped early or
    skipped), and this player hasn't paired yet. Family Ties against known beetles at least narrows down what
    they are not.
    """
    tried = GameAnswer.objects.filter(mode="classify", roi__in=pool).values("roi_id")
    named = GameAnswer.objects.filter(mode="classify", roi__in=pool, skipped=False).exclude(species="").values("roi_id")
    paired = GameAnswer.objects.filter(player=player, mode="pair").values("roi_id")
    return pool.filter(id__in=tried).exclude(id__in=named).exclude(id__in=paired)


def build_pair_items(player, size, fresh_only=False):
    n_checks, n_open = _split_round(player, "pair", size)
    check_pool, open_pool = pools(player)
    target = target_difficulty(player)
    revealed = list(revealed_ids(player))
    seen_open = _seen(player, "pair", False)

    def make_pairs(anchor_qs, n, seen, is_check, exclude=()):
        items = []
        exclude = [c["a"] for c in exclude]
        if is_check:
            anchor_ids = _sample(anchor_qs, n, target, exclude=exclude + revealed, allow_seen=False)
        else:
            # about half of them beetles nobody could name, so Family Ties narrows down what they are not
            n_stuck = round(n * game_setting("GAME_STUCK_SHARE", 0.5))
            anchor_ids = _sample(stuck_rois(player, anchor_qs), n_stuck, target, allow_seen=False) if n_stuck else []
            anchor_ids += _sample(anchor_qs, n - len(anchor_ids), target, seen, list(exclude) + anchor_ids,
                                  allow_seen=not fresh_only)
        for anchor in Beetles.objects.select_related("taxon").filter(id__in=anchor_ids):
            # A scored pair must not lean on a partner whose label the player has been shown.
            partner = _partner_for(anchor, target, revealed if is_check else ())
            if partner is None:
                continue
            items.append({
                "a": str(anchor.id), "b": str(partner), "check": is_check,
                "flip": random.random() < 0.5,
            })
        return items

    checks, opens = _fill(
        n_checks, n_open,
        lambda n, exclude=(): make_pairs(check_pool, n, None, True, exclude),
        lambda n: make_pairs(open_pool, n, seen_open, False),
    )
    return checks + opens


# ---------------------------------------------------------------------------
# Odd One Out (#369)
# ---------------------------------------------------------------------------
# How hard it is to spot the odd one by the rank at which it differs: another subfamily stands out, another species of
# the same genus hardly at all.
ODD_RANK_DIFFICULTY = {"subfamily": 0.15, "tribe": 0.4, "genus": 0.65, "species": 0.85}


def odd_tile_count(level):
    """Four beetles (2x2) to choose from, and six (2x3) from level GAME_ODD_SIX_FROM_LEVEL."""
    return 6 if level >= game_setting("GAME_ODD_SIX_FROM_LEVEL", 5) else 4


def odd_open_count(level, tiles):
    """
    How many of an item's beetles are not validated: a share that grows from GAME_ODD_OPEN_SHARE_START at level 1 to
    GAME_ODD_OPEN_SHARE_END at the top, of all but the odd one, always leaving the odd one and one other validated.
    """
    from .game_levels import LEVELS

    start = game_setting("GAME_ODD_OPEN_SHARE_START", 0.25)
    end = game_setting("GAME_ODD_OPEN_SHARE_END", 0.5)
    share = start + (end - start) * (level - 1) / (len(LEVELS) - 1)
    return max(0, min(tiles - 2, round((tiles - 1) * share)))


def lineage(taxon, rank):
    """{rank: name} from subfamily down to ``rank`` (species as "Genus species"), or None if any of them is blank."""
    out = {}
    for r in RANKS[: RANKS.index(rank) + 1]:
        if r == "species":
            value = f"{taxon.genus} {taxon.species}" if taxon.genus and taxon.species else ""
        else:
            value = (getattr(taxon, r, "") or "").strip()
        if not value:
            return None
        out[r] = value
    return out


def rank_q(rank, value, prefix="taxon__"):
    """Q for beetles whose taxon is ``value`` at ``rank`` (species values are "Genus species")."""
    if rank == "species":
        genus, _, species = value.partition(" ")
        return Q(**{f"{prefix}genus__iexact": genus, f"{prefix}species__iexact": species})
    return Q(**{f"{prefix}{rank}__iexact": value})


def _named_at(rank):
    """Q for beetles whose taxon has a name at ``rank`` (and a genus, for species)."""
    if rank == "species":
        return ~Q(taxon__species="") & ~Q(taxon__species__isnull=True)
    return ~Q(**{f"taxon__{rank}": ""}) & ~Q(**{f"taxon__{rank}__isnull": True})


def _predicted(rank, value, low, high):
    """
    Q for unvalidated beetles a classifier puts at ``value`` at ``rank`` with a confidence in [low, high): what it
    said for that rank, or else what its species implies, with the species' confidence.
    """
    if rank == "species":
        return rank_q(rank, value, "predictions__taxon__") & Q(predictions__confidence__gte=low,
                                                                predictions__confidence__lt=high)
    said = Q(**{f"predictions__rank_confidence__{rank}__value__iexact": value,
                f"predictions__rank_confidence__{rank}__confidence__gte": low,
                f"predictions__rank_confidence__{rank}__confidence__lt": high})
    implied = (Q(**{f"predictions__rank_confidence__{rank}__isnull": True}) & rank_q(rank, value, "predictions__taxon__")
               & Q(predictions__confidence__gte=low, predictions__confidence__lt=high))
    return said | implied


def _distinct_photos(ids, taken, limit):
    """
    Up to ``limit`` of the ids, in order, each on a photo not used yet (``taken``: ImageAsset ids, which grows with
    the ones kept), so no two beetles of an item come from one photo and give the odd one away by its background.
    """
    if limit <= 0 or not ids:
        return []
    photo = dict(Beetles.objects.filter(id__in=ids).values_list("id", "image_asset_id"))
    out = []
    for i in ids:
        if len(out) >= limit:
            break
        if photo.get(i) is not None and photo[i] not in taken:
            taken.add(photo[i])
            out.append(i)
    return out


# The AI beetles in a grid (Odd One Out, Select all): unvalidated beetles a classifier puts in the group. Every grid
# has at least one it is sure about (a sure call makes a hard, informative round) and one it is unsure about (could be
# anything: hard or very easy), as well as validated ones; more fill out bigger grids. Below GAME_AI_UNSURE_BELOW is
# "unsure", from GAME_AI_SURE_FROM up "sure", and the band between them fills the rest.
def ai_bands():
    sure = game_setting("GAME_AI_SURE_FROM", 0.9)
    unsure = game_setting("GAME_AI_UNSURE_BELOW", 0.6)
    return (sure, 1.01), (0.0, unsure), (unsure, sure)


def ai_required():
    """The sure + unsure minimum applies once predictions exist (before any upload a grid is validated beetles only)."""
    from .models import ModelPrediction
    return game_setting("GAME_GRID_REQUIRE_AI", True) and ModelPrediction.objects.exists()


def _ai_beetles(open_pool, rank, value, n_open, target, avoid, photos, required):
    """
    ``n_open`` (at least 2 when required) unvalidated beetles the classifier puts at ``value``: one sure, one unsure,
    then the rest from all bands. None when a required sure or unsure one cannot be found.
    """
    sure_band, unsure_band, middle_band = ai_bands()

    def pick(band, n):
        if n <= 0:
            return []
        q = open_pool.filter(_predicted(rank, value, *band)).exclude(id__in=avoid).distinct()
        return _distinct_photos(_sample(q, n * 2, target, allow_seen=False), photos, n)

    sure, unsure = pick(sure_band, 1), pick(unsure_band, 1)
    if required and not (sure and unsure):
        return None
    chosen = sure + unsure
    for band in (sure_band, unsure_band, middle_band):   # fill out bigger grids, any confidence
        if len(chosen) >= n_open:
            break
        more = pick(band, n_open - len(chosen))
        chosen += more
        avoid = set(avoid) | set(more)
    return chosen[:max(n_open, 2 if required else 0)]


def _odd_item(tiles, n_open, target, deepest, check_pool, open_pool, avoid, required=False):
    """One Odd One Out item, or None when no part of the tree has enough beetles for it."""
    for rank in _relation_order(target, {r: d for r, d in ODD_RANK_DIFFICULTY.items()
                                         if RANKS.index(r) <= RANKS.index(deepest)}):
        anchors = _sample(check_pool.filter(_named_at(rank)).exclude(id__in=avoid), 3, target, allow_seen=False)
        for anchor in Beetles.objects.select_related("taxon").filter(id__in=anchors):
            group = lineage(anchor.taxon, rank) if anchor.taxon else None
            if group is None:
                continue
            same = rank_q(rank, group[rank])
            photos = {anchor.image_asset_id}
            # The odd one: validated, so there is always a known answer; a near relative (the same parent) on harder rounds
            odd_pool = check_pool.filter(_named_at(rank)).exclude(same).exclude(id__in=avoid)
            parent = RANKS[RANKS.index(rank) - 1] if rank != "subfamily" else None
            near = parent and random.random() < min(0.9, game_setting("GAME_ODD_NEAR_FLOOR", 0.3) + target)
            odd = _distinct_photos(_sample(odd_pool.filter(rank_q(parent, group[parent])), 3, target, allow_seen=False),
                                   photos, 1) if near else []
            odd = odd or _distinct_photos(_sample(odd_pool, 3, target, allow_seen=False), photos, 1)
            if not odd:
                continue
            # The rest: AI beetles (at least one sure and one unsure, ai_bands), then validated ones
            opens = _ai_beetles(open_pool, rank, group[rank], max(n_open, 2) if required else n_open, target, avoid,
                                photos, required)
            if opens is None:
                continue
            need = tiles - 2 - len(opens)
            rest = _distinct_photos(
                _sample(check_pool.filter(same).exclude(id__in=avoid).exclude(id=anchor.id), need * 3, target,
                        allow_seen=True), photos, need)
            shown = [anchor.id, *rest, *opens, odd[0]]
            if len(shown) < tiles:
                continue
            random.shuffle(shown)
            return {"a": str(odd[0]), "b": None, "check": True, "mode": "odd",
                    "tiles": [str(i) for i in shown], "rank": rank, "group": group}
    return None


def build_odd_items(player, size, fresh_only=False):
    """
    Odd One Out: each item shows four beetles (six at higher levels) of which all but one share a name at one rank,
    and the player picks the one that doesn't belong. The rank follows the player's open ranks (game_levels rank steps)
    and their target difficulty: subfamily first, then tribe, genus and species, and on harder rounds the odd one is a
    near relative (the same tribe, say, but another genus).

    The odd one and at least one of the rest are always validated, so every item has a known answer. Of the rest, at
    least one is a beetle the classifier is sure belongs and one it is unsure about (ai_bands), more as the player
    rises (GAME_ODD_OPEN_SHARE_*):
    a player who picks one of those says it does not belong, which is scored later by agreement, like a name.
    Beetles whose answer the player has been shown are never used again for them (revealed_ids).
    """
    from .game_levels import for_player, rank_unlock

    info = for_player(player)
    answered = GameAnswer.objects.filter(player=player, skipped=False).count()
    deepest = rank_unlock(info["level"], answered, bool(info.get("granted")))["rank"]
    tiles = odd_tile_count(info["level"])
    n_open = odd_open_count(info["level"], tiles)
    check_pool, open_pool = pools(player)
    target = target_difficulty(player)
    avoid = set(revealed_ids(player))
    required = ai_required()
    items = []
    for _ in range(size * 2):
        if len(items) >= size:
            break
        item = _odd_item(tiles, n_open, target, deepest, check_pool, open_pool, avoid, required)
        if item is None:
            break
        avoid.update(uuid.UUID(t) for t in item["tiles"])
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Select all (#370)
# ---------------------------------------------------------------------------
SELECT_TILES = 9


def select_open_count(level, members):
    """
    How many beetles in a Select all grid are not validated: one at level 1, up to three at the top, never so many
    that the grid holds fewer validated non-members than validated members (so tapping everything always loses).
    """
    from .game_levels import LEVELS

    wanted = 1 + round(2 * (level - 1) / (len(LEVELS) - 1))
    return max(0, min(wanted, SELECT_TILES - 2 * members))


def _select_item(n_open_at, target, deepest, check_pool, open_pool, avoid, required=False):
    """One Select all item, or None when no part of the tree has enough beetles for it."""
    for rank in _relation_order(target, {r: d for r, d in ODD_RANK_DIFFICULTY.items()
                                         if RANKS.index(r) <= RANKS.index(deepest)}):
        # a few more anchors than Odd One Out tries: a grid needs three members of one group
        anchors = _sample(check_pool.filter(_named_at(rank)).exclude(id__in=avoid), 5, target, allow_seen=False)
        for anchor in Beetles.objects.select_related("taxon").filter(id__in=anchors):
            group = lineage(anchor.taxon, rank) if anchor.taxon else None
            if group is None:
                continue
            same = rank_q(rank, group[rank])
            photos = {anchor.image_asset_id}
            # 3 or 4 validated members (a third to under half of the grid), so tapping everything never pays; 3 when
            # the grid must also hold a sure and an unsure AI beetle (two more), so non-members still outnumber members
            members = [anchor.id] + _distinct_photos(
                _sample(check_pool.filter(same).exclude(id__in=avoid).exclude(id=anchor.id), 9, target, allow_seen=True),
                photos, 2 if required else random.choice((2, 3)))
            if len(members) < 3:
                continue
            n_open = max(2, n_open_at(len(members))) if required else n_open_at(len(members))
            # AI beetles: at least one the classifier is sure is in the group and one it is unsure about (ai_bands)
            opens = _ai_beetles(open_pool, rank, group[rank], n_open, target, avoid, photos, required) if n_open else []
            if opens is None:
                continue
            # the rest: validated beetles of other groups at this rank; near relatives (the same parent) on harder rounds
            need = SELECT_TILES - len(members) - len(opens)
            others = check_pool.filter(_named_at(rank)).exclude(same).exclude(id__in=avoid)
            parent = RANKS[RANKS.index(rank) - 1] if rank != "subfamily" else None
            near = parent and random.random() < min(0.9, game_setting("GAME_ODD_NEAR_FLOOR", 0.3) + target)
            rest = _distinct_photos(_sample(others.filter(rank_q(parent, group[parent])), need * 2, target, allow_seen=True),
                                    photos, need) if near else []
            rest += _distinct_photos(_sample(others.exclude(id__in=rest), need * 3, target, allow_seen=True),
                                     photos, need - len(rest))
            shown = [*members, *opens, *rest]
            if len(rest) < len(members) or len(shown) < SELECT_TILES:
                continue
            random.shuffle(shown)
            return {"a": str(anchor.id), "b": None, "check": True, "mode": "select",
                    "tiles": [str(i) for i in shown], "rank": rank, "group": group}
    return None


def build_select_items(player, size, fresh_only=False):
    """
    Select all: nine beetles and a group to find ("Tap every Platypodinae"). The rank follows the player's open ranks
    and difficulty, like Odd One Out; three or four of the nine are validated members, the rest validated beetles of
    other groups (near relatives on harder rounds), plus beetles nobody has validated that a classifier puts in the
    group: at least one it is sure about and one it is unsure about (ai_bands), up to three as players rise. Taps on those are recorded, never scored. Beetles whose answer the player has been shown are
    never used again for them (revealed_ids).
    """
    from .game_levels import for_player, rank_unlock

    info = for_player(player)
    answered = GameAnswer.objects.filter(player=player, skipped=False).count()
    deepest = rank_unlock(info["level"], answered, bool(info.get("granted")))["rank"]
    check_pool, open_pool = pools(player)
    target = target_difficulty(player)
    avoid = set(revealed_ids(player))
    required = ai_required()
    items = []
    for _ in range(size * 2):
        if len(items) >= size:
            break
        item = _select_item(lambda members: select_open_count(info["level"], members), target, deepest,
                            check_pool, open_pool, avoid, required)
        if item is None:
            break
        avoid.update(uuid.UUID(t) for t in item["tiles"])
        items.append(item)
    return items


def resumable_round(player, mode):
    """The player's latest unfinished round in this mode, if recent enough to pick up again."""
    since = timezone.now() - timedelta(hours=game_setting("GAME_RESUME_HOURS", 12))
    return (
        GameRound.objects.filter(player=player, mode=mode, finished_at__isnull=True, started_at__gte=since)
        .order_by("-started_at").first()
    )


def spread(items):
    """
    Put the checks at even spacing among the open items, with a random start, instead of a plain shuffle.

    The player sees one continuous feed, so scored items should turn up now and then, not in a clump and not
    in a predictable rhythm: the gaps between them are the same on average but the first one is random.
    """
    checks = [i for i in items if i["check"]]
    opens = [i for i in items if not i["check"]]
    random.shuffle(checks)
    random.shuffle(opens)
    if not checks or not opens:
        return checks + opens
    total = len(items)
    # positions of the checks: evenly spread over the batch, shifted by a random fraction of one gap
    gap = total / len(checks)
    offset = random.random() * gap
    slots = {min(total - 1, int(offset + k * gap)) for k in range(len(checks))}
    k = 0
    while len(slots) < len(checks):          # two checks landed on one slot: take the next free one
        if k not in slots:
            slots.add(k)
        k += 1
    feed, c, o = [], iter(checks), iter(opens)
    for position in range(total):
        feed.append(next(c) if position in slots else next(o))
    return feed


def start_round(player, mode, size=None, fresh_only=False):
    """
    Create a batch of items for the player's continuous feed. Returns None if nothing is playable.

    ``fresh_only`` leaves out unscored items the player has already answered: used to carry on from one batch
    into the next, where running out of new beetles should end the feed rather than repeat what they've seen.
    """
    size = size or game_setting("GAME_ROUND_SIZE", 10)
    if mode == GameRound.Mode.MIXED:
        items = build_mixed_items(player, size, fresh_only=fresh_only)
    else:
        items = build(mode, player, size, fresh_only)
    if not items:
        return None
    return GameRound.objects.create(player=player, mode=mode, items=spread(items))


BUILDERS = {"pair": "build_pair_items", "odd": "build_odd_items", "select": "build_select_items",
            "classify": "build_classify_items"}


def build(game_key, player, size, fresh_only=False):
    """Items for one game (looked up by name, so each builder can be swapped out on its own)."""
    return globals()[BUILDERS[game_key]](player, size, fresh_only=fresh_only)


def play_mode(player, info=None):
    """
    Which game the player plays: one they chose, or "both", a mix of every game they have. New players have only
    Similarity (the easiest); level 2 opens Odd One Out and the choice, level 4 Identification (game_levels.GAMES).
    A chosen game the player no longer has (levels can go down) falls back to the mix.
    """
    from .game_levels import CHOOSE_GAME, for_player, games
    from .models import GamePreference

    info = info or for_player(player)
    available = games(info["perks"])
    if CHOOSE_GAME not in info["perks"]:
        return available[0] if len(available) == 1 else "both"
    pref = GamePreference.objects.filter(player=player).values_list("play_mode", flat=True).first()
    return pref if pref in available else "both"


def build_mixed_items(player, size, fresh_only=False):
    """
    One feed of every game the player has, mixed at random. Beginners see mostly Similarity and experts mostly
    Identification, with Odd One Out beside them (game_levels.game_shares); a player who chose one game sees only that
    one. In the mix, a game that runs out of beetles is filled in by the others. Every item carries its own "mode".
    """
    from .game_levels import for_player, game_shares, games

    info = for_player(player)
    chosen = play_mode(player, info)
    if chosen in BUILDERS:
        items = build(chosen, player, size, fresh_only)
        for it in items:
            it["mode"] = chosen
        return items
    shares = game_shares(info["level"], games(info["perks"]))
    plan = defaultdict(int)
    for game_key in random.choices(list(shares), weights=list(shares.values()), k=size):
        plan[game_key] += 1
    items, full = [], []
    for game_key in shares:
        got = build(game_key, player, plan[game_key], fresh_only) if plan[game_key] else []
        for it in got:
            it["mode"] = game_key
        items += got
        if len(got) == plan[game_key]:
            full.append(game_key)   # had beetles to spare, so it can fill in for the others
    shown = {i for it in items for i in _item_ids(it)}
    for game_key in sorted(full, key=lambda g: -shares[g]):
        if len(items) >= size:
            break
        for it in build(game_key, player, size - len(items), fresh_only):
            ids = _item_ids(it)
            if shown.isdisjoint(ids):   # never the same beetle twice in one batch
                it["mode"] = game_key
                items.append(it)
                shown.update(ids)
    return items


def _item_ids(item):
    """Every beetle an item shows."""
    return set(item.get("tiles") or []) | {item["a"]} | ({item["b"]} if item.get("b") else set())


def _in_background(player_ids):
    """Queue a recompute for these players on the Celery worker; do it here if the queue can't be reached."""
    from django.db import transaction

    from .game_scoring import recompute
    from .tasks import recompute_game_players_task

    def queue():
        try:
            recompute_game_players_task.apply_async(args=[list(player_ids)], retry=False)
        except Exception:
            recompute(list(player_ids))

    transaction.on_commit(queue)


def close_idle_rounds(player, idle_minutes=10):
    """
    Finish the player's feed batches that were left open (they closed the tab, or their phone went to sleep),
    so their answers reach their skills and the difficulty of the images without waiting for them to come back.
    """
    cutoff = timezone.now() - timedelta(minutes=idle_minutes)
    for rnd in GameRound.objects.filter(player=player, finished_at__isnull=True, started_at__lt=cutoff):
        last = rnd.answers.order_by("-answered_at").values_list("answered_at", flat=True).first()
        if last is None or last < cutoff:
            finish_round(rnd)


def finish_round(rnd):
    """Close a round and refresh everything derived from its answers."""
    from .game_trust import recompute_skills

    from .game_scoring import recompute

    from .game_scoring import players_sharing_beetles, sync_late_truth

    if rnd.finished_at is None:
        rnd.finished_at = timezone.now()
        rnd.save(update_fields=["finished_at"])
    sync_late_truth([rnd.player_id])
    recompute_skills(rnd.player)
    update_difficulty(rnd.answers.values_list("roi_id", flat=True))
    # the player now; everyone who answered the same unvalidated beetles too, since their agreement points move with
    # this (in the background in production, so finishing stays quick however many players there are)
    open_ids = rnd.answers.filter(is_check=False).values_list("roi_id", flat=True)
    others = players_sharing_beetles(rnd.player_id, open_ids)
    if others and game_setting("GAME_RECOMPUTE_IN_BACKGROUND", False):
        recompute([rnd.player_id])
        _in_background(others)
    else:
        recompute([rnd.player_id, *others])
    from .game_trust import auto_apply_expert_labels
    auto_apply_expert_labels(list(rnd.answers.filter(is_check=False).values_list("roi_id", flat=True)))
    from .game_discoveries import find
    find([rnd.player_id])


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def _norm(value):
    return (value or "").strip().lower()


def rank_values(taxon):
    """{rank: value} for a Taxon; species is 'genus species' so the epithet alone never matches."""
    if taxon is None:
        return {r: "" for r in RANKS}
    species = f"{_norm(taxon.genus)} {_norm(taxon.species)}" if taxon.genus and taxon.species else ""
    return {
        "subfamily": _norm(taxon.subfamily),
        "tribe": _norm(taxon.tribe),
        "genus": _norm(taxon.genus),
        "species": species,
    }


def answer_values(answer):
    """{rank: value} for a classification answer dict (same shape as rank_values)."""
    species = ""
    if answer.get("species") and answer.get("genus"):
        species = f"{_norm(answer['genus'])} {_norm(answer['species'])}"
    return {
        "subfamily": _norm(answer.get("subfamily")),
        "tribe": _norm(answer.get("tribe")),
        "genus": _norm(answer.get("genus")),
        "species": species,
    }


def score_classification(answer, truth_taxon):
    """
    Per-rank correctness of a classification against the validated taxon.
    None for a rank the player did not answer or that has no reference value.
    """
    given = answer_values(answer)
    truth = rank_values(truth_taxon)
    return {
        r: (given[r] == truth[r]) if given[r] and truth[r] else None
        for r in RANKS
    }


def shared_ranks(taxon_a, taxon_b):
    """
    Per rank: do the two taxa share it? True/False, or None when either lacks the rank.
    Sharing a deeper rank implies sharing the ones above it even where those are blank.
    """
    a, b = rank_values(taxon_a), rank_values(taxon_b)
    same = {r: (a[r] == b[r]) if a[r] and b[r] else None for r in RANKS}
    for i, r in enumerate(RANKS):
        if same[r] is None and any(same[deeper] for deeper in RANKS[i + 1:]):
            same[r] = True
    return same


def score_pair(pair_answer, taxon_a, taxon_b):
    """
    Per-rank correctness of a pair answer. "Same genus" claims the pair shares
    subfamily, tribe and genus but not species; each of those claims is scored.
    """
    if pair_answer not in PAIR_DEPTH:
        return {r: None for r in RANKS}
    depth = PAIR_DEPTH[pair_answer]
    truth = shared_ranks(taxon_a, taxon_b)
    return {
        r: ((i <= depth) == truth[r]) if truth[r] is not None else None
        for i, r in enumerate(RANKS)
    }


def group_taxon(group):
    """A taxon-like object for an Odd One Out group ({rank: name} down to its rank), blank below it."""
    from types import SimpleNamespace

    genus_species = group.get("species", "")
    return SimpleNamespace(subfamily=group.get("subfamily", ""), tribe=group.get("tribe", ""),
                           genus=group.get("genus", ""), species=genus_species.partition(" ")[2])


def score_odd(picked_taxon, rank, group):
    """
    Per-rank correctness of an Odd One Out pick: at the round's rank, True when the picked beetle is not one of the
    group (rightly picked out) and False when it is; None everywhere else, or when it has no name at that rank.
    """
    out = {r: None for r in RANKS}
    mine, theirs = rank_values(picked_taxon).get(rank, ""), _norm((group or {}).get(rank))
    if rank in out and mine and theirs:
        out[rank] = mine != theirs
    return out


def score_select(tiles, picks, rank, group):
    """
    How a Select all grid went. ``tiles`` are the Beetles shown (None for one that is gone), ``picks`` the places
    tapped. Each validated beetle is "right" (a member, tapped), "wrong" (not one, tapped), "missed" (a member left
    out) or "clear" (not one, left out); one nobody has validated is "vote" when tapped and "" otherwise.
    Returns {"tiles": [state, ...], "right", "wrong", "missed", "members", "perfect"}.
    """
    theirs, picked = _norm((group or {}).get(rank)), set(picks or [])
    states, count = [], {"right": 0, "wrong": 0, "missed": 0, "clear": 0, "members": 0}
    for i, roi in enumerate(tiles):
        mine = rank_values(roi.taxon).get(rank, "") if roi is not None and roi.taxon else ""
        if roi is None or not roi.bbox_is_validated or roi.is_deleted or not mine or not theirs:
            states.append("vote" if i in picked else "")
            continue
        member = mine == theirs
        count["members"] += member
        state = ("right" if member else "wrong") if i in picked else ("missed" if member else "clear")
        count[state] += 1
        states.append(state)
    return dict(count, tiles=states, perfect=count["wrong"] == 0 and count["missed"] == 0 and count["members"] > 0)


# ---------------------------------------------------------------------------
# Player statistics
# ---------------------------------------------------------------------------
def _rank_counts():
    """Aggregate expressions: correct and judged counts for each rank."""
    exprs = {}
    for r in RANKS:
        exprs[f"{r}_ok"] = Count("id", filter=Q(score_hold=False, **{f"correct_{r}": True}))
        exprs[f"{r}_n"] = Count("id", filter=Q(score_hold=False, **{f"correct_{r}__isnull": False}))
    return exprs


def _accuracy(row):
    ok = sum(row[f"{r}_ok"] for r in RANKS)
    n = sum(row[f"{r}_n"] for r in RANKS)
    return (ok / n if n else None), n


def player_summary(player):
    """Items labelled and overall accuracy (share of judged ranks correct on checks)."""
    labelled = GameAnswer.objects.filter(player=player, skipped=False).count()
    row = GameAnswer.objects.filter(player=player, is_check=True, is_retry=False).aggregate(**_rank_counts())
    accuracy, judged = _accuracy(row)
    min_judged = game_setting("GAME_MIN_JUDGED_FOR_ACCURACY", 10)
    return {
        "labelled": labelled,
        "accuracy": accuracy if judged >= min_judged else None,
    }


def week_start(now=None):
    """Midnight on the Monday of the current week (server time): the leaderboard's "this week" begins here."""
    today = (now or timezone.now()).astimezone(timezone.get_current_timezone()).date()
    monday = today - timedelta(days=today.weekday())
    return timezone.make_aware(datetime(monday.year, monday.month, monday.day))


def leaderboard(limit=50, sort="labelled", since=None):
    """The players ranked by beetles labelled (or accuracy). ``since`` limits it to answers from then on."""
    in_period = GameAnswer.objects.all() if since is None else GameAnswer.objects.filter(answered_at__gte=since)
    labelled = {
        row["player"]: row["n"]
        for row in in_period.filter(skipped=False)
        .values("player").annotate(n=Count("id"))
    }
    names = dict(
        get_user_model().objects.filter(id__in=labelled).values_list("id", "username")
    )
    min_judged = game_setting("GAME_MIN_JUDGED_FOR_ACCURACY", 10)
    accuracy = {}
    for row in (
        in_period.filter(is_check=True, is_retry=False)
        .values("player").annotate(**_rank_counts())
    ):
        acc, judged = _accuracy(row)
        if judged >= min_judged:
            accuracy[row["player"]] = acc

    rows = [
        {"player_id": pid, "username": names.get(pid, "?"), "labelled": n, "accuracy": accuracy.get(pid)}
        for pid, n in labelled.items()
    ]
    if sort == "accuracy":
        rows.sort(key=lambda r: (r["accuracy"] is None, -(r["accuracy"] or 0), -r["labelled"]))
    else:
        rows.sort(key=lambda r: (-r["labelled"], r["username"]))
    for i, row in enumerate(rows, start=1):
        row["position"] = i
    return rows[:limit]


def player_reliability(player_ids=None):
    """
    {player_id: {mode: {rank: {"ok", "n", "accuracy", "weight"}}}} from check answers,
    plus mode "all" combining every game.

    ``weight`` is the Laplace-smoothed accuracy (ok + 1) / (n + 2): a player with no
    checks at a rank counts as a coin flip, and the weight moves toward their real
    accuracy as they answer more checks.
    """
    out = defaultdict(dict)
    qs = GameAnswer.objects.filter(is_check=True, is_retry=False).exclude(mode="select")   # see game_scoring.ratings
    if player_ids is not None:
        qs = qs.filter(player_id__in=list(player_ids))
    for row in qs.values("player", "mode").annotate(**_rank_counts()):
        out[row["player"]][row["mode"]] = row
    result = {}
    for pid, modes in out.items():
        result[pid] = {}
        for mode in ("classify", "pair", "odd", "select", "all"):
            result[pid][mode] = {}
            for r in RANKS:
                if mode == "all":
                    ok = sum(m[f"{r}_ok"] for m in modes.values())
                    n = sum(m[f"{r}_n"] for m in modes.values())
                else:
                    ok = modes.get(mode, {}).get(f"{r}_ok", 0)
                    n = modes.get(mode, {}).get(f"{r}_n", 0)
                result[pid][mode][r] = {
                    "ok": ok, "n": n,
                    "accuracy": ok / n if n else None,
                    "weight": (ok + 1) / (n + 2),
                }
    return result


def default_weight():
    return {r: {"ok": 0, "n": 0, "accuracy": None, "weight": 0.5} for r in RANKS}


# ---------------------------------------------------------------------------
# Consensus on unvalidated ROIs
# ---------------------------------------------------------------------------
class Vote(dict):
    """A player's {rank: value} for one beetle, with how much it counts: 1 for a name, less for a Select all tap."""

    def __init__(self, labels, weight=1.0):
        super().__init__(labels)
        self.weight = weight


def tap_weight():
    """How much a Select all tap counts towards a beetle's name, against a direct identification (GAME_SELECT_TAP_WEIGHT)."""
    return float(game_setting("GAME_SELECT_TAP_WEIGHT", 0.8))


def showing(roi_ids):
    """Q for grid answers whose grid showed any of these beetles."""
    q = Q(pk__in=[])
    for rid in {str(r) for r in roi_ids}:
        q |= Q(tiles__contains=[rid])
    return q


def tap_votes(roi_ids=None, voters=None):
    """
    [(roi_id, player_id, Vote)] from Select all grids: tapping a beetle nobody has validated says it is in the grid's
    group, down to the grid's rank (e.g. tribe Xyleborini and genus Xyleborus). Each counts tap_weight() of a name.
    """
    answers = GameAnswer.objects.filter(mode="select", skipped=False).exclude(picks=[])
    if roi_ids is not None:
        answers = answers.filter(showing(roi_ids))
    if voters is not None:
        answers = answers.filter(player_id__in=list(voters))
    rows, wanted = [], {str(r) for r in roi_ids} if roi_ids is not None else None
    for ans in answers.only("player_id", "tiles", "picks", "grid_rank", "grid_group"):
        if ans.grid_rank not in RANKS or not ans.grid_group:
            continue
        labels = {r: ans.grid_group[r] for r in RANKS[: RANKS.index(ans.grid_rank) + 1] if ans.grid_group.get(r)}
        for i in ans.picks or []:
            if isinstance(i, int) and 0 <= i < len(ans.tiles or []) and (wanted is None or ans.tiles[i] in wanted):
                rows.append((ans.tiles[i], ans.player_id, labels))
    if not rows:
        return []
    open_ids = {str(i) for i in Beetles.objects.filter(id__in={r for r, _, _ in rows}, bbox_is_validated=False)
                .values_list("id", flat=True)}
    weight = tap_weight()
    return [(uuid.UUID(r), pid, Vote(labels, weight)) for r, pid, labels in rows if r in open_ids]


def grid_exclusions(answer):
    """
    [(roi_id, rank, value)] a grid answer says beetles nobody has validated are *not* in: in Odd One Out the picked
    beetle is not of the rest's group; in Select all the beetles left untapped are not of the grid's group.
    """
    if answer.skipped or answer.mode not in ("odd", "select") or answer.grid_rank not in RANKS or not answer.grid_group:
        return []
    value = answer.grid_group.get(answer.grid_rank)
    if not value:
        return []
    if answer.mode == "odd":
        roi = answer.roi
        return [(roi.id, answer.grid_rank, value)] if roi is not None and not roi.bbox_is_validated else []
    picked = set(answer.picks or [])
    if not picked:   # tapped nothing: says too little about each beetle
        return []
    untapped = [t for i, t in enumerate(answer.tiles or []) if i not in picked]
    open_ids = Beetles.objects.filter(id__in=untapped, bbox_is_validated=False, is_deleted=False).values_list("id", flat=True)
    return [(rid, answer.grid_rank, value) for rid in open_ids]


def implied_labels(answer):
    """
    The rank values an answer on an unvalidated item says the ROI has.

    Classify: what the player picked. Pair: the ranks the player says the unvalidated
    ROI shares with its validated partner, taken from the partner's taxon. "Different
    subfamily" and "not sure" say nothing positive, so they imply nothing, and nor does a grid answer here: it is about
    other beetles than its own ``roi`` (Select all taps count through tap_votes, what a grid says a beetle is not
    through grid_exclusions).
    Species values are "Genus species".
    """
    if answer.skipped or answer.mode in ("odd", "select"):
        return {}
    if answer.mode == "classify":
        values = {
            "subfamily": answer.subfamily, "tribe": answer.tribe, "genus": answer.genus,
            "species": f"{answer.genus} {answer.species}" if answer.genus and answer.species else "",
        }
        return {r: v for r, v in values.items() if v}
    depth = PAIR_DEPTH.get(answer.pair_answer, -1)
    partner = answer.roi_b.taxon if answer.roi_b_id and answer.roi_b else None
    if depth < 0 or partner is None:
        return {}
    values = {
        "subfamily": partner.subfamily, "tribe": partner.tribe, "genus": partner.genus,
        "species": f"{partner.genus} {partner.species}" if partner.genus and partner.species else "",
    }
    return {r: values[r] for r in RANKS[: depth + 1] if values[r]}


def consensus(limit=None, roi_ids=None, voters=None):
    """
    Reliability-weighted votes for each unvalidated ROI that has answers, with the
    trusted-expert verdict from game_trust.

    Returns a list of dicts sorted by number of answers (most first):
    {"roi", "answers", "players", "ranks": {rank: {"value", "support", "votes",
    "trusted", "trusted_votes"}}, "trusted_rank", "taxon"}
    ``support`` is the winning value's share of the total vote weight at that rank.
    ``voters``, when given, limits it to the answers of those players (see game_levels.suggestion_voters).
    """
    from .game_trust import TrustContext

    answers = (
        GameAnswer.objects.filter(is_check=False, skipped=False).exclude(mode__in=["odd", "select"])   # not names
        .select_related("roi", "roi__taxon", "roi_b__taxon")
        .order_by("roi_id", "answered_at")
    )
    if roi_ids is not None:
        answers = answers.filter(roi_id__in=list(roi_ids))
    if voters is not None:
        answers = answers.filter(player_id__in=list(voters))
    answers = list(answers)
    player_ids = {a.player_id for a in answers}
    reliability = player_reliability(player_ids)
    trust = TrustContext(player_ids)

    per_roi = {}
    for ans in answers:
        entry = per_roi.setdefault(ans.roi_id, {
            "roi": ans.roi, "answers": 0, "players": set(), "votes": [],
        })
        entry["answers"] += 1
        entry["players"].add(ans.player_id)
        labels = implied_labels(ans)
        if labels:
            entry["votes"].append((ans.player_id, labels))
    # Select all taps: a little lighter than a name (tap_weight), and never enough for an expert's verdict
    taps = tap_votes(roi_ids, voters)
    if taps:
        tapped = Beetles.objects.select_related("taxon").in_bulk({r for r, _, _ in taps})
        reliability.update(player_reliability({p for _, p, _ in taps} - set(reliability)))
        for roi_id, pid, vote in taps:
            if roi_id not in tapped:
                continue
            entry = per_roi.setdefault(roi_id, {"roi": tapped[roi_id], "answers": 0, "players": set(), "votes": []})
            entry["answers"] += 1
            entry["players"].add(pid)
            entry["votes"].append((pid, vote))

    results = []
    for entry in per_roi.values():
        ranks = {}
        for r in RANKS:
            tally, count = defaultdict(float), defaultdict(int)
            display = {}
            for pid, labels in entry["votes"]:
                if r not in labels:
                    continue
                key = labels[r].lower()
                display.setdefault(key, labels[r])
                weights = reliability.get(pid, {}).get("all") or default_weight()
                tally[key] += weights[r]["weight"] * getattr(labels, "weight", 1.0)
                count[key] += 1
            if not tally:
                ranks[r] = None
                continue
            key = max(tally, key=tally.get)
            ranks[r] = {
                "value": display[key],
                "support": tally[key] / sum(tally.values()),
                "votes": count[key],
            }
        verdict = trust.verdict([(p, l) for p, l in entry["votes"] if getattr(l, "weight", 1.0) == 1.0], ranks)
        for r in RANKS:
            if ranks[r]:
                ranks[r].update(verdict["ranks"][r])
        results.append({
            "roi": entry["roi"], "answers": entry["answers"],
            "players": len(entry["players"]), "ranks": ranks, "votes": entry["votes"],
            "rank_list": [(r, ranks[r]) for r in RANKS],
            "trusted_rank": verdict["trusted_rank"],
            "taxon": verdict["taxon"],
        })
    results.sort(key=lambda e: (-e["answers"], str(e["roi"].id)))
    return results[:limit] if limit else results


def specimen_photos(roi, limit=6):
    """
    Other photos of exactly this beetle: ROIs with the same specimen id (depicts_specimen), each on a photo that
    shows only that one beetle, as must the photo of ``roi`` itself (a photo of several beetles can't promise
    which is which). Not deleted, with a box. Used by the "More photos of each beetle" unlock.
    """
    from django.db.models import Count
    from django.db.models.functions import Lower, Trim

    key = (getattr(roi, "depicts_specimen", None) or "").strip()
    if not key or roi.image_asset_id is None:
        return []

    def single_beetle(image_ids):
        rows = (Beetles.objects.filter(image_asset_id__in=image_ids, is_deleted=False)
                .values("image_asset_id").annotate(n=Count("id")))
        return {r["image_asset_id"] for r in rows if r["n"] == 1}

    if roi.image_asset_id not in single_beetle([roi.image_asset_id]):
        return []
    candidates = list(
        Beetles.objects.annotate(specimen=Lower(Trim("depicts_specimen")))
        .filter(specimen=key.lower(), is_deleted=False, image_asset__isnull=False,
                image_asset__is_deleted=False, bbox_x__isnull=False, bbox_width__gt=0)
        .exclude(image_asset_id=roi.image_asset_id).select_related("image_asset").order_by("aspect", "id")
    )
    ok = single_beetle({c.image_asset_id for c in candidates})
    return [c for c in candidates if c.image_asset_id in ok][:limit]
