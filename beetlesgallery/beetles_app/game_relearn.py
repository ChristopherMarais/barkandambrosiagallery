"""
Learning from mistakes, and hard beetles through the easy games first (#490).

Mistakes come back. A validated beetle the player got wrong, in any game, comes back in a later sitting (not the one
they missed it in) until they get it right, at most GAME_RETRY_MAX times and GAME_RETRY_PER_BATCH per batch. It comes
back first in an easier game than the one it was missed in (Similarity < Odd One Out < Select all < Identification,
among the games the player has), and once they get it right there, in the game it was missed in. A mistake in the
easiest game they have comes back in that game. Retries earn GAME_POINTS_RETRY_FACTOR of the points and stay out
of ratings, skills and badges (GameAnswer.is_retry). Any other beetle whose names a player has seen comes back too,
after a while (game.held_back_ids), at full points but likewise out of ratings and skills (GameAnswer.seen_before).

Hard beetles go through the easy games first. An unvalidated beetle is hard when players disagree on it, nobody could
take it to species, IBBI-AI is unsure of it, or nobody has answered it and IBBI-AI has no confident call (hard_q).
Similarity leans towards hard beetles (hard_rois); Identification prefers beetles Similarity or a confident IBBI-AI
has already placed in a subfamily or tribe, and holds hard, unplaced ones back for the easier games while there are
others (identification_open). So a label is checked in more than one game before anyone names it.
"""
import random
import uuid
from collections import defaultdict
from datetime import timedelta

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from . import game
from .game import RANKS, game_setting
from .models import Beetles, GameAnswer, GameRound, ModelPrediction, RoiDifficulty

# The games from easiest to hardest (game_levels.GAMES has them in this order too)
LADDER = ("pair", "odd", "select", "classify")
# The Similarity partner that tests a beetle at a rank: a near relative with another name at that rank
NEAR_RELATION = {"subfamily": "different", "tribe": "subfamily", "genus": "tribe", "species": "genus"}
# Similarity: how related two beetles are, by the deepest rank they share (game.PAIR_DEPTH the other way round)
RELATION_AT_DEPTH = {d: name for name, d in game.PAIR_DEPTH.items()}
GRID_TILES = 4


# ---------------------------------------------------------------------------
# Sittings
# ---------------------------------------------------------------------------
def sitting_start(player, now=None):
    """
    When the player's current sitting began: their answers since then follow each other with no break of
    GAME_SESSION_GAP_MINUTES or more. ``now`` itself when they are only just sitting down.
    """
    gap = timedelta(minutes=game_setting("GAME_SESSION_GAP_MINUTES", 30))
    start = now or timezone.now()
    times = (GameAnswer.objects.filter(player=player, answered_at__lte=start)
             .order_by("-answered_at").values_list("answered_at", flat=True)[:2000])
    for when in times:
        if start - when >= gap:
            break
        start = when
    return start


# ---------------------------------------------------------------------------
# Mistakes
# ---------------------------------------------------------------------------
def _first_wrong(row):
    return next((r for r in RANKS if row[f"correct_{r}"] is False), None)


def _verdict(row):
    """True (right), False (wrong) or None (nothing judged) for a scored answer."""
    oks = [row[f"correct_{r}"] for r in RANKS if row[f"correct_{r}"] is not None]
    return None if not oks else all(oks)


def _pair_relation(row):
    """How related the two beetles of a scored Similarity answer really are, from what was right and wrong in it."""
    said = game.PAIR_DEPTH.get(row["pair_answer"])
    if said is None:
        return None
    depth = -1
    for i, r in enumerate(RANKS):
        ok = row[f"correct_{r}"]
        if ok is None:
            break
        if (i <= said) == ok:   # the answer claimed they share this rank and was right, or denied it and was wrong
            depth = i
        else:
            break
    return RELATION_AT_DEPTH[depth]


def _grid_misses(rows):
    """
    {answer id: [beetle ids picked or tapped wrongly, or left out]} for imperfect Select all grids and for Odd One Out
    grids answered with ``picks`` (#540: several odd ones, so every odd one missed and every wrong pick comes back).
    """
    grids = [r for r in rows if _verdict(r) is False and (r["mode"] == "select" or (r["mode"] == "odd" and r["picks"]))]
    found = Beetles.objects.select_related("taxon").in_bulk({uuid.UUID(str(t)) for r in grids for t in r["tiles"] or []})
    score = {"select": game.score_select, "odd": game.score_odd_grid}
    out = {}
    for r in grids:
        tiles = [found.get(uuid.UUID(str(t))) for t in r["tiles"] or []]
        states = score[r["mode"]](tiles, r["picks"], r["grid_rank"], r["grid_group"], r["flagged"])["tiles"]
        out[r["id"]] = [t.id for t, s in zip(tiles, states) if t is not None and s in ("wrong", "missed")]
    return out


def _events(player):
    """The events of the player's answers (_compute_events), kept while their answers stand (game.per_history)."""
    return game.per_history(player, "events", _compute_events)


def _compute_events(player):
    """
    {beetle id: [event, ...]} oldest first, from the player's scored answers on validated beetles in every game.
    An event is {"when", "ok", "mode", "rank", "relation", "retry"}: ``ok`` whether the beetle the answer was about
    came out right; ``rank`` where a wrong one went wrong; ``relation`` the Similarity partner that tests it there;
    ``retry`` whether it was a retry of that very beetle.
    The beetle an answer is about: Identification and Similarity its beetle (in Similarity the one that isn't the
    partner); Odd One Out the odd one, and when a pick was wrong every beetle wrongly picked and every odd one left
    out too; Select all its member, and when the grid was not perfect every beetle tapped wrongly or left out.
    """
    rows = list(
        GameAnswer.objects.filter(player=player, is_check=True, skipped=False, score_hold=False, mode__in=LADDER)
        .order_by("answered_at", "index")
        .values("id", "roi_id", "roi_b_id", "mode", "is_retry", "answered_at", "pair_answer", "tiles", "picks",
                "grid_rank", "grid_group", "flagged", *[f"correct_{r}" for r in RANKS])
    )
    misses = _grid_misses(rows)
    events = defaultdict(list)
    for row in rows:
        ok = _verdict(row)
        if ok is None:
            continue
        mode = row["mode"]
        subject = row["roi_b_id"] if mode == "odd" else row["roi_id"]
        base = {"when": row["answered_at"], "mode": mode}
        if ok:
            events[subject].append(dict(base, ok=True, retry=row["is_retry"]))
            continue
        rank = row["grid_rank"] if mode in ("odd", "select") else _first_wrong(row)
        if rank not in RANKS:
            continue
        relation = _pair_relation(row) if mode == "pair" else NEAR_RELATION[rank]
        wrong = dict(base, ok=False, rank=rank, relation=relation or NEAR_RELATION[rank])
        # in an imperfect grid its member (or an odd one) may have been chosen right; a retry grid, though, was all
        # about it
        if row["id"] not in misses or row["is_retry"] or subject in misses[row["id"]]:
            events[subject].append(dict(wrong, retry=row["is_retry"]))
        # an Odd One Out pick from before several odd ones (#540) is just the one beetle, ``roi``
        others = misses[row["id"]] if row["id"] in misses else [row["roi_id"]] if mode == "odd" else []
        for beetle in others:
            if beetle != subject:
                events[beetle].append(dict(wrong, retry=False))
    return events


def open_mistakes(player):
    """
    {beetle id: mistake} for the player's mistakes not put right yet, each {"origin": the game it was missed in,
    "stage": "easier" (come back in an easier game first) or "origin" (got right there: back in the original game),
    "rank", "relation", "last": the last try, "tries": retries so far}.
    """
    out = {}
    for beetle, events in _events(player).items():
        state = None
        for ev in events:
            if not ev["ok"]:
                origin = state["origin"] if state and ev["retry"] else ev["mode"]
                tries = (state["tries"] if state else 0) + int(ev["retry"])
                state = {"origin": origin, "stage": "easier", "rank": ev["rank"], "relation": ev["relation"],
                         "last": ev["when"], "tries": tries}
            elif state is not None:
                state["tries"] += int(ev["retry"])
                if LADDER.index(ev["mode"]) >= LADDER.index(state["origin"]):
                    state = None   # right in the game it was missed in (or a harder one): learned
                else:
                    state.update(stage="origin", last=ev["when"])
        if state is not None:
            out[beetle] = state
    return out


def due(player, now=None):
    """
    The player's mistakes ready to come back: from before this sitting, not put right since, tried fewer than
    GAME_RETRY_MAX times, and still a validated beetle in the game. {beetle id: mistake}, in random order.
    """
    start = sitting_start(player, now)
    most = game_setting("GAME_RETRY_MAX", 3)
    ready = {b: m for b, m in open_mistakes(player).items() if m["last"] < start and m["tries"] < most}
    if not ready:
        return {}
    usable = list(game.check_rois().filter(id__in=list(ready)).values_list("id", flat=True))
    random.shuffle(usable)
    return {b: ready[b] for b in usable}


def game_for(mistake, available):
    """
    Which of the ``available`` games (easiest first) a mistake comes back in: the hardest one easier than where it was
    missed, and the original game once it was got right there. Where that game isn't available, the nearest one.
    """
    origin = LADDER.index(mistake["origin"])
    if mistake["stage"] == "easier":
        easier = [g for g in available if LADDER.index(g) < origin]
        wanted = LADDER.index(easier[-1]) if easier else origin
    else:
        wanted = origin
    return min(available, key=lambda g: (abs(LADDER.index(g) - wanted), LADDER.index(g) > wanted))


# ---------------------------------------------------------------------------
# Retry items
# ---------------------------------------------------------------------------
def _clamp(rank, deepest):
    return rank if RANKS.index(rank) <= RANKS.index(deepest) else deepest


def _classify_item(beetle, mistake, ctx):
    return {"a": str(beetle.id), "b": None, "check": True}


def _pair_item(beetle, mistake, ctx):
    """The beetle beside a validated partner related the way it was missed (game._partner_for)."""
    partner = game._partner_for(beetle, ctx["target"], ctx["revealed"] | ctx["avoid"], relation=mistake["relation"])
    if partner is None:
        return None
    return {"a": str(beetle.id), "b": str(partner), "check": True, "flip": random.random() < 0.5}


def _others(beetle, rank, ctx, exclude_ids=()):
    """Validated beetles named at ``rank`` but not as ``beetle`` is: near relatives (the same parent) first."""
    group = game.lineage(beetle.taxon, rank)
    pool = (game.check_rois().filter(game._named_at(rank)).exclude(game.rank_q(rank, group[rank]))
            .exclude(id__in=list(ctx["revealed"] | ctx["avoid"] | set(exclude_ids))))
    if rank == "subfamily":
        return [pool]
    parent = RANKS[RANKS.index(rank) - 1]
    return [pool.filter(game.rank_q(parent, group[parent])), pool]


def _odd_item(beetle, mistake, ctx):
    """Four beetles: three of one group, and this beetle as the one that doesn't belong at the rank it was missed."""
    rank = _clamp(mistake["rank"], ctx["deepest"])
    if game.lineage(beetle.taxon, rank) is None:
        return None
    for pool in _others(beetle, rank, ctx):
        for anchor in Beetles.objects.select_related("taxon").filter(id__in=game._sample(pool, 3, ctx["target"])):
            group = game.lineage(anchor.taxon, rank)
            if group is None:
                continue
            photos = {beetle.image_asset_id, anchor.image_asset_id}
            if len(photos) < 2:
                continue
            same = (game.check_rois().filter(game.rank_q(rank, group[rank]))
                    .exclude(id__in=list(ctx["revealed"] | ctx["avoid"] | {anchor.id})))
            rest = game._distinct_photos(game._sample(same, 6, ctx["target"]), photos, GRID_TILES - 2)
            if len(rest) < GRID_TILES - 2:
                continue
            tiles = [beetle.id, anchor.id, *rest]
            random.shuffle(tiles)
            return {"a": str(beetle.id), "b": None, "check": True, "tiles": [str(t) for t in tiles], "rank": rank,
                    "group": group}
    return None


def _select_item(beetle, mistake, ctx):
    """Four beetles, "tap every <the beetle's group>": the beetle, another member if there is one, and near relatives."""
    rank = _clamp(mistake["rank"], ctx["deepest"])
    group = game.lineage(beetle.taxon, rank)
    if group is None:
        return None
    photos = {beetle.image_asset_id}
    same = (game.check_rois().filter(game.rank_q(rank, group[rank]))
            .exclude(id__in=list(ctx["revealed"] | ctx["avoid"] | {beetle.id})))
    members = [beetle.id, *game._distinct_photos(game._sample(same, 3, ctx["target"]), photos, 1)]
    others = []
    for pool in _others(beetle, rank, ctx):
        need = GRID_TILES - len(members) - len(others)
        if need <= 0:
            break
        others += game._distinct_photos(game._sample(pool.exclude(id__in=others), need * 3, ctx["target"]), photos, need)
    if len(members) + len(others) < GRID_TILES:
        return None
    tiles = members + others
    random.shuffle(tiles)
    return {"a": str(beetle.id), "b": None, "check": True, "tiles": [str(t) for t in tiles], "rank": rank, "group": group}


BUILD = {"classify": _classify_item, "pair": _pair_item, "odd": _odd_item, "select": _select_item}


def _round_games(player, mode):
    """The games a batch may hold: the round's own game, or in the mix what the player plays (game.play_mode)."""
    from .game_levels import for_player, games

    if mode != GameRound.Mode.MIXED:
        return [mode] if mode in BUILD else []
    info = for_player(player)
    chosen = game.play_mode(player, info)
    return [chosen] if chosen in BUILD else [g for g in games(info["perks"]) if g in BUILD]


def retry_items(player, mode, room, avoid=()):
    """
    Up to ``room`` (and GAME_RETRY_PER_BATCH) items that bring back the player's mistakes, each in the game chosen by
    game_for (another game where that one can't be built), marked "retry" and with its "mode". ``avoid``: beetles
    not to use.
    """
    from .game_levels import rank_for

    want = min(game_setting("GAME_RETRY_PER_BATCH", 2), room)
    available = _round_games(player, mode)
    if want <= 0 or not available:
        return []
    ready = due(player)
    if not ready:
        return []
    # the partners and the rest of a grid: never a beetle whose names were shown a moment ago (game.held_back_ids)
    ctx = {"target": game.target_difficulty(player), "revealed": game.held_back_ids(player),
           "avoid": {uuid.UUID(str(a)) for a in avoid}, "deepest": rank_for(player)["rank"]}
    beetles = Beetles.objects.select_related("taxon").in_bulk(list(ready))
    items = []
    for beetle_id, mistake in ready.items():
        if len(items) >= want:
            break
        beetle = beetles.get(beetle_id)
        if beetle is None or beetle.id in ctx["avoid"]:
            continue
        first = game_for(mistake, available)
        for key in sorted(available, key=lambda g: (g != first, abs(LADDER.index(g) - LADDER.index(first)))):
            item = BUILD[key](beetle, mistake, ctx)
            if item is not None:
                items.append(dict(item, retry=True, mode=key))
                ctx["avoid"] |= {uuid.UUID(i) for i in game._item_ids(item)}
                break
    return items


def _space(feed):
    """Move the retries to evenly spaced check places, so two never come one after the other when it can be helped."""
    places = [i for i, it in enumerate(feed) if it["check"]]
    retries = [it for it in feed if it.get("retry")]
    if len(retries) < 2:
        return feed
    step, offset = len(places) / len(retries), random.random()
    chosen = {places[min(len(places) - 1, int((k + offset) * step))] for k in range(len(retries))}
    checks = iter([feed[p] for p in places if not feed[p].get("retry")])
    retry_iter = iter(retries)
    out = list(feed)
    for p in places:
        out[p] = next(retry_iter) if p in chosen else next(checks)
    return out


def _swap_in(items, retries):
    """
    The batch with each retry in place of the items that show any of its beetles (never one beetle twice in a batch),
    or else of a scored item picked at random, so the batch does not grow.
    """
    out = list(items)
    for retry in retries:
        ids = game._item_ids(retry)
        clash = [it for it in out if not it.get("retry") and ids & game._item_ids(it)]
        if clash:
            out = [it for it in out if not any(it is c for c in clash)]
        else:
            checks = [k for k, it in enumerate(out) if it["check"] and not it.get("retry")]
            if not checks:
                continue
            out.pop(random.choice(checks))
        out.append(retry)
    return out


def feed_with_retries(player, mode, items):
    """
    A batch's items in the order shown (game.spread), with the player's due mistakes in place of some of its scored
    items, spread through it.
    """
    retries = retry_items(player, mode, sum(1 for it in items if it["check"]))
    return _space(game.spread(_swap_in(items, retries) if retries else items))


# ---------------------------------------------------------------------------
# Hard beetles through the easier games first
# ---------------------------------------------------------------------------
def _sure():
    """The IBBI-AI confidence that counts as a confident call: the other side of GAME_HARD_FROM."""
    return 1 - game_setting("GAME_HARD_FROM", 0.5)


def hard_q():
    """
    Q for unvalidated beetles that are hard to name: players disagree on the genus, or IBBI-AI is unsure of it (each
    from GAME_HARD_FROM up, RoiDifficulty); players tried to name it but nobody reached species; or nobody has
    answered it and IBBI-AI has no confident call.
    """
    hard_from = game_setting("GAME_HARD_FROM", 0.5)
    rated = RoiDifficulty.objects.filter(roi=OuterRef("pk"))
    named = GameAnswer.objects.filter(roi=OuterRef("pk"), mode="classify")
    return (
        Q(Exists(rated.filter(Q(game_difficulty__gte=hard_from) | Q(model_difficulty__gte=hard_from))))
        | (Q(Exists(named)) & ~Q(Exists(named.filter(skipped=False).exclude(species=""))))
        | (~Q(Exists(GameAnswer.objects.filter(roi=OuterRef("pk"))))
           & ~Q(Exists(ModelPrediction.objects.filter(roi=OuterRef("pk"), confidence__gte=_sure()))))
    )


def placed_q():
    """
    Q for unvalidated beetles already put in a subfamily or tribe: a Similarity answer says they share at least a
    subfamily with a validated beetle, or IBBI-AI is confident of the subfamily, tribe or species.
    """
    sure = _sure()
    return (
        Q(Exists(GameAnswer.objects.filter(roi=OuterRef("pk"), mode="pair", is_check=False, skipped=False,
                                           pair_answer__in=["subfamily", "tribe", "genus", "species"])))
        | Q(Exists(ModelPrediction.objects.filter(roi=OuterRef("pk")).filter(
            Q(confidence__gte=sure) | Q(rank_confidence__subfamily__confidence__gte=sure)
            | Q(rank_confidence__tribe__confidence__gte=sure))))
    )


def hard_rois(player, pool):
    """Hard beetles in ``pool`` (hard_q) this player hasn't compared yet: Similarity's first pick for them."""
    paired = GameAnswer.objects.filter(player=player, mode="pair").values("roi_id")
    return pool.filter(hard_q()).exclude(id__in=paired)


def identification_open(open_pool, n, target, seen, exclude=(), fresh_only=False):
    """
    ``n`` unvalidated beetles for Identification: at least GAME_ID_PLACED_SHARE of them already placed (placed_q)
    when there are enough, then any but the hard, unplaced ones (they wait for the easier games), and only then any
    beetle at all, so the feed never runs dry.
    """
    if n <= 0:
        return []
    exclude = list(exclude)
    n_placed = round(n * game_setting("GAME_ID_PLACED_SHARE", 0.5))
    ids = game._sample(open_pool.filter(placed_q()), n_placed, target, seen, exclude, allow_seen=False) if n_placed else []
    held_back = hard_q() & ~placed_q()
    ids += game._sample(open_pool.exclude(held_back), n - len(ids), target, seen, exclude + ids, allow_seen=False)
    ids += game._sample(open_pool, n - len(ids), target, seen, exclude + ids, allow_seen=not fresh_only)
    return ids
