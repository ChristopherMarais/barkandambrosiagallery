"""
Importing classifier predictions from a CSV.

Used by the superuser upload page (views.upload_predictions) and by
``manage.py import_model_predictions`` (for large files loaded on the server).

The CSV has one row per ROI and model:

    record_id           the ROI's id (the record_id column of a gallery download)      required
    valid_species_id    the predicted species, as in the species list                   required
    confidence          0 to 1                                                          required
    model_name          e.g. "ibbi-rtdetr" (or give one default for the whole file)    required
    model_version       e.g. "2026-09"                                                  optional
    top_k               further candidates, best first: "1733:0.08;2210:0.03"           optional
                        (or a JSON list of {"valid_species_id": ..., "confidence": ...})
    subfamily, tribe,   what the model said at that rank, if it says so, each with      optional
    genus               a <rank>_confidence column (0 to 1); kept in rank_confidence

A box the model found can come in the same file: leave record_id empty and give

    image_id            the image's id (the image_id column of a gallery download)
    bbox_x, bbox_y,     the box as fractions of the image, top-left corner (as in a download)
    bbox_width, bbox_height

The box goes to the ROI already on the image with the same box (overlap of half or more), else to the image's
ROI that has no box yet, else to a new ROI with the image's details and no species label. Rows of the same file
with the same box (e.g. two models) share it. With a record_id, the box columns are not used.

The whole file is checked before anything is written; if any row is wrong nothing is saved and
every problem is reported with its row number. Uploading the same model version again replaces
its predictions. Predictions never change an ROI's label.
"""
import csv
import io
import json
import uuid
from dataclasses import asdict, dataclass, field

from django.db import transaction
from django.utils import timezone

from . import roi_defaults
from .bbox_rules import BOX_COLUMNS, is_blank, parse_box
from .classify_assist import SAME_BOX_IOU, iou
from .models import Beetles, ImageAsset, ModelPrediction, PredictionUpload, RoiDifficulty, Taxon

COLUMN_ALIASES = {
    "predicted_valid_species_id": "valid_species_id",
    "species_id": "valid_species_id",
    "roi_id": "record_id",
    "model": "model_name",
    "version": "model_version",
}
REQUIRED_COLUMNS = ("valid_species_id", "confidence")   # and record_id, or image_id with a box
UPPER_RANKS = ("subfamily", "tribe", "genus")   # optional per-rank columns, each with <rank>_confidence
RANKS = UPPER_RANKS + ("species",)
MAX_ERRORS_SHOWN = 30
CHUNK = 2000
PROGRESS_EVERY = 500   # rows between progress reports while checking


@dataclass
class ImportResult:
    rows: int = 0
    created: int = 0
    updated: int = 0
    errors: list = field(default_factory=list)
    error_count: int = 0
    dry_run: bool = False
    boxes_matched: int = 0     # rows without a record_id whose box was already on the image
    boxes_created: int = 0     # new boxes (a box-less ROI filled in, or a new ROI)

    @property
    def ok(self):
        return self.error_count == 0

    @property
    def hidden_errors(self):
        """Problems found beyond the ones listed in ``errors``."""
        return self.error_count - len(self.errors)


def normalise_species_id(value):
    """'1733', ' 1733 ' and the '1733.0' a spreadsheet writes are the same id."""
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def parse_confidence(value, what="confidence"):
    """(number, None) or (None, error). Percentages (87) are refused, not guessed at."""
    try:
        number = float(str(value).strip())
    except ValueError:
        return None, f"{what} '{value}' is not a number"
    if number != number or number in (float("inf"), float("-inf")):
        return None, f"{what} '{value}' is not a number"
    if not 0 <= number <= 1:
        return None, f"{what} {number:g} must be between 0 and 1 (use 0.87, not 87)"
    return number, None


def parse_top_k(text, species_ids, primary):
    """
    Parse the top_k cell. Returns (list, None) or (None, error).
    Each entry is {"valid_species_id", "confidence"}; the list is sorted best first and leaves out
    the primary species, which is already stored as the prediction itself.
    """
    text = (text or "").strip()
    if not text:
        return [], None
    entries = []
    try:
        if text.startswith("["):
            for item in json.loads(text):
                entries.append((normalise_species_id(item["valid_species_id"]), item["confidence"]))
        else:
            for part in text.split(";"):
                if part.strip():
                    species, _, conf = part.partition(":")
                    entries.append((normalise_species_id(species), conf))
    except (ValueError, KeyError, TypeError):
        return None, "top_k must look like '1733:0.08;2210:0.03' (or a JSON list of valid_species_id/confidence)"

    cleaned, seen = [], set()
    for species, conf in entries:
        if species not in species_ids:
            return None, f"top_k has a species id that is not in the species list: '{species}'"
        number, error = parse_confidence(conf, f"top_k confidence for {species}")
        if error:
            return None, error
        if species in seen:
            return None, f"top_k lists species {species} twice"
        seen.add(species)
        if species != primary:
            cleaned.append({"valid_species_id": species, "confidence": number})
    cleaned.sort(key=lambda e: -e["confidence"])
    return cleaned, None


def parse_rank_confidence(row, known):
    """
    The optional subfamily / tribe / genus columns and their confidences. Returns (dict, None) or (None, error).
    ``known`` is {rank: {lower-cased name: name as in the taxonomy}}.
    """
    out = {}
    for rank in UPPER_RANKS:
        value, conf = row.get(rank, ""), row.get(f"{rank}_confidence", "")
        if not value and not conf:
            continue
        if not value or not conf:
            return None, f"{rank} and {rank}_confidence go together: give both or neither"
        name = known[rank].get(value.lower())
        if name is None:
            return None, f"{rank} '{value}' is not in the species list"
        number, error = parse_confidence(conf, f"{rank}_confidence")
        if error:
            return None, error
        out[rank] = {"value": name, "confidence": number}
    return out, None


def rank_tips(prediction):
    """
    What the model thinks at each rank, best guess and confidence: {rank: {"value", "confidence", "source"}}.
    "model" when the upload gave that rank's confidence; otherwise "species", added up from the species and its
    runners-up (a lower bound when the runners-up were cut short). Meant for a future game unlock that shows players
    the AI's opinion, and for curators.
    """
    taxa = {t.valid_species_id: t for t in Taxon.objects.filter(
        valid_species_id__in=[prediction.valid_species_id] + [c["valid_species_id"] for c in prediction.top_k or []])}
    candidates = [(prediction.valid_species_id, prediction.confidence)] + [
        (c["valid_species_id"], c["confidence"]) for c in prediction.top_k or []]
    tips = {}
    for rank in RANKS:
        given = (prediction.rank_confidence or {}).get(rank)
        if given:
            tips[rank] = {"value": given["value"], "confidence": given["confidence"], "source": "model"}
            continue
        totals = {}
        for species_id, conf in candidates:
            taxon = taxa.get(species_id)
            if taxon is None:
                continue
            value = f"{taxon.genus} {taxon.species}" if rank == "species" else getattr(taxon, rank, "")
            if value:
                totals[value] = totals.get(value, 0.0) + conf
        if totals:
            value, conf = max(totals.items(), key=lambda kv: kv[1])
            tips[rank] = {"value": value, "confidence": round(min(conf, 1.0), 4), "source": "species"}
    return tips


def read_rows(source):
    """Rows of a CSV as dicts of stripped strings, with header names lower-cased and aliases mapped."""
    text = source.read() if hasattr(source, "read") else source
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if reader.fieldnames is None:
        return [], []
    names = [COLUMN_ALIASES.get(n.strip().lower(), n.strip().lower()) for n in reader.fieldnames]
    rows = []
    for raw in reader:
        values = [v if isinstance(v, str) else "" for v in raw.values()]
        rows.append(dict(zip(names, (v.strip() for v in values))))
    return names, rows


def _chunks(items, size=CHUNK):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def import_predictions(source, user=None, default_model="", default_version="", dry_run=False, progress=None):
    """
    Validate a predictions CSV and, unless dry_run, save it. Returns an ImportResult.

    ``source`` is a file object, bytes or text. ``default_model`` / ``default_version`` fill
    rows whose model_name / model_version cell is empty. ``progress(phase, fraction)``, if given,
    hears how far along it is (checking is the first 70%, saving the rest).
    """
    result = ImportResult(dry_run=dry_run)
    report = progress or (lambda phase, fraction: None)

    def problem(row_num, message):
        result.error_count += 1
        if len(result.errors) < MAX_ERRORS_SHOWN:
            result.errors.append(f"Row {row_num}: {message}." if row_num else f"{message}.")

    columns, rows = read_rows(source)
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    has_boxes = "image_id" in columns and all(c in columns for c in BOX_COLUMNS)
    if "record_id" not in columns and not has_boxes:
        missing.insert(0, "record_id (or image_id with bbox_x, bbox_y, bbox_width, bbox_height)")
    if missing:
        problem(0, f"missing column(s): {', '.join(missing)}. The columns are record_id, valid_species_id, "
                   "confidence, model_name (or a default for the whole file), model_version and top_k")
        return result
    if not rows:
        problem(0, "the file has no rows")
        return result
    result.rows = len(rows)

    species_ids = {sid: pk for sid, pk in Taxon.objects.values_list("valid_species_id", "id")}
    known = {rank: {} for rank in UPPER_RANKS}
    if any(rank in columns for rank in UPPER_RANKS):
        for values in Taxon.objects.values_list(*UPPER_RANKS):
            for rank, value in zip(UPPER_RANKS, values):
                if value:
                    known[rank].setdefault(value.lower(), value)
    wanted = {}
    for i, row in enumerate(rows):
        try:
            wanted[i] = uuid.UUID(row.get("record_id", ""))
        except ValueError:
            wanted[i] = None
    found = set()
    for chunk in _chunks({w for w in wanted.values() if w}, 5000):
        found.update(Beetles.objects.filter(id__in=chunk, is_deleted=False).values_list("id", flat=True))
    boxes = BoxPlanner([row for row in rows if not row.get("record_id")])

    now = timezone.now()
    keys, pending, best = {}, [], {}
    for i, row in enumerate(rows):
        row_num = i + 2
        before = result.error_count
        if i % PROGRESS_EVERY == 0:
            report("Checking rows", 0.7 * i / len(rows))

        roi_id, plan = wanted[i], None
        if not row.get("record_id"):
            target, error = boxes.resolve(row_num, row)
            if error:
                problem(row_num, error)
            elif isinstance(target, NewBox):
                plan = target
            else:
                roi_id = target
        elif roi_id is None:
            problem(row_num, f"record_id '{row.get('record_id', '')}' is not a valid id")
        elif roi_id not in found:
            problem(row_num, f"record_id {roi_id} is not an ROI in the gallery (or it was deleted)")

        species = normalise_species_id(row.get("valid_species_id"))
        if not species:
            problem(row_num, "valid_species_id is empty")
        elif species not in species_ids:
            problem(row_num, f"valid_species_id '{species}' is not in the species list")

        confidence, error = parse_confidence(row.get("confidence", ""))
        if error:
            problem(row_num, error)

        model_name = row.get("model_name") or default_model
        model_version = row.get("model_version") or default_version
        if not model_name:
            problem(row_num, "model_name is empty (fill the column, or give a model name for the whole file)")
        elif len(model_name) > 100 or len(model_version) > 50:
            problem(row_num, "model_name is limited to 100 characters and model_version to 50")

        top_k, error = parse_top_k(row.get("top_k"), species_ids, species)
        if error:
            problem(row_num, error)

        ranks, error = parse_rank_confidence(row, known)
        if error:
            problem(row_num, error)

        if result.error_count != before:
            continue
        target = plan or roi_id
        key = (target, model_name, model_version)
        if key in keys:
            problem(row_num, f"repeats row {keys[key]} (same record_id or box, model_name and model_version)")
            continue
        keys[key] = row_num
        prediction = ModelPrediction(
            roi_id=roi_id, valid_species_id=species, taxon_id=species_ids[species], confidence=confidence,
            top_k=top_k, rank_confidence=ranks, model_name=model_name, model_version=model_version, uploaded_by=user,
        )
        pending.append((prediction, plan))
        if target not in best or confidence > best[target][0]:
            best[target] = (confidence, model_name)

    if not result.ok:
        return result

    result.boxes_matched = boxes.matched_rows
    result.boxes_created = len(boxes.new)
    existing = 0
    on_known = [p for p, plan in pending if plan is None]
    for chunk in _chunks({p.roi_id for p in on_known}):
        stored = set(ModelPrediction.objects.filter(roi_id__in=chunk).values_list("roi_id", "model_name", "model_version"))
        existing += sum(1 for p in on_known if (p.roi_id, p.model_name, p.model_version) in stored)
    result.updated = existing
    result.created = len(pending) - existing
    if dry_run:
        return result

    report("Saving", 0.7)
    with transaction.atomic():
        boxes.create(user, now)
        for prediction, plan in pending:
            if plan is not None:
                prediction.roi_id = plan.roi_id
        best = {(t.roi_id if isinstance(t, NewBox) else t): v for t, v in best.items()}
        pending = [prediction for prediction, _ in pending]
        for n, chunk in enumerate(_chunks(pending)):
            report("Saving", 0.7 + 0.25 * n * CHUNK / len(pending))
            ModelPrediction.objects.bulk_create(
                chunk, update_conflicts=True,
                unique_fields=["roi", "model_name", "model_version"],
                update_fields=["valid_species_id", "taxon", "confidence", "top_k", "rank_confidence", "uploaded_by",
                               "created_at"],
            )
        # The game matches images to players by difficulty: a model that is unsure is a hard image.
        difficulties = [
            RoiDifficulty(roi_id=roi_id, model_difficulty=round(1 - conf, 4), model_name=name, model_updated_at=now)
            for roi_id, (conf, name) in best.items()
        ]
        for chunk in _chunks(difficulties):
            RoiDifficulty.objects.bulk_create(
                chunk, update_conflicts=True, unique_fields=["roi"],
                update_fields=["model_difficulty", "model_name", "model_updated_at", "updated_at"],
            )
    return result



def run_upload(job_id):
    """Check and (unless it is a check only) save an uploaded predictions file, keeping the job's progress current."""
    job = PredictionUpload.objects.get(pk=job_id)
    job.status, job.phase, job.percent = PredictionUpload.Status.RUNNING, "Reading the file", 1
    job.save(update_fields=["status", "phase", "percent"])

    def progress(phase, fraction):
        percent = max(1, min(99, int(fraction * 100)))
        if (phase, percent) != (job.phase, job.percent):
            job.phase, job.percent = phase, percent
            job.save(update_fields=["phase", "percent"])

    try:
        with job.file.open("rb") as handle:
            result = import_predictions(handle.read(), user=job.uploaded_by, default_model=job.model_name,
                                        default_version=job.model_version, dry_run=job.dry_run, progress=progress)
        job.result = asdict(result) | {"ok": result.ok, "hidden_errors": result.hidden_errors}
        job.status = PredictionUpload.Status.DONE
    except Exception as error:   # shown in the dialog; the worker log has the traceback
        job.result = {"ok": False, "errors": [f"The file could not be processed: {error}"], "error_count": 1,
                      "hidden_errors": 0}
        job.status = PredictionUpload.Status.FAILED
        raise
    finally:
        job.phase, job.percent, job.finished_at = "", 100, timezone.now()
        job.save(update_fields=["status", "phase", "percent", "result", "finished_at"])
    return job

class NewBox:
    """A box from the file that is not on its image yet; roi_id is set once it is saved."""

    def __init__(self, image_id, box, row_num):
        self.image_id, self.box, self.row_num, self.roi_id = image_id, box, row_num, None


class BoxPlanner:
    """Finds, for rows without a record_id, the ROI their box belongs to, or plans a new one (see the docstring)."""

    def __init__(self, rows):
        ids = set()
        for row in rows:
            try:
                ids.add(uuid.UUID(row.get("image_id", "")))
            except ValueError:
                pass
        self.images = set()
        self.boxes = {}       # image id -> [(box, roi id)] on the site
        for chunk in _chunks(ids, 5000):
            self.images.update(ImageAsset.objects.filter(id__in=chunk).values_list("id", flat=True))
            for roi_id, image_id, *box in Beetles.objects.filter(
                    image_asset_id__in=chunk, is_deleted=False, bbox_x__isnull=False).values_list(
                    "id", "image_asset_id", "bbox_x", "bbox_y", "bbox_width", "bbox_height"):
                self.boxes.setdefault(image_id, []).append((tuple(box), roi_id))
        self.new = []         # NewBox, in file order
        self.matched_rows = 0

    def resolve(self, row_num, row):
        """(roi id or NewBox, None), or (None, what is wrong)."""
        try:
            image_id = uuid.UUID(row.get("image_id", ""))
        except ValueError:
            if is_blank(row.get("image_id")):
                return None, "give a record_id, or an image_id with the box (bbox_x, bbox_y, bbox_width, bbox_height)"
            return None, f"image_id '{row.get('image_id')}' is not a valid id"
        if image_id not in self.images:
            return None, f"image_id {image_id} is not an image in the gallery"
        box, error = parse_box(*(row.get(c) for c in BOX_COLUMNS))
        if error:
            return None, error
        if box is None:
            return None, "a row without a record_id needs the box (bbox_x, bbox_y, bbox_width, bbox_height)"
        on_site = [(iou(box, other), roi_id) for other, roi_id in self.boxes.get(image_id, [])]
        overlap, roi_id = max(on_site, default=(0, None))
        if overlap >= SAME_BOX_IOU:
            self.matched_rows += 1
            return roi_id, None
        for planned in self.new:
            if planned.image_id == image_id and iou(box, planned.box) >= SAME_BOX_IOU:
                return planned, None   # the same box as an earlier row (another model, say)
        planned = NewBox(image_id, box, row_num)
        self.new.append(planned)
        return planned, None

    def create(self, user, now):
        """Save the planned boxes: the image's box-less ROI takes the first, the rest are new ROIs."""
        by_image = {}
        for planned in self.new:
            by_image.setdefault(planned.image_id, []).append(planned)
        for image_id, planned_boxes in by_image.items():
            asset = ImageAsset.objects.get(id=image_id)
            template = roi_defaults.template_roi(asset)
            inherited = roi_defaults.inherited_fields(roi_defaults.latest_roi(asset), species=False)
            for planned in planned_boxes:
                audit = dict(zip(BOX_COLUMNS, planned.box), bbox_is_validated=False, bbox_created_by=user,
                             bbox_created_at=now, last_updated_by=user)
                if template is not None:
                    roi, template = template, None
                    for name, value in audit.items():
                        setattr(roi, name, value)
                    roi.save()
                else:
                    roi = Beetles.objects.create(image_asset=asset, **inherited, **audit)
                planned.roi_id = roi.id


MAX_MODELS_SHOWN = 3


def suggestions_for(rois):
    """
    What the models said about these ROIs, for curators and viewers: {roi_id: [suggestion, ...]}, newest model
    first. Each suggestion has the model and version, one line per rank (subfamily, tribe, genus, species) with the
    model's name and confidence and whether the ROI's current label agrees, and the species runners-up. Its
    ``top_species`` is the species the model ranks first, if that is in the species list: what a curator can accept
    on the annotation page (None otherwise).
    """
    rois = list(rois)
    preds = list(ModelPrediction.objects.filter(roi_id__in=[r.id for r in rois]).order_by("-created_at"))
    if not preds:
        return {}
    ids = {p.valid_species_id for p in preds} | {c["valid_species_id"] for p in preds for c in p.top_k or []}
    taxa = {t.valid_species_id: t for t in Taxon.objects.filter(valid_species_id__in=ids)}
    labels = {r.id: r.taxon for r in rois}

    def species_name(species_id):
        t = taxa.get(species_id)
        return f"{t.genus} {t.species}" if t and t.genus and t.species else (t.scientific_name if t else species_id)

    out = {}
    for p in preds:
        shown = out.setdefault(p.roi_id, [])
        if len(shown) >= MAX_MODELS_SHOWN:
            continue
        label = labels.get(p.roi_id)
        given = p.rank_confidence or {}
        levels = []
        tips = rank_tips(p) if any(not given.get(rank) for rank in UPPER_RANKS) else {}
        for rank in UPPER_RANKS:
            if given.get(rank):
                value, conf, source = given[rank]["value"], given[rank]["confidence"], "model"
            else:   # the model gave no number for this rank: add up its species candidates (a lower bound)
                tip = tips.get(rank)
                if not tip:
                    continue
                value, conf, source = tip["value"], tip["confidence"], "species"
            current = getattr(label, rank, "") if label else ""
            levels.append({"rank": rank, "value": value, "confidence": conf, "source": source,
                           "agrees": (current.lower() == value.lower()) if current else None})
        levels.append({"rank": "species", "value": species_name(p.valid_species_id), "confidence": p.confidence,
                       "source": "model",
                       "agrees": (label.valid_species_id == p.valid_species_id) if label else None})
        shown.append({
            "model_name": p.model_name,
            "model_version": p.model_version,
            "created_at": p.created_at,
            "levels": levels,
            "runners_up": [{"value": species_name(c["valid_species_id"]), "confidence": c["confidence"]}
                           for c in (p.top_k or [])[:3]],
            "top_species": {"valid_species_id": p.valid_species_id, "name": species_name(p.valid_species_id),
                            "confidence": p.confidence} if p.valid_species_id in taxa else None,
        })
    return out
