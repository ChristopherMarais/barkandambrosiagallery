"""
What each IBBI-AI model can name, for the IBBI-AI page's "See which" tree: subfamily, tribe, genus, species.

The lists are beetlesgallery/data/ibbi_species/<list>.csv (scripts/ibbi_species_lists.py rebuilds them from the models);
each model in ibbi_models.MODELS names its list. A species that is in the gallery's species list links to it in the
Taxonomy Browser.
"""
import csv
from functools import lru_cache

from django.conf import settings

from beetlesgallery.tools import ibbi_models

DATA_DIR = settings.BASE_DIR / "beetlesgallery" / "data" / "ibbi_species"


@lru_cache(maxsize=None)
def rows(list_id):
    """(subfamily, tribe, genus, species) for every species in a list, sorted."""
    with open(DATA_DIR / f"{list_id}.csv", newline="", encoding="utf-8") as fh:
        return tuple(sorted(tuple(row[level] for level in ibbi_models.LEVELS) for row in csv.DictReader(fh)))


def counts(list_id):
    """How many subfamilies, tribes, genera and species a list holds."""
    return {level: len({row[i] for row in rows(list_id)}) for i, level in enumerate(ibbi_models.LEVELS)}


def tree(list_id, species_ids=None):
    """
    The list as a tree: [{"name": subfamily, "children": [{"name": tribe, "children": [{"name": genus,
    "children": [{"name": species, "id": valid_species_id or None}]}]}]}], each level sorted by name.
    """
    species_ids = species_ids or {}
    out, nodes = [], {}
    for subfamily, tribe, genus, species in rows(list_id):
        parent = out
        for path in ((subfamily,), (subfamily, tribe), (subfamily, tribe, genus)):
            if path not in nodes:
                nodes[path] = {"name": path[-1], "children": []}
                parent.append(nodes[path])
            parent = nodes[path]["children"]
        parent.append({"name": species, "id": species_ids.get(species)})
    return out


def _gallery_species_ids():
    """Species name -> its id in the gallery's species list."""
    from .models import Taxon

    return {name: str(pk) for name, pk in Taxon.objects.exclude(scientific_name__isnull=True)
            .values_list("scientific_name", "valid_species_id")}


def page_data():
    """What the IBBI-AI page needs: each model's label, kind and list, and each list's counts and tree."""
    ids = _gallery_species_ids()
    list_ids = list(dict.fromkeys(spec["species_list"] for spec in ibbi_models.MODELS.values()))
    return {
        "models": {key: {"label": spec["label"], "kind": spec["kind"], "list": spec["species_list"]}
                   for key, spec in ibbi_models.MODELS.items()},
        "lists": {list_id: {"counts": counts(list_id), "tree": tree(list_id, ids)} for list_id in list_ids},
    }


def summary(key):
    """The line under the model picker: "Names 400 species in 115 genera." """
    c = counts(ibbi_models.MODELS[key]["species_list"])
    return f"Names {c['species']} species in {c['genus']} genera."
