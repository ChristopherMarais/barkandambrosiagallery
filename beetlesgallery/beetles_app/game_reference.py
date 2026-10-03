"""
Reference names for beetles nobody has validated yet (#395), so answers on them can earn points straight away.

For each rank of an unvalidated beetle, the reference is:
  * expert   what proven experts for that part of the tree said (game_trust), when every expert who named that
             rank agrees; or else
  * model    the classifier's name for that rank (ModelPrediction), but only when it is sure of this beetle
             (GAME_REF_MODEL_MIN_CONFIDENCE, 0.9) *and* it has been right about that very taxon at that rank on
             validated beetles (GAME_REF_MODEL_MIN_PRECISION, 95%, over at least GAME_REF_MODEL_MIN_CHECKED, 20,
             of its sure calls). A model that is good at Xyleborus but poor at Xylosandrus is only used for the
             first.

A reference is not the truth: an answer that matches it earns that rank's points up to GAME_POINTS_REFERENCE_CAP
(60%, like agreement), an answer that doesn't simply earns nothing from it, and it never counts towards accuracy
or expertise. Points move with it: when a beetle is validated, the real name takes over.
"""
from collections import defaultdict

from django.core.cache import cache

from .game import RANKS, game_setting
from .models import ModelPrediction

PRECISION_CACHE = "game:model-precision:v1"


def _model_ranks(prediction, taxa):
    """{rank: (name, confidence)} for one prediction: the per-rank numbers it came with, and the species."""
    out = {r: (v["value"], v["confidence"]) for r, v in (prediction.rank_confidence or {}).items() if r in RANKS[:3]}
    taxon = taxa.get(prediction.valid_species_id)
    if taxon is not None and taxon.genus and taxon.species:
        out["species"] = (f"{taxon.genus} {taxon.species}", prediction.confidence)
    return out


def _truth(taxon, rank):
    if taxon is None:
        return ""
    if rank == "species":
        return f"{taxon.genus} {taxon.species}" if taxon.genus and taxon.species else ""
    return getattr(taxon, rank, "") or ""


def model_precision():
    """
    {(model_name, rank, name in lower case): (right, checked)} over validated beetles, counting only the calls the
    model was sure of. Kept for an hour.
    """
    table = cache.get(PRECISION_CACHE)
    if table is not None:
        return table
    sure = game_setting("GAME_REF_MODEL_MIN_CONFIDENCE", 0.9)
    tally = defaultdict(lambda: [0, 0])
    preds = (ModelPrediction.objects.filter(roi__bbox_is_validated=True, roi__is_deleted=False, roi__taxon__isnull=False)
             .select_related("roi__taxon", "taxon"))
    for p in preds:
        taxa = {p.valid_species_id: p.taxon} if p.taxon else {}
        for rank, (name, conf) in _model_ranks(p, taxa).items():
            if conf < sure:
                continue
            key = (p.model_name, rank, name.lower())
            tally[key][1] += 1
            tally[key][0] += int(name.lower() == _truth(p.roi.taxon, rank).lower())
    table = {k: tuple(v) for k, v in tally.items()}
    cache.set(PRECISION_CACHE, table, 3600)
    return table


def _model_reference(roi_ids):
    """{roi_id: {rank: name}} where the newest prediction is sure and that model has earned trust for that name."""
    sure = game_setting("GAME_REF_MODEL_MIN_CONFIDENCE", 0.9)
    min_precision = game_setting("GAME_REF_MODEL_MIN_PRECISION", 0.95)
    min_checked = game_setting("GAME_REF_MODEL_MIN_CHECKED", 20)
    latest = {}
    for p in ModelPrediction.objects.filter(roi_id__in=list(roi_ids)).select_related("taxon").order_by("created_at"):
        latest[p.roi_id] = p
    if not latest:
        return {}
    table = model_precision()
    out = {}
    for roi_id, p in latest.items():
        taxa = {p.valid_species_id: p.taxon} if p.taxon else {}
        for rank, (name, conf) in _model_ranks(p, taxa).items():
            right, checked = table.get((p.model_name, rank, name.lower()), (0, 0))
            if conf >= sure and checked >= min_checked and right / checked >= min_precision:
                out.setdefault(roi_id, {})[rank] = name
    return out


def _expert_reference(votes, trust):
    """{rank: name} where every proven expert who named the rank agrees."""
    seen = defaultdict(set)
    for judge_id, labels in votes:
        for rank, value in labels.items():
            if trust.trusted_through(judge_id, rank, labels):
                seen[rank].add(value.strip())
    return {rank: next(iter(v)) for rank, v in seen.items() if len({x.lower() for x in v}) == 1}


def model_references(roi_ids):
    """{roi_id: {rank: name}} the model can be trusted on, for these unvalidated beetles (worked out once per batch)."""
    return _model_reference(roi_ids)


def reference_for(answer, votes, judges, model_refs):
    """
    {rank: (name, "expert" | "model")} for the beetle of this answer. Experts outrank the model, and a player's own
    answer is never their own reference.
    """
    out = {rank: (name, "model") for rank, name in (model_refs.get(answer.roi_id) or {}).items()}
    trust = getattr(judges, "trust", None)
    if trust is not None:
        others = [(pid, labels) for pid, labels in votes if pid != answer.player_id]
        for rank, name in _expert_reference(others, trust).items():
            out[rank] = (name, "expert")
    return out


def reference_points(claims, reference, weight_of):
    """{rank: points} for the claimed ranks that match the reference, before the Identification weight."""
    cap = game_setting("GAME_POINTS_REFERENCE_CAP", 0.6)
    return {rank: cap * weight_of[rank] for rank, value in claims.items()
            if rank in reference and reference[rank][0].strip().lower() == value.strip().lower()}
