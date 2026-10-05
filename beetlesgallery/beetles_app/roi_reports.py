"""
Report a beetle (an ROI) from its details page, as players do in the game: the report goes to the curators on the
Image Annotation page (game_feedback.create_report) and the beetle stays out of the game until they deal with it.
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from .models import Beetles, GameReport

# One line each, as in the game's report menu. The name is shown on the details page, so "Wrong name" is offered here.
REASONS = [("wrong_label", "Wrong name"), ("bad_box", "Box doesn't fit"), ("bad_image", "Bad photo"),
           ("other", "Something else")]


@login_required
@require_POST
def report_roi(request, beetle_id):
    from . import game_feedback
    roi = get_object_or_404(Beetles, pk=beetle_id, is_deleted=False)
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        body = {}
    reason = body.get("reason")
    if reason not in dict(REASONS):
        return JsonResponse({"error": "Please choose a reason."}, status=400)
    already = GameReport.objects.filter(roi=roi, reporter=request.user, status=GameReport.Status.OPEN).exists()
    report = game_feedback.create_report(request.user, roi, reason, str(body.get("note") or ""))
    return JsonResponse({"status": report.status, "already": already})
