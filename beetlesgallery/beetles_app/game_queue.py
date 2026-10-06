"""
Game label proposals as a work queue for the Image Annotation page: which images have proposals waiting for a
curator, and how confident each is, so the page can filter to them and sort from most to least confident.

Confidence of one beetle's proposal, from most to least important:
  1. how far down proven experts back it (subfamily .. species, game_trust),
  2. how far down reliable players agree (at least GAME_TIP_MIN_SUPPORT of the weighted vote from
     at least two players),
  3. the share of the vote at that rank, then how many players answered.
An image takes the confidence of its most confident beetle. A proposal a curator already accepted or dismissed
drops out until new answers come in. Cached for GAME_QUEUE_CACHE_SECONDS; reviewing a proposal clears it.
"""
from django.core.cache import cache

from . import game, game_levels
from .game import RANKS, game_setting
from .models import LabelReview

CACHE_KEY = "game:proposal_queue:v1"


def _confidence(entry):
    ranks = entry["ranks"]
    min_support = game_setting("GAME_TIP_MIN_SUPPORT", 0.75)
    agreed, support = 0, 0.0
    for depth, r in enumerate(RANKS, start=1):
        v = ranks.get(r)
        if not v:
            break
        if v["votes"] >= 2 and v["support"] >= min_support:
            agreed, support = depth, v["support"]
        elif not agreed:
            support = v["support"]
    trusted = RANKS.index(entry["trusted_rank"]) + 1 if entry["trusted_rank"] else 0
    deepest = RANKS[max(trusted, agreed) - 1] if max(trusted, agreed) else ""
    best = (ranks.get(deepest) or {}).get("value", "") if deepest else ""
    return {
        "key": (trusted, agreed, round(support, 3), entry["players"]),
        "trusted_rank": entry["trusted_rank"], "agreed_rank": RANKS[agreed - 1] if agreed else "",
        "rank": deepest, "value": best, "support": round(support, 3), "players": entry["players"],
    }


def _build():
    entries = game.consensus(voters=game_levels.suggestion_voters())
    if not entries:
        return {}
    reviewed = {}
    for review in LabelReview.objects.filter(roi_id__in=[e["roi"].id for e in entries]).order_by("-reviewed_at"):
        reviewed.setdefault(review.roi_id, review)
    per_image = {}
    for entry in entries:
        roi = entry["roi"]
        if roi.is_deleted or roi.bbox_is_validated:
            continue
        review = reviewed.get(roi.id)
        if review is not None and entry["answers"] <= review.answers:
            continue
        conf = _confidence(entry)
        image = str(roi.image_asset_id)
        if image not in per_image or conf["key"] > per_image[image]["key"]:
            per_image[image] = conf
    return per_image


def by_image():
    """{image_asset_id (str): confidence dict} for images with a game proposal waiting."""
    queue = cache.get(CACHE_KEY)
    if queue is None:
        queue = _build()
        cache.set(CACHE_KEY, queue, game_setting("GAME_QUEUE_CACHE_SECONDS", 120))
    return queue


def ranked_ids(queue, expert_only=False):
    """Image ids, most confident first."""
    items = [(k, v) for k, v in queue.items() if v["trusted_rank"] or not expert_only]
    items.sort(key=lambda kv: (tuple(-x for x in kv[1]["key"]), kv[0]))
    return [k for k, _ in items]


class RankedFirst:
    """
    The images of a queryset for a Paginator, with the ones in ``ranked`` (image ids) first, in that order, and the
    rest after them in the queryset's own order. Only the ranked ids are held in memory, so the "most confident first"
    sort can page through every image, not just the ones with a proposal.
    """

    def __init__(self, qs, ranked):
        present = {str(i) for i in qs.filter(id__in=ranked).values_list("id", flat=True)}
        self.qs = qs
        self.ranked = [i for i in ranked if i in present]
        self.rest = qs.exclude(id__in=self.ranked)

    def count(self):
        return len(self.ranked) + self.rest.count()

    def __len__(self):
        return self.count()

    def __getitem__(self, page):
        start, stop, n = page.start or 0, page.stop, len(self.ranked)
        head = self.ranked[start:stop]
        found = {str(img.id): img for img in self.qs.filter(id__in=head)}
        images = [found[i] for i in head if i in found]
        if stop > n:
            images += list(self.rest[max(start - n, 0):stop - n])
        return images


def forget():
    cache.delete(CACHE_KEY)


def public(conf):
    """What the image list shows for one image."""
    return {k: conf[k] for k in ("trusted_rank", "agreed_rank", "rank", "value", "support", "players")}
