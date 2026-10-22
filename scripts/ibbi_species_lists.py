"""
Rebuild the species lists of the IBBI models (beetlesgallery/data/ibbi_species/*.csv) from Hugging Face, and check
that every model the site offers knows exactly the species of its list. The IBBI-AI page shows these lists ("See
which"), so run this when a model is added or retrained.

    pixi run python scripts/ibbi_species_lists.py           # writes the CSVs, then checks
    pixi run python scripts/ibbi_species_lists.py --check   # only checks

Standard library only; it reads the models' small config files, never the weights.
"""
import csv
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "beetlesgallery" / "tools"))
import ibbi_models  # noqa: E402

HUB = "https://huggingface.co/IBBI-bio/{repo}/resolve/main/{file}"
OUT = ROOT / "beetlesgallery" / "data" / "ibbi_species"
LEVELS = ("subfamily", "tribe", "genus", "species")
# ibbi model name -> its Hugging Face repository (the names differ for the classifiers)
REPOS = {
    "dinov3_400species_hierarchical_classifier": "ibbi_dinov3l_400sp_hierarchical_classifier",
    "dinov3_hierarchical_classifier": "ibbi_dinov3l_hierarchical_classifier",
    "bioclip2_hierarchical_classifier": "ibbi_bioclip2_hierarchical_classifier",
}
# list -> the classifier whose taxonomy is the list
SOURCES = {"400_species": "dinov3_400species_hierarchical_classifier", "65_species": "dinov3_hierarchical_classifier"}


def fetch_json(model):
    repo = REPOS.get(model, f"ibbi_{model}")
    with urllib.request.urlopen(HUB.format(repo=repo, file="config.json"), timeout=60) as r:
        return json.load(r)


def classifier_rows(config):
    """(subfamily, tribe, genus, species) for every species a hierarchical classifier knows."""
    tax = config["taxonomy"]
    return sorted(tuple(tax["nodes"][lvl][p[i]] for i, lvl in enumerate(LEVELS)) for p in tax["path"])


def species_of(model):
    """The species names a model can name."""
    config = fetch_json(model)
    if "taxonomy" in config:
        return {row[-1] for row in classifier_rows(config)}
    classes = config["classes"]
    classes = classes.values() if isinstance(classes, dict) else classes
    return {" ".join(str(c).replace("_", " ").split()) for c in classes}


def read(list_id):
    with open(OUT / f"{list_id}.csv", newline="", encoding="utf-8") as fh:
        return [tuple(row[lvl] for lvl in LEVELS) for row in csv.DictReader(fh)]


def write(list_id, rows):
    with open(OUT / f"{list_id}.csv", "w", newline="", encoding="utf-8") as fh:
        out = csv.writer(fh, lineterminator="\n")
        out.writerow(LEVELS)
        out.writerows(rows)
    print(f"{list_id}.csv: {len(rows)} species")


def check():
    ok = True
    for key, spec in ibbi_models.MODELS.items():
        model = spec.get("classifier") or spec["detector"]
        listed = {row[-1] for row in read(spec["species_list"])}
        knows = species_of(model)
        if knows != listed:
            ok = False
            print(f"{key}: {model} differs from {spec['species_list']}: "
                  f"{len(knows - listed)} not in the list, {len(listed - knows)} not in the model")
        else:
            print(f"{key}: {len(knows)} species, as listed")
    return ok


if __name__ == "__main__":
    if "--check" not in sys.argv:
        for list_id, model in SOURCES.items():
            write(list_id, classifier_rows(fetch_json(model)))
    sys.exit(0 if check() else 1)
