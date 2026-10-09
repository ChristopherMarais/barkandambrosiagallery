"""
Tips for curators on the Image Annotation page, from what Beetle ID players said about a beetle.

Two kinds:

* **Agreement**: Naming experts, or many reliable players, agree on a name at some rank. The deepest such
  rank is the tip ("Naming experts say genus Xyleborus"). If it disagrees with the beetle's current label,
  the tip says so.
* **Not in**: answers also say what a beetle is *not* (game.ruled_out, one place for every game): in Find Them All
  the beetles left untapped are not of the grid's group; in Odd One Out the picked beetles are not of the rest's
  group; in Similarity a player who says an unnamed beetle and a validated *Xyleborus affinis* are only "same tribe"
  says the beetle is not a *Xyleborus*. A name for something else says so too (a beetle named *Platypus* is not a
  *Xyleborus*: game.rules_out). When enough players say so, and few or none say the opposite, it becomes a tip
  ("Players are confident it is not in genus Xyleborus"). Only a name somebody ruled out becomes a "not in" tip:
  what the names agree on is the agreement tip's.

Both read the same claims as the players' confidence everywhere else (game.evidence), and only the answers of players
whose labels reach the curators count (game_levels.suggestion_voters).
Settings: GAME_TIP_MIN_VOTES (3), GAME_TIP_MIN_SUPPORT (0.75), GAME_TIP_MIN_NOT_VOTES (2).
"""
from collections import defaultdict

from . import game, game_levels
from .game import RANKS, game_setting
from .models import PlayerSkill


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


def _not_tips(roi_ids, voters, found=None):
    """
    {roi_id: ["not in" tip, ...]}: for each name somebody ruled out (game.ruled_out), the players who say the beetle
    is not that (ruled it out, or named something else: game.rules_out) against those who named it. ``found`` is
    game.evidence() already read for these beetles and voters.
    """
    if found is None:
        found = game.evidence(roi_ids, voters)
    against, ruled = defaultdict(set), {}
    for roi_id, entry in found.items():
        for pid, claim in entry["ruled"]:
            key = (roi_id, claim.rank, claim.value.lower())
            against[key].add(pid)
            ruled.setdefault(key, claim)
    if not against:
        return {}
    inside = defaultdict(set)
    for key, claim in ruled.items():
        roi_id, rank, value = key
        for pid, labels in found[roi_id]["votes"]:
            if (labels.get(rank) or "").lower() == value:
                inside[key].add(pid)
            elif game.rules_out(labels, claim):   # a name for something else: Naming's "not"
                against[key].add(pid)
    experts = set(PlayerSkill.objects.filter(proven=True, player_id__in={p for s in against.values() for p in s})
                  .values_list("player_id", flat=True))
    min_votes = game_setting("GAME_TIP_MIN_NOT_VOTES", 2)
    min_support = game_setting("GAME_TIP_MIN_SUPPORT", 0.75)
    rois = {roi_id: entry["roi"] for roi_id, entry in found.items() if entry["roi"] is not None}
    rois.update(game.Beetles.objects.select_related("taxon").in_bulk({k[0] for k in against} - set(rois)))
    tips = defaultdict(list)
    for key, players in against.items():
        roi_id, rank, _ = key
        said_in = inside.get(key, set()) - players
        if len(players) < min_votes or len(players) / (len(players) + len(said_in)) < min_support:
            continue
        value = ruled[key].value
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
    found = game.evidence(roi_ids, voters)   # read once for both kinds
    out = defaultdict(list)
    for entry in game.consensus(roi_ids=roi_ids, voters=voters, found=found):
        tip = _agreement_tip(entry)
        if tip:
            out[entry["roi"].id].append(tip)
    for roi_id, items in _not_tips(roi_ids, voters, found).items():
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
