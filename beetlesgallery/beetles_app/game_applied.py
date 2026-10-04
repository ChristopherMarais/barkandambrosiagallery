"""
Labels the game wrote into the database, and how a curator takes one back (issue #427).

A game label reaches a beetle in two ways: a curator accepts a game proposal on the annotation page, or enough proven
experts agree and game_trust.auto_apply_expert_labels writes it (a LabelReview with no reviewer). Either way a
LabelReview "accepted" records it, and the beetle's own history holds the label it had before.

Reverting puts that earlier label back (species, label source and its detail) and records a "dismissed" LabelReview
by the curator. The newest review then says dismissed, so the beetle drops out of the "applied from the game" list,
and the game never writes a label onto it again by itself (auto-apply skips beetles that have any review).
"""
from django.db.models import OuterRef, Subquery

from .models import Beetles, LabelReview


def _latest_decision():
    return Subquery(LabelReview.objects.filter(roi=OuterRef("pk")).order_by("-reviewed_at").values("decision")[:1])


def applied_rois():
    """Beetles whose newest game review applied a label (accepted by a curator, or automatically)."""
    return Beetles.objects.annotate(game_decision=_latest_decision()).filter(
        game_decision=LabelReview.Decision.ACCEPTED, is_deleted=False)


def applied_for(roi_ids):
    """{roi_id: the accepted LabelReview} for those of these beetles whose newest review applied a game label."""
    out = {}
    for review in LabelReview.objects.filter(roi_id__in=roi_ids).select_related("reviewed_by", "taxon", "roi"):
        out.setdefault(review.roi_id, review)   # newest first (Meta.ordering)
    return {rid: r for rid, r in out.items() if r.decision == LabelReview.Decision.ACCEPTED}


def label_before(roi, when, applied_id):
    """
    The beetle's label fields as they were before the game's label: the newest history record up to ``when`` whose
    species isn't the applied one (blanks if the beetle had no history before that).
    """
    fields = ("depicts_valid_name_id", "label_source", "label_source_detail")
    for old in roi.history.filter(history_date__lte=when).order_by("-history_date", "-history_id"):
        if str(old.depicts_valid_name_id or "") != str(applied_id or ""):
            return {f: getattr(old, f, None) for f in fields}
    return {f: None for f in fields}


class RevertError(Exception):
    pass


def revert(roi, user):
    """Put back the label the beetle had before the game's, and record that a curator took it back."""
    review = applied_for([roi.id]).get(roi.id)
    if review is None:
        raise RevertError("No label from the game to revert on this beetle.")
    applied = review.taxon.valid_species_id if review.taxon else None
    if applied and str(roi.depicts_valid_name_id or "") != str(applied):
        raise RevertError("Its label has been changed since the game set it; edit it directly instead.")
    before = label_before(roi, review.reviewed_at, applied)
    roi.depicts_valid_name_id = before["depicts_valid_name_id"]
    roi.label_source = before["label_source"] or ""
    roi.label_source_detail = before["label_source_detail"] or ""
    roi.last_updated_by = user
    roi._change_reason = "Reverted a label applied from the game"
    roi.save()
    LabelReview.objects.create(
        roi=roi, decision=LabelReview.Decision.DISMISSED, reviewed_by=user, answers=review.answers,
        subfamily=review.subfamily, tribe=review.tribe, genus=review.genus, species=review.species, taxon=review.taxon,
    )
    return before
