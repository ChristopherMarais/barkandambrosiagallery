"""Pages to download, upload and update interactions (see beetles_app/interaction_upload.py)."""
import os

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone

from . import interaction_upload as upload
from .areas import INTERACTIONS, area_required
from .models import PathogenInteraction
from .views import _format_size


class _Echo:
    def write(self, value):
        return value


@login_required
def interactions_export(request):
    """The interactions in the database as a CSV, with record_id, ready to edit and upload again."""
    import csv
    writer = csv.DictWriter(_Echo(), fieldnames=upload.EXPORT_COLUMNS)

    def lines():
        yield "﻿" + writer.writeheader()
        for row in upload.export_rows():
            yield writer.writerow(row)

    response = StreamingHttpResponse(lines(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="interactions_{timezone.localtime():%Y%m%d_%H%M%S}.csv"'
    return response


@area_required(INTERACTIONS)
def interactions_initial_file(request):
    """The published v1.0 dataset as an upload file (see make_interactions_upload_file), to start an empty database by hand."""
    import json

    from .management.commands.make_interactions_upload_file import SOURCE, render

    text = render(json.loads(SOURCE.read_text(encoding="utf-8")))
    response = HttpResponse(text.encode("utf-8"), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="interactions_v1.0_upload.csv"'
    return response


@area_required(INTERACTIONS)
def upload_interactions(request):
    limit = getattr(settings, "MAX_UPLOAD_SIZE_INTERACTIONS", 20 * 1024 * 1024)
    counts = {origin: PathogenInteraction.objects.filter(origin=origin).count() for origin in PathogenInteraction.Origin.values}
    context = {"max_mb": limit // (1024 * 1024), "counts": counts}
    if request.method == "POST":
        csv_file = request.FILES.get("csv_file")
        if not csv_file:
            context["error"] = "Please attach a .csv file."
        elif os.path.splitext(csv_file.name)[1].lower() != ".csv":
            context["error"] = "The interactions file must be a .csv."
        elif csv_file.size and csv_file.size > limit:
            context["error"] = f"The file is too large ({_format_size(csv_file.size)}); the limit is {_format_size(limit)}."
        else:
            context["result"] = upload.import_interactions(csv_file, user=request.user, dry_run=bool(request.POST.get("dry_run")))
            context["filename"] = csv_file.name
            context["counts"] = {origin: PathogenInteraction.objects.filter(origin=origin).count() for origin in PathogenInteraction.Origin.values}
    return render(request, "beetles/upload_interactions.html", context)
