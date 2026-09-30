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

The whole file is checked before anything is written; if any row is wrong nothing is saved and
every problem is reported with its row number. Uploading the same model version again replaces
its predictions. Predictions never change an ROI's label.
"""
import csv
import io
import json
import uuid
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from .models import Beetles, ModelPrediction, RoiDifficulty, Taxon

COLUMN_ALIASES = {
    "predicted_valid_species_id": "valid_species_id",
    "species_id": "valid_species_id",
    "roi_id": "record_id",
    "model": "model_name",
    "version": "model_version",
}
REQUIRED_COLUMNS = ("record_id", "valid_species_id", "confidence")
MAX_ERRORS_SHOWN = 30
CHUNK = 2000


@dataclass
class ImportResult:
    rows: int = 0
    created: int = 0
    updated: int = 0
    errors: list = field(default_factory=list)
    error_count: int = 0
    dry_run: bool = False

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


def import_predictions(source, user=None, default_model="", default_version="", dry_run=False):
    """
    Validate a predictions CSV and, unless dry_run, save it. Returns an ImportResult.

    ``source`` is a file object, bytes or text. ``default_model`` / ``default_version`` fill
    rows whose model_name / model_version cell is empty.
    """
    result = ImportResult(dry_run=dry_run)

    def problem(row_num, message):
        result.error_count += 1
        if len(result.errors) < MAX_ERRORS_SHOWN:
            result.errors.append(f"Row {row_num}: {message}." if row_num else f"{message}.")

    columns, rows = read_rows(source)
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        problem(0, f"missing column(s): {', '.join(missing)}. The columns are record_id, valid_species_id, "
                   "confidence, model_name (or a default for the whole file), model_version and top_k")
        return result
    if not rows:
        problem(0, "the file has no rows")
        return result
    result.rows = len(rows)

    species_ids = {sid: pk for sid, pk in Taxon.objects.values_list("valid_species_id", "id")}
    wanted = {}
    for i, row in enumerate(rows):
        try:
            wanted[i] = uuid.UUID(row.get("record_id", ""))
        except ValueError:
            wanted[i] = None
    found = set()
    for chunk in _chunks({w for w in wanted.values() if w}, 5000):
        found.update(Beetles.objects.filter(id__in=chunk, is_deleted=False).values_list("id", flat=True))

    now = timezone.now()
    keys, pending, best = {}, [], {}
    for i, row in enumerate(rows):
        row_num = i + 2
        before = result.error_count

        roi_id = wanted[i]
        if roi_id is None:
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

        if result.error_count != before:
            continue
        key = (roi_id, model_name, model_version)
        if key in keys:
            problem(row_num, f"repeats row {keys[key]} (same record_id, model_name and model_version)")
            continue
        keys[key] = row_num
        pending.append(ModelPrediction(
            roi_id=roi_id, valid_species_id=species, taxon_id=species_ids[species], confidence=confidence,
            top_k=top_k, model_name=model_name, model_version=model_version, uploaded_by=user,
        ))
        if roi_id not in best or confidence > best[roi_id][0]:
            best[roi_id] = (confidence, model_name)

    if not result.ok:
        return result

    existing = 0
    for chunk in _chunks({p.roi_id for p in pending}):
        stored = set(ModelPrediction.objects.filter(roi_id__in=chunk).values_list("roi_id", "model_name", "model_version"))
        existing += sum(1 for p in pending if (p.roi_id, p.model_name, p.model_version) in stored)
    result.updated = existing
    result.created = len(pending) - existing
    if dry_run:
        return result

    with transaction.atomic():
        for chunk in _chunks(pending):
            ModelPrediction.objects.bulk_create(
                chunk, update_conflicts=True,
                unique_fields=["roi", "model_name", "model_version"],
                update_fields=["valid_species_id", "taxon", "confidence", "top_k", "uploaded_by", "created_at"],
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


MAX_ALTERNATIVES_SHOWN = 3


def suggestions_for(roi_ids):
    """
    Model suggestions for some ROIs, as ``{roi_id: [suggestion, ...]}`` (ROIs without any are left out).

    Each ROI gets one suggestion per model, from that model's most recent upload, best first.
    Runs a fixed number of queries however many ROIs are asked for. A suggestion is a dict:
    model_name, model_version, valid_species_id, scientific_name, confidence,
    alternatives (up to three of {valid_species_id, scientific_name, confidence}).
    """
    roi_ids = list(roi_ids)
    if not roi_ids:
        return {}
    rows = []
    for chunk in _chunks(roi_ids, 5000):
        rows.extend(ModelPrediction.objects.filter(roi_id__in=chunk).select_related("taxon").order_by("-created_at", "-confidence"))

    latest = {}  # (roi, model) -> newest upload; rows are newest first
    for row in rows:
        latest.setdefault((row.roi_id, row.model_name), row)

    alternative_ids = {a["valid_species_id"] for row in latest.values() for a in row.top_k[:MAX_ALTERNATIVES_SHOWN]}
    names = dict(Taxon.objects.filter(valid_species_id__in=alternative_ids).values_list("valid_species_id", "scientific_name"))

    out = {}
    for row in latest.values():
        out.setdefault(row.roi_id, []).append({
            "model_name": row.model_name,
            "model_version": row.model_version,
            "valid_species_id": row.valid_species_id,
            "scientific_name": (row.taxon.scientific_name if row.taxon else "") or row.valid_species_id,
            "confidence": row.confidence,
            "alternatives": [
                {"valid_species_id": a["valid_species_id"],
                 "scientific_name": names.get(a["valid_species_id"]) or a["valid_species_id"],
                 "confidence": a["confidence"]}
                for a in row.top_k[:MAX_ALTERNATIVES_SHOWN]
            ],
        })
    for suggestions in out.values():
        suggestions.sort(key=lambda s: -s["confidence"])
    return out
