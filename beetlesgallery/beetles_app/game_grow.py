"""
Batches that start small and grow (#575).

A batch built while the player waits (a switch of game with nothing built ahead, or a batch end the worker hadn't got
to) used to be built whole: ten beetles, most of a second on a quiet server and far more on a busy one. Now such a
batch is made with its first FIRST beetles only, which takes a fraction of that, and grows to its full size behind the
player's back: on the worker, or, when the worker is busy, a few beetles at a time in the feed's look-ahead request
(game_views.game_upcoming) while the player is still answering. Its items are only ever added at the end, so the
places of the beetles already shown (and their answers and crops) never move.

Where game work stays in the request (GAME_RECOMPUTE_IN_BACKGROUND off, as when developing) batches are built whole,
as before.
"""
import logging
import time

from django.core.cache import cache
from django.db import transaction

logger = logging.getLogger(__name__)

FIRST = 2                      # the beetles a batch built while the player waits starts with (GAME_FIRST_ITEMS)
STEP = 3                       # the beetles one look-ahead request adds when the worker hasn't grown the batch yet
SPEC = "game:grow:{}"          # round: {"size": its full size, "fresh_only": as built, "misses": empty tries}
LOCK = "game:growing:{}"       # round: one grower at a time, so two never add the same beetles
KEEP = 60 * 60 * 24
LOCK_SECONDS = 120
MISSES = 3                     # tries that add nothing before a batch stops growing
GROW_WAIT = 5                  # seconds an answer waits for another grower before the batch is taken as ended


def _background():
    from . import game

    return game.game_setting("GAME_RECOMPUTE_IN_BACKGROUND", False)


def start_round(player, mode, fresh_only=False):
    """
    A new batch for a player who is waiting on it, as game.start_round: its first beetles now (GAME_FIRST_ITEMS), the
    rest on the worker (grow_later). A whole batch where game work stays in the request. None if nothing is playable.
    """
    from . import game

    size = game.game_setting("GAME_ROUND_SIZE", 10)
    first = game.game_setting("GAME_FIRST_ITEMS", FIRST)   # 0: always whole
    if not _background() or not 0 < first < size:
        return game.start_round(player, mode, fresh_only=fresh_only)
    rnd = game.start_round(player, mode, size=first, fresh_only=fresh_only)
    if rnd is not None:
        cache.set(SPEC.format(rnd.id), {"size": size, "fresh_only": fresh_only, "misses": 0}, KEEP)
        grow_later(rnd)
    return rnd


def wanted(rnd):
    """How many beetles this batch is still to get: 0 once it is whole, finished, or was never meant to grow."""
    if rnd is None or rnd.finished_at is not None:
        return 0
    spec = cache.get(SPEC.format(rnd.id))
    return max(0, spec["size"] - len(rnd.items)) if spec else 0


def planned_size(rnd):
    """The batch's size once it has grown (its size now, for one that doesn't grow)."""
    return len(rnd.items) + wanted(rnd)


def grow(rnd, n=None):
    """
    Add up to ``n`` beetles (all it still wants by default) to the end of a growing batch, built as the batch was and
    never showing a beetle it (or the batch built ahead of it) has already. One grower at a time: returns 0 at once
    when another is at it. ``rnd.items`` is brought up to date. Returns how many were added.
    """
    from . import game, game_crops
    from .models import GameRound

    key = SPEC.format(rnd.id)
    spec = cache.get(key)
    if not spec or not cache.add(LOCK.format(rnd.id), 1, LOCK_SECONDS):
        return 0
    try:
        current = GameRound.objects.filter(id=rnd.id, finished_at__isnull=True).values_list("items", flat=True).first()
        if current is None:
            cache.delete(key)
            return 0
        rnd.items = current
        missing = spec["size"] - len(current)
        if missing <= 0:
            cache.delete(key)
            return 0
        want = missing if n is None else min(n, missing)
        taken, items, built = _taken(rnd), [], []
        for _attempt in range(2):   # a second go when some still came out as beetles it has already
            token = game.avoiding.set(frozenset(taken))
            try:
                built, _ = game.batch_items(rnd.player, rnd.mode, size=want - len(items), fresh_only=spec["fresh_only"])
            finally:
                game.avoiding.reset(token)
            for it in built:
                found = {str(i) for i in game._item_ids(it)}
                if len(items) < want and taken.isdisjoint(found):
                    items.append(it)
                    taken |= found
            if len(items) >= want or not built:
                break
        if not items:
            # nothing playable: the batch ends where it is; only beetles it has already: try again, a few times
            spec["misses"] += 1
            if not built or spec["misses"] >= MISSES:
                cache.delete(key)
            else:
                cache.set(key, spec, KEEP)
            return 0
        with transaction.atomic():
            locked = GameRound.objects.select_for_update().filter(id=rnd.id, finished_at__isnull=True).first()
            if locked is None:
                cache.delete(key)
                return 0
            locked.items = list(locked.items) + items
            locked.save(update_fields=["items"])
        rnd.items = locked.items
        if len(locked.items) >= spec["size"]:
            cache.delete(key)
        stats = game_crops.built.get()
        if stats is not None:   # for the request's timing line (game_views._timed)
            stats["items"] += len(items)
        return len(items)
    finally:
        cache.delete(LOCK.format(rnd.id))


def grow_or_wait(rnd, n, wait=None):
    """
    The next beetles of a growing batch, for a player waiting on them: grown here, or, when another grower (the worker,
    or a look-ahead request) is at it this moment, theirs, waited for up to ``wait`` seconds (GROW_WAIT). Without it the
    batch would seem to end early and the feed would jump to a new one. Returns how many it gained.
    """
    from .models import GameRound

    before = len(rnd.items)
    if grow(rnd, n):
        return len(rnd.items) - before
    deadline = time.monotonic() + (GROW_WAIT if wait is None else wait)
    while cache.get(LOCK.format(rnd.id)) and time.monotonic() < deadline:
        time.sleep(0.1)
    rnd.items = GameRound.objects.filter(id=rnd.id).values_list("items", flat=True).first() or rnd.items
    if len(rnd.items) == before and wanted(rnd):
        grow(rnd, n)   # the other grower gave up, or timed out: one more go here
    return len(rnd.items) - before


def _taken(rnd):
    """
    Every beetle the batch shows, and the player's other open batches of its kind (the one in play before a batch built
    ahead, or the one built ahead of it), and every other photo of the same specimens (#541).
    """
    from . import game
    from .models import GameRound

    ids = set()
    for item in rnd.items:
        ids |= game._item_ids(item)
    others = (GameRound.objects.filter(player_id=rnd.player_id, mode=rnd.mode, finished_at__isnull=True)
              .exclude(id=rnd.id).values_list("items", flat=True))
    for items in others:
        for item in items:
            ids |= game._item_ids(item)
    return {str(i) for i in game.same_specimen(ids)}


def grow_later(rnd):
    """Queue grow_now() on the worker. Nothing is lost if the queue can't be reached: the feed grows it as it goes."""
    from .tasks import grow_game_round_task

    def queue():
        try:
            grow_game_round_task.apply_async(args=[str(rnd.id)], retry=False)
        except Exception:
            logger.info("Batch %s not queued to grow: the feed grows it as it goes", rnd.id)

    transaction.on_commit(queue)


def grow_now(round_id):
    """The worker's part: the batch grown whole, then the crops of its new beetles cut, small ones first."""
    from . import game_crops
    from .models import GameRound

    rnd = GameRound.objects.select_related("player").filter(id=round_id).first()
    if rnd is None or not wanted(rnd):
        return 0
    before = len(rnd.items)
    added = grow(rnd)
    if added:
        rois = game_crops.item_rois(rnd.items[before:])
        for size in game_crops.SIZES:
            for roi in rois:
                game_crops.ensure(roi, size)
    return added
