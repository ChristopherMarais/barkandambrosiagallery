"""
The taxonomy behind the game's rank pickers, read from the species list exactly as stored (the Taxon table, loaded
from the ground-truth valid_species CSV), column by column (issue #420). No guessing from name endings.

The one thing the list does on purpose that the pickers must not copy: placeholder rows "X sp. undetermined",
which mean "identified only to X". Their species column is "sp. undetermined", and when X is a subfamily, tribe or
subtribe that name also sits in the genus column (genus = "Ipini"). So a species placeholder is never offered as a
species, and a genus value that is the row's own subfamily, tribe or subtribe (or is used at a higher rank anywhere
in the list) is never offered as a genus. "Not sure" in the pickers covers these. Subtribes are not part of the game.
Each genus and tribe hangs under the parents most rows give it (in the 2026 list every genus has a single tribe), and
a subfamily is offered only when some row files a tribe or genus under it, so a row shifted out of its columns (an
epithet alone in the subfamily column) offers nothing.
"""
from collections import Counter, defaultdict

from django.core.cache import cache

from .models import Taxon

CACHE_KEY = "game_taxa:tree:v2"
CACHE_SECONDS = 600
RANKS = ("subfamily", "tribe", "subtribe", "genus", "species")


def _clean(value):
    return (value or "").strip()


def is_placeholder_species(epithet):
    """The list's "sp. undetermined" (identified only to the rank above): not a species name."""
    return _clean(epithet).lower().startswith("sp.")


def higher_names(rows):
    """Every name the list uses as a subfamily, tribe or subtribe."""
    return {n for subfamily, tribe, subtribe, _g, _s in rows for n in (subfamily, tribe, subtribe) if n}


def is_placeholder_genus(row, higher=None):
    """A genus column holding a higher rank's name (a placeholder row's "Ipini sp. undetermined")."""
    subfamily, tribe, subtribe, genus, _species = row
    return bool(genus) and (genus in (subfamily, tribe, subtribe) or (higher is not None and genus in higher))


def _majority(counter):
    """The most common value; ties go to the alphabetically first so the answer is stable."""
    return min(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def _rows():
    return Taxon.objects.values_list("subfamily", "tribe", "subtribe", "genus", "species")


def build_tree(rows=None):
    """
    {"subfamilies": [...], "tribes": {tribe: subfamily}, "genera": {genus: (subfamily, tribe)},
     "species": {genus: [epithets]}}, from the list's columns as they are, without its placeholder rows' names.
    """
    rows = [tuple(_clean(v) for v in row) for row in (_rows() if rows is None else rows)]
    higher = higher_names(rows)
    subtribes = {r[2] for r in rows if r[2]}   # a name the list uses as a subtribe is not offered as a tribe

    tribe_parent = defaultdict(Counter)
    genus_parent = defaultdict(Counter)
    species = defaultdict(set)
    subfamilies = set()
    for row in rows:
        subfamily, tribe, _subtribe, genus, epithet = row
        if not subfamily:
            continue
        if tribe in subtribes:
            tribe = ""
        if tribe:
            tribe_parent[tribe][subfamily] += 1
            subfamilies.add(subfamily)
        if not genus or is_placeholder_genus(row, higher):
            continue
        subfamilies.add(subfamily)   # only a subfamily something sits under is offered (not a shifted row's value)
        genus_parent[genus][(subfamily, tribe)] += 1
        if epithet and not is_placeholder_species(epithet):
            species[genus].add(epithet)

    tribes = {t: _majority(c) for t, c in tribe_parent.items()}
    genera = {}
    for genus, counter in genus_parent.items():
        # A row without a tribe says nothing about the tribe; only fall back to it when no row has one.
        with_tribe = Counter({k: v for k, v in counter.items() if k[1]})
        subfamily, tribe = _majority(with_tribe or counter)
        if tribe and tribes.get(tribe) != subfamily:
            tribe = ""  # the tribe itself belongs elsewhere; don't send the genus there
        genera[genus] = (subfamily, tribe)
    return {
        "subfamilies": sorted(subfamilies),
        "tribes": tribes,
        "genera": genera,
        "species": {g: sorted(s) for g, s in species.items()},
    }


def tree():
    data = cache.get(CACHE_KEY)
    if data is None:
        data = build_tree()
        cache.set(CACHE_KEY, data, CACHE_SECONDS)
    return data


def options(rank, parents):
    """The picker options for one rank, narrowed by the parents already chosen."""
    data = tree()
    want = {r: (v or "").strip().lower() for r, v in parents.items() if v}
    if rank == "subfamily":
        return [{"value": s} for s in data["subfamilies"]]
    if rank == "tribe":
        return [{"value": t} for t, sf in sorted(data["tribes"].items())
                if not want.get("subfamily") or sf.lower() == want["subfamily"]]
    if rank == "genus":
        out = []
        for genus, (subfamily, tribe) in sorted(data["genera"].items()):
            if want.get("subfamily") and subfamily.lower() != want["subfamily"]:
                continue
            if want.get("tribe") and tribe.lower() != want["tribe"]:
                continue
            out.append({"value": genus, "subfamily": subfamily, "tribe": tribe})
        return out
    if rank == "species":
        genus = next((g for g in data["species"] if g.lower() == want.get("genus")), None)
        return [{"value": s} for s in data["species"].get(genus, [])]
    return []


def known(answer):
    """
    Whether a classify answer only uses names the pickers offer, and they fit together:
    the tribe in the subfamily, the genus under both, the species in the genus.
    """
    data = tree()
    pick = {r: (answer.get(r) or "").lower() for r in ("subfamily", "tribe", "genus", "species")}
    subfamilies = {s.lower() for s in data["subfamilies"]}
    tribes = {t.lower(): sf.lower() for t, sf in data["tribes"].items()}
    genera = {g.lower(): (sf.lower(), t.lower()) for g, (sf, t) in data["genera"].items()}
    if pick["subfamily"] and pick["subfamily"] not in subfamilies:
        return False
    if pick["tribe"]:
        if pick["tribe"] not in tribes or pick["subfamily"] not in ("", tribes[pick["tribe"]]):
            return False
    if pick["genus"]:
        if pick["genus"] not in genera:
            return False
        subfamily, tribe = genera[pick["genus"]]
        if pick["subfamily"] not in ("", subfamily) or pick["tribe"] not in ("", tribe):
            return False
    if pick["species"]:
        epithets = next((e for g, e in data["species"].items() if g.lower() == pick["genus"]), [])
        return pick["species"] in {e.lower() for e in epithets}
    return True


def audit(rows=None):
    """
    Things in the species list the pickers have to work around, for curators to check: names used at more than one
    rank other than by placeholder rows, genera under more than one tribe, tribes under more than one subfamily,
    and rows missing a rank above one they have.
    """
    rows = [tuple(_clean(v) for v in row) for row in (_rows() if rows is None else rows)]
    higher = higher_names(rows)
    by_rank = defaultdict(set)
    genus_parents, tribe_parents = defaultdict(set), defaultdict(set)
    gaps = []
    for row in rows:
        subfamily, tribe, subtribe, genus, epithet = row
        placeholder = is_placeholder_genus(row, higher)
        for rank, name in zip(RANKS[:4], row[:4]):
            if name and not (rank == "genus" and placeholder):
                by_rank[name].add(rank)
        if genus and not placeholder:
            genus_parents[genus].add((subfamily, tribe))
        if tribe:
            tribe_parents[tribe].add(subfamily)
        if (tribe or genus) and not subfamily or epithet and not genus:
            gaps.append(" / ".join(v or "-" for v in row))
    return {
        "names_at_several_ranks": sorted((n, sorted(r)) for n, r in by_rank.items() if len(r) > 1),
        "genera_with_several_parents": sorted((g, sorted(p)) for g, p in genus_parents.items() if len(p) > 1),
        "tribes_in_several_subfamilies": sorted((t, sorted(s)) for t, s in tribe_parents.items() if len(s) > 1),
        "rows_missing_a_rank": sorted(gaps),
    }


CSV_COLUMNS = {"subfamily": "subfamily", "tribe": "tribe", "subtribe": "subtribe", "genus": "genus", "species": "species",
               "subspecies": "subspecies", "scientific_name": "scientificName"}


def compare_with_csv(path):
    """
    Differences between the Taxon table and a ground-truth valid_species CSV, by valid_species_id: ids only in one
    of them, and ids whose names differ (field: database value -> CSV value).
    """
    import csv

    with open(path, newline="", encoding="utf-8-sig") as f:
        truth = {_clean(r["valid_species_id"]): r for r in csv.DictReader(f) if _clean(r.get("valid_species_id"))}
    stored = {t.valid_species_id: t for t in Taxon.objects.all()}
    changed = []
    for vid in sorted(set(truth) & set(stored)):
        diffs = [f"{field}: {_clean(getattr(stored[vid], field))!r} -> {_clean(truth[vid].get(col))!r}"
                 for field, col in CSV_COLUMNS.items()
                 if col in truth[vid] and _clean(getattr(stored[vid], field)) != _clean(truth[vid].get(col))]
        if diffs:
            changed.append((vid, diffs))
    return {
        "only_in_csv": sorted(set(truth) - set(stored)),
        "only_in_database": sorted(set(stored) - set(truth)),
        "different": changed,
    }
