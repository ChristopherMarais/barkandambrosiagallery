"""
Beetles a curator validated after the player had answered them: what they said, what it turned out to be, and the
points that moved. Shown once on the game home (and all of them on /game/checked/) so players learn from them.
Only validations make one (game_scoring records RetroCredit); points from agreement never do.
"""
from django.utils import timezone

from .game import RANKS
from .models import RetroCredit


def _said(answer):
    if answer.mode == "pair":
        return answer.get_pair_answer_display()
    if answer.mode == "select":
        return f"{len(answer.picks or [])} tapped"
    if answer.mode == "odd":   # picked as the odd one out: "not one of the group"
        group = (answer.grid_group or {}).get(answer.grid_rank, "")
        return f"Not {group}" if group else "Odd one out"
    given = [getattr(answer, r) for r in RANKS[:3] if getattr(answer, r)]
    if answer.genus and answer.species:
        return f"{answer.genus} {answer.species}"
    return given[-1] if given else "Skipped"


def _roi_image(roi):
    return {"url": roi.display_url, "box": [roi.bbox_x, roi.bbox_y, roi.bbox_width, roi.bbox_height]}


def items(player, unseen_only=False, limit=None):
    credits = (
        RetroCredit.objects.filter(player=player)
        .select_related("answer__roi__image_asset", "answer__roi_b__image_asset", "answer__roi_b__taxon")
        .order_by("-created_at")
    )
    if unseen_only:
        credits = credits.filter(seen_at__isnull=True)
    if limit:
        credits = credits[:limit]
    out = []
    for c in credits:
        a = c.answer
        if a.roi is None or not a.roi.has_bbox():
            continue
        row = {
            "id": c.id, "mode": a.mode, "said": _said(a), "truth": c.validated_name,
            "change": c.change, "after": round(c.points_after, 1), "validated_at": c.validated_at,
            "answered_at": a.answered_at, "images": [_roi_image(a.roi)], "new": c.seen_at is None,
            "right": all(getattr(a, f"correct_{r}") is not False for r in RANKS),
        }
        if a.mode == "pair" and a.roi_b is not None and a.roi_b.has_bbox():
            row["images"].append(_roi_image(a.roi_b))
            from .game_scoring import species_name
            row["partner"] = species_name(a.roi_b.taxon)
        out.append(row)
    return out


def pop_unseen(player, limit=3):
    """
    The newest checked beetles the player hasn't been shown, how many there are and the points they moved in all
    (#382: "Since your last visit: 5 answers checked, +34 points"), then all unseen ones marked as seen.
    """
    shown = items(player, unseen_only=True, limit=limit)
    unseen = RetroCredit.objects.filter(player=player, seen_at__isnull=True)
    moved = list(unseen.values_list("points_before", "points_after"))
    if moved:
        unseen.update(seen_at=timezone.now())
    return shown, len(moved), round(sum(after - before for before, after in moved), 1)
