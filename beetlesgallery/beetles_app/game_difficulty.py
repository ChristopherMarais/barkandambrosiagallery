"""
How hard a beetle is compared with the others (#492), for points that follow it (game_scoring) and for the Scoring
page's distributions.

A beetle's difficulty (RoiDifficulty.value: 1 - IBBI-AI's confidence, else how often players get it wrong) becomes a
percentile among all playable beetles: 0 the easiest, 1 the hardest, 0.5 while it isn't known. The table behind it is
one grouped query (difficulty to 0.001, validated and not), kept for a few minutes.
"""
import math
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Avg, Count, F, IntegerField, Q, Sum
from django.db.models.functions import Cast, Coalesce, Floor
from django.utils import timezone

from . import game
from .models import AnswerPoints, GameAnswer, PlayerScore

BINS = 1000   # difficulty is grouped to 0.001: plenty for a percentile, and a small table to keep
CACHE_KEY = "game:difficulty:histogram:v1"
UNKNOWN_PERCENTILE = 0.5


def _bin(value):
    return min(BINS, max(0, math.floor(value * BINS)))


def histogram():
    """
    {"validated": {bin: n}, "open": {bin: n}, "unknown": {"validated": n, "open": n}} over playable beetles, where
    bin is the difficulty times BINS, rounded down. Cached for GAME_DIFFICULTY_CACHE_SECONDS.
    """
    table = cache.get(CACHE_KEY)
    if table is not None:
        return table
    rows = (
        game.playable_rois()
        .annotate(v=Coalesce("difficulty__model_difficulty", "difficulty__game_difficulty"))
        .annotate(bin=Cast(Floor(F("v") * BINS), IntegerField()))
        .values("bbox_is_validated", "bin").annotate(n=Count("id"))
    )
    table = {"validated": {}, "open": {}, "unknown": {"validated": 0, "open": 0}}
    for row in rows:
        side = "validated" if row["bbox_is_validated"] else "open"
        if row["bin"] is None:
            table["unknown"][side] += row["n"]
        else:
            b = min(BINS, max(0, row["bin"]))
            table[side][b] = table[side].get(b, 0) + row["n"]
    cache.set(CACHE_KEY, table, game.game_setting("GAME_DIFFICULTY_CACHE_SECONDS", 600))
    return table


def forget():
    cache.delete(CACHE_KEY)


def _combined(table):
    counts = dict(table["validated"])
    for b, n in table["open"].items():
        counts[b] = counts.get(b, 0) + n
    return counts


def bin_percentile(b, counts, total):
    """The percentile of a difficulty bin: the share of beetles below it plus half of those in it (ties sit in the middle)."""
    if not total:
        return UNKNOWN_PERCENTILE
    below = sum(n for k, n in counts.items() if k < b)
    return (below + 0.5 * counts.get(b, 0)) / total


def percentile_of(value, table=None):
    """Where a difficulty value sits among the playable beetles' (0 to 1); 0.5 when unknown or nothing is known."""
    if value is None:
        return UNKNOWN_PERCENTILE
    counts = _combined(table or histogram())
    return bin_percentile(_bin(value), counts, sum(counts.values()))


def percentile(roi_id):
    """How hard this beetle is right now, as a percentile of the playable beetles (0.5 when unknown), to 3 places."""
    from .models import RoiDifficulty

    found = RoiDifficulty.objects.filter(roi_id=roi_id).first()
    return round(percentile_of(found.value if found else None), 3)


# ---------------------------------------------------------------------------
# The Scoring page's distributions: grouped in SQL, a handful of rows each
# ---------------------------------------------------------------------------
POINT_BINS = [(-10, "below −10"), (-2, "−10 to −2"), (2, "−2 to 2"), (10, "2 to 10"), (30, "10 to 30"), (None, "30 and up")]


def _bars(counts):
    """[{"count", "height"}] with heights in % of the tallest bar."""
    top = max(counts) if counts and max(counts) else 1
    return [{"count": c, "height": round(100 * c / top)} for c in counts]


def beetle_chart(table=None):
    """How hard the playable beetles are, in tenths, validated and not."""
    table = table or histogram()
    out = []
    for side, label in (("validated", "Validated"), ("open", "Not validated")):
        tenths = [0] * 10
        for b, n in table[side].items():
            tenths[min(9, b * 10 // BINS)] += n
        out.append({"label": label, "total": sum(tenths), "unknown": table["unknown"][side], "bars": _bars(tenths)})
    return out


def rating_chart():
    """Players' reliability (rating) in tenths, for players with at least one judged answer."""
    tenths = [0] * 10
    rows = (PlayerScore.objects.filter(judged__gt=0).annotate(t=Cast(Floor(F("rating") * 10), IntegerField()))
            .values("t").annotate(n=Count("pk")))
    for row in rows:
        tenths[min(9, max(0, row["t"] or 0))] += row["n"]
    return {"total": sum(tenths), "bars": _bars(tenths)}


def points_chart(days=30):
    """Points per answer in each game over the last ``days``: how many, the average, and how they spread."""
    from .game_levels import GAME_NAMES, GAMES

    bins, low = {}, None
    for i, (high, _) in enumerate(POINT_BINS):
        q = Q() if low is None else Q(points__gte=low)
        if high is not None:
            q &= Q(points__lt=high)
        bins[f"b{i}"] = Count("pk", filter=q)
        low = high
    since = timezone.now() - timedelta(days=days)
    rows = {
        row["answer__mode"]: row for row in
        AnswerPoints.objects.filter(answer__answered_at__gte=since).values("answer__mode")
        .annotate(n=Count("pk"), avg=Avg("points"), total=Sum("points"), lost=Count("pk", filter=Q(points__lt=0)), **bins)
    }
    out = []
    for key in GAMES:
        row = rows.get(key)
        if row is None:
            continue
        out.append({"game": GAME_NAMES[key], "answers": row["n"], "average": row["avg"], "total": row["total"],
                    "lost_share": row["lost"] / row["n"],
                    "bars": [dict(bar, label=label) for bar, (_, label) in
                             zip(_bars([row[f"b{i}"] for i in range(len(POINT_BINS))]), POINT_BINS)]})
    return out


def right_chart(table=None):
    """
    The share of answers right (Identification and Similarity, validated beetles, first sightings) by the beetle's
    difficulty decile now: falling bars mean the difficulty really predicts mistakes. Each decile also shows the
    points multiplier there (game_scoring.difficulty_multiplier at its middle).
    """
    from .game_scoring import SCALED_MODES, difficulty_multiplier

    table = table or histogram()
    counts = _combined(table)
    total = sum(counts.values())
    rows = (
        GameAnswer.objects.filter(mode__in=SCALED_MODES, is_retry=False, score_hold=False)
        .filter(Q(is_check=True) | Q(validated_later=True))
        .annotate(v=Coalesce("roi__difficulty__model_difficulty", "roi__difficulty__game_difficulty"))
        .filter(v__isnull=False)
        .annotate(bin=Cast(Floor(F("v") * BINS), IntegerField()))
        .values("bin").annotate(n=Count("id"), right=Count("id", filter=game.ANSWERED_RIGHT))
    )
    n, right = [0] * 10, [0] * 10
    for row in rows:
        d = min(9, int(10 * bin_percentile(min(BINS, max(0, row["bin"])), counts, total)))
        n[d] += row["n"]
        right[d] += row["right"]
    return {"answers": sum(n), "deciles": [
        {"answers": n[d], "share": (right[d] / n[d]) if n[d] else None,
         "height": round(100 * right[d] / n[d]) if n[d] else 0, "multiplier": round(difficulty_multiplier((d + 0.5) / 10), 2)}
        for d in range(10)]}


def distributions():
    table = histogram()
    return {"beetles": beetle_chart(table), "ratings": rating_chart(), "points": points_chart(), "right": right_chart(table)}
