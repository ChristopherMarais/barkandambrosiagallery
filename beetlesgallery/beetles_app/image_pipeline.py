from __future__ import annotations
from pathlib import Path
import io, os
import imghdr
import tempfile
from typing import BinaryIO, Tuple, Optional

from django.conf import settings

from django.core.files.base import File, ContentFile
from django.core.files.storage import default_storage

from PIL import Image, ImageOps
from .models import Beetles

# --- Utilities ---

def ensure_thumbnail(beetle, size: int = 96) -> str:
    """
    Build a 96px thumbnail if missing and return the relative storage path.
    Uses Beetles.path_for_thumb96() to compute the canonical location.
    Does nothing if no original image_file or no image_sha256.
    """
    if not beetle.image_file or not beetle.image_sha256:
        return ""

    # Canonical WEBP path
    rel = Beetles.path_for_thumb96(beetle.image_sha256, webp=True)

    # If thumb already exists, done
    try:
        if default_storage.exists(rel):
            return rel
    except Exception:
        pass

    # If original is missing, bail gracefully
    try:
        if not default_storage.exists(beetle.image_file.name):
            return ""
    except Exception:
        return ""

    # Open original from storage (local or S3)
    with beetle.image_file.storage.open(beetle.image_file.name, "rb") as fh:
        im = Image.open(fh)
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        im.thumbnail((size, size), Image.LANCZOS)

        buf = io.BytesIO()
        try:
            im.save(buf, format="WEBP", quality=80, method=6)
            buf.seek(0)
            default_storage.save(rel, buf)
            return rel
        except Exception:
            # JPEG fallback
            rel_jpg = Beetles.path_for_thumb96(beetle.image_sha256, webp=False)
            buf = io.BytesIO()
            im.convert("RGB").save(buf, format="JPEG", quality=85, optimize=True)
            buf.seek(0)
            default_storage.save(rel_jpg, buf)
            return rel_jpg

def _guess_ext_from_path_or_hdr(tmp_path: str) -> str:
    kind = imghdr.what(tmp_path)
    if kind in {"jpeg", "jpg"}:
        return "jpg"
    if kind in {"png", "bmp", "gif", "tiff", "webp"}:
        return "webp" if kind == "webp" else kind
    return "jpg"

def _ensure_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGB", "L"):
        return img
    if img.mode in ("RGBA", "LA"):
        return img.convert("RGBA")
    return img.convert("RGB")

def _center_crop_square(img: Image.Image) -> Image.Image:
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))

# --- Public entrypoint used by import ---

def write_original_and_thumb96(sha256: str, fileobj: BinaryIO) -> dict:
    """
    Stream ZIP member to temp file, then:
      - Save original to content-addressed path (streaming, no RAM copy)
      - Generate 96x96 thumb
    """
    # 1) Stream to temp file (no large RAM usage)
    # On Windows, use delete=False so we can reopen it; we'll unlink at the end.
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp_path = tmp.name
    try:
        # Copy in chunks
        for chunk in iter(lambda: fileobj.read(1024 * 1024), b""):
            tmp.write(chunk)
        tmp.flush()
        tmp.close()

        # 2) Guess extension and build original path
        orig_ext = _guess_ext_from_path_or_hdr(tmp_path)
        orig_rel = Beetles.path_for_original(sha256, orig_ext)

        # Save original by streaming from disk to storage; keep if already exists
        if not default_storage.exists(orig_rel):
            with open(tmp_path, "rb") as src:
                default_storage.save(orig_rel, File(src))

        # 3) Open with PIL from temp (EXIF-aware), then make 96x96 thumb
        with Image.open(tmp_path) as img:
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            img = _ensure_rgb(img)
            w, h = img.size

            thumb = _center_crop_square(img)
            thumb = thumb.resize((96, 96), Image.LANCZOS)

            # Prefer WEBP; fallback to JPEG if WEBP not available
            thumb_is_webp = True
            thumb_rel = Beetles.path_for_thumb96(sha256, webp=True)
            buf = io.BytesIO()
            try:
                thumb.save(buf, format="WEBP", quality=80, method=4)
            except Exception:
                thumb_is_webp = False
                thumb_rel = Beetles.path_for_thumb96(sha256, webp=False)
                thumb_rgb = thumb.convert("RGB") if thumb.mode != "RGB" else thumb
                buf = io.BytesIO()
                thumb_rgb.save(buf, format="JPEG", quality=85, optimize=True)

            if not default_storage.exists(thumb_rel):
                default_storage.save(thumb_rel, ContentFile(buf.getvalue()))

        return {
            "original_path": orig_rel,
            "thumb_path": thumb_rel,
            "image_size": (w, h),
            "thumb_size": (96, 96),
        }
    finally:
        # 4) Clean up temp file
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
