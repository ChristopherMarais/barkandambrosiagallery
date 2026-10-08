"""
IBBI-AI's calls, for the AI beetles of the grid games (game._ai_beetles), kept in memory.

A grid looks for unvalidated beetles IBBI-AI puts in its group (a sure one, an unsure one, more as the player rises).
Asked in SQL, that joined every prediction and filtered on numbers inside JSON that Postgres can't estimate, so it
collected and sorted every match before taking a dozen: about a third of a second a query on a large collection, and
three or four queries a grid. Predictions only change when they are uploaded, so each process reads them once, indexed
by rank and name, and again when they change (refresh, once per grid build); the first to read them leaves them in the
cache for the other processes. A draw then takes candidates from memory and checks just those against the beetles the
grid may use.

What counts as a call is what it always was: at each rank above species, what the model said for that rank
(rank_confidence), or, where it said nothing for it, what its species implies, with the species' confidence; at
species, its species. Any of a beetle's predictions may make the call.
"""
import random
from array import array

from django.core.cache import cache
from django.db.models import Count, Max

RANKS = ("subfamily", "tribe", "genus", "species")
CHUNK = 200   # candidates checked against the grid's pool in one query
KEY = "game:ai-calls:v1:{}:{}"   # the predictions' count and newest upload: new predictions, a new key
KEEP = 60 * 60 * 24

_calls = {"version": None, "rois": [], "by_name": {}, "by_rank": {}}


def _number(value):
    """A confidence as stored in JSON: a number (never a bool, which Postgres orders apart from numbers), else None."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def call(rank, confidence, said, names):
    """(name, confidence) of one prediction at ``rank``, as game._ai_beetles reads it, or None when it makes none."""
    if rank == "species":
        return names["species"], confidence
    if rank in said:
        at = said[rank]
        if not isinstance(at, dict) or _number(at.get("confidence")) is None:
            return None
        return str(at.get("value") or ""), _number(at.get("confidence"))
    return names[rank], confidence


def _key(rank, name):
    """A name as the calls are looked up by: in any case; a species as its genus and epithet (game.rank_q)."""
    if rank == "species":
        genus, _, species = name.partition(" ")
        return rank, f"{genus} {species}".upper()
    return rank, name.upper()


def _load():
    from .models import ModelPrediction

    rois, where, by_name, by_rank = [], {}, {}, {}
    rows = ModelPrediction.objects.values_list("roi_id", "confidence", "rank_confidence", "taxon__subfamily",
                                               "taxon__tribe", "taxon__genus", "taxon__species")
    for roi_id, confidence, said, subfamily, tribe, genus, species in rows.iterator(chunk_size=5000):
        at = where.setdefault(roi_id, len(rois))
        if at == len(rois):
            rois.append(roi_id)
        names = {"subfamily": subfamily or "", "tribe": tribe or "", "genus": genus or "",
                 "species": f"{genus} {species or ''}" if genus else ""}
        for rank in RANKS:
            made = call(rank, confidence, said if isinstance(said, dict) else {}, names)
            if made is None:
                continue
            name, sure = made
            for key, index in ((_key(rank, name), by_name), (rank, by_rank)):
                index.setdefault(key, []).append((sure, at))
    return rois, _shuffled(by_name), _shuffled(by_rank)


def _shuffled(index):
    """Each list of calls in a random order, once: a draw then reads a stretch of it from a random place."""
    out = {}
    for key, found in index.items():
        random.shuffle(found)
        out[key] = (array("d", (sure for sure, _ in found)), array("l", (at for _, at in found)))
    return out


def refresh():
    """Read the calls again if the predictions changed since (one small query). The grid builder calls it per build."""
    from .models import ModelPrediction

    found = ModelPrediction.objects.aggregate(n=Count("id"), last=Max("created_at"))
    version = (found["n"], found["last"])
    if version != _calls["version"]:
        key = KEY.format(found["n"], found["last"].timestamp() if found["last"] else 0)
        built = cache.get(key)
        if built is None:
            built = _load()
            cache.set(key, built, KEEP)
        rois, by_name, by_rank = built
        _calls.update(version=version, rois=rois, by_name=by_name, by_rank=by_rank)


def draw(pool, rank, value, low, high, n, avoid=()):
    """
    Up to ``n`` ids, in random order, of beetles in ``pool`` (a Beetles queryset, or a set of their ids already read),
    none in ``avoid``, that IBBI-AI puts at ``value`` at ``rank`` (at any name when ``value`` is None) with a confidence
    in [low, high).
    """
    found = _calls["by_name"].get(_key(rank, value)) if value else _calls["by_rank"].get(rank)
    if n <= 0 or not found:
        return []
    confidences, places = found
    rois, avoid = _calls["rois"], set(avoid)
    out, chunk, taken = [], [], set()

    def check():
        if isinstance(pool, (set, frozenset)):
            usable = pool
        else:
            usable = set(pool.filter(id__in=chunk).values_list("id", flat=True))
        out.extend(i for i in chunk if i in usable)
        chunk.clear()

    # From a random place in the list, which was shuffled once (_shuffled), as far as needed: random like a walk from a
    # random id (game._random_ids), without shuffling thousands of calls for every draw
    start = random.randrange(len(places))
    for step in range(len(places)):
        at = (start + step) % len(places)
        roi = rois[places[at]]
        if not low <= confidences[at] < high or roi in avoid or roi in taken:
            continue
        taken.add(roi)
        chunk.append(roi)
        if len(chunk) >= CHUNK:
            check()
            if len(out) >= n:
                break
    if chunk and len(out) < n:
        check()
    return out[:n]
