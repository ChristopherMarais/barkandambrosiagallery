"""
Beetle ID game: picking items, scoring answers, player statistics and label consensus.

Four modes:
  classify  one region of interest (ROI) is shown; the player names its subfamily,
            tribe, genus and species, stopping at any rank.
  pair      two ROIs are shown; the player says the deepest rank they share.
  odd       4, 9, 16 or 25 ROIs are shown; all but one to four share a name at some rank,
            and the player picks the ones that don't belong (Odd One Out).
  select    4, 9, 16 or 25 ROIs are shown; the player taps every one of a named group (Select all).
            Both grid games grow with the player, from 4 to 25 and from subfamily to species
            (game_grid_ladder).

Some items in every round are "checks": items with a validated answer
(``Beetles.bbox_is_validated``). Only checks are scored, and the player is never told
which items they were. Answers on the other items are the labels we collect.

Items are matched to players by difficulty (RoiDifficulty): newer or weaker players
get easier images, and the target rises as they play. Some checks are aimed at the
branches a player has been labelling, so they get the chance to prove themselves
there (see game_trust.py for how expertise and trusted labels work).

The views live in game_views.py; this module has no request handling.
"""
import contextvars
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


# Beetles the batch being built must leave out, besides those each builder leaves out itself: the ones already in a
# batch that grows (game_grow, #575). Read where the builders draw their beetles (_random_ids).
avoiding = contextvars.ContextVar("game_avoiding", default=frozenset())


def _random_ids(qs, n):
    """
    Up to n ids from qs in random order.

    Starts at a random UUID and walks the primary key index, wrapping around, instead
    of ORDER BY random(), which has to sort the whole table. Beetles ids are random
    UUIDs, so the window is a random sample.
    """
    if n <= 0:
        return []
    if avoiding.get():
        qs = qs.exclude(id__in=list(avoiding.get()))
    pivot = uuid.uuid4()
    ids = list(qs.filter(id__gte=pivot).order_by("id").values_list("id", flat=True)[:n])
    if len(ids) < n:
        ids += list(qs.filter(id__lt=pivot).order_by("id").values_list("id", flat=True)[: n - len(ids)])
    random.shuffle(ids)
    return ids


def _seen(player, mode, is_check):
    """Subquery of ROIs this player already answered in this mode."""
    return GameAnswer.objects.filter(player=player, mode=mode, is_check=is_check).values("roi_id")


def reveals(player):
    """
    {roi_id: {"at", "modes"}}: the ROIs whose names this player has been shown after an answer, when last and in which
    games: every scored item, every validated partner in a pair, and every beetle of a grid (the review names them all
    at every rank, #541; a grid at species names them in its prompt too, skipped or not), with every other photo of
    the same specimen (#386: once its name was shown, any photo of it tests memory first).
    """
    out = {}

    def shown(roi_id, at, mode):
        entry = out.setdefault(roi_id, {"at": at, "modes": set()})
        entry["at"] = max(entry["at"], at)
        entry["modes"].add(mode)

    answers = GameAnswer.objects.filter(player=player)
    for roi_id, at, mode in answers.filter(is_check=True).values_list("roi_id", "answered_at", "mode"):
        shown(roi_id, at, mode)
    # the validated partner of a pair, and the odd one an Odd One Out round was built around
    for roi_id, at, mode in (answers.filter(mode__in=["pair", "odd"], roi_b__isnull=False)
                             .values_list("roi_b_id", "answered_at", "mode")):
        shown(roi_id, at, mode)
    for tiles, at, mode in (answers.filter(mode__in=["odd", "select"]).filter(Q(skipped=False) | Q(grid_rank="species"))
                            .values_list("tiles", "answered_at", "mode")):
        for t in tiles or []:
            shown(uuid.UUID(str(t)), at, mode)
    from django.db.models.functions import Lower, Trim

    by_specimen = Beetles.objects.annotate(specimen=Lower(Trim("depicts_specimen"))).exclude(specimen="")
    specimen_of = dict(by_specimen.filter(id__in=list(out)).values_list("id", "specimen"))
    if specimen_of:
        groups = defaultdict(list)
        siblings = by_specimen.filter(specimen__in=set(specimen_of.values()) - {None})
        for roi_id, specimen in siblings.values_list("id", "specimen"):
            groups[specimen].append(roi_id)
        for roi_id, specimen in specimen_of.items():
            if specimen is None:
                continue
            for sibling in groups[specimen]:
                for mode in list(out[roi_id]["modes"]):
                    shown(sibling, out[roi_id]["at"], mode)
    return out


def was_shown(player, roi_ids, since=None):
    """
    Whether this player has been shown the names of any of these ROIs, or of another photo of the same specimen
    (reveals, asked about a few beetles: a handful of EXISTS queries instead of all the player's answers). With
    ``since``, only reveals from then on count.
    """
    ids = same_specimen(roi_ids)
    if not ids:
        return False
    answers = GameAnswer.objects.filter(player=player)
    if since is not None:
        answers = answers.filter(answered_at__gte=since)
    in_grid = Q()
    for i in ids:
        in_grid |= Q(tiles__contains=[str(i)])
    return (answers.filter(is_check=True, roi_id__in=ids).exists()
            or answers.filter(mode__in=["pair", "odd"], roi_b_id__in=ids).exists()
            or answers.filter(mode__in=["odd", "select"]).filter(Q(skipped=False) | Q(grid_rank="species"))
            .filter(in_grid).exists())


def seen_recently(player, roi_ids, now=None):
    """
    Whether naming these ROIs now would only show memory, not expertise (GameAnswer.seen_before): the player was
    shown their names less than GAME_EXPERTISE_RECALL_DAYS ago (#555). Naming a beetle after a longer gap is real
    recall, so it counts again: players can become experts in a taxon whose checked photos they have all seen.
    """
    days = game_setting("GAME_EXPERTISE_RECALL_DAYS", 30)
    return was_shown(player, roi_ids, since=(now or timezone.now()) - timedelta(days=days))


def seen_recently_ids(player, roi_ids, now=None, exclude=None):
    """
    seen_recently, photo by photo: which of these ROIs (UUIDs) the player was shown the names of, or of another photo
    of the same specimen, in the GAME_EXPERTISE_RECALL_DAYS before ``now``. ``exclude``: an answer left out (the one
    being reviewed). A grid's review marks each of these tiles "Seen before" (#600). A few queries for the whole grid.
    """
    from django.db.models.functions import Lower, Trim

    ids = [uuid.UUID(str(i)) for i in roi_ids if i]
    if not ids:
        return set()
    by_specimen = Beetles.objects.annotate(specimen=Lower(Trim("depicts_specimen"))).exclude(specimen="")
    specimen_of = {i: s for i, s in by_specimen.filter(id__in=ids).values_list("id", "specimen") if s}
    photos = defaultdict(set)
    if specimen_of:
        for i, s in by_specimen.filter(specimen__in=set(specimen_of.values())).values_list("id", "specimen"):
            photos[s].add(i)
    family = {i: {i} | photos.get(specimen_of.get(i), set()) for i in ids}
    every = set().union(*family.values())
    days = game_setting("GAME_EXPERTISE_RECALL_DAYS", 30)
    answers = GameAnswer.objects.filter(player=player, answered_at__gte=(now or timezone.now()) - timedelta(days=days))
    if now is not None:
        answers = answers.filter(answered_at__lte=now)
    if exclude is not None:
        answers = answers.exclude(pk=exclude)
    shown = set(answers.filter(is_check=True, roi_id__in=every).values_list("roi_id", flat=True))
    shown |= set(answers.filter(mode__in=["pair", "odd"], roi_b_id__in=every).values_list("roi_b_id", flat=True))
    in_grid = Q()
    for i in every:
        in_grid |= Q(tiles__contains=[str(i)])
    grids = answers.filter(mode__in=["odd", "select"]).filter(Q(skipped=False) | Q(grid_rank="species")).filter(in_grid)
    for tiles in grids.values_list("tiles", flat=True):
        shown |= {uuid.UUID(str(t)) for t in tiles or []} & every
    return {i for i in ids if family[i] & shown}


def same_specimen(roi_ids):
    """These ROIs (UUIDs) and every other photo of the same specimens (#386): showing one names them all."""
    from django.db.models.functions import Lower, Trim

    ids = {uuid.UUID(str(i)) for i in roi_ids}
    if not ids:
        return ids
    by_specimen = Beetles.objects.annotate(specimen=Lower(Trim("depicts_specimen"))).exclude(specimen="")
    specimens = set(by_specimen.filter(id__in=list(ids)).values_list("specimen", flat=True)) - {None}
    if specimens:
        ids |= set(by_specimen.filter(specimen__in=specimens).values_list("id", flat=True))
    return ids


def revealed_ids(player):
    """
    Validated ROIs whose names this player has been shown (reveals). An answer on one of them counts for points but,
    within GAME_EXPERTISE_RECALL_DAYS, not for accuracy or expertise (seen_recently), and it comes back only after a
    while (held_back_ids).
    """
    return set(reveals(player))


# On while a chosen game that came up empty tries again with a wider pool (build_chosen)
widened = contextvars.ContextVar("game_widened", default=False)


def held_back_ids(player, now=None, shown=None):
    """
    The revealed ROIs that are not scored for this player yet: shown in the current sitting (game_relearn.sitting_start,
    GAME_SESSION_GAP_MINUTES), or less than GAME_REVEAL_COOLDOWN_HOURS ago. After that a beetle may come back, so a
    player learns the beetles by playing, but not straight from the answer they just saw. A beetle they got wrong comes
    back only as a retry, at the retry's points (game_relearn). ``shown``: reveals(player).
    """
    from .game_relearn import open_mistakes, sitting_start

    now = now or timezone.now()
    shown = reveals(player) if shown is None else shown
    cutoff = sitting_start(player, now)
    if not widened.get():   # a chosen game that ran short (build_chosen) takes back what was shown before this sitting
        cutoff = min(cutoff, now - timedelta(hours=game_setting("GAME_REVEAL_COOLDOWN_HOURS", 2)))
    return {roi_id for roi_id, entry in shown.items() if entry["at"] >= cutoff} | set(open_mistakes(player))


def shown_in(shown, mode):
    """The revealed ROIs (from reveals) shown in this game: they come back in another game first, if there are others."""
    return {roi_id for roi_id, entry in shown.items() if mode in entry["modes"]}


# ---------------------------------------------------------------------------
# Difficulty
# ---------------------------------------------------------------------------
UNKNOWN_DIFFICULTY = 0.5
DIFFICULTY_FLOOR = 0.05   # easing off never takes the target below this

# An answer on a validated beetle counts as right when nothing in it was wrong and something was judged right; a skip
# or "Not sure" is not right. ANSWERED_RIGHT says the same in SQL (the Scoring page's distributions).
JUDGED = [f"correct_{r}" for r in RANKS]
ANSWERED_RIGHT = (
    Q(skipped=False) & ~Q(correct_subfamily=False) & ~Q(correct_tribe=False) & ~Q(correct_genus=False)
    & ~Q(correct_species=False)
    & (Q(correct_subfamily=True) | Q(correct_tribe=True) | Q(correct_genus=True) | Q(correct_species=True))
)


def answered_right(skipped, judged):
    return not skipped and True in judged and False not in judged


def recent_share_right(player, mode):
    """
    How many of the player's last GAME_DIFFICULTY_RECENT answers on validated beetles in this game were right
    (0 to 1), or None until they have given that many. Held answers (reported photos) are left out.
    """
    n = int(game_setting("GAME_DIFFICULTY_RECENT", 10))
    if n <= 0:
        return None
    rows = list(
        GameAnswer.objects.filter(player=player, mode=mode, score_hold=False)
        .filter(Q(is_check=True) | Q(validated_later=True))
        .order_by("-answered_at").values_list("skipped", *JUDGED)[:n]
    )
    if len(rows) < n:
        return None
    return sum(answered_right(skipped, judged) for skipped, *judged in rows) / n


def target_difficulty(player, mode=None):
    """
    The difficulty this player's next items should sit around, from 0 (easy) to 1.

    Mostly their reliability (PlayerScore.rating, a cautious estimate of how often they are right), so experts get
    hard beetles and novices easy ones, plus a little for every finished round so it keeps creeping up.
    How hard a beetle is comes from how often other players get it right (update_difficulty); how hard a
    Family Ties pair is also depends on how close the two beetles are (RELATION_DIFFICULTY). GAME_DIFFICULTY_*.

    The rating moves slowly, so with a ``mode`` the target also follows how the player is doing in that game right
    now (#492): when under GAME_DIFFICULTY_EASE_BELOW of their recent answers there are right it eases off by
    GAME_DIFFICULTY_EASE (never below DIFFICULTY_FLOOR), and over GAME_DIFFICULTY_PUSH_ABOVE it rises by
    GAME_DIFFICULTY_PUSH (never above the maximum). Struggling players get a breather; strong ones keep being stretched.
    """
    from .models import PlayerScore

    rounds = GameRound.objects.filter(player=player, finished_at__isnull=False).count()
    skill = PlayerScore.objects.filter(player=player).values_list("rating", flat=True).first() or 0.0
    target = (
        game_setting("GAME_DIFFICULTY_START", 0.15)
        + game_setting("GAME_DIFFICULTY_PER_ROUND", 0.005) * rounds
        + game_setting("GAME_DIFFICULTY_SKILL_WEIGHT", 0.7) * skill
    )
    top = game_setting("GAME_DIFFICULTY_MAX", 0.9)
    target = min(top, target)
    share = recent_share_right(player, mode) if mode else None
    if share is not None and share < game_setting("GAME_DIFFICULTY_EASE_BELOW", 0.6):
        target = max(min(target, DIFFICULTY_FLOOR), target - game_setting("GAME_DIFFICULTY_EASE", 0.15))
    elif share is not None and share > game_setting("GAME_DIFFICULTY_PUSH_ABOVE", 0.85):
        target = max(target, min(top, target + game_setting("GAME_DIFFICULTY_PUSH", 0.05)))
    return target


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


def _focused_open(qs, focus):
    """
    Unvalidated beetles in the focus (#375, #340): named in it, or placed in it by the species classifier, whose
    prediction for the focus rank is the focus taxon with at least GAME_FOCUS_AI_MIN confidence at that rank. Where
    an upload gave no confidence for that rank, the top species' own confidence counts: a lower bound for its genus,
    tribe and subfamily. Players are never told why a beetle was chosen, so the model's guess doesn't anchor them.
    """
    from .models import ModelPrediction

    if not focus:
        return qs
    rank, value = focus
    least = game_setting("GAME_FOCUS_AI_MIN", {"subfamily": 0.6, "tribe": 0.6, "genus": 0.5}).get(rank, 0.6)
    said = Q(**{f"rank_confidence__{rank}__value__iexact": value, f"rank_confidence__{rank}__confidence__gte": least})
    top_species = Q(**{f"taxon__{rank}__iexact": value, "confidence__gte": least})
    predicted = ModelPrediction.objects.filter(said | top_species).values("roi_id")
    return qs.filter(Q(**{f"taxon__{rank}__iexact": value}) | Q(id__in=predicted))


def pools(player):
    """
    The beetles to choose from: (validated, not validated). With a focus, only that part of the tree: validated
    beetles by their name, unvalidated ones by their name or the classifier's (_focused_open). A round tops up from
    the other pool when one runs short (_fill); only when both are empty does it fall back to everything, so the
    feed never runs dry because of a focus.
    """
    focus = player_focus(player)
    if not focus:
        return check_rois(), open_rois()
    checks, opens = _focused(check_rois(), focus), _focused_open(open_rois(), focus)
    if not (checks.exists() or opens.exists()):
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
    from . import game_relearn

    n_checks, n_open = _split_round(player, "classify", size)
    check_pool, open_pool = pools(player)
    target = target_difficulty(player, "classify")
    shown = reveals(player)
    held, shown_here = list(held_back_ids(player, shown=shown)), list(shown_in(shown, "classify"))
    seen_open = _seen(player, "classify", False)
    focus = focus_filter(player)

    def pick_checks(n, exclude=()):
        # never one whose name was shown a moment ago; one shown in this game only when there are no others
        exclude = [c["a"] for c in exclude] + held
        ids = []
        n_focus = n // 2 if focus is not None else 0
        if n_focus:
            ids = _sample(check_pool.filter(focus), n_focus, target, shown_here, exclude=exclude)
        ids += _sample(check_pool, n - len(ids), target, shown_here, exclude=exclude + ids)
        return [{"a": str(i), "b": None, "check": True} for i in ids]

    def pick_open(n):
        # about half of them beetles others have named, so names get a second and third opinion
        n_peer = round(n * game_setting("GAME_PEER_SHARE", 0.5))
        ids = _sample(peer_rois(player, open_pool), n_peer, target, allow_seen=False) if n_peer else []
        # placed beetles first; hard, unplaced ones wait for the easier games (game_relearn, #490)
        ids += game_relearn.identification_open(open_pool, n - len(ids), target, seen_open, ids, fresh_only)
        return [{"a": str(i), "b": None, "check": False} for i in ids]

    checks, opens = _fill(n_checks, n_open, pick_checks, pick_open)
    return checks + opens   # beetles they got wrong come back in every game's batches (game_relearn)


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


def _partner_for(anchor, target, exclude=(), relation=None):
    """
    A validated ROI to pair with ``anchor``, at a relation (same species / genus / tribe / subfamily / different)
    chosen at random but leaning towards the player's difficulty: close relatives for experts, distant ones for
    novices (RELATION_DIFFICULTY). ``relation`` is tried first when given (a retry, game_relearn).

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
    for rel in ([relation] if relation in relations else []) + _relation_order(target):
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
    from . import game_relearn

    n_checks, n_open = _split_round(player, "pair", size)
    check_pool, open_pool = pools(player)
    target = target_difficulty(player, "pair")
    shown = reveals(player)
    held, shown_here = list(held_back_ids(player, shown=shown)), list(shown_in(shown, "pair"))
    seen_open = _seen(player, "pair", False)

    def make_pairs(anchor_qs, n, seen, is_check, exclude=()):
        items = []
        exclude = [c["a"] for c in exclude]
        if is_check:   # never one shown a moment ago; one shown in this game only when there are no others
            anchor_ids = _sample(anchor_qs, n, target, shown_here, exclude=exclude + held)
        else:
            # about half of them hard beetles (nobody could name them, players disagree, IBBI-AI is unsure): Family
            # Ties narrows down what they are before anyone has to name them (game_relearn.hard_rois)
            n_stuck = round(n * game_setting("GAME_STUCK_SHARE", 0.5))
            anchor_ids = (_sample(game_relearn.hard_rois(player, anchor_qs), n_stuck, target, allow_seen=False)
                          if n_stuck else [])
            anchor_ids += _sample(anchor_qs, n - len(anchor_ids), target, seen, list(exclude) + anchor_ids,
                                  allow_seen=not fresh_only)
        for anchor in Beetles.objects.select_related("taxon").filter(id__in=anchor_ids):
            # A scored pair must not lean on a partner whose label the player has just been shown.
            partner = _partner_for(anchor, target, held if is_check else ())
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
# The grid games: Odd One Out (#369) and Select all (#370)
# ---------------------------------------------------------------------------
# Every grid is built at the player's step on the grid ladder (game_grid_ladder, #489): 4, 9, 16 or 25 beetles, at a rank
# from subfamily down to species. When the beetles for that are short it falls back to an easier grid, never to none.
GRID_SIZES = (4, 9, 16, 25)   # 25 (5×5) is the most a phone shows comfortably
GRID_ANCHORS = 8   # beetles tried per rank as the heart of a grid: enough for a sparse tree, few enough to stay quick


def odd_open_count(level, tiles, odds=1):
    """
    How many of an item's beetles are not validated: a share that grows from GAME_ODD_OPEN_SHARE_START at level 1 to
    GAME_ODD_OPEN_SHARE_END at the top, of all but the ``odds`` odd ones, always leaving the odd ones and one other
    validated.
    """
    from .game_levels import LEVELS

    start = game_setting("GAME_ODD_OPEN_SHARE_START", 0.25)
    end = game_setting("GAME_ODD_OPEN_SHARE_END", 0.5)
    share = start + (end - start) * (level - 1) / (len(LEVELS) - 1)
    return max(0, min(tiles - odds - 1, round((tiles - odds) * share)))


# Select all by the grid's size: the fewest and most validated members (about a quarter to under half of the grid),
# and its AI beetles at level 1 and at the top level. Validated non-members are never fewer than the members, so
# tapping everything loses while a wrong tap costs more than a member earns (k above 1: GAME_POINTS_CONFIDENCE over 50%).
SELECT_MEMBERS = {4: (1, 2), 9: (3, 4), 16: (5, 7), 25: (7, 10)}
SELECT_AI = {4: (1, 1), 9: (1, 3), 16: (2, 4), 25: (3, 6)}


def select_open_count(level, size):
    """How many AI beetles a Select all grid of ``size`` holds: more as the player rises (SELECT_AI)."""
    from .game_levels import LEVELS

    low, high = SELECT_AI[size]
    return low + round((high - low) * (level - 1) / (len(LEVELS) - 1))


def select_mix(size, members, opens, others, ai):
    """
    (members, AI beetles, others) for a Select all grid of ``size`` from the beetles found: at most ``ai`` AI beetles,
    members within SELECT_MEMBERS, and never fewer others than members. None when what was found can't make one.
    """
    low, high = SELECT_MEMBERS[size]
    a = min(opens, ai)
    fits = [m for m in range(low, min(high, members) + 1) if m <= size - m - a <= others]
    if not fits:
        return None
    m = random.choice(fits)
    return m, a, size - m - a


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
    Q for unvalidated beetles a classifier puts at ``value`` at ``rank`` (at any name when ``value`` is None) with a
    confidence in [low, high): what it said for that rank, or else what its species implies, with the species' confidence.
    """
    if rank == "species":
        named = rank_q(rank, value, "predictions__taxon__") if value else Q()
        return named & Q(predictions__confidence__gte=low, predictions__confidence__lt=high)
    said = Q(**{f"predictions__rank_confidence__{rank}__confidence__gte": low,
                f"predictions__rank_confidence__{rank}__confidence__lt": high})
    implied = (Q(**{f"predictions__rank_confidence__{rank}__isnull": True})
               & Q(predictions__confidence__gte=low, predictions__confidence__lt=high))
    if value:
        said &= Q(**{f"predictions__rank_confidence__{rank}__value__iexact": value})
        implied &= rank_q(rank, value, "predictions__taxon__")
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


# The AI beetles in a grid (Odd One Out, Select all): unvalidated beetles IBBI-AI puts in the group. When its
# predictions allow, a grid holds one it is sure about (a sure call makes a hard, informative round) and one it is
# unsure about (could be anything: hard or very easy), as well as validated ones; more fill out bigger grids. Below
# GAME_AI_UNSURE_BELOW is "unsure", from GAME_AI_SURE_FROM up "sure", and the band between them fills the rest. That pair
# is looked for, not required: where no group has both, the grid is built without them (#489).
def ai_bands():
    sure = game_setting("GAME_AI_SURE_FROM", 0.9)
    unsure = game_setting("GAME_AI_UNSURE_BELOW", 0.6)
    return (sure, 1.01), (0.0, unsure), (unsure, sure)


def ai_preferred():
    """Grids look for a sure and an unsure AI beetle once predictions exist (GAME_GRID_PREFER_AI)."""
    from .models import ModelPrediction
    return game_setting("GAME_GRID_PREFER_AI", True) and ModelPrediction.objects.exists()


def _ai_beetles(open_pool, rank, value, n, target, avoid, photos, pair):
    """
    Up to ``n`` unvalidated beetles IBBI-AI puts at ``value``, each on a photo not used yet. With ``pair``, one it is
    sure about and one it is unsure about first (for a single place, one of either), then any confidence.
    Returns (ids, kinds): each one's "sure", "unsure" or "other".
    """
    sure, unsure, _ = ai_bands()
    chosen, kinds, avoid = [], [], set(avoid)

    def take(band, k, kind):
        if k <= 0:
            return
        q = open_pool.filter(_predicted(rank, value, *band)).exclude(id__in=avoid).distinct()
        got = _distinct_photos(_sample(q, k * 2, target, allow_seen=False), photos, k)
        avoid.update(got)
        chosen.extend(got)
        kinds.extend([kind] * len(got))

    if pair and n >= 2:
        take(sure, 1, "sure")
        take(unsure, 1, "unsure")
    elif pair and n == 1:
        for band, kind in random.sample([(sure, "sure"), (unsure, "unsure")], 2):
            take(band, 1, kind)
            if chosen:
                break
    take((0.0, 1.01), n - len(chosen), "other")
    return chosen, kinds


def _has_pair(kinds, n):
    """Whether the first ``n`` AI beetles are what a grid looks for: a sure and an unsure one (one of either for one)."""
    head = kinds[:n]
    if n <= 0:
        return True
    if n == 1:
        return head[:1] in (["sure"], ["unsure"])
    return "sure" in head and "unsure" in head


class _Grids:
    """One batch's grids of one game, and what they all share: the player's step and the beetles to draw on."""

    def __init__(self, game_key, player, avoid=()):
        from .game_grid_ladder import plan
        from .game_levels import for_player, rank_unlock

        info = for_player(player)
        answered = GameAnswer.objects.filter(player=player, skipped=False).count()
        deepest = rank_unlock(info["level"], answered, bool(info.get("granted")))["rank"]
        self.game, self.level = game_key, info["level"]
        self.plan = plan(player, game_key, deepest, player_focus(player))
        self.check_pool, self.open_pool = pools(player)
        self.target = target_difficulty(player)
        # Beetles whose names the player has just been shown wait a while (held_back_ids); ones shown in this game come
        # back in another game first, so they are used here only when a grid can't be built without them
        shown = reveals(player)
        self.avoid = held_back_ids(player, shown=shown) | {uuid.UUID(str(i)) for i in avoid}
        self.later = shown_in(shown, game_key) - self.avoid
        self.prefer_ai = ai_preferred()
        self.pairs = {}   # rank: whether IBBI-AI's predictions could give a grid there a sure and an unsure beetle

    def items(self, n):
        items = []
        while len(items) < n:
            item = self.item()
            if item is None:
                break
            self.avoid.update(uuid.UUID(t) for t in item["tiles"])
            items.append(item)
        return items

    def item(self):
        """One grid (build), without the beetles shown in this game before if it can be."""
        if self.later:
            avoid, self.avoid = self.avoid, self.avoid | self.later
            try:
                found = self.build()
            finally:
                self.avoid = avoid
            if found is not None:
                return found
        return self.build()

    def build(self):
        """
        One grid at the player's step. When the beetles for it are short, in turn: the same grid without the pair of AI
        beetles, a smaller one at that rank, the nearest other ranks (the shallower first). None when there is nothing.
        """
        size, odds = self.plan["size"], self.plan.get("odds", 1)
        for rank in self.plan["ranks"]:
            pair = self.pair_wanted(rank)
            best = None
            anchors = _sample(self.check_pool.filter(_named_at(rank)).exclude(id__in=self.avoid), GRID_ANCHORS,
                              self.target, allow_seen=False)
            for anchor in Beetles.objects.select_related("taxon").filter(id__in=anchors):
                grid = (self.odd if self.game == "odd" else self.select)(anchor, rank, pair)
                if grid is None:
                    continue
                shape = (grid["size"], len(grid.get("odds", [])), grid["paired"])
                if shape[:2] == (size, odds if self.game == "odd" else 0) and grid["paired"]:
                    return self.finish(grid, rank)
                if best is None or shape > (best["size"], len(best.get("odds", [])), best["paired"]):
                    best = grid
            if best is not None:
                if pair and not best["paired"]:
                    self.pairs[rank] = False   # no group here had both: the rest of the batch doesn't look again
                return self.finish(best, rank)
        return None

    def pair_wanted(self, rank):
        """Look for a sure and an unsure AI beetle at ``rank``? Only when predictions there have both somewhere."""
        if not self.prefer_ai:
            return False
        if rank not in self.pairs:
            sure, unsure, _ = ai_bands()
            self.pairs[rank] = all(self.open_pool.filter(_predicted(rank, None, *band)).exists() for band in (sure, unsure))
        return self.pairs[rank]

    def finish(self, grid, rank):
        tiles = [str(i) for i in grid["tiles"]]
        random.shuffle(tiles)
        item = {"a": str(grid["a"]), "b": None, "check": True, "mode": self.game, "tiles": tiles, "rank": rank,
                "group": grid["group"], "size": len(tiles), "step": self.plan["step"]}
        if "odds" in grid:   # Odd One Out: every odd one ("a" is the first), never sent to the browser (#540)
            item["odds"] = [str(i) for i in grid["odds"]]
        return item

    def sizes(self):
        """The grid sizes this batch may use, biggest first: the step's size and the smaller ones."""
        return [s for s in reversed(GRID_SIZES) if s <= self.plan["size"]]

    def near(self, rank, group):
        """On harder rounds the beetles outside the group are near relatives: Q for the group's parent, else None."""
        parent = RANKS[RANKS.index(rank) - 1] if rank != "subfamily" else None
        if parent and random.random() < min(0.9, game_setting("GAME_ODD_NEAR_FLOOR", 0.3) + self.target):
            return rank_q(parent, group[parent])
        return None

    def odd_ai(self, size, pair, odds=1):
        """AI beetles among the rest of an Odd One Out grid: more as the player rises, two when looking for a pair."""
        n = odd_open_count(self.level, size, odds)
        return min(size - odds - 1, max(n, 2) if pair else n)

    def select_ai(self, size, pair):
        """AI beetles in a Select all grid: more as the player rises, two when looking for a pair and there is room."""
        n = select_open_count(self.level, size)
        return max(n, 2) if pair and size > 4 else n

    def odd(self, anchor, rank, pair):
        """
        The biggest Odd One Out grid up to the step's size and number of odd ones around ``anchor``'s group at
        ``rank``, or None. Short of beetles it hides fewer odd ones first, then shrinks: the ladder's order (#540).
        """
        from .game_grid_ladder import most_odds

        group = lineage(anchor.taxon, rank) if anchor.taxon else None
        if group is None:
            return None
        same = rank_q(rank, group[rank])
        photos = {anchor.image_asset_id}
        most, wanted = self.plan["size"], self.plan.get("odds", 1)
        # The odd ones: validated, so there is always a known answer, each outside the group (they may share another);
        # near relatives (the same parent) on harder rounds
        odd_pool = self.check_pool.filter(_named_at(rank)).exclude(same).exclude(id__in=self.avoid)
        near = self.near(rank, group)
        odds = _distinct_photos(_sample(odd_pool.filter(near), 3 * wanted, self.target, allow_seen=False), photos,
                                wanted) if near else []
        if len(odds) < wanted:
            odds += _distinct_photos(_sample(odd_pool.exclude(id__in=odds), 3 * (wanted - len(odds)), self.target,
                                             allow_seen=False), photos, wanted - len(odds))
        if not odds:
            return None
        # The rest share the group: AI beetles (a sure and an unsure one when there are predictions), then validated
        # ones, at least the anchor
        opens, kinds = _ai_beetles(self.open_pool, rank, group[rank], self.odd_ai(most, pair), self.target, self.avoid,
                                   photos, pair)
        rest = _distinct_photos(
            _sample(self.check_pool.filter(same).exclude(id__in=self.avoid).exclude(id=anchor.id), (most - 2) * 3,
                    self.target, allow_seen=True), photos, most - 2)
        for size in self.sizes():
            for k in range(min(wanted, most_odds(size), len(odds)), 0, -1):
                n = min(len(opens), self.odd_ai(size, pair, k))
                need = size - 1 - k - n
                if len(rest) >= need:
                    return {"a": odds[0], "odds": odds[:k], "tiles": [anchor.id, *rest[:need], *opens[:n], *odds[:k]],
                            "group": group, "size": size, "paired": not pair or _has_pair(kinds, n)}
        return None

    def select(self, anchor, rank, pair):
        """The biggest Select all grid up to the step's size around ``anchor``'s group at ``rank``, or None."""
        group = lineage(anchor.taxon, rank) if anchor.taxon else None
        if group is None:
            return None
        same = rank_q(rank, group[rank])
        photos = {anchor.image_asset_id}
        most = self.plan["size"]
        # Validated members: the anchor and as many more as the biggest grid takes
        members = [anchor.id] + _distinct_photos(
            _sample(self.check_pool.filter(same).exclude(id__in=self.avoid).exclude(id=anchor.id),
                    (SELECT_MEMBERS[most][1] - 1) * 3, self.target, allow_seen=True), photos, SELECT_MEMBERS[most][1] - 1)
        # AI beetles: a sure and an unsure one first when there are predictions. Taps on them are votes, never scored
        opens, kinds = _ai_beetles(self.open_pool, rank, group[rank], self.select_ai(most, pair), self.target, self.avoid,
                                   photos, pair)
        # The rest: validated beetles of other groups at this rank; near relatives (the same parent) on harder rounds
        want = most - SELECT_MEMBERS[most][0]
        others = self.check_pool.filter(_named_at(rank)).exclude(same).exclude(id__in=self.avoid)
        near = self.near(rank, group)
        rest = _distinct_photos(_sample(others.filter(near), want * 2, self.target, allow_seen=True),
                                photos, want) if near else []
        rest += _distinct_photos(_sample(others.exclude(id__in=rest), (want - len(rest)) * 3, self.target, allow_seen=True),
                                 photos, want - len(rest))
        for size in self.sizes():
            mix = select_mix(size, len(members), len(opens), len(rest), self.select_ai(size, pair))
            if mix:
                m, a, k = mix
                return {"a": anchor.id, "tiles": [*members[:m], *opens[:a], *rest[:k]], "group": group, "size": size,
                        "paired": not pair or _has_pair(kinds, a)}
        return None


def build_grid_items(game_key, player, n, avoid=()):
    """``n`` grids of one grid game ("odd" or "select") at the player's step, none showing a beetle in ``avoid``."""
    return _Grids(game_key, player, avoid).items(n)


def build_odd_items(player, size, fresh_only=False, avoid=()):
    """
    Odd One Out: each item shows 4, 9, 16 or 25 beetles of which all but one to four share a name at one rank, and
    the player picks the ones that don't belong. The size, the number of odd ones and the rank follow the player's step
    on the grid ladder (game_grid_ladder): at each rank the grids grow first, then hide more odd ones (#540), then go a
    rank deeper, from subfamily to species; on harder rounds the odd ones are near relatives (the same tribe, say, but
    another genus). The item's "a" is the first odd one and "odds" all of them.

    The odd ones and at least one of the rest are always validated, so every item has a known answer. Of the rest, when
    IBBI-AI's predictions allow, one is a beetle it is sure belongs and one it is unsure about (ai_bands), more as the
    player rises (GAME_ODD_OPEN_SHARE_*): a player who picks one of those says it does not belong, which is scored later
    by agreement, like a name. Beetles whose names the player has just been shown wait a while (held_back_ids).
    """
    return build_grid_items("odd", player, size, avoid)


def build_select_items(player, size, fresh_only=False, avoid=()):
    """
    Select all: 4, 9, 16 or 25 beetles and a group to find ("Tap every Platypodinae"), the size and the rank following the
    player's step on the grid ladder like Odd One Out. About a quarter to under half are validated members
    (SELECT_MEMBERS), validated beetles of other groups at least as many (near relatives on harder rounds), plus beetles
    nobody has validated that IBBI-AI puts in the group (SELECT_AI): a sure and an unsure one when its predictions
    allow. Taps on those are recorded, never scored. Beetles whose names the player has just been shown wait a while
    (held_back_ids).
    """
    return build_grid_items("select", player, size, avoid)


def resumable_round(player, mode):
    """
    The player's latest unfinished round in this mode, if recent enough to pick up again. A batch built ahead of time
    (game_views: nothing answered in it yet) waits while the batch in play before it still has items to answer.
    """
    from django.db.models import Max

    since = timezone.now() - timedelta(hours=game_setting("GAME_RESUME_HOURS", 12))
    latest = list(
        GameRound.objects.filter(player=player, mode=mode, finished_at__isnull=True, started_at__gte=since)
        .order_by("-started_at")[:2]
    )
    if len(latest) == 2 and not latest[0].answers.exists():
        last = latest[1].answers.aggregate(m=Max("index"))["m"]
        if last is not None and last < len(latest[1].items) - 1:
            return latest[1]
    return latest[0] if latest else None


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

    A game the player chose stays that game (#604): when it runs short it widens its own pool (build_chosen), and when
    there is still nothing the feed ends with a line that says so (nothing_to_play), never another game. The round's
    ``notice`` is a line for the page ("" when there is nothing to say); it is not saved, so a reload that picks the
    batch up again doesn't repeat it.
    """
    items, notice = batch_items(player, mode, size, fresh_only)
    if not items:
        return None
    rnd = GameRound.objects.create(player=player, mode=mode, items=items)
    rnd.notice = notice
    return rnd


def batch_items(player, mode, size=None, fresh_only=False, choice=None):
    """
    (items, notice) for a new batch (start_round), in the order shown; ([], "") when nothing is playable. ``choice``:
    build the mixed feed for this game choice instead of the one the player saved, for a batch built before they
    switch to it (game_warm).
    """
    from . import game_relearn

    size = size or game_setting("GAME_ROUND_SIZE", 10)
    notice = ""
    if mode == GameRound.Mode.MIXED:
        items = build_mixed_items(player, size, fresh_only=fresh_only, choice=choice)
    else:
        items = build_chosen(mode, player, size, fresh_only)
    if not items:
        return [], ""
    # spread(), with the player's due mistakes in place of some scored items (#490)
    return game_relearn.feed_with_retries(player, mode, items), notice


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


def build_mixed_items(player, size, fresh_only=False, choice=None):
    """
    One feed of every game the player has, mixed at random. Beginners see mostly Similarity and experts mostly
    Identification, with Odd One Out beside them (game_levels.game_shares); a player who chose one game sees only that
    one (or ``choice``, one they may switch to), never another (#604). In the mix, a game that runs out of beetles is
    filled in by the others. Every item carries its own "mode".
    """
    from .game_levels import for_player, games

    info = for_player(player)
    chosen = choice or play_mode(player, info)
    if chosen in BUILDERS:
        items = build_chosen(chosen, player, size, fresh_only)
        for it in items:
            it["mode"] = chosen
        return items
    return _mix(player, info["level"], games(info["perks"]), size, fresh_only)


def build_chosen(game_key, player, size, fresh_only=False):
    """
    Items for the one game a player chose (#604). When it has nothing, it tries again with a wider pool before giving
    up: validated beetles whose names the player was shown before this sitting come back, though it is less than
    GAME_REVEAL_COOLDOWN_HOURS ago (held_back_ids). Their answers then count for points, not for accuracy or expertise
    (seen_recently). Never another game: when this is empty too, the feed says so (nothing_to_play).
    """
    items = build(game_key, player, size, fresh_only)
    if items:
        return items
    token = widened.set(True)
    try:
        return build(game_key, player, size, fresh_only)
    finally:
        widened.reset(token)


def _mix(player, level, game_keys, size, fresh_only=False):
    """``size`` items of these games by their shares of the feed; one that runs short is filled in by the rest."""
    from .game_levels import game_shares

    shares = game_shares(level, game_keys)
    if not shares:
        return []
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


def nothing_to_play(player, mode=GameRound.Mode.MIXED):
    """
    Why the feed has nothing (new) for this player, in plain words for the page: there are no beetles at all yet, they
    have seen every one, they have seen every one in their focus (``clear_focus``: clearing it would give them more),
    or their games can't use the ones there are (Similarity and the grid games need checked beetles). ``mode`` is the
    page's game, or the mix. A few EXISTS queries; nothing is built.
    """
    from .game_levels import GAME_NAMES, for_player, games

    checks, opens = check_rois(), open_rois()
    if not (checks.exists() or opens.exists()):
        return {"text": "No beetles are ready for the game yet. Please check back soon.", "clear_focus": False}
    answered = GameAnswer.objects.filter(player=player).values("roi_id")
    held = list(held_back_ids(player))

    def new(check_pool, open_pool):   # never answered, or validated and its names not shown to them a moment ago
        return open_pool.exclude(id__in=answered).exists() or check_pool.exclude(id__in=held).exists()

    if not new(checks, opens):
        return {"text": "You've seen every beetle we have. New photos are added regularly.", "clear_focus": False}
    if player_focus(player) and not new(*pools(player)):
        return {"text": "You've seen every beetle in your focus. Clear it to see more.", "clear_focus": True}
    info = for_player(player)
    mode = mode if mode in GAME_NAMES else play_mode(player, info)   # the feed plays the game they chose (#604)
    mine = [mode] if mode in GAME_NAMES else games(info["perks"])
    which = GAME_NAMES[mine[0]] if len(mine) == 1 else "your games"
    if len(mine) == 1 and len(games(info["perks"])) > 1:   # they could switch: say so
        return {"text": f"Not enough beetles for {which} right now. Pick another game, or check back soon.",
                "clear_focus": False}
    return {"text": f"Not enough checked beetles for {which} yet. Please check back soon.", "clear_focus": False}


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


# A feed batch untouched this long was left (the tab closed, the phone asleep, the app killed): the game home
# closes it, and the game page, back after a hide this long, starts afresh rather than carry on (#578)
IDLE_MINUTES = 10


def close_idle_rounds(player, idle_minutes=IDLE_MINUTES):
    """
    Finish the player's feed batches that were left open (they closed the tab, or their phone went to sleep),
    so their answers reach their skills and the difficulty of the images without waiting for them to come back.
    The work is done on the worker (finish_round_later). A batch with nothing answered (one built ahead and never
    reached) has nothing to count, so it is dropped rather than counted as played.
    Returns when the last answer in the batches it closed was given (None if it closed none), for the home's recap.
    """
    cutoff = timezone.now() - timedelta(minutes=idle_minutes)
    latest = None
    for rnd in GameRound.objects.filter(player=player, finished_at__isnull=True, started_at__lt=cutoff):
        last = rnd.answers.order_by("-answered_at").values_list("answered_at", flat=True).first()
        if last is None:
            rnd.delete()
        elif last < cutoff:
            finish_round_later(rnd)
            latest = max(latest or last, last)
    return latest


def finish_round(rnd):
    """Close a round and refresh everything derived from its answers."""
    _close(rnd)
    refresh_round(rnd)


def finish_round_later(rnd):
    """
    Close a round now, and refresh everything derived from its answers on the Celery worker (#494), so the player
    isn't kept waiting at the end of a batch. Done here if the queue can't be reached, and here too where game work
    isn't sent to the worker (GAME_RECOMPUTE_IN_BACKGROUND off, as when developing).
    """
    from django.db import transaction

    from .tasks import finish_game_round_task

    if not game_setting("GAME_RECOMPUTE_IN_BACKGROUND", False):
        finish_round(rnd)
        return
    _close(rnd)

    def queue():
        try:
            finish_game_round_task.apply_async(args=[str(rnd.id)], retry=False)
        except Exception:
            refresh_round(rnd)

    transaction.on_commit(queue)


def _close(rnd):
    if rnd.finished_at is None:
        rnd.finished_at = timezone.now()
        rnd.save(update_fields=["finished_at"])


def refresh_round(rnd):
    """Everything derived from a closed round's answers: skills, image difficulty, scores, applied labels, discoveries."""
    from .game_trust import recompute_skills

    from .game_scoring import recompute

    from .game_scoring import players_sharing_beetles, sync_late_truth

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
    Below the first wrong one nothing is judged (#530): "same genus" for beetles of two subfamilies is wrong, and its
    "but not the same species" is no right answer, only a consequence of the mistake.
    """
    if pair_answer not in PAIR_DEPTH:
        return {r: None for r in RANKS}
    depth = PAIR_DEPTH[pair_answer]
    truth = shared_ranks(taxon_a, taxon_b)
    out, wrong = {}, False
    for i, r in enumerate(RANKS):
        out[r] = None if wrong or truth[r] is None else (i <= depth) == truth[r]
        wrong = wrong or out[r] is False
    return out


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


def score_select(tiles, picks, rank, group, flagged=()):
    """
    How a Select all grid went. ``tiles`` are the Beetles shown (None for one that is gone), ``picks`` the places
    tapped. Each validated beetle is "right" (a member, tapped), "wrong" (not one, tapped), "missed" (a member left
    out) or "clear" (not one, left out); one nobody has validated is "vote" when tapped and "" otherwise. A photo the
    player flagged (``flagged``: its place) is "flagged" and counts for nothing, so a flagged member isn't missed.
    Returns {"tiles": [state, ...], "right", "wrong", "missed", "members", "perfect"}.
    """
    theirs, picked, flagged = _norm((group or {}).get(rank)), set(picks or []), set(flagged or [])
    states, count = [], {"right": 0, "wrong": 0, "missed": 0, "clear": 0, "members": 0}
    for i, roi in enumerate(tiles):
        if i in flagged:
            states.append("flagged")
            continue
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


def score_odd_grid(tiles, picks, rank, group, flagged=()):
    """
    How an Odd One Out grid went, tile by tile like score_select (#540). ``tiles`` are the Beetles shown (None for one
    that is gone), ``picks`` the places picked. A validated beetle outside the group is an odd one: "right" when
    picked, "missed" when not; a validated beetle of the group is "wrong" when picked and "clear" when not; one nobody
    has validated is "vote" when picked and "" otherwise; a photo the player flagged is "flagged".
    Returns {"tiles": [state, ...], "right", "wrong", "missed", "votes", "odds"}: ``odds`` the odd ones it held.
    """
    theirs, picked, flagged = _norm((group or {}).get(rank)), set(picks or []), set(flagged or [])
    states, count = [], {"right": 0, "wrong": 0, "missed": 0, "votes": 0}
    for i, roi in enumerate(tiles):
        if i in flagged:
            states.append("flagged")
            continue
        mine = rank_values(roi.taxon).get(rank, "") if roi is not None and roi.taxon else ""
        if roi is None or not roi.bbox_is_validated or roi.is_deleted or not mine or not theirs:
            count["votes"] += i in picked
            states.append("vote" if i in picked else "")
            continue
        odd = mine != theirs
        state = ("right" if odd else "wrong") if i in picked else ("missed" if odd else "")
        if state:
            count[state] += 1
        states.append(state or "clear")
    return dict(count, tiles=states, odds=count["right"] + count["missed"])


def odd_verdict(grid):
    """
    Whether an Odd One Out grid's picks were right (score_odd_grid): False once any is a beetle of the group, True when
    every one is a validated odd one, None while picks on beetles nobody has validated are what is left to tell.
    """
    if grid["wrong"]:
        return False
    return True if grid["right"] and not grid["votes"] else None


def odd_picks(answer):
    """The places an Odd One Out answer picked: ``picks``, or the one beetle (``roi``) of an answer from before (#540)."""
    if answer.picks:
        return list(answer.picks)
    tiles = [str(t) for t in answer.tiles or []]
    return [tiles.index(str(answer.roi_id))] if not answer.skipped and str(answer.roi_id) in tiles else []


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
    row = GameAnswer.objects.filter(player=player, is_check=True, is_retry=False, seen_before=False).aggregate(
        **_rank_counts())
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
        in_period.filter(is_check=True, is_retry=False, seen_before=False)
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
    # a Select all grid counts once (game_scoring.ratings); a beetle seen before (#541) shows memory, not skill
    qs = GameAnswer.objects.filter(is_check=True, is_retry=False, seen_before=False)
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
    [(roi_id, player_id, Vote)] from the grid games, for beetles nobody has validated: a Select all tap says the beetle
    is in the grid's group, and so does being left with the rest in an Odd One Out grid whose odd ones the player all
    found (#489, #540), down to the grid's rank (e.g. tribe Xyleborini and genus Xyleborus). Each counts tap_weight()
    of a name. A photo the player flagged says nothing.
    """
    # every odd one found: the one pick was the odd one (answers from before #540), or every pick was right
    all_found = Q()
    for r in RANKS:
        all_found |= Q(grid_rank=r, **{f"correct_{r}": True})
    answers = GameAnswer.objects.filter(mode__in=["odd", "select"], skipped=False).filter(
        (Q(mode="select") & ~Q(picks=[]))
        | (Q(mode="odd") & Q(picks=[]) & Q(roi_id=F("roi_b_id")))
        | (Q(mode="odd") & ~Q(picks=[]) & all_found))
    if roi_ids is not None:
        answers = answers.filter(showing(roi_ids))
    if voters is not None:
        answers = answers.filter(player_id__in=list(voters))
    rows, wanted = [], {str(r) for r in roi_ids} if roi_ids is not None else None
    for ans in answers.only("player_id", "mode", "roi_id", "skipped", "tiles", "picks", "flagged", "grid_rank",
                             "grid_group"):
        if ans.grid_rank not in RANKS or not ans.grid_group:
            continue
        labels = {r: ans.grid_group[r] for r in RANKS[: RANKS.index(ans.grid_rank) + 1] if ans.grid_group.get(r)}
        tiles, flagged = ans.tiles or [], set(ans.flagged or [])
        if ans.mode == "select":
            places = [i for i in ans.picks or [] if isinstance(i, int) and 0 <= i < len(tiles)]
        else:   # the odd ones found: every other beetle was judged one of the group
            picked = set(odd_picks(ans))
            places = [i for i in range(len(tiles)) if i not in picked]
        for i in places:
            if i not in flagged and (wanted is None or tiles[i] in wanted):
                rows.append((tiles[i], ans.player_id, labels))
    if not rows:
        return []
    open_ids = {str(i) for i in Beetles.objects.filter(id__in={r for r, _, _ in rows}, bbox_is_validated=False)
                .values_list("id", flat=True)}
    weight = tap_weight()
    return [(uuid.UUID(r), pid, Vote(labels, weight)) for r, pid, labels in rows if r in open_ids]


def grid_exclusions(answer):
    """
    [(roi_id, rank, value)] a grid answer says beetles nobody has validated are *not* in: in Odd One Out the picked
    beetles are not of the rest's group; in Select all the beetles left untapped are not of the grid's group (a photo
    the player flagged says nothing).
    """
    if answer.skipped or answer.mode not in ("odd", "select") or answer.grid_rank not in RANKS or not answer.grid_group:
        return []
    value = answer.grid_group.get(answer.grid_rank)
    if not value:
        return []
    if answer.mode == "odd" and not answer.picks:   # one pick, from before several odd ones (#540)
        roi = answer.roi
        return [(roi.id, answer.grid_rank, value)] if roi is not None and not roi.bbox_is_validated else []
    if answer.mode == "odd":
        tiles = answer.tiles or []
        picked = [tiles[i] for i in answer.picks if isinstance(i, int) and 0 <= i < len(tiles)]
        open_ids = Beetles.objects.filter(id__in=picked, bbox_is_validated=False, is_deleted=False).values_list("id", flat=True)
        return [(rid, answer.grid_rank, value) for rid in open_ids]
    picked = set(answer.picks or [])
    if not picked:   # tapped nothing: says too little about each beetle
        return []
    left_out = picked | set(answer.flagged or [])
    untapped = [t for i, t in enumerate(answer.tiles or []) if i not in left_out]
    open_ids = Beetles.objects.filter(id__in=untapped, bbox_is_validated=False, is_deleted=False).values_list("id", flat=True)
    return [(rid, answer.grid_rank, value) for rid in open_ids]


def implied_labels(answer):
    """
    The rank values an answer on an unvalidated item says the ROI has.

    Classify: what the player picked. Pair: the ranks the player says the unvalidated
    ROI shares with its validated partner, taken from the partner's taxon. "Different
    subfamily" and "not sure" say nothing positive, so they imply nothing, and nor does a grid answer here: it is about
    other beetles than its own ``roi`` (what a grid says a beetle is counts through tap_votes, what it says a beetle is
    not through grid_exclusions).
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
    # The grid games (Select all taps, the rest of a solved Odd One Out grid): a little lighter than a name
    # (tap_weight), and never enough for an expert's verdict
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
