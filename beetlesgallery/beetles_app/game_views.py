"""
Pages and JSON endpoints for the Beetle ID game. The game logic is in game.py and
expertise / trusted labels in game_trust.py.

Item payloads carry only an image URL and a bounding box: never the ROI id, its
label, or whether the item is a check, so the player cannot tell which answers are scored.
"""
import csv
import json

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from . import game, game_feedback, game_trust
from .models import Beetles, GameAnswer, GameReport, GameRound, ImageLock, LabelReview, Taxon
from .predictions import suggestions_for

MODES = {m.value: m.label for m in GameRound.Mode}
PAIR_CHOICES = [(c.value, c.label) for c in GameAnswer.PairAnswer]
TAXA_CACHE_SECONDS = 600
MAX_RESPONSE_MS = 60 * 60 * 1000


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@login_required
def game_home(request):
    sort = "accuracy" if request.GET.get("sort") == "accuracy" else "labelled"
    return render(request, "beetles/game_home.html", {
        "summary": game.player_summary(request.user),
        "leaderboard": game.leaderboard(limit=25, sort=sort),
        "sort": sort,
    })


@login_required
def game_play(request, mode):
    if mode not in MODES:
        raise Http404("Unknown game mode")
    return render(request, "beetles/game_play.html", {
        "mode": mode,
        "mode_label": MODES[mode],
        "pair_choices": PAIR_CHOICES,
    })


def _render_report(request, player):
    return render(request, "beetles/game_report.html", {
        "player": player,
        "is_self": player == request.user,
        "report": game_trust.player_report(player),
    })


@login_required
def game_report(request):
    return _render_report(request, request.user)


@staff_member_required
def game_player_report(request, user_id):
    return _render_report(request, get_object_or_404(get_user_model(), id=user_id))


@login_required
def game_round_review(request, round_id):
    """Feedback on a finished round: each answer next to what the database says."""
    rnd = get_object_or_404(GameRound, id=round_id)
    if rnd.player != request.user and not request.user.is_staff:
        raise Http404("No such round")
    if rnd.finished_at is None:
        return redirect("game_play", mode=rnd.mode)
    feedback = game_feedback.round_feedback(rnd)
    return render(request, "beetles/game_round_review.html", {
        "round": rnd,
        "feedback": feedback,
        "feedback_json": feedback["items"],
        "is_self": rnd.player == request.user,
        "reasons": GameReport.Reason.choices,
    })


@login_required
@require_POST
def game_report_roi(request):
    """A player reports an ROI from one of their finished rounds as looking wrong."""
    body = _json_body(request) or {}
    rnd = GameRound.objects.filter(id=body.get("round"), player=request.user).first() if _is_uuid(body.get("round")) else None
    if rnd is None or rnd.finished_at is None:
        return JsonResponse({"error": "You can report images from your finished rounds."}, status=404)
    answer = rnd.answers.filter(index=body.get("index")).first() if isinstance(body.get("index"), int) else None
    if answer is None:
        return JsonResponse({"error": "Unknown item."}, status=404)
    roi_id = str(body.get("roi") or "")
    if roi_id not in {str(answer.roi_id), str(answer.roi_b_id)}:
        return JsonResponse({"error": "Unknown image."}, status=400)
    reason = body.get("reason")
    if reason not in GameReport.Reason.values:
        return JsonResponse({"error": "Please choose a reason."}, status=400)
    roi = answer.roi if str(answer.roi_id) == roi_id else answer.roi_b
    report = game_feedback.create_report(request.user, roi, reason, str(body.get("note") or ""), answer)
    return JsonResponse({"status": report.status, "reason": report.get_reason_display()})


def _is_uuid(value):
    import uuid as _uuid

    try:
        _uuid.UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Round API
# ---------------------------------------------------------------------------
def _json_body(request):
    try:
        body = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _box(roi):
    return [roi.bbox_x, roi.bbox_y, roi.bbox_width, roi.bbox_height]


def _item_rois(item):
    """The Beetles rows of a round item, (a, b) with b None for classify. None if any is gone."""
    ids = [item["a"]] + ([item["b"]] if item.get("b") else [])
    found = {str(k): v for k, v in Beetles.objects.select_related("image_asset", "taxon").in_bulk(ids).items()}
    if any(i not in found or not found[i].has_bbox() for i in ids):
        return None
    return found[item["a"]], found.get(item.get("b"))


def _next_index(rnd, start=None):
    """First item at or after ``start`` (default: after the last answer) whose ROIs still exist."""
    if start is None:
        last = rnd.answers.aggregate(m=Max("index"))["m"]
        start = 0 if last is None else last + 1
    for i in range(start, len(rnd.items)):
        if _item_rois(rnd.items[i]) is not None:
            return i
    return None


def _item_images(rnd, index):
    a, b = _item_rois(rnd.items[index])
    rois = [a] if b is None else ([b, a] if rnd.items[index].get("flip") else [a, b])
    return [{"url": r.display_url, "box": _box(r)} for r in rois]


def _item_payload(rnd, index):
    payload = {
        "index": index,
        "position": rnd.answers.count() + 1,
        "total": len(rnd.items),
        "images": _item_images(rnd, index),
        "prefetch": [],
    }
    # Let the browser start downloading the next photos while this item is answered.
    following = _next_index(rnd, index + 1)
    if following is not None:
        payload["prefetch"] = [im["url"] for im in _item_images(rnd, following)]
    return payload


def _finish(rnd):
    game.finish_round(rnd)
    summary = game.player_summary(rnd.player)
    summary["round_labelled"] = rnd.answers.filter(skipped=False).count()
    return {"done": True, "summary": summary, "review_url": reverse("game_round_review", args=[rnd.id])}


@login_required
@require_POST
def game_start(request):
    body = _json_body(request)
    mode = (body or {}).get("mode")
    if mode not in MODES:
        return JsonResponse({"error": "Unknown game mode."}, status=400)

    # Pick up where the player left off (e.g. after a reload) before starting afresh.
    rnd = game.resumable_round(request.user, mode)
    index = _next_index(rnd) if rnd else None
    if index is None:
        if rnd is not None:
            game.finish_round(rnd)
        rnd = game.start_round(request.user, mode)
        index = _next_index(rnd, 0) if rnd else None
    if index is None:
        return JsonResponse({
            "error": "There are no images ready for this game yet. Please check back later."
        }, status=404)
    return JsonResponse({"round": str(rnd.id), "item": _item_payload(rnd, index)})


def _clean_classification(body):
    """The rank values from a classify answer, or None if they don't match the taxonomy."""
    answer = {r: str(body.get(r) or "").strip()[:100] for r in game.RANKS}
    if answer["species"] and not answer["genus"]:
        return None
    if not any(answer.values()):
        return None
    filters = {f"{r}__iexact": v for r, v in answer.items() if v}
    if not Taxon.objects.filter(game.COMPLETE_TAXON, **filters).exists():
        return None
    return answer


def _response_ms(body):
    try:
        value = int(body.get("elapsed_ms"))
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= MAX_RESPONSE_MS else None


@login_required
@require_POST
def game_answer(request, round_id):
    rnd = get_object_or_404(GameRound, id=round_id, player=request.user)
    body = _json_body(request)
    if body is None:
        return JsonResponse({"error": "Invalid request."}, status=400)
    if rnd.finished_at is not None:
        return JsonResponse({"error": "This round is already finished."}, status=409)

    index = _next_index(rnd)
    if index is None:
        return JsonResponse(_finish(rnd))
    if body.get("index") != index:
        return JsonResponse({"error": "Out of step with the round; please reload."}, status=409)

    item = rnd.items[index]
    roi_a, roi_b = _item_rois(item)
    record = GameAnswer(
        round=rnd, player=request.user, mode=rnd.mode, index=index,
        is_check=bool(item.get("check")), roi=roi_a, roi_b=roi_b,
        skipped=bool(body.get("skipped")), response_ms=_response_ms(body),
    )
    if record.is_check and roi_a.taxon:
        record.ref_subfamily = roi_a.taxon.subfamily or ""
        record.ref_tribe = roi_a.taxon.tribe or ""
        record.ref_genus = roi_a.taxon.genus or ""
        record.ref_species = roi_a.taxon.species or ""

    scores = {}
    if not record.skipped:
        if rnd.mode == GameRound.Mode.CLASSIFY:
            answer = _clean_classification(body)
            if answer is None:
                return JsonResponse({"error": "Please choose a name from the lists."}, status=400)
            for r, v in answer.items():
                setattr(record, r, v)
            if record.is_check:
                scores = game.score_classification(answer, roi_a.taxon)
        else:
            choice = body.get("pair_answer")
            if choice not in dict(PAIR_CHOICES):
                return JsonResponse({"error": "Please choose an answer."}, status=400)
            record.pair_answer = choice
            if record.is_check:
                scores = game.score_pair(choice, roi_a.taxon, roi_b.taxon)
    for r, ok in scores.items():
        setattr(record, f"correct_{r}", ok)
    try:
        with transaction.atomic():
            record.save()
    except IntegrityError:
        # The same item was submitted twice (double tap, two tabs).
        return JsonResponse({"error": "That answer was already saved; please reload."}, status=409)

    nxt = _next_index(rnd, index + 1)
    if nxt is None:
        return JsonResponse(_finish(rnd))
    return JsonResponse({"item": _item_payload(rnd, nxt)})


# ---------------------------------------------------------------------------
# Taxonomy pickers
# ---------------------------------------------------------------------------
@login_required
@require_GET
def game_taxa(request):
    """
    Options for one picker. ``rank`` is the list wanted; the chosen higher ranks
    narrow it. Genus options also carry their subfamily and tribe so picking a genus
    can fill those in. Only well-formed taxa are offered.
    """
    rank = request.GET.get("rank")
    if rank not in game.RANKS:
        return JsonResponse({"error": "Unknown rank."}, status=400)
    parents = {r: (request.GET.get(r) or "").strip() for r in game.RANKS[: game.RANKS.index(rank)]}
    if rank == "species" and not parents.get("genus"):
        return JsonResponse({"options": []})

    key = "game_taxa:v2:" + rank + ":" + "|".join(parents.get(r, "").lower() for r in game.RANKS[:3])
    options = cache.get(key)
    if options is None:
        qs = Taxon.objects.filter(game.COMPLETE_TAXON).exclude(**{f"{rank}__isnull": True}).exclude(**{rank: ""})
        qs = qs.filter(**{f"{r}__iexact": v for r, v in parents.items() if v})
        if rank == "genus":
            seen = {}
            for genus, subfamily, tribe in qs.values_list("genus", "subfamily", "tribe").order_by("genus"):
                seen.setdefault(genus, {"value": genus, "subfamily": subfamily or "", "tribe": tribe or ""})
            options = list(seen.values())
        else:
            values = qs.values_list(rank, flat=True).distinct().order_by(rank)
            options = [{"value": v} for v in values]
        cache.set(key, options, TAXA_CACHE_SECONDS)
    return JsonResponse({"options": options})


@login_required
@require_GET
def game_taxa_search(request):
    """Jump straight to a genus or species by typing part of its name."""
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})
    complete = Taxon.objects.filter(game.COMPLETE_TAXON)
    results = []
    for genus, subfamily, tribe in (
        complete.filter(genus__istartswith=q)
        .values_list("genus", "subfamily", "tribe").distinct().order_by("genus")[:5]
    ):
        results.append({
            "label": genus, "kind": "genus",
            "subfamily": subfamily or "", "tribe": tribe or "", "genus": genus, "species": "",
        })
    seen = set()
    species_qs = (
        complete.filter(scientific_name__icontains=q)
        .exclude(species__isnull=True).exclude(species="")
        .values_list("subfamily", "tribe", "genus", "species")
        .order_by("genus", "species")[:30]
    )
    for subfamily, tribe, genus, species in species_qs:
        if (genus, species) in seen:  # subspecies share genus + species
            continue
        seen.add((genus, species))
        results.append({
            "label": f"{genus} {species}", "kind": "species",
            "subfamily": subfamily or "", "tribe": tribe or "", "genus": genus, "species": species,
        })
    return JsonResponse({"results": results[:20]})


# ---------------------------------------------------------------------------
# Label proposals (annotation page)
# ---------------------------------------------------------------------------
def _proposal_json(entry, review):
    taxon = entry["taxon"]
    return {
        "answers": entry["answers"],
        "players": entry["players"],
        "trusted_rank": entry["trusted_rank"],
        "ranks": {
            r: (None if v is None else {
                "value": v["value"], "support": round(v["support"], 3), "votes": v["votes"],
                "trusted": v["trusted"], "trusted_votes": v["trusted_votes"],
            })
            for r, v in entry["ranks"].items()
        },
        "taxon": None if taxon is None else {
            "valid_species_id": taxon.valid_species_id, "scientific_name": taxon.scientific_name,
        },
        "review": None if review is None else {
            "decision": review.decision,
            "reviewed_by": review.reviewed_by.username if review.reviewed_by else "",
            "reviewed_at": review.reviewed_at.isoformat(),
            "answers": review.answers,
        },
    }


@staff_member_required
@require_GET
def game_proposals(request):
    """
    Suggestions for the ROIs of one image, keyed by ROI id: game label proposals, open player
    reports and classifier predictions (the annotator only offers a prediction while an ROI has no species).
    """
    image_id = request.GET.get("image_asset")
    if not image_id:
        return JsonResponse({"error": "image_asset is required"}, status=400)
    try:
        roi_ids = list(Beetles.objects.filter(image_asset_id=image_id, is_deleted=False).values_list("id", flat=True))
    except Exception:
        return JsonResponse({"error": "Invalid image id"}, status=400)
    latest = {}
    for review in LabelReview.objects.filter(roi_id__in=roi_ids).select_related("reviewed_by"):
        latest.setdefault(review.roi_id, review)  # ordered newest first
    proposals = {
        str(entry["roi"].id): _proposal_json(entry, latest.get(entry["roi"].id))
        for entry in game.consensus(roi_ids=roi_ids)
    }
    reports = {}
    for r in GameReport.objects.filter(roi_id__in=roi_ids, status=GameReport.Status.OPEN).select_related("reporter"):
        reports.setdefault(str(r.roi_id), []).append({
            "reason": r.get_reason_display(), "note": r.note, "reporter": r.reporter.username,
            "created_at": r.created_at.isoformat(), "was_validated": r.was_validated,
        })
    predictions = {str(roi_id): found for roi_id, found in suggestions_for(roi_ids).items()}
    return JsonResponse({"proposals": proposals, "reports": reports, "predictions": predictions})


@staff_member_required
@require_POST
def game_resolve_reports(request, roi_id):
    """Close the open player reports on one ROI (see game_feedback.resolve_reports)."""
    roi = get_object_or_404(Beetles, id=roi_id)
    body = _json_body(request) or {}
    outcome = body.get("outcome")
    if outcome not in (GameReport.Status.CORRECTED, GameReport.Status.CONFIRMED):
        return JsonResponse({"error": "outcome must be corrected or confirmed"}, status=400)
    closed = game_feedback.resolve_reports(roi, outcome, request.user, str(body.get("note") or ""))
    return JsonResponse({"closed": closed})


@staff_member_required
@require_POST
def game_proposal_review(request, roi_id):
    """
    Accept or dismiss the game proposal for one ROI.

    Accepting sets the ROI's species to the proposal's species (it does not validate the
    ROI; staff still do that as usual). Both decisions are recorded in LabelReview.
    """
    roi = get_object_or_404(Beetles, id=roi_id, is_deleted=False)
    body = _json_body(request) or {}
    decision = body.get("decision")
    if decision not in ("accept", "dismiss"):
        return JsonResponse({"error": "decision must be accept or dismiss"}, status=400)

    lock = ImageLock.objects.filter(image_asset_id=roi.image_asset_id).select_related("locked_by").first()
    if lock and lock.locked_by_id != request.user.id and not lock.is_expired():
        return JsonResponse({"error": f"{lock.locked_by.username} is editing this image."}, status=409)

    entries = game.consensus(roi_ids=[roi.id])
    if not entries:
        return JsonResponse({"error": "There is no game proposal for this ROI."}, status=404)
    entry = entries[0]
    ranks = entry["ranks"]
    review = LabelReview(
        roi=roi, reviewed_by=request.user, answers=entry["answers"],
        trusted_rank=entry["trusted_rank"], taxon=entry["taxon"],
        **{r: (ranks[r]["value"] if ranks[r] else "") for r in game.RANKS},
    )
    if decision == "accept":
        if entry["taxon"] is None:
            return JsonResponse({"error": "Only species-level proposals can be accepted."}, status=400)
        roi.depicts_valid_name_id = entry["taxon"].valid_species_id
        roi.last_updated_by = request.user
        roi.save()
        review.decision = LabelReview.Decision.ACCEPTED
    else:
        review.decision = LabelReview.Decision.DISMISSED
    review.save()
    return JsonResponse({
        "decision": review.decision,
        "depicts_valid_name_id": roi.depicts_valid_name_id,
    })


# ---------------------------------------------------------------------------
# Staff review
# ---------------------------------------------------------------------------
def _player_rows():
    reliability = game.player_reliability()
    labelled = {row["player_id"]: row["labelled"] for row in game.leaderboard(limit=None)}
    proven = {}
    for skill in game_trust.PlayerSkill.objects.filter(proven=True):
        proven.setdefault(skill.player_id, []).append(skill)
    ids = set(reliability) | set(labelled)
    users = get_user_model().objects.in_bulk(ids)
    rows = []
    for pid in ids:
        rel = reliability.get(pid) or {m: game.default_weight() for m in ("classify", "pair", "all")}
        rows.append({
            "id": pid,
            "username": users[pid].username if pid in users else "?",
            "labelled": labelled.get(pid, 0),
            "classify": [rel["classify"][r] for r in game.RANKS],
            "pair": [rel["pair"][r] for r in game.RANKS],
            "proven": sorted(proven.get(pid, []), key=lambda s: (game.RANKS.index(s.rank), s.branch)),
        })
    rows.sort(key=lambda r: (-r["labelled"], r["username"]))
    return rows


@staff_member_required
def game_review(request):
    only_trusted = request.GET.get("trusted") == "1"
    entries = game.consensus()
    if only_trusted:
        entries = [e for e in entries if e["trusted_rank"]]
    return render(request, "beetles/game_review.html", {
        "open_reports": GameReport.objects.filter(status=GameReport.Status.OPEN)
        .select_related("reporter", "roi__taxon").order_by("created_at")[:200],
        "ranks": game.RANKS,
        "players": _player_rows(),
        "consensus": entries[:200],
        "consensus_total": len(entries),
        "only_trusted": only_trusted,
        "rounds": GameRound.objects.count(),
        "answers": GameAnswer.objects.count(),
        "needed": game_trust.answers_needed(),
    })


def _pct(value):
    return "" if value is None else f"{value:.3f}"


@staff_member_required
def game_export(request, kind):
    response = HttpResponse(content_type="text/csv")
    writer = csv.writer(response)
    if kind == "labels":
        response["Content-Disposition"] = 'attachment; filename="game_label_consensus.csv"'
        header = ["roi_id", "image_id", "current_valid_name_id", "answers", "players",
                  "trusted_rank", "proposed_valid_species_id"]
        for r in game.RANKS:
            header += [f"{r}", f"{r}_support", f"{r}_votes", f"{r}_trusted"]
        writer.writerow(header)
        for entry in game.consensus():
            roi = entry["roi"]
            row = [roi.id, roi.image_asset_id, roi.depicts_valid_name_id or "", entry["answers"],
                   entry["players"], entry["trusted_rank"],
                   entry["taxon"].valid_species_id if entry["taxon"] else ""]
            for r in game.RANKS:
                vote = entry["ranks"][r]
                row += ([vote["value"], _pct(vote["support"]), vote["votes"], vote["trusted"]]
                        if vote else ["", "", "", ""])
            writer.writerow(row)
    elif kind == "players":
        response["Content-Disposition"] = 'attachment; filename="game_player_reliability.csv"'
        header = ["username", "labelled"]
        for mode in ("classify", "pair"):
            for r in game.RANKS:
                header += [f"{mode}_{r}_correct", f"{mode}_{r}_judged", f"{mode}_{r}_accuracy"]
        header.append("proven_skills")
        writer.writerow(header)
        for p in _player_rows():
            row = [p["username"], p["labelled"]]
            for mode in ("classify", "pair"):
                for cell in p[mode]:
                    row += [cell["ok"], cell["n"], _pct(cell["accuracy"])]
            row.append("; ".join(f"{s.rank}:{s.branch or 'all'}" for s in p["proven"]))
            writer.writerow(row)
    elif kind == "skills":
        response["Content-Disposition"] = 'attachment; filename="game_player_skills.csv"'
        writer.writerow(["username", "rank", "branch", "correct", "judged", "lower_bound", "proven", "proven_at"])
        for s in game_trust.PlayerSkill.objects.select_related("player").order_by("player__username", "rank", "branch"):
            writer.writerow([s.player.username, s.rank, s.branch, s.correct, s.judged,
                             f"{s.lower_bound:.3f}", s.proven, s.proven_at.isoformat() if s.proven_at else ""])
    else:
        raise Http404("Unknown export")
    return response
