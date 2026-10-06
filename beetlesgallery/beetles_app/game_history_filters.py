"""
The History page's filters (#574): one game (?game=pair|odd|select|classify) and a day (?day=today, ?day=streak or
?day=2026-10-05). The game home's cards open History this way: a game card its game, the "today" card today and the
streak card the days of the streak. Days are the player's own (the time zone the middleware activated), as for the
daily goal.
"""
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode

from django.db.models import Q
from django.utils import dateformat, timezone

from . import game_levels, game_rewards


def game(value):
    """The game key asked for, or "" for every game."""
    return value if value in game_levels.GAMES else ""


def day_window(player, value):
    """
    {"key", "first", "last", "start", "end", "heading"} for ?day=, or None for every day. ``start``/``end`` bound
    the answers (end excluded); the streak runs from its first day to today, so today's play shows while it lasts.
    """
    today = timezone.localdate()
    last = today
    if value == "today":
        first, heading = today, "Today"
    elif value == "streak":
        days = game_rewards.goal_days(player)
        n = game_rewards.streak_days(days, today)
        end = today if today in days else today - timedelta(days=1)
        first = end - timedelta(days=n - 1) if n else today
        heading = f"Your {n}-day streak" if n else "No streak yet"
    else:
        try:
            first = date.fromisoformat(value or "")
        except ValueError:
            return None
        if first > today:
            return None
        value, last, heading = first.isoformat(), first, dateformat.format(first, "l, M j")
    return {"key": value, "first": first, "last": last, "heading": heading,
            "start": _midnight(first), "end": _midnight(last + timedelta(days=1))}


def _midnight(day):
    return timezone.make_aware(datetime.combine(day, time.min))


def answers_q(game_key, window, prefix="", labelled=True):
    """
    The answers the filters keep, as a Q (``prefix`` "answers__" from a round). ``labelled=False`` keeps skipped
    ones too: they count no beetle but can carry points (a "not sure" in a grid).
    """
    q = Q(**{f"{prefix}skipped": False}) if labelled else Q()
    if game_key:
        q &= Q(**{f"{prefix}mode": game_key})
    if window:
        q &= Q(**{f"{prefix}answered_at__gte": window["start"], f"{prefix}answered_at__lt": window["end"]})
    return q


def query(game_key="", day="", **extra):
    """The query string for these filters, without the empty ones."""
    return urlencode({k: v for k, v in {"game": game_key, "day": day, **extra}.items() if v})


def choices(game_key, window):
    """The two rows of chips: every game (keeping the day) and the days (keeping the game)."""
    day = window["key"] if window else ""
    games = [{"label": "All", "url": "?" + query("", day), "active": not game_key}]
    games += [{"label": game_levels.GAME_NAMES[g], "url": "?" + query(g, day), "active": g == game_key}
              for g in game_levels.GAMES]
    days = [{"label": "All days", "url": "?" + query(game_key), "active": not window},
            {"label": "Today", "url": "?" + query(game_key, "today"), "active": day == "today"},
            {"label": "Streak", "url": "?" + query(game_key, "streak"), "active": day == "streak"}]
    if window and day not in ("today", "streak"):
        days.append({"label": window["heading"], "url": "?" + query(game_key, day), "active": True})
    return games, days
