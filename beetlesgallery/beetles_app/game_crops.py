"""
The game's crops, cut on the server (#494).

The feed shows each beetle as a window around its box: the box plus a quarter of it on every side, plain grey where
the window runs past the photo's edge. The browser used to cut that window out of the full photo, so every beetle
meant downloading and decoding a whole (often very large) photo. Here the same window is cut once, at two sizes: a
small one the feed shows at once and a large one it swaps in when it arrives. The box outline is drawn by the browser
(it sits at 1/6..5/6 of the window), so it stays sharp at any size.

Files live under MEDIA_ROOT/crops/, named by the photo and the box, so a moved box or a replaced photo gets a new file
and an old file never has to be invalidated. A new batch's crops are cut on the worker as soon as the batch is made,
so they are usually ready before the feed asks for them; any that aren't are cut on that first request. Every
beetle's small crop is also cut ahead of any batch, by a sweep on the heavy worker (precut).
"""
import contextvars
import hashlib
import logging
import math
import os
import tempfile
import uuid
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

PAD = 0.25                                  # context around the box, as a share of the box on each side
SIZES = {"small": 360, "large": 1400}       # longest side in pixels; "small" first, "large" swapped in
GREY = (229, 231, 235)                      # where the window runs past the photo (#e5e7eb, as the feed's grey)
VERSION = 1                                 # bump when the cutting changes, so files cut the old way aren't reused

# What a request built, for the timing line in the server log (game_views._timed). None outside a timed request.
built = contextvars.ContextVar("game_built", default=None)


def _webp():
    from PIL import features

    return features.check("webp")


def file_format():
    """("WEBP", "webp", "image/webp"), or JPEG where this Pillow can't write WebP."""
    return ("WEBP", "webp", "image/webp") if _webp() else ("JPEG", "jpg", "image/jpeg")


def window(box):
    """The window around a box, in photo fractions: (x0, y0, width, height). It may run past the photo's edges."""
    bx, by, bw, bh = (float(v) for v in box)
    return bx - bw * PAD, by - bh * PAD, bw * (1 + 2 * PAD), bh * (1 + 2 * PAD)


def _box(roi):
    return [roi.bbox_x, roi.bbox_y, roi.bbox_width, roi.bbox_height]


def crop_key(roi):
    """A name for this photo and box: a new box (or a new photo) gives a new name, so files can be cached forever."""
    asset = roi.image_asset
    photo = (asset.image_sha256 or asset.image_file.name or "") if asset else ""
    box = ",".join(f"{float(v):.6f}" for v in _box(roi))
    return hashlib.sha256(f"{photo}|{box}|{VERSION}".encode()).hexdigest()[:20]


def size_name(size):
    """The size as SIZES spells it (its own key, never the caller's string), or None for a size it doesn't have."""
    return next((name for name in SIZES if name == size), None)


def crop_name(roi, size):
    """The crop's path under MEDIA_ROOT. None for an unknown size."""
    size = size_name(size)
    if size is None:
        return None
    key = crop_key(roi)
    return f"crops/{key[:2]}/{key[2:4]}/{uuid.UUID(str(roi.id))}_{key}_{size}.{file_format()[1]}"


def aspect(roi):
    """
    The crop's width over its height (the window around the box keeps the box's shape), from the photo's stored size:
    the page sizes a photo's tile with it before the crop arrives. None when the photo's size isn't stored.
    """
    asset = roi.image_asset
    width, height = (asset.image_width, asset.image_height) if asset is not None else (None, None)
    if not (width and height and roi.has_bbox()):
        return None
    return round(float(roi.bbox_width) * width / (float(roi.bbox_height) * height), 4)


def crop_url(round_id, index, image, roi, size):
    """
    Where the feed gets a shown beetle's crop (game_views.game_crop): by its batch, its item and its place there, never
    by the beetle. ``v`` changes with the box, so the browser may keep the file for good.
    """
    from django.urls import reverse

    return f"{reverse('game_crop', args=[round_id, index, image, size])}?v={crop_key(roi)}"


def crop_path(roi, size):
    """
    Where the crop is kept (an absolute Path), or None for an unknown size or a name that would leave the crops
    folder. The name is made only from the beetle's id, a hash and SIZES' own key, so it never can; the check says
    so to the code scanner too.
    """
    name = crop_name(roi, size)
    if name is None:
        return None
    folder = os.path.normpath(os.path.join(settings.MEDIA_ROOT, "crops"))
    path = os.path.normpath(os.path.join(settings.MEDIA_ROOT, name))
    if not path.startswith(folder + os.sep):
        return None
    return Path(path)


def cut(fileobj, box, max_side):
    """
    Cut the window around ``box`` out of a photo, at most ``max_side`` pixels on its longest side, as an RGB image:
    the same window the feed used to cut in the browser, without the outline.
    """
    from PIL import Image, ImageOps

    with Image.open(fileobj) as im:
        raw_w, raw_h = im.size
        turned = (im.getexif().get(0x0112) or 1) in (5, 6, 7, 8)   # EXIF orientations that swap width and height
        width, height = (raw_h, raw_w) if turned else (raw_w, raw_h)
        x0, y0, ww, wh = window(box)
        scale = min(1.0, max_side / max(ww * width, wh * height, 1e-9))
        out_w, out_h = max(1, round(ww * width * scale)), max(1, round(wh * height * scale))
        if scale < 1:
            # a JPEG can be decoded at 1/2, 1/4 or 1/8 of its size: much quicker for a small crop
            im.draft("RGB", (math.ceil(raw_w * scale), math.ceil(raw_h * scale)))
        photo = ImageOps.exif_transpose(im)
        if photo.mode != "RGB":
            photo = photo.convert("RGB")
        pw, ph = photo.size
        canvas = Image.new("RGB", (out_w, out_h), GREY)
        # the part of the window that is on the photo
        sx0, sy0, sx1, sy1 = max(0.0, x0), max(0.0, y0), min(1.0, x0 + ww), min(1.0, y0 + wh)
        if sx1 > sx0 and sy1 > sy0:
            part = photo.crop((round(sx0 * pw), round(sy0 * ph), max(round(sx0 * pw) + 1, round(sx1 * pw)),
                               max(round(sy0 * ph) + 1, round(sy1 * ph))))
            dx0, dy0 = round((sx0 - x0) / ww * out_w), round((sy0 - y0) / wh * out_h)
            dx1, dy1 = round((sx1 - x0) / ww * out_w), round((sy1 - y0) / wh * out_h)
            canvas.paste(part.resize((max(1, dx1 - dx0), max(1, dy1 - dy0)), Image.LANCZOS), (dx0, dy0))
        return canvas


def _source(asset):
    """The file to cut from: a TIFF's display JPEG where there is one (quicker to open), else the photo itself."""
    name = (asset.image_file.name or "").lower()
    if name.endswith((".tif", ".tiff")) and asset.image_sha256:
        display = asset.path_for_display(asset.image_sha256)
        storage = asset.image_file.storage
        if storage.exists(display):
            return storage.open(display, "rb")
    return asset.image_file.open("rb")


def ensure(roi, size):
    """
    The crop's file (an absolute Path), cut now if it isn't there yet. None if the photo can't be read: the feed then
    falls back to cutting the whole photo itself.
    """
    path = crop_path(roi, size)
    if path is None:
        return None
    if path.exists():
        return path
    asset = roi.image_asset
    if asset is None or not asset.image_file or not roi.has_bbox():
        return None
    try:
        with _source(asset) as fh:
            image = cut(fh, _box(roi), SIZES[size_name(size)])
    except Exception:   # a missing or unreadable photo: not worth failing the feed over
        logger.warning("Could not cut the %s crop of %s", size, roi.id, exc_info=True)
        return None
    fmt = file_format()[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    # written under a hidden name and renamed, so a half-written file is never served (or reused)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out:
            if fmt == "WEBP":
                image.save(out, format=fmt, quality=80, method=4)
            else:
                image.save(out, format=fmt, quality=85, optimize=True)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


# A grid of this many beetles or more (4×4, 5×5) shows each one small, smaller than its small crop on any screen: its
# sharp crops are not cut or loaded ahead, only a tile's own once it is zoomed. They were most of the work of a big grid
# (a large crop takes two to four times as long to cut as a small one) and most of its download.
BIG_GRID = 16


def sharp_on_zoom(item):
    """Whether a batch item is a big grid, whose sharp crops wait until a tile is zoomed (BIG_GRID)."""
    return len(item.get("tiles") or []) >= BIG_GRID


def sizes_ahead(item):
    """The crops of a batch item cut and loaded ahead of time: small and large, a big grid's small ones only."""
    return ("small",) if sharp_on_zoom(item) else tuple(SIZES)


def ahead(items):
    """[(roi, sizes)]: every beetle these batch items show, in their order, with the crops cut ahead for it."""
    from .models import Beetles

    wanted = {}
    for item in items:
        for i in (item.get("tiles") or [item.get("a"), item.get("b")]):
            if i:
                wanted.setdefault(i, set()).update(sizes_ahead(item))
    found = {str(k): v for k, v in Beetles.objects.select_related("image_asset").in_bulk(list(wanted)).items()}
    return [(found[i], [s for s in SIZES if s in sizes]) for i, sizes in wanted.items()
            if i in found and found[i].has_bbox()]


def cut_ahead(items):
    """Cut these batch items' crops ahead of time: every small one first (shown first), then the large ones."""
    made, rois = 0, ahead(items)
    for size in SIZES:
        for roi, sizes in rois:
            if size in sizes:
                made += ensure(roi, size) is not None
    return made


def prepare(round_id):
    """Cut a batch's crops ahead of time (cut_ahead)."""
    from .models import GameRound

    rnd = GameRound.objects.filter(id=round_id).first()
    return 0 if rnd is None else cut_ahead(rnd.items)


def prepare_later(rnd):
    """Queue prepare() on the Celery worker. Nothing is lost if the queue can't be reached: the feed cuts on request."""
    from .tasks import prepare_game_crops_task

    def queue():
        try:
            prepare_game_crops_task.apply_async(args=[str(rnd.id)], retry=False)
        except Exception:
            logger.info("Crops of batch %s not queued: they are cut as the feed asks for them", rnd.id)

    transaction.on_commit(queue)


# Every beetle's small crop, cut before any batch needs it: then no player ever waits for one to be cut, however fast
# they play or however busy the quick worker is. The sweep runs on the heavy worker (one job at a time, at low CPU
# priority), a part at a time, so an upload queued meanwhile goes between two parts. Small crops only: they are about
# 12 KB each, and most of what a batch shows; the large ones (five times the size) are still cut per batch, as before.
PRECUT_KEY = "game:precut"
PRECUT_EVERY = 6 * 60 * 60     # seconds: a sweep at most this often (it only cuts what's missing: new or moved boxes)
PRECUT_PART = 100              # crops one part cuts before queueing the next


def precut(after=None, part=PRECUT_PART):
    """
    The heavy worker's part of precut_later: cut the small crop of each playable beetle that has none yet, in id order
    from just after ``after``. After ``part`` crops it queues the rest (from the last beetle it got to) and stops.
    Returns how many it tried to cut. A photo that can't be read is passed over (ensure logs it).
    """
    from . import game

    rois = game.playable_rois().select_related("image_asset").order_by("pk")
    if after:
        rois = rois.filter(pk__gt=after)
    tried = 0
    for roi in rois.iterator(chunk_size=500):
        path = crop_path(roi, "small")
        if path is None or path.exists():
            continue
        ensure(roi, "small")
        tried += 1
        if tried >= part:
            _queue_precut(str(roi.pk))
            break
    return tried


def _queue_precut(after=None):
    from .tasks import precut_game_crops_task

    try:
        precut_game_crops_task.apply_async(args=[after], retry=False)
        return True
    except Exception:
        logger.info("Crop sweep not queued: crops are cut per batch and on request, as before")
        return False


def precut_later():
    """Queue a sweep of the small crops (precut) on the heavy worker, at most once every PRECUT_EVERY seconds."""
    if not cache.add(PRECUT_KEY, 1, PRECUT_EVERY):
        return False

    def queue():
        if not _queue_precut():
            cache.delete(PRECUT_KEY)   # try again with the next batch

    transaction.on_commit(queue)
    return True


@receiver(post_save, sender="beetles_app.GameRound", dispatch_uid="game_crops_new_batch")
def _new_batch(sender, instance, created, **kwargs):
    """A new batch: cut its crops on the worker while the player is still busy with the one before."""
    if not created:
        return
    prepare_later(instance)
    precut_later()
    stats = built.get()
    if stats is not None:
        stats["batches"] += 1
        stats["items"] += len(instance.items or [])
        stats["crops"] += sum(len(sizes_ahead(it)) * (len(it.get("tiles") or []) or 1 + bool(it.get("b")))
                              for it in instance.items or [])
