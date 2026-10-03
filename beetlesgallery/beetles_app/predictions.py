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
UPPER_RANKS = ("subfamily", "tribe", "genus")   # optional per-rank columns, each with <rank>_confidence
RANKS = UPPER_RANKS + ("species",)
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

        ranks, error = parse_rank_confidence(row, known)
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
            top_k=top_k, rank_confidence=ranks, model_name=model_name, model_version=model_version, uploaded_by=user,
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


MAX_MODELS_SHOWN = 3


def suggestions_for(rois):
    """
    What the models said about these ROIs, for curators and viewers: {roi_id: [suggestion, ...]}, newest model
    first. Each suggestion has the model and version, one line per rank (subfamily, tribe, genus, species) with the
    model's name and confidence and whether the ROI's current label agrees, and the species runners-up.
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
        })
    return out
