"""
The taxonomy behind the game's rank pickers, cleaned of rows that would put a name
at the wrong rank.

The species list has rows where a name sits in the wrong column (a tribe in the
genus column, a subtribe in the tribe column, an epithet in the subfamily column)
and genera filed under more than one tribe. Taken row by row, those leak into the
wrong list and make picking a genus fill in the wrong tribe. Here every name has to
look like its rank, and each genus and tribe hangs under the parents most rows give it.
Subtribes are left out of the game altogether.
"""
import re
from collections import Counter, defaultdict

from django.core.cache import cache

from .models import Taxon

CACHE_KEY = "game_taxa:tree:v1"
CACHE_SECONDS = 600

# Zoological suffixes: subfamily -inae, tribe -ini, subtribe -ina.
# Case is not checked: the list is not consistent about it, and the game compares names case-insensitively.
_SUBFAMILY = re.compile(r"^[a-z]+inae$", re.I)
_TRIBE = re.compile(r"^[a-z]+ini$", re.I)
_GENUS = re.compile(r"^[a-z]+$", re.I)
_NOT_GENUS = re.compile(r"(?:inae|ini|idae|oidea)$", re.I)
_SPECIES = re.compile(r"^[a-z][a-z-]+$", re.I)


def _clean(value):
    return (value or "").strip()


def is_subfamily(name):
    return bool(_SUBFAMILY.match(name))


def is_tribe(name):
    return bool(_TRIBE.match(name))


def is_genus(name):
    return bool(_GENUS.match(name)) and not _NOT_GENUS.search(name)


def is_species(name):
    return bool(_SPECIES.match(name))


def _majority(counter):
    """The most common value; ties go to the alphabetically first so the answer is stable."""
    return min(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def _rows():
    return Taxon.objects.values_list("subfamily", "tribe", "subtribe", "genus", "species")


def build_tree(rows=None):
    """
    {"subfamilies": [...], "tribes": {tribe: subfamily}, "genera": {genus: (subfamily, tribe)},
     "species": {genus: [epithets]}}. Names used at a higher rank anywhere are never genera.
    """
    rows = [tuple(_clean(v) for v in row) for row in (_rows() if rows is None else rows)]
    higher = set()
    for subfamily, tribe, subtribe, _genus, _species in rows:
        higher.update(n for n in (subfamily, tribe, subtribe) if n)

    tribe_parent = defaultdict(Counter)
    genus_parent = defaultdict(Counter)
    species = defaultdict(set)
    subfamilies = set()
    for subfamily, tribe, _subtribe, genus, epithet in rows:
        if not is_subfamily(subfamily):
            continue
        subfamilies.add(subfamily)
        tribe = tribe if is_tribe(tribe) else ""
        if tribe:
            tribe_parent[tribe][subfamily] += 1
        if not is_genus(genus) or genus in higher:
            continue
        genus_parent[genus][(subfamily, tribe)] += 1
        if is_species(epithet):
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
    """Problems in the species list that the pickers have to work around, for curators to fix."""
    rows = [tuple(_clean(v) for v in row) for row in (_rows() if rows is None else rows)]
    by_rank = defaultdict(set)
    for row in rows:
        for rank, name in zip(("subfamily", "tribe", "subtribe", "genus"), row[:4]):
            if name:
                by_rank[name].add(rank)
    genus_parents, tribe_parents = defaultdict(set), defaultdict(set)
    for subfamily, tribe, _subtribe, genus, _species in rows:
        if genus:
            genus_parents[genus].add((subfamily, tribe))
        if tribe:
            tribe_parents[tribe].add(subfamily)
    return {
        "names_at_several_ranks": sorted((n, sorted(r)) for n, r in by_rank.items() if len(r) > 1),
        "bad_subfamilies": sorted({r[0] for r in rows if r[0] and not is_subfamily(r[0])}),
        "bad_tribes": sorted({r[1] for r in rows if r[1] and not is_tribe(r[1])}),
        "bad_genera": sorted({r[3] for r in rows if r[3] and not is_genus(r[3])}),
        "genera_with_several_parents": sorted((g, sorted(p)) for g, p in genus_parents.items() if len(p) > 1),
        "tribes_in_several_subfamilies": sorted((t, sorted(s)) for t, s in tribe_parents.items() if len(s) > 1),
    }
