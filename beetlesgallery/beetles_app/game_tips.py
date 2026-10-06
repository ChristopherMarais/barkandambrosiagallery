"""
Tips for curators on the Image Annotation page, from what Beetle ID players said about a beetle.

Two kinds:

* **Agreement**: Naming experts, or many reliable players, agree on a name at some rank. The deepest such
  rank is the tip ("Naming experts say genus Xyleborus"). If it disagrees with the beetle's current label,
  the tip says so.
* **Not in**: answers also say what a beetle is *not*. In Odd One Out the picked beetle is not of the rest's group;
  in Select all the beetles left untapped are not of the grid's group (game.grid_exclusions). And Family Ties answers: A player who says an unnamed beetle and a
  validated *Xyleborus affinis* are only "same tribe" says the beetle is not a *Xyleborus*. When enough reliable
  players say so, and few or none say the opposite, it becomes a tip ("Players are confident it is not in genus
  Xyleborus").

Only the answers of players whose labels reach the curators count (game_levels.suggestion_voters).
Settings: GAME_TIP_MIN_VOTES (3), GAME_TIP_MIN_SUPPORT (0.75), GAME_TIP_MIN_NOT_VOTES (2).
"""
from collections import defaultdict

from . import game, game_levels
from .game import PAIR_DEPTH, RANKS, game_setting
from .game_scoring import is_truth
from .models import GameAnswer, PlayerSkill


def excluded(answer):
    """(rank, value) a Family Ties answer says the beetle is not in, or None. Species values are "Genus species"."""
    if answer.mode != "pair" or answer.skipped or not is_truth(answer.roi_b):
        return None
    depth = PAIR_DEPTH.get(answer.pair_answer)
    if depth is None or depth >= len(RANKS) - 1:
        return None
    rank = RANKS[depth + 1]
    partner = answer.roi_b.taxon
    value = f"{partner.genus} {partner.species}" if rank == "species" else getattr(partner, rank)
    if not value or (rank == "species" and not partner.species):
        return None
    return rank, value


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
    answers = GameAnswer.objects.filter(roi_id__in=roi_ids, skipped=False).select_related("roi", "roi_b__taxon")
    if voters is not None:
        answers = answers.filter(player_id__in=list(voters))
    answers = list(answers)
    against, inside, display = defaultdict(set), defaultdict(set), {}
    for ans in answers:
        hit = excluded(ans)
        if hit:
            key = (ans.roi_id, hit[0], hit[1].lower())
            against[key].add(ans.player_id)
            display.setdefault(key, hit[1])
        for rank, value in game.implied_labels(ans).items():
            inside[(ans.roi_id, rank, value.lower())].add(ans.player_id)
    # the grid games: a beetle picked as the odd one, or left untapped, is not of that group
    wanted = {str(r) for r in roi_ids}
    grids = GameAnswer.objects.filter(game.showing(roi_ids), mode__in=["odd", "select"], skipped=False).select_related("roi")
    if voters is not None:
        grids = grids.filter(player_id__in=list(voters))
    for ans in grids:
        for roi_id, rank, value in game.grid_exclusions(ans):
            if str(roi_id) in wanted:
                key = (roi_id, rank, value.lower())
                against[key].add(ans.player_id)
                display.setdefault(key, value)
    for roi_id, pid, vote in game.tap_votes(roi_ids, voters):
        for rank, value in vote.items():
            inside[(roi_id, rank, value.lower())].add(pid)
    if not against:
        return {}
    experts = set(PlayerSkill.objects.filter(proven=True, player_id__in={p for s in against.values() for p in s})
                  .values_list("player_id", flat=True))
    min_votes = game_setting("GAME_TIP_MIN_NOT_VOTES", 2)
    min_support = game_setting("GAME_TIP_MIN_SUPPORT", 0.75)
    rois = {a.roi_id: a.roi for a in answers}
    rois.update(game.Beetles.objects.in_bulk({k[0] for k in against} - set(rois)))
    tips = defaultdict(list)
    for key, players in against.items():
        roi_id, rank, _ = key
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
