"""
Big files arrive in pieces, so uploads never need to bypass Cloudflare (its free plan refuses any request over
100 MB; the old workaround sent people to direct.barkandambrosiagallery.org, which also pulled the staging site's
visitors over to the live one).

The browser cuts the file into CHUNK_BYTES pieces and posts them one after the other to upload_chunk(); they are
written into one file in MEDIA_ROOT/tmp_uploads/. The form that needs the file (the new-data upload) then sends
the file's upload id instead of the file, and take() hands the assembled file over. A piece that is sent again
(a retry after a dropped connection) is written over itself.

An upload that stopped part-way carries on: the browser remembers the file's upload id, asks upload_chunk_status()
how much of it is here and sends only the rest. Files never finished are removed by the nightly cleanup
(storage_cleanup.sweep_upload_temp_files) once no piece has arrived for TEMP_FILE_KEEP_HOURS, which is how long
an upload can be carried on.
"""
import os
import re
import uuid
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .areas import UPLOAD, area_required

CHUNK_BYTES = int(getattr(settings, "UPLOAD_CHUNK_BYTES", 50 * 1024 * 1024))   # well under Cloudflare's 100 MB per request
NAME = re.compile(r"^[0-9a-f-]{36}$")


def _limit():
    return int(getattr(settings, "MAX_UPLOAD_SIZE_ZIP", 2 * 1024 ** 3))


def part_path(user, upload_id):
    """Where a user's file in progress is kept. None for an id that is not a UUID."""
    upload_id = str(upload_id or "").lower()
    if not NAME.match(upload_id):
        return None
    try:
        upload_id = uuid.UUID(upload_id)
    except ValueError:
        return None
    folder = os.path.normpath(os.path.join(settings.MEDIA_ROOT, "tmp_uploads"))
    path = os.path.normpath(os.path.join(folder, f"chunk-{user.pk}-{upload_id}.part"))
    if not path.startswith(folder + os.sep):   # a UUID cannot leave the folder; the check says so to the code scanner
        return None
    return Path(path)


def _received(path):
    """Bytes of a file in progress that are here: 0 when there is none (never started, or swept by the cleanup)."""
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


@area_required(UPLOAD)
@require_POST
def upload_chunk(request):
    """One piece of a file: upload_id, offset (bytes), total (bytes) and the piece itself as "chunk"."""
    path = part_path(request.user, request.POST.get("upload_id"))
    piece = request.FILES.get("chunk")
    try:
        offset, total = int(request.POST.get("offset", "")), int(request.POST.get("total", ""))
    except ValueError:
        return JsonResponse({"error": "offset and total must be numbers."}, status=400)
    if path is None or piece is None:
        return JsonResponse({"error": "Missing upload_id or chunk."}, status=400)
    if total <= 0 or total > _limit():
        return JsonResponse({"error": f"The file must be under {_limit() // 1024 ** 2} MB."}, status=400)
    if piece.size > CHUNK_BYTES or offset < 0 or offset + piece.size > total:
        return JsonResponse({"error": "This piece does not fit the file."}, status=400)

    path.parent.mkdir(parents=True, exist_ok=True)
    have = _received(path)
    if offset > have:
        return JsonResponse({"error": "A piece is missing; send again from 'received'.", "received": have}, status=409)
    with open(path, "r+b" if path.exists() else "wb") as out:
        out.seek(offset)
        for block in piece.chunks():
            out.write(block)
        out.truncate(offset + piece.size)   # a retried piece replaces what was there
    return JsonResponse({"received": offset + piece.size, "complete": offset + piece.size == total})


@area_required(UPLOAD)
@require_GET
@never_cache
def upload_chunk_status(request):
    """How much of an upload (?upload_id=) is here, so a browser carries on where it stopped: {"received": bytes}."""
    path = part_path(request.user, request.GET.get("upload_id"))
    if path is None:
        return JsonResponse({"error": "Missing or bad upload_id."}, status=400)
    return JsonResponse({"received": _received(path)})


def take(user, upload_id, total, name="images.zip"):
    """
    The assembled file as a Django File (open, seekable), or None when it is missing or not all there.
    The caller saves it where it belongs and then calls discard().
    """
    path = part_path(user, upload_id)
    try:
        total = int(total)
    except (TypeError, ValueError):
        return None
    if path is None or not path.exists() or path.stat().st_size != total:
        return None
    handle = File(open(path, "rb"), name=name)
    handle.size = total
    return handle


def discard(user, upload_id):
    path = part_path(user, upload_id)
    if path is not None:
        path.unlink(missing_ok=True)
