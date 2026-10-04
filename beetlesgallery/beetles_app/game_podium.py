"""
Praise for the top players when a week, a month or a year ends: a pop-up on the game home with gold, silver and bronze,
and a few numbers for each (points, beetles, species named right, accuracy). Each player sees each one once, during
the first week of the new period (remembered per browser session, so it needs no table).
"""
from collections import defaultdict
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.utils import timezone

from . import game
from .game import RANKS
from .models import AnswerPoints, GameAnswer

SHOW_FOR = timedelta(days=7)   # how long after a period ends its podium is shown
SESSION_KEY = "podiums_seen"
TITLES = {"week": "Last week", "month": "Last month", "year": "Last year"}


def finished_periods(now=None):
    """The periods that ended within SHOW_FOR, biggest first: [(kind, start, end)]."""
    now = now or timezone.now()
    local = now.astimezone(timezone.get_current_timezone()).date()
    make = lambda d: timezone.make_aware(datetime(d.year, d.month, d.day))   # noqa: E731
    week_end = game.week_start(now)
    month_end = make(local.replace(day=1))
    year_end = make(local.replace(month=1, day=1))
    prev_month = (month_end - timedelta(days=1)).date().replace(day=1)
    out = [
        ("year", make(year_end.date().replace(year=year_end.year - 1)), year_end),
        ("month", make(prev_month), month_end),
        ("week", week_end - timedelta(days=7), week_end),
    ]
    return [(kind, start, end) for kind, start, end in out if now - end < SHOW_FOR]


def standings(start, end):
    """Every player's numbers in [start, end), best first by points: [{"player_id", "username", "points", ...}]."""
    min_judged = game.game_setting("GAME_MIN_JUDGED_FOR_ACCURACY", 10)
    in_period = Q(answered_at__gte=start, answered_at__lt=end)
    points = dict(AnswerPoints.objects.filter(answer__answered_at__gte=start, answer__answered_at__lt=end)
                  .values("answer__player").annotate(s=Sum("points")).values_list("answer__player", "s"))
    answers = GameAnswer.objects.filter(in_period, skipped=False)
    beetles = dict(answers.values("player").annotate(n=Count("id")).values_list("player", "n"))
    species = dict(answers.filter(is_check=True, is_retry=False, correct_species=True)
                   .values("player").annotate(n=Count("id")).values_list("player", "n"))
    judged, correct = defaultdict(int), defaultdict(int)
    rows = (answers.filter(Q(is_check=True) | Q(validated_later=True), is_retry=False, score_hold=False)
            .values_list("player", *[f"correct_{r}" for r in RANKS]))
    for pid, *oks in rows:
        for ok in oks:
            if ok is not None:
                judged[pid] += 1
                correct[pid] += int(ok)
    names = dict(get_user_model().objects.filter(id__in=beetles).values_list("id", "username"))
    out = [{
        "player_id": pid, "username": names.get(pid, "?"), "points": round(max(0.0, points.get(pid) or 0.0)),
        "beetles": n, "species": species.get(pid, 0),
        "accuracy": correct[pid] / judged[pid] if judged[pid] >= min_judged else None,
    } for pid, n in beetles.items()]
    out.sort(key=lambda r: (-r["points"], -r["beetles"], r["username"]))
    for i, row in enumerate(out, start=1):
        row["place"] = i
    return [r for r in out if r["points"] > 0]


def ordinal(n):
    """1st, 2nd, 3rd, 4th ... 11th, 12th, 13th, 21st."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def podium(kind, start, end, viewer=None):
    rows = standings(start, end)
    mine = next((r for r in rows if viewer is not None and r["player_id"] == viewer.id), None)
    if mine:
        mine = dict(mine, place_text=ordinal(mine["place"]))
    return {"kind": kind, "key": f"{kind}:{start.date().isoformat()}", "title": TITLES[kind],
            "start": start, "end": end - timedelta(seconds=1), "top": rows[:3],
            "me": mine if mine and mine["place"] > 3 else None, "players": len(rows)}


def unseen(request, now=None):
    """The podiums this player hasn't been shown yet (and marks them shown). Empty periods are skipped."""
    seen = set(request.session.get(SESSION_KEY, []))
    out = []
    for kind, start, end in finished_periods(now):
        p = podium(kind, start, end, request.user)
        if p["top"] and p["key"] not in seen:
            out.append(p)
            seen.add(p["key"])
    if out:
        request.session[SESSION_KEY] = sorted(seen)[-20:]
    return out
