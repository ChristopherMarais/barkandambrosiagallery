"""
Rewards that keep the Beetle ID game fun: a daily goal, a streak of days and badges (levels are in game_levels.py).

Everything is worked out from the player's answers (nothing new is stored), and nothing here says how
*accurate* they are. A count, a streak or a level can be shown while they play; accuracy-based badges are only
shown once they leave (see recap) so the score stays out of sight during play.
"""
from collections import OrderedDict
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.db.models import Count, F
from django.db.models.functions import TruncDate
from django.utils import timezone

from .game_scale import HEX, level_classes, level_hex, level_step, value_step
from .models import GameAnswer

def daily_goal():
    """The lowest a daily goal can be (GAME_DAILY_GOAL, 20): every new player starts there."""
    return getattr(settings, "GAME_DAILY_GOAL", 20)


# ---------------------------------------------------------------------------
# The daily goal adapts, like a fitness watch's step goal
# ---------------------------------------------------------------------------
def goal_history(counts, until):
    """
    {day: goal} for every day from the first day played up to ``until`` (inclusive), given ``counts`` =
    {day: beetles labelled that day}. Each day's goal comes from the days before it only, so it never moves
    during the day:

    * goal met: the next goal rises halfway towards what was done (at most 25% more);
    * played but fell short: it eases a quarter of the way down (at most 15% less);
    * a day off: it eases 5%;
    * never below GAME_DAILY_GOAL (20) nor above GAME_DAILY_GOAL_MAX (300), in steps of 5.
    """
    from datetime import timedelta

    low = daily_goal()
    high = getattr(settings, "GAME_DAILY_GOAL_MAX", 300)
    played = sorted(d for d, n in counts.items() if n)
    if not played or played[0] > until:
        return {until: low}
    goals, goal, day = {}, float(low), played[0]
    while day <= until:
        # the exact goal is carried from day to day; only what players see is rounded to 5
        goals[day] = low if goal < low + 2.5 else int(max(low, min(high, 5 * round(goal / 5))))
        done = counts.get(day, 0)
        if done >= goals[day]:
            goal = min(goal + 0.5 * (done - goal), goal * 1.25)
        elif done:
            goal = max(goal - 0.25 * (goal - done), goal * 0.85)
        else:
            goal *= 0.95
        goal = max(low, min(high, goal))
        day += timedelta(days=1)
    return goals


def _day_counts(player, since=None, before=None):
    rows = labelled(player)
    if since is not None:
        rows = rows.filter(answered_at__date__gte=since)
    if before is not None:
        rows = rows.filter(answered_at__lt=before)
    rows = rows.annotate(day=TruncDate("answered_at", tzinfo=timezone.get_current_timezone())).values("day").annotate(n=Count("id"))
    return {r["day"]: r["n"] for r in rows}


def player_goal(player, day=None):
    """Today's daily goal for this player (from the last 60 days of play)."""
    from datetime import timedelta

    day = day or timezone.localdate()
    counts = _day_counts(player, since=day - timedelta(days=60))
    return goal_history(counts, day)[day]


def labelled(player, **filters):
    return GameAnswer.objects.filter(player=player, skipped=False, **filters)


def active_days(player):
    """The set of calendar days (server time) on which the player answered something."""
    days = (
        GameAnswer.objects.filter(player=player, skipped=False)
        .annotate(day=TruncDate("answered_at", tzinfo=timezone.get_current_timezone()))
        .values_list("day", flat=True).distinct()
    )
    return set(days)


def goal_days(player):
    """
    The days on which the player reached that day's daily goal (each day against its own goal, see goal_history).
    Only these days make a streak (issue #423): playing a few beetles doesn't keep it going, reaching the goal does.
    """
    counts = _day_counts(player)
    if not counts:
        return set()
    goals = goal_history(counts, max(counts))
    return {day for day, n in counts.items() if n >= goals.get(day, daily_goal())}


def streak_days(days, today=None):
    """Consecutive days ending today (or yesterday: the streak is still alive until the day is over)."""
    today = today or timezone.localdate()
    day = today if today in days else today - timedelta(days=1)
    n = 0
    while day in days:
        n += 1
        day -= timedelta(days=1)
    return n


def progress(player):
    """What the home page and the feed's little chip show."""
    from . import game_levels
    total = labelled(player).count()
    today = labelled(player, answered_at__date=timezone.localdate()).count()
    goal = player_goal(player)
    level = game_levels.for_player(player)
    ranks = game_levels.rank_unlock(level["level"], total, bool(level.get("granted")))
    return {
        "rank": ranks["rank"], "rank_next": ranks["next"],
        "total": total, "today": today, "goal": goal, "goal_met": today >= goal,
        "streak": streak_days(goal_days(player)),
        "level": level["level"], "level_name": level["name"], "proposals": level["proposals"],
        "perks": sorted(level["perks"]),
        "to_next": level["next"]["points_needed"] if level["next"] else None,
        "next": level["next"],
        "level_progress": round(level["progress"], 3),
        "score": round(level["score"]),
        "next_at": level["next"]["points"] if level["next"] else None,
    }


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------
BADGES = OrderedDict([
    # key: (name, how to get it, icon, shown_during_play)
    ("first", ("First steps", "Label your first beetle", "fi-rr-flag", True)),
    ("ten", ("Warming up", "Label 10 beetles", "fi-rr-fire-flame-curved", True)),
    ("hundred", ("Centurion", "Label 100 beetles", "fi-rr-medal", True)),
    ("thousand", ("Thousand eyes", "Label 1,000 beetles", "fi-rr-eye", True)),
    ("streak3", ("On a roll", "Reach your daily goal 3 days in a row", "fi-rr-calendar", True)),
    ("streak7", ("Week warrior", "Reach your daily goal 7 days in a row", "fi-rr-calendar-check", True)),
    ("streak30", ("Habitat regular", "Reach your daily goal 30 days in a row", "fi-rr-trophy", True)),
    ("goal", ("Goal getter", "Reach the daily goal", "fi-rr-bullseye-arrow", True)),
    ("both", ("All-rounder", "Play more than one game", "fi-rr-apps", True)),
    ("species1", ("Species spotter", "Name a species we know the answer to", "fi-rr-search", False)),
    ("species25", ("Sharp eyes", "Name 25 species we know the answer to", "fi-rr-star", False)),
    ("expert", ("Trusted expert", "Become a Naming expert in a taxon", "fi-rr-shield-check", False)),
    # harder, and some very specific
    ("fivehundred", ("Field season", "Label 500 beetles", "fi-rr-leaf", True)),
    ("tenthousand", ("Ten thousand eyes", "Label 10,000 beetles", "fi-rr-binoculars", True)),
    ("streak100", ("Centennial", "Reach your daily goal 100 days in a row", "fi-rr-calendar-star", True)),
    ("streak365", ("Year of the beetle", "Reach your daily goal 365 days in a row", "fi-rr-sun", True)),
    ("marathon", ("Marathon", "Label 200 beetles in one day", "fi-rr-running", True)),
    ("goal7", ("Creature of habit", "Reach the daily goal on 7 days", "fi-rr-calendar-check", True)),
    ("nightowl", ("Night owl", "Play between midnight and 4 am", "fi-rr-moon", True)),
    ("earlybird", ("Early bird", "Play between 5 and 6 am", "fi-rr-sunrise", True)),
    ("species100", ("Eagle eye", "Name 100 species we know the answer to", "fi-rr-eye", False)),
    ("flawless", ("Flawless", "20 checked beetles in a row, every rank correct", "fi-rr-diamond", False)),
    ("genera10", ("Genus hopper", "Name the correct species in 10 different genera", "fi-rr-shuffle", False)),
    ("genera50", ("Taxonomic tourist", "Name the correct species in 50 different genera", "fi-rr-globe", False)),
    ("platypod", ("Pinhole borer", "Name 25 Platypodinae species correctly", "fi-rr-bullseye", False)),
    ("similar50", ("Family resemblance", "50 Similarity answers exactly correct", "fi-rr-link", False)),
    ("twins", ("Doppelganger", "Spot 10 pairs of the very same species", "fi-rr-copy", False)),
    ("comeback", ("Comeback", "Get a beetle correct the second time", "fi-rr-refresh", False)),
    ("ahead", ("Ahead of the curators", "10 answers proven correct after curators reviewed them", "fi-rr-time-forward", False)),
    ("curator", ("Sharp-eyed", "3 of your reports led to a fix", "fi-rr-flag-alt", False)),
    ("discovery3", ("Explorer", "Find 3 new species", "fi-rr-map", False)),
    ("expert5", ("Polymath", "Be a Naming expert in 5 taxa", "fi-rr-graduation-cap", False)),
    ("king", ("Royalty", "Reach the top level", "fi-rr-crown", False)),
    ("discovery", ("New species finder", "Name a species the gallery had never validated, confirmed later by a curator", "fi-rr-sparkles", False)),
    # round 4 (#608): a few more, some of them strange
    ("fullhouse", ("Full house", "A perfect 25-beetle Find Them All grid", "fi-rr-grid", False)),
    ("imposters", ("Odd one hunter", "Find every odd one in 50 Odd One Out grids", "fi-rr-incognito", False)),
    ("splitter", ("Splitter", "Become a Distinction expert in a taxon", "fi-rr-split", False)),
    ("lumpsplit", ("Lumper and splitter", "Naming and Distinction expert in the same taxon", "fi-rr-arrows-repeat", False)),
    ("fungus", ("Fungus farmer", "Name 25 Xyleborini species correctly", "fi-rr-mushroom", False)),
    ("machine", ("Beat the machine", "5 checked beetles right where IBBI-AI was sure and wrong", "fi-rr-robot", False)),
    ("lonewolf", ("Lone wolf", "5 checked beetles right where most players were wrong", "fi-rr-paw", False)),
    ("weekend", ("Weekend naturalist", "Reach the daily goal on a Saturday and the Sunday after", "fi-rr-calendar-day", True)),
    ("leap", ("Leap beetle", "Play on 29 February", "fi-rr-frog", True)),
    ("fullmoon", ("Full moon", "Play on a night of the full moon", "fi-rr-moon-stars", True)),
])


def earned_badges(player, before=None):
    """The set of badge keys the player has earned, counting only answers before ``before`` if given."""
    answers = GameAnswer.objects.filter(player=player)
    if before is not None:
        answers = answers.filter(answered_at__lt=before)
    done = answers.filter(skipped=False)
    total = done.count()
    per_day = done.annotate(day=TruncDate("answered_at", tzinfo=timezone.get_current_timezone())).values("day").annotate(n=Count("id"))
    # each day is judged against that day's own goal (it adapts, see goal_history)
    counts = {row["day"]: row["n"] for row in per_day}
    goals = goal_history(counts, max(counts)) if counts else {}
    per_day = [dict(row, goal=goals.get(row["day"], daily_goal())) for row in per_day]
    goal_days = any(row["n"] >= row["goal"] for row in per_day)
    # the longest run of days with the goal reached, up to the cut-off (only goal days make a streak, #423)
    best = _best_streak({row["day"] for row in per_day if row["n"] >= row["goal"]})
    right_species = answers.filter(is_check=True, is_retry=False, correct_species=True, score_hold=False).count()
    have = set()
    for key, needed in (("first", 1), ("ten", 10), ("hundred", 100), ("thousand", 1000)):
        if total >= needed:
            have.add(key)
    for key, needed in (("streak3", 3), ("streak7", 7), ("streak30", 30)):
        if best >= needed:
            have.add(key)
    if goal_days:
        have.add("goal")
    if done.values("mode").distinct().count() >= 2:
        have.add("both")
    if right_species >= 1:
        have.add("species1")
    if right_species >= 25:
        have.add("species25")
    if right_species >= 100:
        have.add("species100")
    have |= _harder_badges(player, answers, done, total, best, per_day)
    have |= _calendar_badges(done, per_day)
    if before is None:
        from .game_levels import for_player
        from .game_trust import distinction_experts, skills_for
        proven = {(s.rank, s.branch.lower()) for s in skills_for(player) if s.proven}
        if proven:
            have.add("expert")
        if len(proven) >= 5:
            have.add("expert5")
        apart = {(rank, branch.lower()) for rank, branch in distinction_experts(player)}
        if apart:
            have.add("splitter")
        if apart & proven:
            have.add("lumpsplit")
        finds = player.species_discoveries.count()
        if finds:
            have.add("discovery")
        if finds >= 3:
            have.add("discovery3")
        if player.game_reports.filter(status="corrected").count() >= 3:
            have.add("curator")
        if for_player(player)["next"] is None:
            have.add("king")
    return have


def _harder_badges(player, answers, done, total, best, per_day):
    """The harder badges that come from answers (see BADGES)."""
    from django.db.models import Q
    from django.db.models.functions import ExtractHour

    have = set()
    for key, needed in (("fivehundred", 500), ("tenthousand", 10000)):
        if total >= needed:
            have.add(key)
    for key, needed in (("streak100", 100), ("streak365", 365)):
        if best >= needed:
            have.add(key)
    day_counts = [row["n"] for row in per_day]
    if any(n >= 200 for n in day_counts):
        have.add("marathon")
    if sum(1 for row in per_day if row["n"] >= row["goal"]) >= 7:
        have.add("goal7")
    hours = set(done.annotate(h=ExtractHour("answered_at", tzinfo=timezone.get_current_timezone())).values_list("h", flat=True).distinct())
    if hours & {0, 1, 2, 3}:
        have.add("nightowl")
    if 5 in hours:
        have.add("earlybird")
    known = answers.filter(Q(is_check=True) | Q(validated_later=True), skipped=False, score_hold=False)
    first = known.filter(is_retry=False)
    right_species = first.filter(mode="classify", correct_species=True)
    genera = right_species.values("ref_genus").distinct().count()
    if genera >= 10:
        have.add("genera10")
    if genera >= 50:
        have.add("genera50")
    if right_species.filter(ref_subfamily__iexact="Platypodinae").count() >= 25:
        have.add("platypod")
    if right_species.filter(ref_tribe__iexact="Xyleborini").count() >= 25:
        have.add("fungus")
    have |= _skill_badges(player, first, right_species)
    exact_pairs = first.filter(mode="pair").exclude(Q(correct_subfamily=False) | Q(correct_tribe=False)
                                                    | Q(correct_genus=False) | Q(correct_species=False))
    if exact_pairs.count() >= 50:
        have.add("similar50")
    if exact_pairs.filter(pair_answer="species").count() >= 10:
        have.add("twins")
    if answers.filter(is_retry=True, correct_species=True).exists() or answers.filter(
            is_retry=True, mode="classify", correct_genus=True, species="").exists():
        have.add("comeback")
    if answers.filter(validated_later=True, correct_species=True).count() >= 10:
        have.add("ahead")
    run = longest = 0
    for oks in first.filter(mode="classify").order_by("answered_at").values_list(
            "correct_subfamily", "correct_tribe", "correct_genus", "correct_species"):
        judged = [ok for ok in oks if ok is not None]
        run = run + 1 if judged and all(judged) else 0
        longest = max(longest, run)
    if longest >= 20:
        have.add("flawless")
    return have


def _skill_badges(player, first, right_species):
    """Full house, Odd one hunter, Beat the machine and Lone wolf (#608), from scored answers."""
    from django.db.models import Exists, OuterRef, Subquery
    from django.db.models.functions import Coalesce

    from .game import game_setting
    from .models import AnswerPoints, ModelPrediction

    have = set()
    points = AnswerPoints.objects.filter(answer__in=first)
    full = points.filter(answer__mode="select", detail__perfect=True).values_list("answer__tiles", flat=True)
    if any(len(tiles or []) >= 25 for tiles in full):
        have.add("fullhouse")
    grids = points.filter(answer__mode="odd").values_list("detail", flat=True)
    if sum(1 for d in grids if d.get("right") is True and "missed" not in (d.get("tiles") or [])) >= 50:
        have.add("imposters")
    # IBBI-AI sure of another species than the one the beetle is (GAME_AI_SURE_FROM, as the grids' "sure")
    sure = game_setting("GAME_AI_SURE_FROM", 0.9)
    fooled = ModelPrediction.objects.filter(roi=OuterRef("roi"), confidence__gte=sure).exclude(
        valid_species_id=OuterRef("roi__depicts_valid_name_id"))
    if right_species.filter(Exists(fooled)).count() >= 5:
        have.add("machine")
    # most of the other players who named the beetle got its species wrong (at least two of them)
    others = GameAnswer.objects.filter(roi=OuterRef("roi"), mode="classify", skipped=False).exclude(player=player)

    def tally(**judged):
        return Coalesce(Subquery(others.filter(**judged).values("roi").annotate(n=Count("id")).values("n")[:1]), 0)

    alone = right_species.annotate(others_wrong=tally(correct_species=False), others_right=tally(correct_species=True))
    if alone.filter(others_wrong__gte=2, others_wrong__gt=F("others_right")).count() >= 5:
        have.add("lonewolf")
    return have


# The moon (#608): new on 6 January 2000 at 18:14 UTC, and full half a synodic month after each new moon
NEW_MOON = datetime(2000, 1, 6, 18, 14, tzinfo=dt_timezone.utc)
SYNODIC_DAYS = 29.530588853


def moon_age(when):
    """Days since the last new moon at ``when`` (an aware datetime), 0 up to 29.53; the moon is full at about 14.77."""
    return ((when - NEW_MOON).total_seconds() / 86400) % SYNODIC_DAYS


def full_moon_night(when):
    """Whether ``when`` is at night (6 pm to 6 am, local time) within a day of the full moon."""
    local = timezone.localtime(when)
    return (local.hour >= 18 or local.hour < 6) and abs(moon_age(when) - SYNODIC_DAYS / 2) <= 1.0


def _calendar_badges(done, per_day):
    """Weekend naturalist, Leap beetle and Full moon (#608), from the days and nights the player played."""
    from django.db.models.functions import ExtractHour

    have = set()
    days = {row["day"] for row in per_day}
    goal_days = {row["day"] for row in per_day if row["n"] >= row["goal"]}
    if any(d.weekday() == 5 and d + timedelta(days=1) in goal_days for d in goal_days):
        have.add("weekend")
    if any((d.month, d.day) == (2, 29) for d in days):
        have.add("leap")
    zone = timezone.get_current_timezone()
    near_full = [d for d in days if abs(moon_age(datetime(d.year, d.month, d.day, 12, tzinfo=zone)) - SYNODIC_DAYS / 2) <= 2]
    if near_full:   # only then is it worth looking at the hours
        nights = (done.annotate(day=TruncDate("answered_at", tzinfo=zone), h=ExtractHour("answered_at", tzinfo=zone))
                  .filter(day__in=near_full).values_list("day", "h").distinct())
        if any(full_moon_night(datetime(d.year, d.month, d.day, h, 30, tzinfo=zone)) for d, h in nights):
            have.add("fullmoon")
    return have


def _best_streak(days):
    best = run = 0
    previous = None
    for day in sorted(days):
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    return best


# How hard each badge is, on the site's one scale (game_scale.py), like the levels: the easiest red, then orange,
# yellow, green, and the two hardest deep green and glowing like the top level.
# Badges are the one exception to "blue is IBBI-AI, purple the players" (#608): the hardest are blue, and the strange
# or secret ones purple. Royalty stays with the top level's colour.
BADGE_TIERS = {
    "fair": ("first", "ten", "goal", "both", "streak3"),
    "decent": ("hundred", "streak7", "species1", "comeback"),
    "good": ("thousand", "streak30", "goal7", "species25", "fivehundred", "genera10", "similar50", "weekend"),
    "great": ("expert", "species100", "platypod", "ahead", "curator", "marathon", "streak100", "discovery3",
              "genera50", "fullhouse", "imposters", "splitter", "fungus"),
    "excellent": ("king",),
    "blue": ("flawless", "expert5", "tenthousand", "streak365", "discovery", "lumpsplit", "machine"),
    "purple": ("nightowl", "earlybird", "twins", "lonewolf", "leap", "fullmoon"),
}
# Each tier as a colour, for drawing (a new badge's confetti)
BADGE_HEX = {**HEX, "blue": "#3b82f6", "purple": "#a855f7"}
BADGE_TIER = {key: tier for tier, keys in BADGE_TIERS.items() for key in keys}


def badge_tier(key):
    return BADGE_TIER.get(key, "fair")


def badge_cards(player):
    """All badges for display: earned or not, with their step on the scale."""
    have = earned_badges(player)
    return [
        {"key": key, "name": name, "how": how, "icon": icon, "earned": key in have, "tier": badge_tier(key)}
        for key, (name, how, icon, _) in BADGES.items()
    ]


# ---------------------------------------------------------------------------
# While playing: little celebrations that say nothing about accuracy
# ---------------------------------------------------------------------------
MILESTONES = (10, 25, 50, 100, 250, 500, 1000)


def play_events(player, before, badges=True):
    """
    What to celebrate after an answer, given ``before`` (the dict progress() returned before it) .
    Returns a list of {"kind", "title", "text"} for the feed to show as a toast. With ``badges``, also one event for
    each badge that answer earned (new_badges): {"kind": "badge", "title", "text", "icon", "tier", "colour"}.
    """
    now = progress(player)
    events = []
    if now["level"] > before["level"]:
        from .game_levels import GAME_PERK, PERKS, PROPOSALS, level_icon
        gained = [p for p in now["perks"] if p not in before["perks"]]
        games = set(GAME_PERK.values())   # a game's name keeps its capitals ("Odd One Out")
        unlocked = " Unlocked: " + ", ".join(PERKS[p][0] if p in games else PERKS[p][0].lower() for p in gained) + "." if gained else ""
        # the pop-up and its confetti take the new level's colour on the scale
        events.append({"kind": "level", "title": f"Level {now['level']}", "text": f"You are now a {now['level_name']}.{unlocked}",
                       "level": now["level"], "icon": level_icon(now["level"]), "step": level_step(now["level"]),
                       "badge": level_classes(now["level"]), "colour": level_hex(now["level"])})
        if PROPOSALS in gained:
            events.append({"kind": "proposals", "title": "Your labels now count", "text": PERKS[PROPOSALS][1]})
    if before.get("rank") and now["rank"] != before["rank"]:
        from .game_levels import RANK_ORDER
        if RANK_ORDER.index(now["rank"]) > RANK_ORDER.index(before["rank"]):
            events.append({"kind": "rank", "title": f"{now['rank'].capitalize()} unlocked",
                           "text": f"You can now name the {now['rank']} too." if now["rank"] != "species"
                           else "You can now name the species: every rank is open."})
    if now["goal_met"] and not before["goal_met"]:
        # reaching the goal is also what grows the streak: one toast for both
        streak = f" {now['streak']}-day streak!" if now["streak"] > 1 else ""
        events.append({"kind": "goal", "title": "Daily goal reached", "text": f"{now['goal']} beetles today.{streak}"})
    for milestone in MILESTONES:
        if before["total"] < milestone <= now["total"]:
            events.append({"kind": "milestone", "title": f"{milestone:,} beetles", "text": "That's a lot of beetles."})
    if badges:
        for key in new_badges(player, before, now):
            name, how, icon, _ = BADGES[key]
            tier = badge_tier(key)
            events.append({"kind": "badge", "title": name, "text": how, "icon": icon, "tier": tier,
                           "colour": BADGE_HEX[tier]})
    return events


COUNT_BADGES = (1, 10, 100, 500, 1000, 10000)   # the totals at which a count badge is earned (earned_badges)
HOUR_BADGES = {"nightowl": (0, 1, 2, 3), "earlybird": (5,)}


def new_badges(player, before, now):
    """
    The badges the player's latest answer earned, of those shown during play (BADGES: accuracy waits for the recap),
    in BADGES' order. ``before`` and ``now`` are progress() around the answer. This runs on every answer, so it first
    asks a few cheap questions (a total passed, the goal reached, a new game, the hour, the date) and only works the
    badges out, before and after the answer, when one of them says a badge may have come.
    """
    latest = GameAnswer.objects.filter(player=player).order_by("-answered_at", "-id").first()
    if latest is None or latest.skipped or not _badge_moment(player, before, now, latest):
        return []
    had = earned_badges(player, before=latest.answered_at)
    have = earned_badges(player, before=latest.answered_at + timedelta(microseconds=1))
    return [key for key, (_, _, _, during_play) in BADGES.items() if during_play and key in have - had]


def _badge_moment(player, before, now, latest):
    """Whether the latest answer may have earned a badge shown during play (new_badges): a few cheap checks."""
    from django.db.models.functions import ExtractHour

    if any(before["total"] < n <= now["total"] for n in COUNT_BADGES):
        return True
    if now["goal_met"] and not before["goal_met"]:   # the goal, the streaks, the weekend
        return True
    if before.get("today", 0) < 200 <= now["today"]:   # Marathon
        return True
    earlier = GameAnswer.objects.filter(player=player, skipped=False, answered_at__lt=latest.answered_at)
    if not earlier.filter(mode=latest.mode).exists():   # a new game: All-rounder
        return True
    zone = timezone.get_current_timezone()
    local = timezone.localtime(latest.answered_at)
    for hours in HOUR_BADGES.values():
        if local.hour in hours:
            return not earlier.annotate(h=ExtractHour("answered_at", tzinfo=zone)).filter(h__in=hours).exists()
    if (local.month, local.day) == (2, 29):
        return not earlier.filter(answered_at__month=2, answered_at__day=29).exists()
    if full_moon_night(latest.answered_at):
        from django.core.cache import cache   # worked out once a night, not on every answer of it
        return cache.add(f"game:fullmoon-checked:{player.pk}:{local.date()}", 1, 60 * 60 * 24)
    return False


def recap(player, since, until=None):
    """
    What to show when they leave: this sitting's numbers, the streak, any badge they earned, and (only now)
    how many of the beetles we know the answer to they got right. ``until`` ends a sitting that a later one followed.
    """
    sitting = GameAnswer.objects.filter(player=player, answered_at__gte=since)
    if until is not None:
        sitting = sitting.filter(answered_at__lt=until)
    done = sitting.filter(skipped=False)
    scored = sitting.filter(is_check=True, skipped=False, score_hold=False).exclude(mode="pair", pair_answer="unsure")
    right = scored.exclude(correct_subfamily=False).exclude(correct_tribe=False).exclude(correct_genus=False).exclude(correct_species=False).count()
    new = [
        {"key": key, "name": BADGES[key][0], "how": BADGES[key][1], "icon": BADGES[key][2], "tier": badge_tier(key)}
        for key in BADGES if key in earned_badges(player) and key not in earned_badges(player, before=since)
    ]
    state = progress(player)
    from django.db.models import Sum
    from .models import AnswerPoints
    points = AnswerPoints.objects.filter(answer__in=sitting).aggregate(s=Sum("points"))["s"] or 0.0
    from . import game
    accuracy = right / scored.count() if scored.exists() else None
    challenge = game.target_difficulty(player)
    return {
        "accuracy": accuracy, "accuracy_tier": value_step(accuracy),
        "challenge": round(challenge * 100), "challenge_tier": value_step(challenge),
        "points": round(points, 1),
        "labelled": done.count(), "skipped": sitting.filter(skipped=True).count(),
        "scored": scored.count(), "right": right,
        "streak": state["streak"], "level": state["level"], "level_name": state["level_name"],
        "today": state["today"], "goal": state["goal"], "badges": new,
    }
