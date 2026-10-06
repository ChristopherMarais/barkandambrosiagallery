"""
Round feedback and player reports for the Beetle ID game.

After a round, players see every item with their answer next to what the database
says: the validated label (with a tick or cross per rank) or, for items that aren't
verified yet, the current unverified label and what other players have said.

Players can report an ROI that looks wrong. While a report is open the ROI is kept out
of scoring (game.check_rois) and the reporter's own scored answers on it are held
(GameAnswer.score_hold), so reporting a bad reference never costs them. Staff then
resolve it: "corrected" re-scores every answer on the ROI against its fixed label (or
voids them if it is no longer validated); "confirmed" releases the held answers.
"""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import game
from .models import Beetles, GameAnswer, GameReport


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------
def _label(taxon):
    """Display ranks for a taxon (or None)."""
    if taxon is None:
        return None
    return {
        "subfamily": taxon.subfamily or "", "tribe": taxon.tribe or "", "genus": taxon.genus or "",
        "species": f"{taxon.genus} {taxon.species}" if taxon.genus and taxon.species else "",
        "name": taxon.scientific_name or "",
    }


def _rank_label(taxon, rank):
    """
    _label() down to ``rank`` only: what an Odd One Out round itself was about. (The review after each answer names
    every grid beetle at every rank since #541, and game.reveals counts them all as shown.)
    """
    full = _label(taxon)
    if full is None:
        return None
    keep = game.RANKS[: game.RANKS.index(rank) + 1] if rank in game.RANKS else ()
    out = {r: full[r] if r in keep else "" for r in game.RANKS}
    out["name"] = next((full[r] for r in reversed(keep) if full[r]), "")
    return out


def _answer_label(answer):
    return {
        "subfamily": answer.subfamily, "tribe": answer.tribe, "genus": answer.genus,
        "species": f"{answer.genus} {answer.species}" if answer.genus and answer.species else "",
    }


def _odd_label(answer):
    """What an Odd One Out answer said, for the round review: which one it picked, or how many (#540)."""
    if len(answer.picks or []) > 1:
        return f"{len(answer.picks)} beetles"
    return "The odd one" if answer.roi_id == answer.roi_b_id else "Another one"


def _results(answer):
    """Per rank: True/False when judged, None otherwise."""
    return {r: getattr(answer, f"correct_{r}") for r in game.RANKS}


def _verdict(answer):
    """
    "right", "partly", "wrong" or None for an answer that wasn't judged. Anything claimed that isn't true makes it
    "wrong", however much else was right (#530); "partly" is an answer true as far as it goes that stopped short of the
    truth: a name left blank where the beetle has one, or a Similarity rung more cautious than the truth.
    """
    results = _results(answer)
    if not any(v is not None for v in results.values()):
        return None
    if answer.mode == "pair":
        return pair_verdict(game.PAIR_DEPTH.get(answer.pair_answer), results)
    if False in results.values():
        return "wrong"
    if answer.mode == "classify" and any(getattr(answer, f"ref_{r}") and not _claimed(answer, r) for r in game.RANKS):
        return "partly"
    return "right"


def pair_verdict(depth, results):
    """
    A Similarity answer's verdict from the rung said (``depth``, -1 different subfamilies ... 3 same species) and its
    per-rank results (game.score_pair): "wrong" when a rank it says the two share is not shared (closer than they are),
    or when "different subfamilies" is said of relatives; "partly" when they are closer than said; else "right".
    """
    if depth is None:
        return None
    if False in [results[r] for r in game.RANKS[: depth + 1]]:
        return "wrong"
    below = results[game.RANKS[depth + 1]] if depth + 1 < len(game.RANKS) else None
    if depth < 0:
        return {True: "right", False: "wrong"}.get(below)
    return "partly" if below is False else "right"


def grid_verdict(grid):
    """A Select all grid's verdict (game.score_select): "right" when perfect, "partly" with some found and none wrong."""
    return "right" if grid["perfect"] else "partly" if grid["right"] and not grid["wrong"] else "wrong"


def _side(roi, player_reports, others):
    """What the database says about one ROI in a feedback item."""
    verified = bool(roi.bbox_is_validated and roi.taxon)
    report = player_reports.get(roi.id)
    return {
        "roi_id": str(roi.id),
        "image_id": str(roi.image_asset_id) if roi.image_asset_id else "",
        "url": roi.display_url,
        "box": [roi.bbox_x, roi.bbox_y, roi.bbox_width, roi.bbox_height],
        "verified": verified,
        "label": _label(roi.taxon),
        "others": others.get(roi.id),
        "report": None if report is None else {"status": report.status, "reason": report.get_reason_display()},
    }


def round_feedback(rnd):
    """Every answered item of a finished round with the database's view of it."""
    answers = list(
        rnd.answers.select_related("roi__taxon", "roi__image_asset", "roi_b__taxon", "roi_b__image_asset", "points")
        .order_by("index")
    )
    roi_ids = {a.roi_id for a in answers} | {a.roi_b_id for a in answers if a.roi_b_id}
    # Odd One Out: every beetle of the grid, in the order shown
    tile_ids = {t for a in answers for t in (a.tiles or [])}
    tiles = {str(k): v for k, v in Beetles.objects.select_related("taxon", "image_asset").in_bulk(list(tile_ids)).items()}
    roi_ids |= {t.id for t in tiles.values()}
    player_reports = {
        r.roi_id: r for r in GameReport.objects.filter(reporter=rnd.player, roi_id__in=roi_ids).order_by("created_at")
    }
    # What other players have said about the unverified ones (weighted consensus).
    others = {}
    for entry in game.consensus(roi_ids=[a.roi_id for a in answers if not a.is_check]):
        ranks = entry["ranks"]
        deepest = next((ranks[r] for r in reversed(game.RANKS) if ranks[r]), None)
        if deepest:
            others[entry["roi"].id] = {"value": deepest["value"], "answers": entry["answers"]}

    items = []
    right = scored = 0
    for a in answers:
        truth_odd = truth_select = select_verdict = None
        if a.mode == "select":
            shown = [tiles.get(str(t)) for t in a.tiles or []]
            grid = game.score_select(shown, a.picks, a.grid_rank, a.grid_group, a.flagged)
            sides = []
            for i, tile in enumerate(shown):
                if tile is not None:
                    side = _side(tile, player_reports, others)
                    side.update(state=grid["tiles"][i], picked=i in set(a.picks or []), odd=False,
                                flagged=i in set(a.flagged or []),
                                label=_rank_label(tile.taxon, a.grid_rank))   # only as far as the round showed
                    sides.append(side)
            truth_select = {"rank": a.grid_rank, "target": (a.grid_group or {}).get(a.grid_rank, ""),
                            "right": grid["right"], "wrong": grid["wrong"], "missed": grid["missed"],
                            "members": grid["members"]}
            if not a.skipped and not a.score_hold and grid["members"]:
                select_verdict = grid_verdict(grid)
        elif a.mode == "odd":
            sides = []
            picked = set(game.odd_picks(a))
            odd_places = {i for i, t in enumerate(a.tiles or []) if str(t) == str(a.roi_b_id)}
            if a.picks:   # several odd ones (#540): each one outside the group, by the truth
                shown = [tiles.get(str(t)) for t in a.tiles or []]
                states = game.score_odd_grid(shown, a.picks, a.grid_rank, a.grid_group, a.flagged)["tiles"]
                odd_places |= {i for i, state in enumerate(states) if state in ("right", "missed")}
            for i, tile_id in enumerate(a.tiles or []):
                if str(tile_id) in tiles:
                    side = _side(tiles[str(tile_id)], player_reports, others)
                    side["picked"] = not a.skipped and i in picked
                    side["odd"] = i in odd_places
                    side["flagged"] = i in set(a.flagged or [])
                    if not (side["picked"] or side["odd"]):
                        side["label"] = _rank_label(tiles[str(tile_id)].taxon, a.grid_rank)
                    sides.append(side)
            names = []
            for i in sorted(odd_places):
                tile = tiles.get(str(a.tiles[i]))
                name = (game.lineage(tile.taxon, a.grid_rank) or {}).get(a.grid_rank, "") if tile and tile.taxon and a.grid_rank in game.RANKS else ""
                if name and name not in names:
                    names.append(name)
            truth_odd = {"rank": a.grid_rank, "group": (a.grid_group or {}).get(a.grid_rank, ""),
                         "odd_name": ", ".join(names), "count": len(odd_places)}
        else:
            sides = [_side(a.roi, player_reports, others)]
        if a.mode not in ("odd", "select") and a.roi_b_id:
            sides.append(_side(a.roi_b, player_reports, others))
            if rnd.items[a.index].get("flip"):
                sides.reverse()
        verdict = select_verdict if a.mode == "select" else _verdict(a) if a.is_check and not a.score_hold else None
        if verdict:
            scored += 1
            right += verdict == "right"
        truth_pair = None
        if a.mode == "pair" and a.roi.taxon and a.roi_b and a.roi_b.taxon and a.is_check:
            same = game.shared_ranks(a.roi.taxon, a.roi_b.taxon)
            deepest = next((r for r in reversed(game.RANKS) if same[r]), None)
            truth_pair = dict(game.GameAnswer.PairAnswer.choices).get(deepest or "different")
        items.append({
            "index": a.index,
            "mode": a.mode,
            "skipped": a.skipped,
            "scored": a.is_check,
            "held": a.score_hold,
            "verdict": verdict,
            "results": _results(a),
            "answer": _answer_label(a) if a.mode == "classify" else (
                _odd_label(a) if a.mode == "odd"
                else f"{len(a.picks or [])} tapped" if a.mode == "select"
                else a.get_pair_answer_display()),
            "truth_pair": truth_pair,
            "truth_odd": truth_odd,
            "truth_select": truth_select,
            "sides": sides,
            "losses": answer_losses(a),
        })
    return {"items": items, "right": right, "scored": scored, "losses": loss_summary(answers)}


# ---------------------------------------------------------------------------
# Where points were lost (validated beetles only), so players can learn
# ---------------------------------------------------------------------------
RANK_LABEL = {"subfamily": "Subfamily", "tribe": "Tribe", "genus": "Genus", "species": "Species"}


def _claimed(answer, rank):
    value = getattr(answer, rank) or ""
    return bool(value and (rank != "species" or answer.genus))


def answer_losses(answer, points=None):
    """
    For one answer scored against a validated label: the points it could have earned at each rank and what it
    earned, so the player sees where the rest went. None for anything else.

    Name That Beetle: {"kind": "classify", "ranks": {rank: {"state", "points", "lost"}}, "earned", "lost"}, where
    state is "right", "wrong" (the first wrong rank, which also costs a penalty), "after" (wrong because a rank
    above it was), "stopped" (left blank though the beetle has one) or None (the label doesn't go that deep).
    Similarity: {"kind": "pair", "state": "right" | "cautious" | "too_close" | "wrong", "said", "truth", "earned", "lost"}.
    Odd One Out: {"kind": "odd", "state": "right" | "wrong", "rank", "earned", "lost"}.
    Select all: {"kind": "select", "rank", "right", "wrong", "missed", "members", "earned", "lost"}.
    Identification and Similarity also carry "multiplier": the beetle's difficulty multiplier m (None on older
    answers); a gain was × m, a loss × (2 − m), and "worth" and "lost" follow it (#492).
    """
    from .game_scoring import PAIR_POINTS, RANK_POINTS, difficulty_factor
    from .models import AnswerPoints

    if points is None:
        try:
            points = answer.points
        except AnswerPoints.DoesNotExist:
            return None
    if points.basis != AnswerPoints.Basis.TRUTH or answer.skipped or answer.score_hold:
        return None
    detail = points.detail or {}
    earned = round(points.points - detail.get("participation", 0.0), 1)
    # what a rank or rung was worth follows the beetle's difficulty, like the points themselves (#492)
    retry = game.game_setting("GAME_POINTS_RETRY_FACTOR", 0.5) if detail.get("retry") else 1.0
    factor = retry * difficulty_factor(detail, earned)
    if answer.mode == "classify":
        weight = float(detail.get("weight", 1.0)) * factor
        ranks, lost, wrong_above = {}, 0.0, False
        for r in game.RANKS:
            truth = getattr(answer, f"ref_{r}") or ""
            ok = getattr(answer, f"correct_{r}")
            got = (detail.get("ranks", {}).get(r) or {}).get("points", 0.0) * factor
            worth = RANK_POINTS[r] * weight
            if ok is True:
                state, miss = "right", 0.0
            elif ok is False:
                state = "after" if wrong_above else "wrong"
                wrong_above = True
                miss = worth - got   # what the rank was worth, plus any penalty
            elif truth and not wrong_above and not _claimed(answer, r):
                state, miss = "stopped", worth
            else:
                state, miss = None, 0.0
            ranks[r] = {"state": state, "points": round(got, 1), "lost": round(miss, 1)}
            lost += miss
        return {"kind": "classify", "ranks": ranks, "earned": earned, "lost": round(lost, 1),
                "multiplier": detail.get("multiplier")}
    if answer.mode == "select":
        worth = float(detail.get("worth", 0.0))
        return {"kind": "select", "rank": answer.grid_rank, **{k: detail.get(k, 0) for k in ("right", "wrong", "missed", "members")},
                "earned": earned, "lost": round(max(0.0, worth - earned), 1)}
    if answer.mode == "odd":
        worth = float(detail.get("worth", 0.0))
        return {"kind": "odd", "state": "right" if detail.get("right") else "wrong", "rank": answer.grid_rank,
                "earned": earned, "lost": round(max(0.0, worth - earned), 1)}
    truth = detail.get("truth")
    depth = {v: k for k, v in {-1: "different subfamilies", 0: "same subfamily", 1: "same tribe", 2: "same genus",
                               3: "same species"}.items()}.get(truth)
    if depth is None:
        return None
    bonus = 1 + game.game_setting("GAME_POINTS_SIMILARITY_BONUS", 0.25) * float(detail.get("similarity", 0.0) or 0.0)
    worth = PAIR_POINTS[depth] * bonus * factor
    given = game.PAIR_DEPTH.get(answer.pair_answer)
    if given is None or given == depth:
        state = "right" if detail.get("right") is True else "wrong"
    elif given >= 0 and depth >= 0:
        state = "cautious" if given < depth else "too_close"   # too close is wrong (#530), but says how
    else:
        state = "wrong"
    return {"kind": "pair", "state": state, "said": answer.get_pair_answer_display(), "truth": truth,
            "earned": earned, "lost": round(max(0.0, worth - earned), 1), "multiplier": detail.get("multiplier")}


def loss_summary(answers):
    """
    Where a set of answers lost points on validated beetles: per rank for Name That Beetle (right / wrong /
    stopped early and the points lost), and by kind of mistake for Similarity. ``answers`` should have their
    points loaded (select_related("points")). Rows are sorted by the points lost, worst first.
    """
    ranks = {r: {"rank": r, "label": RANK_LABEL[r], "right": 0, "wrong": 0, "stopped": 0, "lost": 0.0} for r in game.RANKS}
    pair = {k: {"kind": k, "count": 0, "lost": 0.0} for k in ("right", "cautious", "too_close", "wrong")}
    odd = {r: {"rank": r, "label": RANK_LABEL[r], "right": 0, "wrong": 0, "lost": 0.0} for r in game.RANKS}
    select = {r: {"rank": r, "label": RANK_LABEL[r], "right": 0, "wrong": 0, "missed": 0, "lost": 0.0} for r in game.RANKS}
    seen = 0
    for a in answers:
        loss = answer_losses(a)
        if loss is None:
            continue
        seen += 1
        if loss["kind"] == "select":
            if loss["rank"] in select:
                for k in ("right", "wrong", "missed"):
                    select[loss["rank"]][k] += loss[k]
                select[loss["rank"]]["lost"] += loss["lost"]
        elif loss["kind"] == "odd":
            if loss["rank"] in odd:
                odd[loss["rank"]][loss["state"]] += 1
                odd[loss["rank"]]["lost"] += loss["lost"]
        elif loss["kind"] == "classify":
            for r, cell in loss["ranks"].items():
                if cell["state"] == "right":
                    ranks[r]["right"] += 1
                elif cell["state"] in ("wrong", "after"):
                    ranks[r]["wrong"] += 1
                elif cell["state"] == "stopped":
                    ranks[r]["stopped"] += 1
                ranks[r]["lost"] += cell["lost"]
        else:
            pair[loss["state"]]["count"] += 1
            pair[loss["state"]]["lost"] += loss["lost"]
    rank_rows = sorted((dict(row, lost=round(row["lost"], 1)) for row in ranks.values()
                        if row["right"] or row["wrong"] or row["stopped"]), key=lambda row: -row["lost"])
    pair_rows = sorted((dict(row, lost=round(row["lost"], 1)) for k, row in pair.items() if k != "right" and row["count"]),
                       key=lambda row: -row["lost"])
    odd_rows = [dict(row, lost=round(row["lost"], 1)) for row in odd.values() if row["right"] or row["wrong"]]
    select_rows = [dict(row, lost=round(row["lost"], 1)) for row in select.values()
                   if row["right"] or row["wrong"] or row["missed"]]
    tip = None
    worst = next((row for row in rank_rows if row["lost"] > 0), None)
    if worst and worst["wrong"] >= worst["stopped"]:
        tip = (f"Most of your points went at the {worst['rank']}. A focus on one "
               f"{'genus' if worst['rank'] == 'species' else 'tribe' if worst['rank'] == 'genus' else 'subfamily'} "
               "helps you learn its members side by side.")
    elif worst:
        tip = f"You often stopped before the {worst['rank']}. When you're fairly sure, name it: it's worth the most."
    elif pair_rows and pair_rows[0]["kind"] == "too_close":
        tip = "In Similarity you often called beetles closer relatives than they are. Pick the lowest line you're sure of."
    return {"answers": seen, "ranks": rank_rows, "pair": pair_rows, "odd": odd_rows, "select": select_rows, "tip": tip,
            "lost": round(sum(r["lost"] for r in rank_rows) + sum(r["lost"] for r in pair_rows)
                          + sum(r["lost"] for r in odd_rows) + sum(r["lost"] for r in select_rows), 1)}


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
def _hold_filter(roi, player=None):
    q = Q(is_check=True) & (Q(roi=roi) | Q(roi_b=roi))
    if player is not None:
        q &= Q(player=player)
    return q


@transaction.atomic
def create_report(player, roi, reason, note="", answer=None):
    """
    Record a report and hold the reporter's scored answers on this ROI. Returns the
    report (the existing one if this player already has an open report on the ROI).
    """
    existing = GameReport.objects.filter(roi=roi, reporter=player, status=GameReport.Status.OPEN).first()
    if existing:
        return existing
    report = GameReport.objects.create(
        roi=roi, reporter=player, answer=answer, reason=reason, note=note[:1000],
        was_validated=roi.bbox_is_validated, label_at_report=roi.depicts_valid_name_id or "",
    )
    GameAnswer.objects.filter(_hold_filter(roi, player)).update(score_hold=True)
    _refresh(player_ids=[player.id], roi_ids=[roi.id])
    return report


def rescore_roi(roi):
    """
    Re-score every scored answer on this ROI against its current label. If the ROI is
    no longer a usable reference (unvalidated or no taxon), its answers are voided.
    Returns the ids of the players affected.
    """
    roi = Beetles.objects.select_related("taxon").get(id=roi.id)
    usable = roi.bbox_is_validated and roi.taxon is not None and not roi.is_deleted
    players = set()
    for ans in GameAnswer.objects.filter(_hold_filter(roi)).select_related("roi__taxon", "roi_b__taxon"):
        players.add(ans.player_id)
        if not usable:
            ans.score_hold = True
            ans.save(update_fields=["score_hold"])
            continue
        if ans.mode == "classify":
            scores = game.score_classification(
                {"subfamily": ans.subfamily, "tribe": ans.tribe, "genus": ans.genus, "species": ans.species},
                roi.taxon,
            ) if not ans.skipped else {r: None for r in game.RANKS}
            ans.ref_subfamily = roi.taxon.subfamily or ""
            ans.ref_tribe = roi.taxon.tribe or ""
            ans.ref_genus = roi.taxon.genus or ""
            ans.ref_species = roi.taxon.species or ""
        elif ans.mode == "select":
            from .game_scoring import grid_tiles
            grid = game.score_select(grid_tiles(ans), ans.picks, ans.grid_rank, ans.grid_group, ans.flagged)
            scores = {r: None for r in game.RANKS}
            if not ans.skipped and grid["members"]:
                scores[ans.grid_rank] = grid["perfect"]
        elif ans.mode == "odd" and ans.picks:   # several picks (#540): judged on every beetle picked
            from .game_scoring import grid_tiles
            grid = game.score_odd_grid(grid_tiles(ans), ans.picks, ans.grid_rank, ans.grid_group, ans.flagged)
            scores = {r: None for r in game.RANKS}
            if not ans.skipped and ans.grid_rank in scores:
                scores[ans.grid_rank] = game.odd_verdict(grid)
        elif ans.mode == "odd":
            # judged on the beetle picked; the odd one the round was built around only sets what a pick is worth
            picked = roi.taxon if ans.roi_id == roi.id else (ans.roi.taxon if ans.roi else None)
            if picked is None:
                ans.score_hold = True
                ans.save(update_fields=["score_hold"])
                continue
            scores = game.score_odd(picked, ans.grid_rank, ans.grid_group) if not ans.skipped else {
                r: None for r in game.RANKS
            }
        else:
            other = ans.roi_b if ans.roi_id == roi.id else ans.roi
            if other is None or other.taxon is None:
                ans.score_hold = True
                ans.save(update_fields=["score_hold"])
                continue
            a_taxon = roi.taxon if ans.roi_id == roi.id else ans.roi.taxon
            b_taxon = roi.taxon if ans.roi_b_id == roi.id else ans.roi_b.taxon
            scores = game.score_pair(ans.pair_answer, a_taxon, b_taxon) if not ans.skipped else {
                r: None for r in game.RANKS
            }
        for r, ok in scores.items():
            setattr(ans, f"correct_{r}", ok)
        ans.score_hold = False
        ans.save()
    return players


@transaction.atomic
def resolve_reports(roi, outcome, staff, note=""):
    """
    Close every open report on this ROI.

    corrected  staff fixed the ROI: re-score all answers on it against the new label
               (voided if it is no longer validated).
    confirmed  the label was right: the reporters' held answers count again.
    """
    from .game_label_check import LABEL_CHECK_USER

    reports = list(GameReport.objects.filter(roi=roi, status=GameReport.Status.OPEN).select_related("reporter"))
    if outcome == GameReport.Status.CORRECTED:
        players = rescore_roi(roi)
    elif outcome == GameReport.Status.CONFIRMED:
        held = GameAnswer.objects.filter(_hold_filter(roi))
        # The automatic label check (game_label_check) held everyone's answers; a player's report only their own
        if not any(r.reporter.username == LABEL_CHECK_USER for r in reports):
            held = held.filter(player_id__in={r.reporter_id for r in reports})
        players = set(held.values_list("player_id", flat=True))
        held.update(score_hold=False)
    else:
        raise ValueError(outcome)
    GameReport.objects.filter(id__in=[r.id for r in reports]).update(
        status=outcome, resolved_by=staff, resolved_at=timezone.now(), staff_note=note[:1000],
    )
    _refresh(player_ids=players, roi_ids=[roi.id])
    return len(reports)


def _refresh(player_ids, roi_ids):
    """Recompute what depends on scores after holds or re-scoring changed them."""
    from django.contrib.auth import get_user_model
    from django.core.cache import cache

    from .game_trust import INDEX_CACHE_KEY, recompute_skills

    for player in get_user_model().objects.filter(id__in=list(player_ids)):
        recompute_skills(player)
    game.update_difficulty(roi_ids)
    cache.delete(INDEX_CACHE_KEY)  # testable-branch counts change with the check pool
