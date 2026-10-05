"""
Levels and the perks they unlock in the Beetle ID game.

A level needs two things: **points** (they grow the more you play, see game_scoring.py) and **reliability**
(how often you are right about beetles we know, the rating in PlayerScore). So a level says both how much a
player has played and how far their answers can be trusted. Reliability can go down as well as up, so the higher
levels are kept only while the answers stay good.

    level  name                         points  reliability  unlocks
    1      Egg                               0       -       Similarity
    2      Larva                            50       -       Odd One Out, and choosing your game
    3      Pupa                            150      35%      Select all; focus on one subfamily; more photos of each beetle
    4      Teneral                     400      50%      the identification game; focus on one tribe; lighting
    5      Tunnel master                   800      60%      focus on one genus
    6      Gallery engineer               1500      70%      your labels go to curators as suggestions
    7      Fungus farmer                  3000      75%
    8      Brood guardian                 6000      80%
    9      Colony founder                10000      85%
    10     King of Bark and Ambrosia     25000      92%

Separately from levels, a player who proves themselves on one part of the tree is an *expert* there (game_trust.py:
at least GAME_TRUST_MIN_JUDGED answers on validated beetles in that branch, with a Wilson lower bound on their
accuracy of at least GAME_TRUST_MIN_LOWER_BOUND, 90% by default, and among the most reliable players overall). When
at least two experts, each proven directly in every branch of the label, agree on a beetle that has no name yet,
their name is written straight into the database (game_trust.auto_apply_expert_labels), still marked unvalidated so
a curator can confirm it. Nobody else's labels skip review.
"""
from .game import game_setting

PROPOSALS = "proposals"
CHOOSE_GAME = "choose_game"
ODD_ONE_OUT = "odd_one_out"
SELECT_ALL = "select_all"
IDENTIFY = "identification"
SPECIMEN_PHOTOS = "specimen_photos"
LIGHT = "light"
PERKS = {
    ODD_ONE_OUT: ("Odd One Out", "A new game: tap the beetle that doesn't belong with the rest."),
    CHOOSE_GAME: ("Choose your game", "Play one game, or a mix of every game you have."),
    SELECT_ALL: ("Select all", "A new game: tap every beetle of one group in a grid of nine."),
    IDENTIFY: ("Identification game", "Name beetles: subfamily, tribe, genus and species."),
    "focus_subfamily": ("Focus on a subfamily", "Choose one subfamily and the game shows you only its beetles."),
    "focus_tribe": ("Focus on a tribe", "Narrow your focus to a single tribe."),
    "focus_genus": ("Focus on a genus", "Narrow your focus to a single genus."),
    SPECIMEN_PHOTOS: (
        "More photos of each beetle",
        "When the same beetle was photographed more than once, see its other photos too (only photos of that one beetle).",
    ),
    LIGHT: ("Light", "Make a dark or flat photo brighter or sharper while you look at it."),
    PROPOSALS: (
        "Your labels go to curators",
        "You seem to know your bark beetles: your names for beetles nobody has checked yet are now sent to the "
        "curators as suggestions.",
    ),
}

# (points, reliability, name, perks). The games open one by one, easiest first: Similarity from the start, then Odd One
# Out, Select all and Identification (#369, #370). Players who had Identification before it moved up keep it (kept_perks).
LEVELS = [
    (0, 0.0, "Egg", []),
    (50, 0.0, "Larva", [ODD_ONE_OUT, CHOOSE_GAME]),
    (150, 0.35, "Pupa", [SELECT_ALL, "focus_subfamily", SPECIMEN_PHOTOS]),
    (400, 0.5, "Teneral", [IDENTIFY, "focus_tribe", LIGHT]),
    # After the teneral adult, a bark beetle's life: it bores in, carves its galleries, farms its fungus, guards its
    # brood and founds a colony. (Not "Taxonomist": that word is kept for real taxonomists' identifications.)
    (800, 0.6, "Tunnel master", ["focus_genus"]),
    (1500, 0.7, "Gallery engineer", [PROPOSALS]),
    (3000, 0.75, "Fungus farmer", []),
    (6000, 0.8, "Brood guardian", []),
    (10000, 0.85, "Colony founder", []),
    (25000, 0.92, "King of Bark and Ambrosia", []),
]
FOCUS_PERK = {"subfamily": "focus_subfamily", "tribe": "focus_tribe", "genus": "focus_genus"}
# One icon per level (Flaticon uicons, regular rounded), from egg to crown
LEVEL_ICONS = ["fi-rr-egg", "fi-rr-worm", "fi-rr-hourglass", "fi-rr-bug", "fi-rr-pickaxe", "fi-rr-route",
               "fi-rr-mushroom", "fi-rr-shield", "fi-rr-house-tree", "fi-rr-crown"]


def level_icon(level):
    """The icon class for a level (1-based)."""
    try:
        return LEVEL_ICONS[max(1, min(int(level), len(LEVEL_ICONS))) - 1]
    except (TypeError, ValueError):
        return LEVEL_ICONS[0]


def level_index(score, rating):
    """0-based index of the highest level whose points and reliability are both reached."""
    reached = 0
    for i, (points, reliability, _, _) in enumerate(LEVELS):
        if score >= points and rating >= reliability:
            reached = i
        else:
            break
    return reached


def perk_level(perk):
    """1-based level that unlocks ``perk``."""
    return next(i for i, (_, _, _, perks) in enumerate(LEVELS) if perk in perks) + 1


def pair_share(level):
    """
    Share of Family Ties in the mixed feed: beginners get mostly Family Ties (easier to answer usefully), experts
    mostly Name That Beetle. From GAME_PAIR_SHARE_START at level 1 down to GAME_PAIR_SHARE_END at the top level.
    """
    start = game_setting("GAME_PAIR_SHARE_START", 0.7)
    end = game_setting("GAME_PAIR_SHARE_END", 0.15)
    return start + (end - start) * (level - 1) / (len(LEVELS) - 1)


# The games, in the order they open, and the unlock each needs (Similarity needs none)
GAMES = ("pair", "odd", "select", "classify")
GAME_PERK = {"odd": ODD_ONE_OUT, "select": SELECT_ALL, "classify": IDENTIFY}
GAME_NAMES = {"pair": "Similarity", "odd": "Odd One Out", "select": "Select all", "classify": "Identification"}


def games(perks):
    """The games these unlocks open, easiest first."""
    return [g for g in GAMES if g not in GAME_PERK or GAME_PERK[g] in perks]


def game_level(game):
    """1-based level that opens ``game``."""
    return perk_level(GAME_PERK[game]) if game in GAME_PERK else 1


def game_shares(level, available):
    """
    {game: share of the mixed feed} over the ``available`` games. Similarity and Identification split as pair_share
    says (beginners mostly Similarity, experts mostly Identification); Odd One Out and Select all weigh GAME_ODD_SHARE
    and GAME_SELECT_SHARE beside them.
    """
    pair = pair_share(level)
    weights = {"pair": pair, "odd": game_setting("GAME_ODD_SHARE", 0.3), "select": game_setting("GAME_SELECT_SHARE", 0.25),
               "classify": 1 - pair}
    weights = {g: w for g, w in weights.items() if g in available}
    total = sum(weights.values())
    if total <= 0:   # e.g. Similarity alone, with its share set to nothing: it still plays
        return {g: 1 / len(weights) for g in weights}
    return {g: w / total for g, w in weights.items()}


def unlocked_perks(index):
    return {perk for _, _, _, perks in LEVELS[: index + 1] for perk in perks}


def proposal_level():
    """1-based level from which a player's labels go to curators as suggestions."""
    return next(i for i, (_, _, _, perks) in enumerate(LEVELS) if PROPOSALS in perks) + 1


def describe(score, rating):
    """Everything the game home, the feed and the unlocks page show about a player's level."""
    index = level_index(score, rating)
    points, reliability, name, _ = LEVELS[index]
    nxt = LEVELS[index + 1] if index + 1 < len(LEVELS) else None
    info = {
        "level": index + 1, "name": name, "score": score, "rating": rating,
        "perks": unlocked_perks(index), "proposals": PROPOSALS in unlocked_perks(index),
        "next": None, "progress": 1.0,
    }
    if nxt:
        need_points, need_rating, next_name, next_perks = nxt
        span = max(1.0, need_points - points)
        info["next"] = {
            "level": index + 2, "name": next_name, "points": need_points,
            "points_needed": max(0, round(need_points - score)),
            "rating_needed": need_rating, "rating_short": rating < need_rating,
            "perks": [PERKS[p][0] for p in next_perks],
        }
        info["progress"] = max(0.0, min(1.0, (score - points) / span))
    return info


def _preference_perks(player_or_id):
    """(granted_perks, kept_perks) from the player's GamePreference, both lists."""
    from .models import GamePreference

    pid = getattr(player_or_id, "pk", player_or_id)
    row = GamePreference.objects.filter(player_id=pid).values_list("granted_perks", "kept_perks").first()
    return (row[0] or [], row[1] or []) if row else ([], [])


def _granted_from(perks):
    return set(PERKS) if "all" in perks else {p for p in perks if p in PERKS}


def granted(player_or_id):
    """Unlocks a superuser granted this player, whatever their level (GamePreference.granted_perks)."""
    return _granted_from(_preference_perks(player_or_id)[0])


def for_player(player):
    """
    describe() for this player, plus any unlocks a superuser granted them, and any they kept from before the levels
    changed (those count as unlocks, but unlike a grant they don't open every rank).
    """
    from .game_scoring import score_for
    s = score_for(player)
    info = describe(s.score, s.rating)
    granted_perks, kept_perks = _preference_perks(player)
    extra = _granted_from(granted_perks)
    kept = {p for p in kept_perks if p in PERKS}
    if extra or kept:
        info["perks"] = info["perks"] | extra | kept
        info["proposals"] = PROPOSALS in info["perks"]
    if extra:
        info["granted"] = sorted(extra)
    return info


# ---------------------------------------------------------------------------
# Rank steps: newcomers first name only the subfamily, then the tribe, the genus and the species open after a few
# beetles (answers given, right or wrong), usually within the first session. Players from level
# RANKS_ALL_FROM_LEVEL, or with unlocks granted by a superuser, have every rank.
# ---------------------------------------------------------------------------
RANK_ORDER = ("subfamily", "tribe", "genus", "species")
RANK_STEPS = {"tribe": 5, "genus": 15, "species": 30}
RANKS_ALL_FROM_LEVEL = 3


def rank_steps():
    """{rank: answers needed} for the ranks below subfamily (GAME_RANK_UNLOCK_ANSWERS)."""
    return dict(RANK_STEPS, **(game_setting("GAME_RANK_UNLOCK_ANSWERS", {}) or {}))


def rank_unlock(level, answered, granted_any=False):
    """
    {"rank": deepest rank the player may name, "next": {"rank", "at", "needed"} or None}. Similarity follows it too:
    its rungs go no deeper than this rank.
    """
    if level >= RANKS_ALL_FROM_LEVEL or granted_any:
        return {"rank": "species", "next": None}
    steps = rank_steps()
    deepest, nxt = "subfamily", None
    for rank in RANK_ORDER[1:]:
        if answered >= steps[rank]:
            deepest = rank
        else:
            nxt = {"rank": rank, "at": steps[rank], "needed": steps[rank] - answered}
            break
    return {"rank": deepest, "next": nxt}


def rank_for(player):
    """rank_unlock() for this player."""
    from .models import GameAnswer

    info = for_player(player)
    answered = GameAnswer.objects.filter(player=player, skipped=False).count()
    return rank_unlock(info["level"], answered, bool(info.get("granted")))


def table():
    """The level ladder for the unlocks page."""
    return [
        {"level": i + 1, "name": name, "points": points, "rating": reliability,
         "perks": [{"key": p, "title": PERKS[p][0], "text": PERKS[p][1]} for p in perks]}
        for i, (points, reliability, name, perks) in enumerate(LEVELS)
    ]


def players_at_or_above(level):
    """Ids of players whose current level is at least ``level`` (1-based)."""
    from .models import PlayerScore
    return {
        pid for pid, score, rating in PlayerScore.objects.values_list("player_id", "score", "rating")
        if level_index(score, rating) + 1 >= level
    }


def proposals_enabled():
    return game_setting("GAME_PROPOSALS_NEED_LEVEL", True)


def suggestion_voters():
    """
    Whose game labels reach the curators as suggestions: players at the suggestions level, and anyone who is a
    proven expert somewhere. None (everyone) when GAME_PROPOSALS_NEED_LEVEL is off.
    """
    if not proposals_enabled():
        return None
    from .models import PlayerSkill
    experts = set(PlayerSkill.objects.filter(proven=True).values_list("player_id", flat=True))
    from .models import GamePreference
    granted_ids = {
        pid for pid, perks in GamePreference.objects.exclude(granted_perks=[]).values_list("player_id", "granted_perks")
        if "all" in (perks or []) or PROPOSALS in (perks or [])
    }
    return players_at_or_above(proposal_level()) | experts | granted_ids
