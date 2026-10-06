"""
An ROI's labelling history for the annotation page (#504): what it has been called over time, and by whom.

Four records already hold it; this module reads them together, newest first:
    RoiName          every name the ROI was given, with its identification tier and who gave it
    Beetles.history  validations and un-validations (django-simple-history; whole-image ones too, see
                     models.record_roi_history), and curators reverting a game label
    LabelReview      game proposals accepted or rejected, and labels the game applied by itself
    GameReport       player reports closed: the label was wrong and fixed, or it is correct

events_for() reads any number of ROIs in a fixed number of queries, so the annotation page can count every ROI on
an image at once.
"""
from datetime import timedelta

from .game_applied import REVERT_REASON
from .identification import label as tier_label
from .models import IMAGE_UNVALIDATED, IMAGE_VALIDATED, Beetles, GameReport, LabelReview, RoiName

LIMIT = 50

# A revert saves the ROI, then records its "dismissed" review a moment later
_REVERT_WINDOW = timedelta(minutes=1)


def _event(kind, at, what="", name="", tier="", detail="", by=""):
    return {"kind": kind, "at": at, "what": what, "name": name or "", "tier": tier, "detail": detail or "",
            "by": by or ""}


def _names(roi_ids, out):
    for n in RoiName.objects.filter(roi_id__in=roi_ids).select_related("taxon", "added_by").order_by("created_at", "id"):
        name = (n.taxon.scientific_name if n.taxon else "") or n.valid_species_id
        out[n.roi_id].append(_event("name", n.created_at, name=name, tier=tier_label(n.tier), detail=n.detail,
                                    by=n.added_by.username if n.added_by else ""))


def _validations(roi_ids, out):
    """Validated / unvalidated whenever bbox_is_validated changed between history records. Returns revert times."""
    reverts = {rid: [] for rid in roi_ids}
    rows = (Beetles.history.filter(id__in=roi_ids).order_by("id", "history_date", "history_id")
            .values("id", "history_date", "history_type", "history_change_reason", "bbox_is_validated",
                    "history_user__username", "bbox_validated_by__username", "last_updated_by__username"))
    before = {}
    for row in rows:
        rid = row["id"]
        if row["history_type"] == "-":
            continue
        reason = row["history_change_reason"]
        if reason == REVERT_REASON:
            reverts[rid].append((row["history_date"], row["history_user__username"] or row["last_updated_by__username"]))
        now = bool(row["bbox_is_validated"])
        # A ROI older than its history starts from its first record: no event unless it was created that way
        was = before.get(rid, False if row["history_type"] == "+" else now)
        if now != was:
            # The page's user when the change came through a request; otherwise what the record itself says
            by = row["history_user__username"] or (
                row["bbox_validated_by__username"] if now else row["last_updated_by__username"])
            what = reason if reason in (IMAGE_VALIDATED, IMAGE_UNVALIDATED) else ("Validated" if now else "Unvalidated")
            out[rid].append(_event("validated" if now else "unvalidated", row["history_date"], what=what, by=by))
        before[rid] = now
    return reverts


def _review_name(review):
    if review.taxon and review.taxon.scientific_name:
        return review.taxon.scientific_name
    if review.genus and review.species:
        return f"{review.genus} {review.species}"
    return review.genus or review.tribe or review.subfamily


def _reviews(roi_ids, out, reverts):
    for review in LabelReview.objects.filter(roi_id__in=roi_ids).select_related("taxon", "reviewed_by"):
        by = review.reviewed_by.username if review.reviewed_by else ""
        if review.decision == LabelReview.Decision.ACCEPTED:
            kind, what = ("game_accepted", "Game proposal accepted") if by else (
                "game_applied", "Game label applied automatically (Identification experts agreed)")
        elif any(review.reviewed_at - _REVERT_WINDOW <= at <= review.reviewed_at and who in ("", None, by)
                 for at, who in reverts.get(review.roi_id, [])):
            kind, what = "game_reverted", "Game label reverted"
        else:
            kind, what = "game_dismissed", "Game proposal rejected"
        out[review.roi_id].append(_event(kind, review.reviewed_at, what=what, name=_review_name(review), by=by))


def _reports(roi_ids, out):
    """Closed player reports; reports closed together (one curator's click) count as one event."""
    done = (GameReport.objects.filter(roi_id__in=roi_ids, resolved_at__isnull=False)
            .exclude(status=GameReport.Status.OPEN).select_related("resolved_by").order_by("resolved_at"))
    grouped = {}
    for r in done:
        grouped.setdefault((r.roi_id, r.status, r.resolved_at, r.resolved_by_id), []).append(r)
    for (rid, status, at, _), reports in grouped.items():
        outcome = "label was wrong, fixed" if status == GameReport.Status.CORRECTED else "label is correct"
        n = len(reports)
        what = f"{n} player reports closed: {outcome}" if n > 1 else f"Player report closed: {outcome}"
        by = reports[0].resolved_by.username if reports[0].resolved_by else ""
        out[rid].append(_event("report_" + status, at, what=what, by=by))


def events_for(roi_ids, limit=LIMIT):
    """{roi_id: (events newest first, at most ``limit``, total)} for these ROIs."""
    roi_ids = list(roi_ids)
    out = {rid: [] for rid in roi_ids}
    if not roi_ids:
        return {}
    _names(roi_ids, out)
    reverts = _validations(roi_ids, out)
    _reviews(roi_ids, out, reverts)
    _reports(roi_ids, out)
    result = {}
    for rid, events in out.items():
        # Newest first; events from the same instant keep the order they were read in, reversed (stable sort)
        ordered = sorted(enumerate(events), key=lambda pair: (pair[1]["at"], pair[0]), reverse=True)
        result[rid] = ([e for _, e in ordered[:limit]], len(events))
    return result


def as_json(event):
    return {**event, "at": event["at"].isoformat()}
