"""
The IBBI models the site offers, and how their output becomes the classifier service's answer.

Shared by the Modal service (modal_ibbi_api.py, which ships this file with it) and the site (the classifier page,
"Classify with AI" on the annotation page), so the model list lives in one place. Plain Python, no dependencies.

Two kinds of model (ibbi 0.3.1):

* "pipeline": the arthropod detector finds every specimen, then a hierarchical classifier names it rank by rank
  (subfamily, tribe, genus, species) with a probability at each rank, and says how deep it is sure
  ("Xyleborus sp. (species undetermined)"). Recommended.
* "detector": a one-step detector that names one of the 65 trained species directly, with one confidence.

The answer, per image:
    {"status": "success", "model_used": key, "model_name": "...", "detections": [...],
     "class_names": [...]}                        # class_names + each detection's probs: for older pages
and per detection:
    {"box": [x1, y1, x2, y2] (pixels), "score": detector confidence,
     "label": what to show ("Xyleborus affinis", "Xyleborus sp. (species undetermined)"),
     "species": the best species name, "confidence": the probability behind the label,
     "depth": 0-4 (how many ranks it is sure of; 4 = species),
     "levels": {"subfamily": {"taxon", "prob", "known", "top3": [{"name", "prob"}]}, ...} or None,
     "candidates": [{"name", "prob"}] (species, best first), "probs": [...] (aligned with class_names)}
"""

IBBI_VERSION = "0.3.1"
LEVELS = ("subfamily", "tribe", "genus", "species")

# key -> what the site shows and which ibbi models it runs. Order = the order of the options.
MODELS = {
    "ibbi_dinov3": {
        "label": "Detector + DINOv3 classifier (recommended)", "kind": "pipeline",
        "detector": "yolo11x_arthropod_detector", "classifier": "dinov3_hierarchical_classifier",
    },
    "ibbi_bioclip2": {
        "label": "Detector + BioCLIP 2 classifier", "kind": "pipeline",
        "detector": "yolo11x_arthropod_detector", "classifier": "bioclip2_hierarchical_classifier",
    },
    "rtdetrx": {"label": "RT-DETR species detector", "kind": "detector", "detector": "rtdetrx_species_detector"},
    "yolo12x": {"label": "YOLO12 species detector", "kind": "detector", "detector": "yolo12x_species_detector"},
    "yolo11x": {"label": "YOLO11 species detector", "kind": "detector", "detector": "yolo11x_species_detector"},
    "yolov10x": {"label": "YOLOv10 species detector", "kind": "detector", "detector": "yolov10x_species_detector"},
    "yolov9e": {"label": "YOLOv9 species detector", "kind": "detector", "detector": "yolov9e_species_detector"},
    "yolov8x": {"label": "YOLOv8 species detector", "kind": "detector", "detector": "yolov8x_species_detector"},
}
DEFAULT = "ibbi_dinov3"
# The names used before ibbi 0.3, still accepted (saved links, API clients)
LEGACY = {"rtdetr": "rtdetrx", "yolov12": "yolo12x", "yolov11": "yolo11x", "yolov10": "yolov10x",
          "yolov9": "yolov9e", "yolov8": "yolov8x"}


def resolve(key):
    """The model key for a key or an old name, or None."""
    key = (key or "").strip()
    key = LEGACY.get(key, key)
    return key if key in MODELS else None


def model_name(key):
    spec = MODELS[key]
    return "+".join(n for n in (spec["detector"], spec.get("classifier")) if n)


def all_ibbi_models():
    """Every ibbi model name the service may load (for pre-downloading the weights)."""
    names = []
    for spec in MODELS.values():
        for name in (spec["detector"], spec.get("classifier")):
            if name and name not in names:
                names.append(name)
    return names


def clean(name):
    return " ".join(str(name or "").replace("_", " ").split())


def _prob(value):
    try:
        return round(min(1.0, max(0.0, float(value))), 4)
    except (TypeError, ValueError):
        return 0.0


def from_detector(raw):
    """A species detector's predict() result ({"boxes", "scores", "labels"}) as detections."""
    out = []
    for box, score, label in zip(raw.get("boxes") or [], raw.get("scores") or [], raw.get("labels") or []):
        name, p = clean(label), _prob(score)
        out.append({"box": [float(v) for v in box], "score": p, "label": name, "species": name, "confidence": p,
                    "depth": 4, "levels": None, "candidates": [{"name": name, "prob": p}]})
    return out


def from_pipeline(raw):
    """The identification pipeline's predict() result ({"boxes", "det_scores", "classifications"}) as detections."""
    out = []
    for box, score, rec in zip(raw.get("boxes") or [], raw.get("det_scores") or [], raw.get("classifications") or []):
        levels = {}
        for level in LEVELS:
            r = rec.get(level) or {}
            levels[level] = {
                "taxon": clean(r.get("taxon")), "prob": _prob(r.get("prob")), "known": bool(r.get("known")),
                "top3": [{"name": clean(n), "prob": _prob(p)} for n, p in (r.get("top3") or [])],
            }
        depth = int(rec.get("depth") or 0)
        # the probability behind what is reported: the deepest rank it is sure of (the species when it is unsure)
        sure = LEVELS[depth - 1] if depth else "species"
        out.append({
            "box": [float(v) for v in box], "score": _prob(score), "label": clean(rec.get("reported")),
            "species": levels["species"]["taxon"], "confidence": levels[sure]["prob"], "depth": depth,
            "levels": levels, "candidates": levels["species"]["top3"],
        })
    return out


def response(key, detections):
    """The service's answer. class_names/probs repeat the candidates for pages written before ibbi 0.3."""
    class_names = []
    for det in detections:
        for c in det["candidates"]:
            if c["name"] not in class_names:
                class_names.append(c["name"])
    for det in detections:
        by_name = {c["name"]: c["prob"] for c in det["candidates"]}
        det["probs"] = [by_name.get(name, 0.0) for name in class_names]
    return {"status": "success", "model_used": key, "model_name": model_name(key), "ibbi_version": IBBI_VERSION,
            "detections": detections, "class_names": class_names}
