"""
The leaderboard and player profiles for the Beetle ID game.

The main board ranks by score, accuracy or beetles seen, this week (the default), this month or all time, and can
be searched by name. Only the board's points start again each week or month: levels, reliability, expertise,
badges and streaks never reset. Past weeks' winners are kept on players' profiles (weekly_wins).
The expertise board ranks players inside one part of the tree (a subfamily, tribe or genus) by how well they
identify what is in it, which is where people specialise and compete.
"""
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncWeek
from django.utils import timezone

from . import game, game_levels, game_rewards
from .models import AnswerPoints, GameAnswer, PlayerScore, PlayerSkill, SpeciesDiscovery

SORTS = {"score": "Score", "identification": "Identification accuracy", "similarity": "Similarity accuracy",
         "viewed": "Beetles seen"}
PERIODS = {"week": "This week", "month": "This month", "year": "This year", "all": "All time"}
GAMES = ("classify", "pair", "odd", "select")   # Identification, Similarity, Odd One Out, Select all
# a branch of the tree -> the skill that measures it (see game_trust.BRANCH_OF)
BRANCH_SKILL = {"subfamily": "tribe", "tribe": "genus", "genus": "species"}


def mode_stats(player_ids=None, since=None):
    """
    Identification and Similarity kept apart: {player_id: {"classify": {...}, "pair": {...}}}, each with
    ``accuracy`` (None until GAME_MIN_JUDGED_FOR_ACCURACY ranks were judged), ``judged``, ``correct`` and ``points``.
    Accuracy is counted as for the overall rating (game_scoring.ratings): the first time a player saw a validated
    beetle, rank by rank. With ``since``, only answers given from then on (a leaderboard period).
    """
    from collections import defaultdict

    min_judged = game.game_setting("GAME_MIN_JUDGED_FOR_ACCURACY", 10)
    out = defaultdict(lambda: {m: {"correct": 0, "judged": 0, "points": 0.0, "accuracy": None} for m in GAMES})
    answers = GameAnswer.objects.filter(Q(is_check=True) | Q(validated_later=True), is_retry=False, skipped=False,
                                        score_hold=False)
    points = AnswerPoints.objects.all()
    if since is not None:
        answers = answers.filter(answered_at__gte=since)
        points = points.filter(answer__answered_at__gte=since)
    if player_ids is not None:
        answers = answers.filter(player_id__in=list(player_ids))
        points = points.filter(answer__player_id__in=list(player_ids))
    first = set()
    rows = answers.order_by("answered_at").values_list("player_id", "mode", "roi_id", "roi_b_id",
                                                       *[f"correct_{r}" for r in game.RANKS])
    for pid, mode, a, b, *oks in rows:
        if mode not in GAMES or (pid, mode, a, b) in first:
            continue
        first.add((pid, mode, a, b))
        for ok in oks:
            if ok is not None:
                out[pid][mode]["correct"] += int(ok)
                out[pid][mode]["judged"] += 1
    for pid, mode, total in points.values_list("answer__player", "answer__mode").annotate(s=Sum("points")).values_list(
            "answer__player", "answer__mode", "s"):
        if mode in GAMES:
            out[pid][mode]["points"] = round(total or 0.0)
    for stats in out.values():
        for s in stats.values():
            if s["judged"] >= min_judged:
                s["accuracy"] = s["correct"] / s["judged"]
    return out


def _accuracy(score, games, since, min_judged):
    """All time: the overall rating's accuracy. For a period: both games' judged ranks in that period together."""
    if since is None:
        return score.accuracy if score.judged >= min_judged else None
    correct = sum(g["correct"] for g in games.values())
    judged = sum(g["judged"] for g in games.values())
    return correct / judged if judged >= min_judged else None


def period_start(period, now=None):
    """When the board's period began: Monday 00:00 for "week", the 1st for "month" (server time), None for all time."""
    if period == "week":
        return game.week_start(now)
    if period in ("month", "year"):
        today = (now or timezone.now()).astimezone(timezone.get_current_timezone()).date()
        return timezone.make_aware(datetime(today.year, today.month if period == "month" else 1, 1))
    return None


def period_end(period, now=None):
    """When the period's points start again (None for all time)."""
    start = period_start(period, now)
    if period == "week":
        return start + timedelta(days=7)
    if period == "month":
        return timezone.make_aware(datetime(start.year + start.month // 12, start.month % 12 + 1, 1))
    if period == "year":
        return timezone.make_aware(datetime(start.year + 1, 1, 1))
    return None


def weekly_wins(player_id=None, top=3, now=None):
    """
    The top players of every finished week, best first: [{"week": Monday, "places": [(player_id, points), ...]}],
    newest week first. Worked out from the answers' points, so it follows the same rules as the board
    (and moves with it if a beetle is re-scored later). With player_id, only the weeks where they placed.
    """
    this_week = game.week_start(now)
    rows = (AnswerPoints.objects.filter(answer__answered_at__lt=this_week)
            .annotate(week=TruncWeek("answer__answered_at", tzinfo=timezone.get_current_timezone()))
            .values("week", "answer__player").annotate(points=Sum("points")))
    by_week = {}
    for r in rows:
        if r["points"] and r["points"] > 0:
            by_week.setdefault(r["week"], []).append((r["answer__player"], r["points"]))
    out = []
    for week in sorted(by_week, reverse=True):
        places = sorted(by_week[week], key=lambda pp: (-pp[1], pp[0]))[:top]
        if player_id is None or player_id in [p for p, _ in places]:
            out.append({"week": week, "places": places})
    return out


ANONYMOUS = "A player"


def hidden_names():
    """The players who chose not to show their name on boards (#394)."""
    from .models import GamePreference

    return set(GamePreference.objects.filter(hide_name=True).values_list("player_id", flat=True))


def shown_name(player, viewer_id=None):
    """A player's name as others see it in the game: "A player" if they hid it (#394), except to themselves."""
    if player.id != viewer_id and player.id in hidden_names():
        return ANONYMOUS
    return player.username


def _anonymise(rows, viewer_id):
    """Board rows of players who hid their name show "A player" (and no profile link), except their own row."""
    hidden = hidden_names() - {viewer_id}
    for row in rows:
        row["anonymous"] = row["player_id"] in hidden
        if row["anonymous"]:
            row["username"] = ANONYMOUS
    return rows


def board(sort="score", period="week", q="", limit=50, viewer_id=None):
    """
    Rows: position, player_id, username, anonymous, level, level_name, score, accuracy, id_accuracy, sim_accuracy,
    viewed, is_expert, discoveries. Sort by score, identification or similarity accuracy, or beetles seen.
    Everything but the level and the expert mark follows the period: points, beetles seen, accuracy and finds.
    Players who hid their name show as "A player" to everyone but themselves, and a name search doesn't find them.
    """
    scores = {s.player_id: s for s in PlayerScore.objects.all()}
    names = dict(get_user_model().objects.filter(id__in=scores).values_list("id", "username"))
    unsearchable = hidden_names() - {viewer_id} if q else set()
    experts = set(PlayerSkill.objects.filter(proven=True).values_list("player_id", flat=True))
    since = period_start(period)
    found = SpeciesDiscovery.objects.all() if since is None else SpeciesDiscovery.objects.filter(created_at__gte=since)
    finds = dict(found.values("player").annotate(n=Count("id")).values_list("player", "n"))
    if since is not None:
        week_points = dict(
            AnswerPoints.objects.filter(answer__answered_at__gte=since).values("answer__player")
            .annotate(s=Sum("points")).values_list("answer__player", "s")
        )
        week_viewed = dict(
            GameAnswer.objects.filter(answered_at__gte=since).values("player").annotate(n=Count("id")).values_list("player", "n")
        )
    by_game = mode_stats(since=since)
    min_judged = game.game_setting("GAME_MIN_JUDGED_FOR_ACCURACY", 10)
    rows = []
    for pid, s in scores.items():
        if pid not in names or (s.viewed == 0):
            continue
        if q and (pid in unsearchable or q.lower() not in names[pid].lower()):
            continue
        level = game_levels.describe(s.score, s.rating)
        score, viewed = (max(0.0, week_points.get(pid, 0.0)), week_viewed.get(pid, 0)) if since else (s.score, s.viewed)
        if since and not viewed:
            continue
        rows.append({
            "player_id": pid, "username": names[pid], "level": level["level"], "level_name": level["name"],
            "score": round(score), "accuracy": _accuracy(s, by_game[pid], since, min_judged),
            "viewed": viewed, "is_expert": pid in experts, "discoveries": finds.get(pid, 0),
            "id_accuracy": by_game[pid]["classify"]["accuracy"], "sim_accuracy": by_game[pid]["pair"]["accuracy"],
        })
    if sort in ("identification", "similarity", "accuracy"):
        key = "sim_accuracy" if sort == "similarity" else "id_accuracy"
        rows.sort(key=lambda r: (r[key] is None, -(r[key] or 0), -r["score"]))
    elif sort == "viewed":
        rows.sort(key=lambda r: (-r["viewed"], -r["score"]))
    else:
        rows.sort(key=lambda r: (-r["score"], r["username"]))
    for i, row in enumerate(rows, start=1):
        row["position"] = i
    return _anonymise(rows[:limit] if limit else rows, viewer_id)


def branch_board(rank, value, limit=50, viewer_id=None):
    """
    Players ranked inside one part of the tree: for a genus, how well they name its species; for a tribe, its
    genera; for a subfamily, its tribes. Proven experts first, then by the cautious estimate of their accuracy.
    """
    skill_rank = BRANCH_SKILL.get(rank)
    if not skill_rank or not value:
        return []
    min_shown = game.game_setting("GAME_REPORT_MIN_JUDGED", 5)
    skills = PlayerSkill.objects.filter(rank=skill_rank, branch__iexact=value, judged__gte=min_shown).select_related("player")
    rows = [{
        "player_id": s.player_id, "username": s.player.username, "correct": s.correct, "judged": s.judged,
        "accuracy": s.correct / s.judged if s.judged else None, "lower_bound": s.lower_bound, "is_expert": s.proven,
    } for s in skills]
    rows.sort(key=lambda r: (not r["is_expert"], -r["lower_bound"], -r["judged"]))
    for i, row in enumerate(rows, start=1):
        row["position"] = i
    return _anonymise(rows[:limit], viewer_id)


def profile(player):
    """What anyone signed in can see about a player."""
    from .game_scoring import score_for

    s = score_for(player)
    proven = list(PlayerSkill.objects.filter(player=player, proven=True).order_by("rank", "branch"))
    return {
        "score": s, "level": game_levels.describe(s.score, s.rating), "badges": game_rewards.badge_cards(player),
        "streak": game_rewards.streak_days(game_rewards.goal_days(player)),
        "accuracy": s.accuracy if s.judged >= game.game_setting("GAME_MIN_JUDGED_FOR_ACCURACY", 10) else None,
        "expert_in": [
            {"what": {"tribe": "tribes of", "genus": "genera of", "species": "species of", "subfamily": "subfamilies"}[k.rank],
             "branch": k.branch} for k in proven
        ],
        "discoveries": list(player.species_discoveries.order_by("genus", "species")),
        "modes": dict(GameAnswer.objects.filter(player=player, skipped=False).values_list("mode").annotate(n=Count("id"))),
        "games": mode_stats([player.id])[player.id],
        "weekly": _player_weeks(player.id),
    }


def _player_weeks(player_id):
    """A player's past weekly places: how often 1st and in the top 3, and the weeks themselves (newest first)."""
    weeks = []
    for w in weekly_wins(player_id):
        place = [p for p, _ in w["places"]].index(player_id) + 1
        weeks.append({"week": w["week"], "place": place, "points": round(dict(w["places"])[player_id])})
    return {"wins": sum(1 for w in weeks if w["place"] == 1), "podiums": len(weeks), "weeks": weeks[:10]}


# Accuracy tiers by percentile among rated players, in RPG rarity colours (see includes/game_accuracy.html)
ACCURACY_TIERS = [   # (lowest percentile, key, name)
    (0, "common", "Common"), (25, "uncommon", "Uncommon"), (50, "rare", "Rare"),
    (75, "epic", "Epic"), (90, "legendary", "Legendary"), (98, "mythic", "Mythic"),
]


def accuracy_standing(player, bins=10):
    """
    Where a player's accuracy sits among everyone's: a histogram of players' accuracy (players with enough judged
    answers only), the average, the player's percentile and their tier. ``me`` is None until they have enough.
    """
    min_judged = game.game_setting("GAME_MIN_JUDGED_FOR_ACCURACY", 10)
    rows = list(PlayerScore.objects.filter(judged__gte=min_judged, accuracy__isnull=False).values_list("player_id", "accuracy"))
    counts = [0] * bins
    for _, acc in rows:
        counts[min(bins - 1, int(acc * bins))] += 1
    top = max(counts) or 1
    mine = next((acc for pid, acc in rows if pid == player.id), None)
    out = {
        "players": len(rows), "bins": [{"from": i / bins, "count": c, "height": round(100 * c / top)} for i, c in enumerate(counts)],
        "average": (sum(a for _, a in rows) / len(rows)) if rows else None, "me": None,
    }
    if mine is not None and len(rows) >= 2:
        below = sum(1 for _, a in rows if a < mine) + 0.5 * (sum(1 for _, a in rows if a == mine) - 1)
        pct = round(100 * below / (len(rows) - 1))
        key, name = next((k, n) for lo, k, n in reversed(ACCURACY_TIERS) if pct >= lo)
        out["me"] = {"accuracy": mine, "percentile": pct, "tier": key, "tier_name": name, "bin": min(bins - 1, int(mine * bins))}
    elif mine is not None:
        out["me"] = {"accuracy": mine, "percentile": None, "tier": "common", "tier_name": "Common", "bin": min(bins - 1, int(mine * bins))}
    else:
        judged = PlayerScore.objects.filter(player=player).values_list("judged", flat=True).first() or 0
        out["needed"] = max(0, min_judged - judged)
    return out
