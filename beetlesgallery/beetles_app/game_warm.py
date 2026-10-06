"""
Batches built ahead for the games a player might switch to (#542).

Switching game in the feed's toolbar used to build a whole new batch while the player waited. Now, once the feed is
showing, the worker builds one batch for each of the player's other game choices and cuts the crops of its first
beetles. They wait in the cache; switching to one of those games turns its batch into the player's new round at once
(take). A batch is only used for the choices, focus and unlocks it was built under, and loses any beetle the player
has answered since, so it never shows anything a freshly built one wouldn't.
"""
import logging
import time

from django.core.cache import cache
from django.db import transaction

logger = logging.getLogger(__name__)

KEY = "game:warm:{}:{}"            # player, game choice
LOCK = "game:warming:{}"           # player: one build at a time
KEEP = 60 * 60                     # seconds a batch built ahead is kept
LOCK_SECONDS = 120
FIRST_ITEMS = 2                    # the items whose crops are cut ahead, so the first beetles show at once
MIXED = "mixed"                    # only the mixed feed has the toolbar


def _signature(info, focus):
    """What a batch depends on besides the game: the unlocks (which games and ranks) and the focus."""
    return [sorted(info["perks"]), list(focus) if focus else None]


def choices(player, info=None):
    """The game choices this player could switch to from the one they play now: none until they can choose."""
    from . import game, game_levels

    info = info or game_levels.for_player(player)
    if game_levels.CHOOSE_GAME not in info["perks"]:
        return []
    current = game.play_mode(player, info)
    return [c for c in list(game_levels.games(info["perks"])) + ["both"] if c != current]


def missing(player):
    """The choices with no batch waiting for them under the player's current unlocks and focus."""
    from . import game, game_levels

    info = game_levels.for_player(player)
    sig = _signature(info, game.player_focus(player))
    options = choices(player, info)
    found = cache.get_many([KEY.format(player.pk, c) for c in options])
    return [c for c in options if (found.get(KEY.format(player.pk, c)) or {}).get("sig") != sig]


def build(player):
    """
    Build a batch for each choice that has none waiting, and cut the crops of its first items (the worker's part of
    warm_later). Returns the choices built.
    """
    from . import game, game_crops, game_levels

    if not cache.add(LOCK.format(player.pk), 1, LOCK_SECONDS):
        return []   # already being built
    try:
        info = game_levels.for_player(player)
        sig = _signature(info, game.player_focus(player))
        built = []
        for choice in missing(player):
            if game.play_mode(player) == choice:
                continue   # they switched to it while this ran: the feed has built its batch already (#575)
            items, notice = game.batch_items(player, MIXED, choice=choice)
            if not items:
                continue
            cache.set(KEY.format(player.pk, choice),
                      {"items": items, "notice": notice, "sig": sig, "at": time.time()}, KEEP)
            built.append(choice)
            for roi in _rois(items[:FIRST_ITEMS]):
                for size in game_crops.SIZES:
                    game_crops.ensure(roi, size)
        return built
    finally:
        cache.delete(LOCK.format(player.pk))


def _rois(items):
    from .models import Beetles

    ids = []
    for item in items:
        ids += [i for i in (item.get("tiles") or [item.get("a"), item.get("b")]) if i and i not in ids]
    found = {str(k): v for k, v in Beetles.objects.select_related("image_asset").in_bulk(ids).items()}
    return [found[i] for i in ids if i in found and found[i].has_bbox()]


def warm_later(player, mode):
    """
    Queue build() on the worker, if this feed has a toolbar and some choice has no batch waiting. Nothing is built
    where game work stays in the request (GAME_RECOMPUTE_IN_BACKGROUND off) or the queue can't be reached: switching
    then builds the batch as before. Returns whether it was queued.
    """
    from . import game
    from .tasks import warm_game_batches_task

    if mode != MIXED or not game.game_setting("GAME_RECOMPUTE_IN_BACKGROUND", False):
        return False
    if cache.get(LOCK.format(player.pk)) or not missing(player):
        return False

    def queue():
        try:
            warm_game_batches_task.apply_async(args=[player.pk], retry=False)
        except Exception:
            logger.info("Batches for %s's other games not queued: they are built when the game is switched", player.pk)

    transaction.on_commit(queue)
    return True


def take(player, mode):
    """
    The batch waiting for the game the player now plays, as their new round (its ``notice`` set, as start_round's),
    or None. Items with a beetle the player has answered since it was built are left out.
    """
    from . import game, game_levels
    from .models import GameAnswer, GameRound

    if mode != MIXED:
        return None
    info = game_levels.for_player(player)
    key = KEY.format(player.pk, game.play_mode(player, info))
    entry = cache.get(key)
    if not entry:
        return None
    cache.delete(key)   # used once, whatever happens next
    if entry.get("sig") != _signature(info, game.player_focus(player)):
        return None
    # a minute's margin, for the worker's clock
    since = GameAnswer.objects.filter(player=player, answered_at__gte=_moment(entry["at"] - 60))
    answered = set()
    for roi, roi_b, tiles in since.values_list("roi_id", "roi_b_id", "tiles"):
        answered |= {str(roi), str(roi_b)} | {str(t) for t in tiles or []}
    # the reviews since named those beetles, and so every other photo of the same specimens (#541)
    answered = {str(i) for i in game.same_specimen(answered - {"None"})}
    items = [it for it in entry["items"] if answered.isdisjoint(game._item_ids(it))]
    if not items:
        return None
    rnd = GameRound.objects.create(player=player, mode=mode, items=items)
    rnd.notice = entry.get("notice") or ""
    return rnd


def _moment(stamp):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(stamp, tz=timezone.utc)
