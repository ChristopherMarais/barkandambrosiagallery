"""
Identification tiers (#390): how reliable an ROI's name is, most reliable first.

    Taxonomist ID   a taxonomist examined it, or the vial / specimen label says so
    Expert ID       a curator or proven expert named or validated it (game consensus a curator accepted, too)
    Community ID    players' answers, not checked yet
    External ID     an external database or website (as reliable as Community ID)
    No ID           not recorded

Every name an ROI is given is kept (RoiName). The ROI shows the most reliable: a name from a lower tier than the
one shown is recorded but does not replace it, unless a curator sets it by hand on the annotation page. Only
Taxonomist and Expert IDs are validated: validating a name from a lower tier (or none) makes it an Expert ID, the
validating curator's.

Beetles.save() calls before_save / after_save, so every way of saving one ROI follows these rules. Bulk updates
that bypass save() (ImageAsset.validate) call vouch() themselves.
"""
from django.utils import timezone

TAXONOMIST, EXPERT, COMMUNITY, EXTERNAL, NONE = "taxonomist", "expert", "community", "external", ""
RANK = {TAXONOMIST: 4, EXPERT: 3, COMMUNITY: 2, EXTERNAL: 2, NONE: 0}
VALIDATED_TIERS = {TAXONOMIST, EXPERT}
LABELS = {TAXONOMIST: "Taxonomist ID", EXPERT: "Expert ID", COMMUNITY: "Community ID", EXTERNAL: "External ID", NONE: "No ID"}

# What a spreadsheet may say, as well as the keys and labels above (older files used the old source names)
ALIASES = {
    "vial_label": TAXONOMIST, "vial label": TAXONOMIST, "vial / specimen label": TAXONOMIST,
    "taxonomist examined it": TAXONOMIST, "game_consensus": EXPERT, "game consensus": EXPERT,
    "game consensus accepted by a curator": EXPERT, "external database or website": EXTERNAL,
    "no id": NONE, "none": NONE,
}


def parse_tier(text):
    """A cell's tier: a key, a label or an alias, any case. Returns (tier, ok)."""
    value = str(text or "").strip().lower()
    if not value:
        return NONE, True
    for key, label in LABELS.items():
        if value in (key, label.lower()):
            return key, True
    if value in ALIASES:
        return ALIASES[value], True
    return NONE, False


def rank(tier):
    return RANK.get(tier or NONE, 0)


def label(tier):
    return LABELS.get(tier or NONE, "No ID")


def _stored(roi):
    if roi.pk is None or roi._state.adding:
        return None
    from .models import Beetles
    return Beetles.objects.filter(pk=roi.pk).values(
        "depicts_valid_name_id", "label_source", "label_source_detail", "bbox_is_validated").first()


def before_save(roi, update_fields=None):
    """
    Called by Beetles.save before writing. Keeps the more reliable name, makes a validated name at least an Expert
    ID, and remembers which name to record. Returns the update_fields to use (extended if it changed more).
    """
    old = _stored(roi)
    changed = set()
    pending = None
    name = roi.depicts_valid_name_id or None
    tier = roi.label_source or NONE
    old_name = (old or {}).get("depicts_valid_name_id") or None
    old_tier = (old or {}).get("label_source") or NONE
    if name and (name != old_name or tier != old_tier):
        pending = (name, tier, roi.label_source_detail or "")
        # A less reliable name than the one shown is kept on record only (a curator's hand edit always wins)
        if old_name and name != old_name and rank(tier) < rank(old_tier) and not getattr(roi, "_name_by_hand", False):
            roi.depicts_valid_name_id = old_name
            roi.label_source = old_tier
            roi.label_source_detail = old["label_source_detail"] or ""
            changed |= {"depicts_valid_name_id", "label_source", "label_source_detail"}
            tier = old_tier
    # Only Taxonomist and Expert IDs are validated: validating anything less vouches for it as an Expert ID
    if roi.bbox_is_validated and roi.depicts_valid_name_id and (roi.label_source or NONE) not in VALIDATED_TIERS:
        by = roi.bbox_validated_by or roi.last_updated_by
        roi.label_source = EXPERT
        roi.label_source_detail = (f"Validated by {by.username}" if by else "Validated by a curator")[:255]
        changed |= {"label_source", "label_source_detail"}
        pending = (roi.depicts_valid_name_id, EXPERT, roi.label_source_detail)
    roi._pending_name = pending
    if update_fields is not None and changed:
        update_fields = list(dict.fromkeys(list(update_fields) + sorted(changed)))
    return update_fields


def after_save(roi):
    pending = getattr(roi, "_pending_name", None)
    roi._pending_name = None
    if pending:
        name, tier, detail = pending
        record(roi, name, tier, detail, roi.last_updated_by)


def record(roi, valid_species_id, tier=NONE, detail="", user=None):
    """Keep a name the ROI was given (not the same name and tier twice in a row)."""
    from .models import RoiName, Taxon
    if not valid_species_id:
        return None
    last = RoiName.objects.filter(roi=roi).order_by("-created_at", "-id").values_list("valid_species_id", "tier").first()
    if last == (valid_species_id, tier or NONE):
        return None
    return RoiName.objects.create(
        roi=roi, valid_species_id=valid_species_id, tier=tier or NONE, detail=(detail or "")[:255],
        taxon=Taxon.objects.filter(valid_species_id=valid_species_id).first(), added_by=user,
    )


def vouch(rois, user=None):
    """Validated in bulk (bypassing save): names below Expert ID become the validating curator's Expert IDs."""
    from .models import Beetles
    detail = (f"Validated by {user.username}" if user else "Validated by a curator")[:255]
    lifted = list(Beetles.objects.filter(pk__in=[r.pk for r in rois], depicts_valid_name_id__isnull=False)
                  .exclude(depicts_valid_name_id="").exclude(label_source__in=VALIDATED_TIERS))
    for roi in lifted:
        Beetles.objects.filter(pk=roi.pk).update(label_source=EXPERT, label_source_detail=detail, last_updated_at=timezone.now())
        record(roi, roi.depicts_valid_name_id, EXPERT, detail, user)
    return len(lifted)
