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

from . import game, game_levels, game_rewards, game_trust
from .game_scale import value_step
from .models import AnswerPoints, GameAnswer, PlayerScore, PlayerSkill, SpeciesDiscovery

SORTS = {"score": "Score", "identification": "Naming accuracy", "similarity": "Similarity accuracy",
         "odd": "Odd One Out accuracy", "select": "Find Them All accuracy", "viewed": "Beetles seen"}
PERIODS = {"week": "This week", "month": "This month", "year": "This year", "all": "All time"}
GAMES = ("classify", "pair", "odd", "select")   # Identification, Similarity, Odd One Out, Select all
# each game's accuracy column on the board, and the sort that ranks by it (#543)
ACCURACY_COLUMNS = {"classify": ("id_accuracy", "identification"), "pair": ("sim_accuracy", "similarity"),
                    "odd": ("odd_accuracy", "odd"), "select": ("select_accuracy", "select")}
# a branch of the tree -> the skill that measures it (see game_trust.BRANCH_OF)
BRANCH_SKILL = {"subfamily": "tribe", "tribe": "genus", "genus": "species"}


def mode_stats(player_ids=None, since=None):
    """
    Each game kept apart: {player_id: {"classify": {...}, "pair": {...}, "odd": {...}, "select": {...}}}, each with
    ``accuracy`` (None until GAME_MIN_JUDGED_FOR_ACCURACY ranks were judged), ``judged``, ``correct`` and ``points``.
    Accuracy is counted as for the overall rating (game_scoring.ratings): the first time a player saw a validated
    beetle, rank by rank, and a Find Them All grid once, right when perfect. With ``since``, only answers given from
    then on (a leaderboard period).
    """
    from collections import defaultdict

    min_judged = game.game_setting("GAME_MIN_JUDGED_FOR_ACCURACY", 10)
    out = defaultdict(lambda: {m: {"correct": 0, "judged": 0, "points": 0.0, "accuracy": None} for m in GAMES})
    # a beetle seen before (#541) earns its points but stays out of accuracy, as in the overall rating
    answers = GameAnswer.objects.filter(Q(is_check=True) | Q(validated_later=True), is_retry=False, seen_before=False,
                                        skipped=False, score_hold=False)
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
    """All time: the overall rating's accuracy. For a period: every game's judged ranks in that period together."""
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


def last_week_top(top=3, now=None):
    """
    Last week's top players, placed as weekly_wins places them: [{"position", "player_id", "username", "points"}],
    best first, or [] if nobody scored. Reads that one week only, so it is cheap enough for the game home.
    """
    end = game.week_start(now)
    start = game.week_start(end - timedelta(days=1))
    places = list(
        AnswerPoints.objects.filter(answer__answered_at__gte=start, answer__answered_at__lt=end)
        .values("answer__player").annotate(total=Sum("points")).filter(total__gt=0)
        .order_by("-total", "answer__player").values_list("answer__player", "total")[:top]
    )
    names = dict(get_user_model().objects.filter(id__in=[p for p, _ in places]).values_list("id", "username"))
    return [{"position": i, "player_id": p, "username": names.get(p, ""), "points": round(total)}
            for i, (p, total) in enumerate(places, start=1)]


def board(sort="score", period="week", q="", limit=50):
    """
    Rows: position, player_id, username, level, level_name, score, accuracy, id_accuracy, sim_accuracy, odd_accuracy,
    select_accuracy, viewed, is_expert, discoveries. Sort by score, one game's accuracy, or beetles seen.
    Everything but the level and the expert mark follows the period: points, beetles seen, accuracy and finds.
    """
    scores = {s.player_id: s for s in PlayerScore.objects.all()}
    names = dict(get_user_model().objects.filter(id__in=scores).values_list("id", "username"))
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
        if q and q.lower() not in names[pid].lower():
            continue
        level = game_levels.describe(s.score, s.rating)
        score, viewed = (max(0.0, week_points.get(pid, 0.0)), week_viewed.get(pid, 0)) if since else (s.score, s.viewed)
        if since and not viewed:
            continue
        rows.append({
            "player_id": pid, "username": names[pid], "level": level["level"], "level_name": level["name"],
            "score": round(score), "accuracy": _accuracy(s, by_game[pid], since, min_judged),
            "viewed": viewed, "is_expert": pid in experts, "discoveries": finds.get(pid, 0),
            **{column: by_game[pid].get(mode, {}).get("accuracy") for mode, (column, _) in ACCURACY_COLUMNS.items()},
        })
    by_sort = {sort_key: column for column, sort_key in ACCURACY_COLUMNS.values()}
    if sort in by_sort or sort == "accuracy":
        key = by_sort.get(sort, "id_accuracy")
        rows.sort(key=lambda r: (r[key] is None, -(r[key] or 0), -r["score"]))
    elif sort == "viewed":
        rows.sort(key=lambda r: (-r["viewed"], -r["score"]))
    else:
        rows.sort(key=lambda r: (-r["score"], r["username"]))
    for i, row in enumerate(rows, start=1):
        row["position"] = i
    return rows[:limit] if limit else rows


def branch_board(rank, value, limit=50):
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
    return rows[:limit]


def profile(player):
    """What anyone signed in can see about a player."""
    from .game_scoring import score_for

    s = score_for(player)
    proven = list(PlayerSkill.objects.filter(player=player, proven=True).order_by("rank", "branch"))
    what = {"tribe": "tribes of", "genus": "genera of", "species": "species of", "subfamily": "subfamilies"}
    return {
        "score": s, "level": game_levels.describe(s.score, s.rating), "badges": game_rewards.badge_cards(player),
        "streak": game_rewards.streak_days(game_rewards.goal_days(player)),
        "accuracy": s.accuracy if s.judged >= game.game_setting("GAME_MIN_JUDGED_FOR_ACCURACY", 10) else None,
        "expert_in": [{"what": what[k.rank], "branch": k.branch} for k in proven],   # Naming experts
        "distinction_in": [{"what": what[rank], "branch": branch}
                           for rank, branch in game_trust.distinction_experts(player)],   # #498
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


def accuracy_standing_by_rank(player, bins=20):
    """
    Where a player's naming accuracy sits among everyone's, rank by rank (subfamily, tribe, genus, species):
    {"ranks": [{"rank", "players", "bins", "average", "me"}, ...], "players", "me", "needed"}. Each rank has a
    histogram of players' accuracy there in ``bins`` steps (20: 5% each), the average and the player's own ``me``
    (None until they have enough answers there). ``players`` counts everyone rated at some rank, ``me`` is True once
    the player is, and ``needed`` is how many more answers their best rank needs until then.
    A player's accuracy at a rank is their skills there added up over every branch (PlayerSkill, Naming and Find Them
    All), as the report's "Accuracy by rank" adds them (game_trust.accuracy_by_rank), so both show the same number;
    it counts once GAME_MIN_JUDGED_FOR_ACCURACY answers there were judged, the overall accuracy's rule.
    One grouped query: a row per player and rank, counted here (never a query per player).
    """
    min_judged = max(1, game.game_setting("GAME_MIN_JUDGED_FOR_ACCURACY", 10))
    rows = list(PlayerSkill.objects.filter(rank__in=game.RANKS).values("player", "rank")
                .annotate(ok=Sum("correct"), n=Sum("judged")).filter(Q(n__gte=min_judged) | Q(player=player))
                .order_by().values_list("player", "rank", "ok", "n"))
    rated = {r: [] for r in game.RANKS}
    mine, judged = {}, {}
    for pid, rank, ok, n in rows:
        if pid == player.id:
            judged[rank] = n
        if n >= min_judged:
            rated[rank].append((ok, n))
            if pid == player.id:
                mine[rank] = (ok, n)
    ranks = [{"rank": r, **_distribution(rated[r], mine.get(r), bins)} for r in game.RANKS]
    out = {"ranks": ranks, "players": len({pid for pid, _, _, n in rows if n >= min_judged}), "me": bool(mine)}
    out["needed"] = 0 if mine else max(0, min_judged - max(judged.values(), default=0))
    return out


def _distribution(rated, mine, bins):
    """
    One rank of accuracy_standing_by_rank from everyone's (correct, judged) there (``rated``, the player's own
    included) and the player's (``mine``, or None): the histogram, the average and the player's place.
    One number, one colour (#site-meaning-85): ``step`` is the accuracy's own step on the site's scale
    (game_scale.value_step), the colour it has everywhere else; the comparison with other players is only a grey
    rank ("Top 20%"), never a colour. Bins are worked out in whole numbers, so 35% is always the 35-40% bin.
    """
    counts = [0] * bins
    for ok, n in rated:
        counts[min(bins - 1, ok * bins // n)] += 1
    players, top = len(rated), max(counts) or 1
    accuracies = [ok / n for ok, n in rated]
    out = {
        "players": players, "bins": [{"from": i / bins, "count": c, "height": round(100 * c / top)} for i, c in enumerate(counts)],
        "average": sum(accuracies) / players if players else None, "me": None,
    }
    if mine is not None:
        ok, n = mine
        accuracy = ok / n
        out["me"] = {"accuracy": accuracy, "percentile": None, "rank": None, "step": value_step(accuracy),
                     "bin": min(bins - 1, ok * bins // n)}
        if players >= 2:
            below = sum(a < accuracy for a in accuracies) + 0.5 * (sum(a == accuracy for a in accuracies) - 1)
            pct = round(100 * below / (players - 1))
            out["me"].update(percentile=pct, rank=f"Top {max(1, 100 - pct)}%")
    return out
