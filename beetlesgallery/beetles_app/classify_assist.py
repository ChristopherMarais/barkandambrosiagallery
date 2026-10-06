"""
"Generate AI recommendation" on the annotation page: ask IBBI-AI for boxes and species, and add them as new ROIs.

The new ROIs are never validated: a person does that. A box that overlaps an existing ROI of the image adds no new
ROI, so running it twice, or on an image that already has boxes, adds only what is new; the existing ROI gets the
AI's name as a suggestion if it has none from that model or a better one yet (its name, tier and box stay as they
are). Each proposed species is kept as a ModelPrediction, so the model's confidence and runners-up are not lost. Only
the best model's suggestion is shown (ibbi_models.MODEL_PREFERENCE), so a worse model's is never added next to it.

A new ROI gets the same metadata as one drawn with the mouse (see roi_defaults.py): the first box fills the image's
box-less template ROI if it has one, and every other box copies the specimen and collection details (aspect, country,
sex, notes...) of the ROI that was most recently updated. The species is the model's; a copied species is never kept
for a box the model labelled differently or not at all.

The AI page uses the same code: a photo kept from it gets its ROIs here, and a photo already in the gallery (opened
from its specimen page) only gets suggestions on its ROIs that have none as good yet (attach_suggestions).
"""
import re

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import roi_defaults
from .bbox_rules import TOLERANCE
from .models import Beetles, ModelPrediction, Taxon

from beetlesgallery.tools import ibbi_models  # the models on offer (one list for the service and the site)

ARCHITECTURES = tuple(ibbi_models.MODELS) + tuple(ibbi_models.LEGACY)
SAME_BOX_IOU = 0.5
# IBBI-AI's suggestions are ModelPredictions named "annotator:<model key>", from the annotation page and the AI page
# alike, so the game's trust in a model (game_reference) counts every run of it. A photo's institution and added_by
# say where it came from.
MODEL_PREFIX = "annotator:"
AI_PAGE_INSTITUTION = "AI page"   # ImageAsset.image_institution of a photo kept from the AI page


class ClassifyError(Exception):
    """A problem to show the annotator; nothing was added."""


class ClassifyTimeout(ClassifyError):
    """The service took too long: usually the model is starting up, so trying again in a minute helps."""


TIMEOUT_MESSAGE = "The AI model is waking up. Please try again in a minute."


def outranked(model_name, other_names):
    """True when one of ``other_names`` is a better model than ``model_name`` (ibbi_models.MODEL_PREFERENCE)."""
    rank = ibbi_models.preference(model_name)
    return any(ibbi_models.preference(name) < rank for name in other_names)


def iou(a, b):
    """Overlap of two boxes given as (x, y, width, height)."""
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    w = min(ax2, bx2) - max(a[0], b[0])
    h = min(ay2, by2) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / (a[2] * a[3] + b[2] * b[3] - inter)


def to_fractions(pixel_box, width, height):
    """(x1, y1, x2, y2) in pixels to (x, y, w, h) fractions clamped to the image, or None if it is empty."""
    x1, y1, x2, y2 = (float(v) for v in pixel_box)
    x1, x2 = max(0.0, min(x1, x2) / width), min(1.0, max(x1, x2) / width)
    y1, y2 = max(0.0, min(y1, y2) / height), min(1.0, max(y1, y2) / height)
    if x2 - x1 <= TOLERANCE or y2 - y1 <= TOLERANCE:
        return None
    return tuple(round(v, 6) for v in (x1, y1, x2 - x1, y2 - y1))


def _norm(name):
    return re.sub(r"\s+", " ", str(name or "").replace("_", " ")).strip().lower()


def call_classifier(image_bytes, filename, content_type, architecture, box_threshold):
    """The Modal service's answer for one image, or ClassifyError."""
    architecture = ibbi_models.resolve(architecture)
    if architecture is None:
        raise ClassifyError("Unknown model.")
    try:
        response = requests.post(
            settings.MODAL_API_URL, data={"architecture": architecture, "box_threshold": box_threshold},
            files={"image": (filename, image_bytes, content_type)}, timeout=300,
        )
    except requests.exceptions.Timeout:
        raise ClassifyTimeout(TIMEOUT_MESSAGE)
    except requests.exceptions.RequestException:
        raise ClassifyError("The AI service could not be reached. Please try again later.")
    if response.status_code != 200:
        raise ClassifyError(f"The AI service returned an error ({response.status_code}).")
    data = response.json()
    if data.get("status") != "success":
        raise ClassifyError("The AI service could not process this image.")
    return data


def known_rank_names():
    """{rank: {lower-cased name: name as in the species list}} for subfamily, tribe and genus."""
    known = {rank: {} for rank in ("subfamily", "tribe", "genus")}
    for values in Taxon.objects.values_list(*known):
        for rank, value in zip(known, values):
            if value:
                known[rank].setdefault(value.lower(), value)
    return known


def rank_confidence(levels, known):
    """What a hierarchical classifier said above the species, as ModelPrediction.rank_confidence."""
    out = {}
    for rank, names in known.items():
        level = (levels or {}).get(rank) or {}
        name = names.get(_norm(level.get("taxon")))
        if name:
            out[rank] = {"value": name, "confidence": round(min(1.0, max(0.0, float(level.get("prob") or 0))), 4)}
    return out


def readable_model_name(model_name):
    """What people see for a prediction's model: "IBBI-AI · DINOv3" for IBBI-AI's runs; any other model as named."""
    if not (model_name or "").startswith(MODEL_PREFIX):
        return model_name
    key = model_name[len(MODEL_PREFIX):]
    spec = ibbi_models.MODELS.get(ibbi_models.resolve(key))
    return f"IBBI-AI · {spec['label'] if spec else key}"


def _image_size(asset):
    width, height = asset.image_width, asset.image_height
    if not (width and height):
        from PIL import Image
        with asset.image_file.open("rb") as fh:
            width, height = Image.open(fh).size
    return width, height


class _Suggestions:
    """Turns the detections of one answer into ModelPrediction rows (the AI's name for a box, as a suggestion)."""

    def __init__(self, result, user):
        self.result = result
        self.names = {_norm(t.scientific_name): t for t in Taxon.objects.exclude(scientific_name__isnull=True)}
        self.ranks = known_rank_names()
        self.class_names = result.get("class_names") or []
        self.model_name = f"{MODEL_PREFIX}{result.get('model_used') or 'classifier'}"[:100]
        self.user = user

    def detections(self, width, height):
        """(box as fractions, detection), best score first; empty boxes are left out."""
        for det in sorted(self.result.get("detections", []), key=lambda d: -float(d.get("score") or 0)):
            box = to_fractions(det.get("box") or [0, 0, 0, 0], width, height)
            if box is not None:
                yield box, det

    def best(self, det):
        """The species the model names for this box, if it is in the species list."""
        return self.names.get(_norm(det.get("species") or det.get("label")))

    def save(self, roi, det):
        """
        Keep the model's name for this box on ``roi``, once, unless a better model already named it (only the best
        model's suggestion is shown). True when a suggestion was added.
        """
        best = self.best(det)
        if best is None:
            return False
        if outranked(self.model_name, ModelPrediction.objects.filter(roi=roi).values_list("model_name", flat=True)):
            return False
        if det.get("candidates") is not None:
            pairs = [(c.get("name"), c.get("prob")) for c in det["candidates"]]
        else:   # a service from before ibbi 0.3
            pairs = list(zip(self.class_names, det.get("probs") or []))
        runners = sorted(
            ((self.names.get(_norm(n)), p) for n, p in pairs if self.names.get(_norm(n)) not in (None, best)),
            key=lambda item: -item[1],
        )[:5]
        species_level = (det.get("levels") or {}).get("species") or {}
        confidence = species_level.get("prob", det.get("confidence", det.get("score")))
        _, created = ModelPrediction.objects.get_or_create(
            roi=roi, model_name=self.model_name, model_version="",
            defaults=dict(
                valid_species_id=best.valid_species_id, taxon=best,
                confidence=min(1.0, max(0.0, float(confidence or 0))),
                top_k=[{"valid_species_id": t.valid_species_id, "confidence": round(float(p), 4)} for t, p in runners],
                rank_confidence=rank_confidence(det.get("levels"), self.ranks), uploaded_by=self.user,
            ),
        )
        return created


def _overlapping(box, boxed):
    """The ROI whose box overlaps ``box`` most, if by SAME_BOX_IOU or more. ``boxed`` is [(box, roi), ...]."""
    best = max(boxed, key=lambda item: iou(box, item[0]), default=None)
    return best[1] if best is not None and iou(box, best[0]) >= SAME_BOX_IOU else None


def _boxed(rois):
    return [((r.bbox_x, r.bbox_y, r.bbox_width, r.bbox_height), r) for r in rois if r.bbox_x is not None]


def add_rois(asset, result, user):
    """
    Create unvalidated ROIs from IBBI-AI's ``result``. A found box over an existing ROI adds no ROI: that ROI gets the
    AI's name as a suggestion if it has none from this model or a better one yet, and nothing else about it changes.
    Returns {"added": new ROIs, "attached": existing ROIs that got a suggestion, "already_boxed": boxes already there
    that added nothing}.
    """
    width, height = _image_size(asset)
    suggestions = _Suggestions(result, user)
    now = timezone.now()

    existing = _boxed(Beetles.objects.filter(image_asset=asset, is_deleted=False, bbox_x__isnull=False))
    before = {roi.id for _, roi in existing}
    suggested = set(ModelPrediction.objects.filter(roi__image_asset=asset, model_name=suggestions.model_name)
                    .values_list("roi_id", flat=True))
    # The metadata every new box starts with, from what the image had before this run
    template = roi_defaults.template_roi(asset)
    inherited = roi_defaults.inherited_fields(roi_defaults.latest_roi(asset), species=False)
    counts = {"added": 0, "attached": 0, "already_boxed": 0}
    with transaction.atomic():
        for box, det in suggestions.detections(width, height):
            there = _overlapping(box, existing)
            if there is not None:
                # Not a new beetle, but the AI's name for it is still kept (once per model; never a label)
                if there.id in before and there.id not in suggested and suggestions.save(there, det):
                    suggested.add(there.id)
                    counts["attached"] += 1
                else:
                    counts["already_boxed"] += 1
                continue
            # The species the model names: a pipeline only labels the box when it is sure down to the species
            # (depth 4); its best species still becomes the prediction, with the ranks above it.
            best = suggestions.best(det)
            sure = det.get("depth", 4) >= 4
            taxon = best if sure else None
            audit = dict(
                bbox_x=box[0], bbox_y=box[1], bbox_width=box[2], bbox_height=box[3],
                bbox_is_validated=False, bbox_created_by=user, bbox_created_at=now, last_updated_by=user,
            )
            if template is not None:
                # The image's own metadata stays; the model's species only fills a blank one.
                roi, template = template, None
                for name, value in audit.items():
                    setattr(roi, name, value)
                if taxon and not roi.depicts_valid_name_id:
                    roi.depicts_valid_name_id = taxon.valid_species_id
                roi.save()
            else:
                roi = Beetles.objects.create(
                    image_asset=asset, depicts_valid_name_id=taxon.valid_species_id if taxon else None,
                    **inherited, **audit,
                )
            if roi.id not in suggested and suggestions.save(roi, det):
                suggested.add(roi.id)
            existing.append((box, roi))
            counts["added"] += 1
    return counts


def attach_suggestions(asset, result, user):
    """
    A photo already in the gallery, run through the AI page from its specimen page: its ROIs that have no suggestion
    from this model or a better one yet get the AI's name, matched by box (SAME_BOX_IOU). When the photo has a single ROI without a box,
    the best box that matches no other ROI is taken to be that one. Never adds an ROI or an image, and never changes
    an ROI. Returns how many ROIs got a suggestion.
    """
    width, height = _image_size(asset)
    suggestions = _Suggestions(result, user)
    rois = list(Beetles.objects.filter(image_asset=asset, is_deleted=False))
    boxed = _boxed(rois)
    boxless = [r for r in rois if r.bbox_x is None]
    only_boxless = boxless[0] if len(boxless) == 1 else None
    suggested = set()   # ROIs named in this run (the first, surest box wins)
    attached = 0
    with transaction.atomic():
        for box, det in suggestions.detections(width, height):
            roi = _overlapping(box, boxed)
            if roi is None:
                roi, only_boxless = only_boxless, None
            if roi is None or roi.id in suggested:
                continue
            if suggestions.save(roi, det):
                suggested.add(roi.id)
                attached += 1
    return attached


# --- Images submitted to the public classifier page ----------------------------------------------------------------
SAVED, DUPLICATE, NOT_SAVED, OPTED_OUT = "saved", "already_on_platform", "not_saved", "opted_out"
# A photo already in the gallery (from its specimen page): the AI's names were kept on its ROIs, or there was nothing new
ATTACHED, NOTHING_NEW = "attached", "nothing_new"
MAX_SUBMISSION_PIXELS = 100_000_000
MAX_SUBMISSION_BYTES = 25 * 1024 * 1024
SUBMISSIONS_PER_HOUR = 20


def save_classifier_submission(image_bytes, filename, result, user=None):
    """
    Keep an image that was sent to the AI page and had at least one beetle found in it, as an unvalidated image with
    the proposed boxes and species (see add_rois). Its institution says "AI page", and added_by is the person who
    sent it when they were signed in.

    An image already on the platform (same bytes, by SHA-256, deleted ones included) is never replaced, touched or
    given new ROIs: the platform's copy is kept. Returns SAVED, DUPLICATE or NOT_SAVED (no beetle, or not a usable image).
    """
    import hashlib
    import io

    from django.db import IntegrityError
    from PIL import Image

    from .image_pipeline import write_original_and_thumb96
    from .models import ImageAsset

    if not result.get("detections") or len(image_bytes) > MAX_SUBMISSION_BYTES:
        return NOT_SAVED
    digest = hashlib.sha256(image_bytes).hexdigest()
    if ImageAsset.objects.filter(image_sha256=digest).exists():
        return DUPLICATE
    try:
        with Image.open(io.BytesIO(image_bytes)) as probe:
            if probe.width * probe.height > MAX_SUBMISSION_PIXELS:
                return NOT_SAVED
            probe.verify()
    except Exception:
        return NOT_SAVED

    saved = write_original_and_thumb96(digest, io.BytesIO(without_location(image_bytes)))
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "image")[:120]
    submitter = user if getattr(user, "is_authenticated", False) else None
    try:
        with transaction.atomic():
            asset = ImageAsset.objects.create(
                full_path_at_import=f"classifier/{digest[:16]}/{safe_name}",
                image_sha256=digest, image_size_bytes=len(image_bytes),
                image_file=saved["original_path"], thumb_small=saved["thumb_path"],
                image_width=saved["image_size"][0], image_height=saved["image_size"][1],
                thumb_width=saved["thumb_size"][0], thumb_height=saved["thumb_size"][1],
                image_institution=AI_PAGE_INSTITUTION,
                image_notes="Submitted to IBBI-AI on the AI page; boxes and species are proposals to be checked.",
                added_by=submitter, last_updated_by=submitter,
            )
            add_rois(asset, result, submitter)
    except IntegrityError:
        return DUPLICATE  # the same image arrived twice at once
    return SAVED


def without_location(image_bytes):
    """The same image without GPS coordinates in its EXIF, so a photo's location is not published. Other bytes are returned as they were."""
    import io

    from PIL import Image

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            exif = img.getexif()
            if 0x8825 not in exif or img.format != "JPEG":   # 0x8825 is the GPS block
                return image_bytes
            del exif[0x8825]
            out = io.BytesIO()
            img.save(out, "JPEG", exif=exif, quality="keep")
            return out.getvalue()
    except Exception:
        return image_bytes
