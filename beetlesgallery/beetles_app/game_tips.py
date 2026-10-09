"""
Tips for curators on the Image Annotation page, from what Beetle ID players said about a beetle.

Two kinds:

* **Agreement**: Naming experts, or many reliable players, agree on a name at some rank. The deepest such
  rank is the tip ("Naming experts say genus Xyleborus"). If it disagrees with the beetle's current label,
  the tip says so.
* **Not in**: answers also say what a beetle is *not*, and each answer writes that down (game_negatives): in Odd One
  Out the picked beetle is not of the rest's group; in Find Them All the beetles left untapped are not of the grid's
  group; and in Similarity a player who says an unnamed beetle and a validated *Xyleborus affinis* are only "same
  tribe" says the beetle is not a *Xyleborus*. When enough reliable players say so, and few or none say the
  opposite, it becomes a tip ("Players are confident it is not in genus Xyleborus").

Only the answers of players whose labels reach the curators count (game_levels.suggestion_voters).
Settings: GAME_TIP_MIN_VOTES (3), GAME_TIP_MIN_SUPPORT (0.75), GAME_TIP_MIN_NOT_VOTES (2).
"""
from collections import defaultdict

from . import game, game_levels, game_negatives
from .game import RANKS, game_setting
from .models import GameAnswer, PlayerSkill


def _current(roi, rank):
    t = roi.taxon
    if t is None:
        return ""
    return f"{t.genus} {t.species}" if rank == "species" and t.genus and t.species else (getattr(t, rank, "") or "")


def _agreement_tip(entry):
    min_votes = game_setting("GAME_TIP_MIN_VOTES", 3)
    min_support = game_setting("GAME_TIP_MIN_SUPPORT", 0.75)
    for rank in reversed(RANKS):
        v = entry["ranks"].get(rank)
        if not v:
            continue
        if v.get("trusted"):
            kind, who = "expert", v.get("trusted_votes") or 1
        elif v["votes"] >= min_votes and v["support"] >= min_support:
            kind, who = "players", v["votes"]
        else:
            continue
        current = _current(entry["roi"], rank)
        return {
            "kind": kind, "rank": rank, "value": v["value"], "count": who, "support": round(v["support"], 3),
            "conflicts": bool(current) and current.lower() != v["value"].lower(), "current": current,
        }
    return None


def _not_tips(roi_ids, voters):
    roi_ids = list(roi_ids)
    # what players said each beetle is not, as written down with their answers (game_negatives)
    against, display = {}, {}
    for roi_id, by_rank in game_negatives.against(roi_ids, voters).items():
        for rank, names in by_rank.items():
            for key, slot in names.items():
                against[(roi_id, rank, key)] = slot["players"]
                display[(roi_id, rank, key)] = slot["value"]
    if not against:
        return {}
    # and what they said it is: a name, a Similarity rung, a tap, the rest of a solved Odd One Out grid
    inside = defaultdict(set)
    answers = GameAnswer.objects.filter(roi_id__in=roi_ids, skipped=False).select_related("roi_b__taxon")
    if voters is not None:
        answers = answers.filter(player_id__in=list(voters))
    for ans in answers:
        for rank, value in game.implied_labels(ans).items():
            inside[(ans.roi_id, rank, value.lower())].add(ans.player_id)
    for roi_id, pid, vote in game.tap_votes(roi_ids, voters):
        for rank, value in vote.items():
            inside[(roi_id, rank, value.lower())].add(pid)
    experts = set(PlayerSkill.objects.filter(proven=True, player_id__in={p for s in against.values() for p in s})
                  .values_list("player_id", flat=True))
    min_votes = game_setting("GAME_TIP_MIN_NOT_VOTES", 2)
    min_support = game_setting("GAME_TIP_MIN_SUPPORT", 0.75)
    rois = game.Beetles.objects.select_related("taxon").in_bulk({k[0] for k in against})
    tips = defaultdict(list)
    for key, players in against.items():
        roi_id, rank, _ = key
        if roi_id not in rois:
            continue
        said_in = inside.get(key, set()) - players
        if len(players) < min_votes or len(players) / (len(players) + len(said_in)) < min_support:
            continue
        value = display[key]
        tips[roi_id].append({
            "kind": "not", "rank": rank, "value": value, "count": len(players),
            "experts": len(players & experts), "against": len(said_in),
            "conflicts": _current(rois[roi_id], rank).lower() == value.lower(),
        })
    for items in tips.values():
        items.sort(key=lambda t: (RANKS.index(t["rank"]), -t["count"]))
    return tips


def tips(roi_ids):
    """{roi_id: [tip, ...]} for these ROIs. Agreement first, then what the beetle is not."""
    roi_ids = list(roi_ids)
    voters = game_levels.suggestion_voters()
    out = defaultdict(list)
    for entry in game.consensus(roi_ids=roi_ids, voters=voters):
        tip = _agreement_tip(entry)
        if tip:
            out[entry["roi"].id].append(tip)
    for roi_id, items in _not_tips(roi_ids, voters).items():
        out[roi_id].extend(items)
    return dict(out)


def text(tip):
    """One plain sentence for a tip (the annotation page builds the same sentence in JavaScript)."""
    rank = tip["rank"]
    if tip["kind"] == "not":
        who = "Naming experts and players" if tip.get("experts") else "Players"
        return f"{who} are confident it is not in {rank} {tip['value']}."
    who = "Naming experts" if tip["kind"] == "expert" else f"{tip['count']} reliable players"
    line = f"{who} say {rank} {tip['value']}."
    if tip.get("conflicts"):
        line += f" The current label says {tip['current']}."
    return line
