"""
Negative labels: what each game answer says a beetle is *not*, written down when the answer is saved (NegativeLabel).

Per game:

* **Find Them All** (select): the beetles left untapped are not the grid's group at its rank ("Tap every Xyleborus":
  an untapped beetle is not a Xyleborus). A grid with nothing tapped says too little, and a photo the player flagged
  says nothing.
* **Odd One Out** (odd): the beetles picked as odd are not the rest's group at the grid's rank. (That the rest *are*
  the group is a positive vote, game.tap_votes.)
* **Similarity** (pair): next to a validated beetle, "same tribe" says the other is not of its genus, "same genus" not
  of its species, "different" not of its subfamily. Next to an unvalidated beetle it says nothing we can name.
* **Naming** (classify): none. The player picks from the whole taxonomy, so "not every other name" is no list worth
  storing, and a name already counts against every rival name at its rank (its share of the vote, game.consensus).

How they are used (game.consensus): a name players have ruled out loses weight. At each rank,

    support(name) = votes for it / (all votes at that rank + the weight of the players who said it is not that name)

the suggestion is the name with the most weight for it less the weight against it, and a name is *ruled out*, never
the suggestion, once at least GAME_NOT_MIN_PLAYERS players said it is not and their weight is more than the weight of
the votes for it; so is every name below it. One stray tap never hides a name. Each player counts once per name and
beetle, with their reliability at that rank times GAME_SELECT_TAP_WEIGHT (a "not" is a lighter claim than a name,
like a tap). The curators' "not in" tips (game_tips) read the same rows.
"""
from collections import defaultdict

from django.db.models.signals import post_save
from django.dispatch import receiver

from .game import PAIR_DEPTH, RANKS, odd_picks
from .models import Beetles, GameAnswer, NegativeLabel


def _pair_negative(answer):
    """(rank, value) a Similarity answer says ``roi`` is not, or None: the rank just below the deepest one shared."""
    from .game_scoring import is_truth

    if answer.mode != "pair" or answer.skipped or not answer.roi_b_id or not is_truth(answer.roi_b):
        return None
    depth = PAIR_DEPTH.get(answer.pair_answer)   # "unsure" says nothing
    if depth is None or depth >= len(RANKS) - 1:
        return None
    rank = RANKS[depth + 1]
    partner = answer.roi_b.taxon
    if rank == "species":
        value = f"{partner.genus} {partner.species}" if partner.genus and partner.species else ""
    else:
        value = (getattr(partner, rank, "") or "").strip()
    return (rank, value) if value else None


def _grid_places(answer):
    """The places of a grid answer's tiles that it says are not of the grid's group."""
    tiles = answer.tiles or []
    if answer.mode == "odd":
        return [i for i in odd_picks(answer) if isinstance(i, int) and 0 <= i < len(tiles)]
    picked = {i for i in answer.picks or [] if isinstance(i, int)}
    if not picked:   # tapped nothing: says too little about each beetle
        return []
    left_out = picked | {i for i in answer.flagged or [] if isinstance(i, int)}
    return [i for i in range(len(tiles)) if i not in left_out]


def derive(answer):
    """[(roi_id, rank, value)] this answer says are not so. Beetles that are gone are left out."""
    if answer.skipped:
        return []
    if answer.mode == "pair":
        hit = _pair_negative(answer)
        return [(answer.roi_id, *hit)] if hit else []
    if answer.mode not in ("odd", "select") or answer.grid_rank not in RANKS or not answer.grid_group:
        return []
    value = (answer.grid_group.get(answer.grid_rank) or "").strip()
    if not value:
        return []
    tiles = answer.tiles or []
    ids = {str(tiles[i]) for i in _grid_places(answer)}
    if answer.mode == "odd" and not ids and not answer.picks and answer.roi_id:   # one pick, not among the tiles
        ids = {str(answer.roi_id)}
    if not ids:
        return []
    alive = Beetles.objects.filter(id__in=ids, is_deleted=False).values_list("id", flat=True)
    return [(rid, answer.grid_rank, value) for rid in sorted(alive, key=str)]


def record(answer):
    """Write this answer's negative labels (once: running it again adds nothing). Returns how many it says."""
    rows = [NegativeLabel(answer_id=answer.pk, player_id=answer.player_id, roi_id=roi_id, mode=answer.mode,
                          rank=rank, value=value[:201])
            for roi_id, rank, value in derive(answer)]
    if rows:
        NegativeLabel.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


@receiver(post_save, sender=GameAnswer, dispatch_uid="game_negatives_new_answer")
def _new_answer(sender, instance, created, raw=False, **kwargs):
    """Every new answer, however it is saved (the game, an import, a test), writes what it says a beetle is not."""
    if created and not raw:
        record(instance)


def against(roi_ids, voters=None, open_only=True):
    """
    {roi_id: {rank: {value_lower: {"value": display name, "players": {player_id, ...}}}}}: who said each beetle is not
    each name. ``open_only`` keeps beetles nobody has validated, except Similarity answers, which name the beetle shown
    (as the "not in" tips always have). ``voters``, when given, limits it to those players.
    """
    from django.db.models import Q

    rows = NegativeLabel.objects.all()
    if roi_ids is not None:   # None: every beetle
        rows = rows.filter(roi_id__in=list(roi_ids))
    if open_only:
        rows = rows.filter(Q(mode="pair") | Q(roi__bbox_is_validated=False))
    if voters is not None:
        rows = rows.filter(player_id__in=list(voters))
    out = defaultdict(lambda: defaultdict(dict))
    for roi_id, player_id, rank, value in rows.values_list("roi_id", "player_id", "rank", "value"):
        slot = out[roi_id][rank].setdefault(value.lower(), {"value": value, "players": set()})
        slot["players"].add(player_id)
    return out
