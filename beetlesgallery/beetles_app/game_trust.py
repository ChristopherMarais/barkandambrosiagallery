"""
Player expertise and trusted game labels.

Expertise is measured per rank *within a branch* of the taxonomy, from a player's scored
"Name That Beetle" answers and Find Them All grids, which name a group to find (#543), on validated ROIs they didn't
know were being scored:

    species   within a genus        e.g. species ID in Xyleborus
    genus     within a tribe        e.g. genus ID in Xyleborini
    tribe     within a subfamily
    subfamily overall

A player is *proven*, an *Naming expert*, at a rank in a taxon once they have covered it: answered at least
GAME_TRUST_IMAGES_PER_SPECIES validated images (all of them for one with fewer) of at least GAME_TRUST_CHILDREN_SHARE
of its children with validated images (the species of a genus, the genera of a tribe, the tribes of a subfamily),
rounded up so a taxon with three or fewer needs all of them; at least GAME_TRUST_MIN_JUDGED answers in total; and at
least GAME_TRUST_MIN_ACCURACY of those answers are right. So a genus with two species needs far fewer answers than
one with forty, a rare genus doesn't stop anyone becoming a tribe expert (#381), and nobody is an expert on a taxon
most of whose members they have never seen.

A *Distinction expert* meets the same rule on telling a taxon's members apart in Similarity and Odd One Out
(apart_counts) instead of naming them (#498): someone who can tell the beetles apart without knowing their names.
It is worked out when shown (the expertise tree, the profile), never stored, and unlocks nothing: trust, judging and
the labels written without review only ever look at naming.

A player is *reliable* in a taxon with at least GAME_TRUST_MIN_JUDGED answers there and the same accuracy, without
the full coverage. A game label on an unvalidated ROI is *trusted* at a rank when a player proven for the label's
own taxon at that rank supports it (and is proven or reliable at every rank above), and no trusted player
disagrees. When the taxon has fewer than GAME_TRUST_MIN_JUDGED validated images, so it can't be tested, proof in at least
GAME_TRUST_SIBLINGS related taxa counts instead: species in an untested genus needs species-level proof in other
genera of the same tribe, and so on. Labels written to the database without review never use that route.

Trusted labels are proposals only: staff accept them on the annotation page.
"""
import math
from collections import defaultdict
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone

from .game import COMPLETE_TAXON, RANKS, check_rois, game_setting, score_select
from .game_scale import WORDS
from .models import Beetles, GameAnswer, PlayerSkill, Taxon

# The rank whose value names the branch a skill is measured in.
BRANCH_OF = {"subfamily": None, "tribe": "subfamily", "genus": "tribe", "species": "genus"}
# What a skill's children are called: genus calls within a tribe cover its genera (#381)
CHILDREN_UNIT = {"subfamily": "subfamilies", "tribe": "tribes", "genus": "genera", "species": "species"}

INDEX_CACHE_KEY = "game:trust_index:v1"
INDEX_CACHE_SECONDS = 600


def wilson_lower_bound(ok, n, z=None):
    """Lower end of the Wilson score interval for ok successes out of n."""
    if n == 0:
        return 0.0
    z = game_setting("GAME_TRUST_Z", 1.96) if z is None else z
    p = ok / n
    z2 = z * z
    centre = p + z2 / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (centre - spread) / (1 + z2 / n)


def per_species():
    """Validated images of a child (a species, genus or tribe) that cover it; the setting kept its old name."""
    return game_setting("GAME_TRUST_IMAGES_PER_SPECIES", 5)


def children_share():
    """The share of a taxon's children an expert must have covered (#381)."""
    return game_setting("GAME_TRUST_CHILDREN_SHARE", 0.75)


def children_needed(total):
    """How many of ``total`` children proof needs: the share, rounded up (so all of them up to three, at 75%)."""
    return min(total, math.ceil(round(children_share() * total, 6))) if total else 0


def min_accuracy():
    return game_setting("GAME_TRUST_MIN_ACCURACY", 0.9)


def coverage(available, answered):
    """
    What proof of a taxon needs, and how far a player is.

    ``available``: {child: validated images in the taxon} for its children (the species of a genus, the genera of
    a tribe, ...). ``answered``: {child: the player's first answers on those images}. A child is covered with
    min(GAME_TRUST_IMAGES_PER_SPECIES, its validated images) answers; proof needs children_needed() of them covered
    and at least GAME_TRUST_MIN_JUDGED answers in all. A taxon with fewer validated images than that can't be
    proven directly at all. Returns {"required", "covered", "children_total", "children_needed", "children_done",
    "complete"}; ``required`` and ``covered`` count answers, for progress: those the cheapest children to cover
    need, and the player's best progress on as many children.
    """
    k = per_species()
    need = {child: min(k, n) for child, n in available.items() if n > 0}
    needed = children_needed(len(need))
    floor = game_setting("GAME_TRUST_MIN_JUDGED", 10)
    required = max(sum(sorted(need.values())[:needed]), floor)
    progress = sorted((min(answered.get(child, 0), n) for child, n in need.items()), reverse=True)
    done = sum(1 for child, n in need.items() if answered.get(child, 0) >= n)
    total_answers = sum(answered.values())
    covered = min(required, max(sum(progress[:needed]), min(total_answers, floor)))
    return {
        "required": required, "covered": covered, "children_total": len(need), "children_needed": needed,
        "children_done": done, "complete": bool(need) and done >= needed and total_answers >= floor,
    }


def is_proven(ok, n, cover):
    """Covered the taxon (see coverage) and right at least GAME_TRUST_MIN_ACCURACY of the time."""
    return bool(cover["complete"]) and n > 0 and ok / n >= min_accuracy()


def is_reliable(ok, n):
    """Enough answers in a taxon, accurate enough, without the full coverage an expert needs."""
    return n >= game_setting("GAME_TRUST_MIN_JUDGED", 10) and ok / n >= min_accuracy()


def is_distinction_expert(ok, n, cover):
    """
    Tells a taxon's members apart as reliably as a Naming expert names them (#498): the same coverage, and
    at least GAME_TRUST_MIN_ACCURACY right over at least GAME_TRUST_MIN_JUDGED judged answers (apart_counts).
    """
    return bool(cover["complete"]) and is_reliable(ok, n)


def species_key(genus, species):
    return f"{genus or ''} {species or ''}".strip().lower()


def branch_for(rank, labels):
    parent = BRANCH_OF[rank]
    return (labels.get(parent) or "") if parent else ""


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
def child_at(rank, genus, species, value):
    """The child a skill at ``rank`` is about: the species ("genus species") or the genus, tribe or subfamily."""
    return species_key(genus, species) if rank == "species" else (value or "").lower()


def skill_counts(player):
    """
    {(rank, branch_lower): [correct, judged, branch_display, {child: answers}]} from scored Identification answers and
    Find Them All grids (#543), where a child is the beetle's taxon at that rank (its genus, for genus calls within a
    tribe). Each validated ROI counts once per rank, in its first answer in either game, so replayed items can't pad a
    record.
    """
    stats = {}
    seen = set()

    def judge(rank, branch, ok, child):
        row = stats.setdefault((rank, branch.lower()), [0, 0, branch, defaultdict(int)])
        row[0] += int(ok)
        row[1] += 1
        row[3][child] += 1

    # a beetle seen before (#541) earns points but shows memory, not skill: left out of naming, grids included
    scored = dict(player=player, is_retry=False, seen_before=False, skipped=False, score_hold=False)
    answers = (
        GameAnswer.objects.filter(Q(is_check=True) | Q(validated_later=True), mode="classify", **scored)
        .values("answered_at", "roi_id", "ref_subfamily", "ref_tribe", "ref_genus", "ref_species",
                *[f"correct_{r}" for r in RANKS])
    )
    grids = list(GameAnswer.objects.filter(mode="select", grid_rank__in=RANKS, **scored).only(
        "answered_at", "tiles", "picks", "flagged", "grid_rank", "grid_group"))
    tiles = grid_beetles(grids)
    for a in sorted([*answers, *grids], key=lambda a: a["answered_at"] if isinstance(a, dict) else a.answered_at):
        if isinstance(a, GameAnswer):
            for rank, branch, ok, child, roi_ids in grid_claims(a, tiles):
                fresh = [i for i in roi_ids if (rank, i) not in seen]
                seen.update((rank, i) for i in fresh)
                if fresh:
                    judge(rank, branch, all(ok[i] for i in fresh), child)
            continue
        labels = {"subfamily": a["ref_subfamily"], "tribe": a["ref_tribe"], "genus": a["ref_genus"],
                  "species": a["ref_species"]}
        for r in RANKS:
            ok = a[f"correct_{r}"]
            if ok is None or (r, a["roi_id"]) in seen:
                continue
            seen.add((r, a["roi_id"]))
            branch = branch_for(r, labels)
            if BRANCH_OF[r] and not branch:
                continue
            judge(r, branch, ok, child_at(r, a["ref_genus"], a["ref_species"], labels[r]))
    return stats


def grid_beetles(grids):
    """{roi_id: Beetles} for every beetle shown in these grid answers, in one query."""
    ids = {str(t) for g in grids for t in g.tiles or []}
    return {str(k): v for k, v in Beetles.objects.select_related("taxon").in_bulk(list(ids)).items()} if ids else {}


def grid_claims(answer, beetles):
    """
    What a Find Them All grid says about naming (#543): tapping a beetle claims it is of the named group, so a member
    tapped is a right claim at the grid's rank, a non-member tapped a wrong one, and a member left out a wrong one too
    (it wasn't recognised). Only validated beetles count, and a photo the player flagged counts for nothing.
    Claims are grouped by the beetle's own taxon, so a grid counts at most once per taxon it showed, whatever its size:
    [(rank, branch_display, {roi_id: ok}, child, [roi_id, ...])], ``ok`` per beetle so beetles already judged can be
    left out.
    """
    rank = answer.grid_rank
    shown = [beetles.get(str(t)) for t in answer.tiles or []]
    states = score_select(shown, answer.picks, rank, answer.grid_group, answer.flagged)["tiles"]
    claims = {}
    for roi, state in zip(shown, states):
        if state not in ("right", "wrong", "missed"):
            continue
        t = roi.taxon
        labels = {r: getattr(t, r) or "" for r in RANKS}
        branch = branch_for(rank, labels)
        if BRANCH_OF[rank] and not branch:
            continue
        child = child_at(rank, t.genus, t.species, labels[rank])
        ok, ids = claims.setdefault((branch.lower(), child), (branch, {}, []))[1:]
        ok[roi.id] = state == "right"
        ids.append(roi.id)
    return [(rank, branch, ok, child, ids) for (_, child), (branch, ok, ids) in claims.items()]


def children_available():
    """{(rank, branch_lower): {child: validated images}}: what proving each skill has to cover."""
    out = defaultdict(lambda: defaultdict(int))
    rows = check_rois().values("taxon__subfamily", "taxon__tribe", "taxon__genus", "taxon__species").annotate(n=Count("id"))
    for row in rows:
        for rank, field in BRANCH_OF.items():
            branch = (row[f"taxon__{field}"] or "").lower() if field else ""
            if field and not branch:
                continue
            out[(rank, branch)][child_at(rank, row["taxon__genus"], row["taxon__species"], row[f"taxon__{rank}"])] += row["n"]
    return out


def recompute_skills(player):
    """Refresh the player's PlayerSkill rows from their answers."""
    now = timezone.now()
    existing = {(s.rank, s.branch.lower()): s for s in PlayerSkill.objects.filter(player=player)}
    available = children_available()
    create, update = [], []
    for key, (ok, n, display, answered) in skill_counts(player).items():
        skill = existing.get(key)
        if skill is None:
            skill = PlayerSkill(player=player, rank=key[0], branch=display)
            create.append(skill)
        else:
            update.append(skill)
        cover = coverage(available.get(key, {}), answered)
        proven = is_proven(ok, n, cover)
        if proven and not skill.proven:
            skill.proven_at = now
        skill.correct, skill.judged, skill.proven = ok, n, proven
        skill.required, skill.covered = cover["required"], cover["covered"]
        skill.children_total, skill.children_needed = cover["children_total"], cover["children_needed"]
        skill.children_done = cover["children_done"]
        skill.lower_bound = round(wilson_lower_bound(ok, n), 4)
        skill.updated_at = now
    PlayerSkill.objects.bulk_create(create)
    PlayerSkill.objects.bulk_update(update, [
        "correct", "judged", "proven", "proven_at", "lower_bound", "required", "covered", "children_total",
        "children_needed", "children_done", "updated_at",
    ])


def skills_for(player):
    return list(PlayerSkill.objects.filter(player=player).order_by("rank", "branch"))


# ---------------------------------------------------------------------------
# Taxonomy index: parents of branches, and which branches can be tested
# ---------------------------------------------------------------------------
def trust_index():
    """
    Cached lookups:
      parent[rank][branch]  the branch one level up (genus -> tribe, tribe -> subfamily)
      validated[rank]       {branch: validated ROIs available to test that rank there}
    """
    index = cache.get(INDEX_CACHE_KEY)
    if index is not None:
        return index
    parent = {"genus": {}, "tribe": {}}
    for subfamily, tribe, genus in Taxon.objects.filter(COMPLETE_TAXON).values_list(
        "subfamily", "tribe", "genus"
    ).distinct():
        if genus and tribe:
            parent["genus"].setdefault(genus.lower(), tribe.lower())
        if tribe:
            parent["tribe"].setdefault(tribe.lower(), subfamily.lower())

    validated = {}
    for rank, field in BRANCH_OF.items():
        if field is None:
            validated[rank] = {"": check_rois().count()}
            continue
        validated[rank] = {
            (row[f"taxon__{field}"] or "").lower(): row["n"]
            for row in check_rois().values(f"taxon__{field}").annotate(n=Count("id"))
        }
    index = {"parent": parent, "validated": validated}
    cache.set(INDEX_CACHE_KEY, index, INDEX_CACHE_SECONDS)
    return index


def branch_parent(rank, branch, index):
    """
    The branch above ``branch`` for skills at ``rank``: the tribe of a genus (species
    skills), the subfamily of a tribe (genus skills), or "" for subfamilies (tribe skills).
    None when unknown or there is nothing above.
    """
    field = BRANCH_OF[rank]
    if field is None:
        return None
    if field == "subfamily":
        return ""
    return index["parent"][field].get(branch)


def is_testable(rank, branch, index):
    """A taxon with at least GAME_TRUST_MIN_JUDGED validated images can be tested; proof there scales with its species."""
    return index["validated"][rank].get(branch, 0) >= game_setting("GAME_TRUST_MIN_JUDGED", 10)


def complete_labels(labels, index):
    """Fill in missing higher ranks from the taxonomy (e.g. the tribe of a genus)."""
    labels = dict(labels)
    if labels.get("genus") and not labels.get("tribe"):
        labels["tribe"] = index["parent"]["genus"].get(labels["genus"].lower(), "")
    if labels.get("tribe") and not labels.get("subfamily"):
        labels["subfamily"] = index["parent"]["tribe"].get(labels["tribe"].lower(), "")
    return labels


def elite_players():
    """
    The players reliable enough overall to be experts: the top GAME_EXPERT_PERCENTILE share by rating (the top
    quarter by default). None, meaning no limit, until GAME_EXPERT_MIN_PLAYERS players are rated, because a
    percentile of a handful of players means little.
    """
    from .game_scoring import cached_ratings

    min_judged = game_setting("GAME_RATER_MIN_JUDGED", 10)
    rated = {pid: r for pid, (r, _, n) in cached_ratings().items() if n >= min_judged}
    if len(rated) < game_setting("GAME_EXPERT_MIN_PLAYERS", 10):
        return None
    ordered = sorted(rated.values())
    cut = ordered[int(len(ordered) * (1 - game_setting("GAME_EXPERT_PERCENTILE", 0.25)))] if ordered else 1.0
    return {pid for pid, r in rated.items() if r >= cut}


class TrustContext:
    """Answers "is this player trusted for this label?" for a set of players."""

    def __init__(self, player_ids):
        self.index = trust_index()
        self.elite = elite_players()
        self.proven = defaultdict(set)
        self.reliable = defaultdict(set)
        for pid, rank, branch, proven, ok, n in PlayerSkill.objects.filter(
            player_id__in=list(player_ids)
        ).values_list("player_id", "rank", "branch", "proven", "correct", "judged"):
            if proven:
                self.proven[pid].add((rank, branch.lower()))
            if proven or is_reliable(ok, n):
                self.reliable[pid].add((rank, branch.lower()))

    def how_trusted(self, player_id, rank, labels, direct_only=False, deepest=True):
        """
        "direct" if proven in this label's taxon, "siblings" if the taxon is untestable
        and the player is proven in enough related taxa, else None. ``direct_only`` refuses the
        sibling route: the player must have proven themselves in this very taxon. For the ranks above the
        label's own (``deepest`` False), being reliable there is enough.
        """
        branch = branch_for(rank, labels).lower()
        if BRANCH_OF[rank] and not branch:
            return None
        if self.elite is not None and player_id not in self.elite:
            return None   # experts also have to be among the most reliable players overall
        proven = self.proven.get(player_id, set())
        if (rank, branch) in proven or (not deepest and (rank, branch) in self.reliable.get(player_id, set())):
            return "direct"
        if direct_only or is_testable(rank, branch, self.index):
            return None
        parent = branch_parent(rank, branch, self.index)
        if parent is None:
            return None
        siblings = [
            b for (r, b) in proven
            if r == rank and b != branch and branch_parent(rank, b, self.index) == parent
        ]
        return "siblings" if len(siblings) >= game_setting("GAME_TRUST_SIBLINGS", 2) else None

    def trusted_through(self, player_id, rank, labels, direct_only=False):
        """Trusted at ``rank`` and at every rank above it."""
        labels = complete_labels(labels, self.index)
        for r in RANKS[: RANKS.index(rank) + 1]:
            if not labels.get(r) or not self.how_trusted(player_id, r, labels, direct_only, deepest=r == rank):
                return False
        return True

    def verdict(self, votes, ranks):
        """
        Which consensus ranks are backed by trusted players.

        votes: [(player_id, {rank: value})], ranks: {rank: {"value", ...} or None}.
        A rank is trusted when at least GAME_TRUST_MIN_VOTES trusted players give the
        winning value (with the winning values above it), no trusted player gives a
        different value, and the rank above is trusted.
        """
        min_votes = game_setting("GAME_TRUST_MIN_VOTES", 1)
        out = {r: {"trusted": False, "trusted_votes": 0} for r in RANKS}
        trusted_rank = ""
        for i, r in enumerate(RANKS):
            if not ranks.get(r):
                break
            winners = {rr: ranks[rr]["value"].lower() for rr in RANKS[: i + 1]}
            support, dissent = 0, False
            for pid, labels in votes:
                if r not in labels or not self.trusted_through(pid, r, labels):
                    continue
                if all((labels.get(rr) or "").lower() == winners[rr] for rr in winners):
                    support += 1
                else:
                    dissent = True
            out[r]["trusted_votes"] = support
            if support < min_votes or dissent:
                break
            out[r]["trusted"] = True
            trusted_rank = r
        return {"ranks": out, "trusted_rank": trusted_rank, "taxon": species_taxon(ranks.get("species"))}


def species_taxon(species_vote):
    """The Taxon for a "Genus species" consensus value, preferring the nominal (no subspecies) row."""
    if not species_vote:
        return None
    genus, _, species = species_vote["value"].partition(" ")
    if not species:
        return None
    return (
        Taxon.objects.filter(COMPLETE_TAXON, genus__iexact=genus, species__iexact=species)
        .order_by("subspecies", "valid_species_id").first()
    )


# ---------------------------------------------------------------------------
# Player report
# ---------------------------------------------------------------------------
def accuracy_by_rank(skills, apart):
    """
    [{"rank", "naming", "distinction"}] for the report's "Accuracy by rank" (#605), each {"ok", "n", "accuracy"}:
    the expertise tree's own counts added up over every branch at that rank, so the numbers match the tree. Naming is
    Naming and Find Them All (the player's skills, skill_counts); Distinction is Similarity and Odd One Out
    (apart_counts).
    """
    totals = {r: {"naming": [0, 0], "distinction": [0, 0]} for r in RANKS}
    for s in skills:
        if s.rank in totals:
            totals[s.rank]["naming"][0] += s.correct
            totals[s.rank]["naming"][1] += s.judged
    for (rank, _), (ok, n, *_) in apart.items():
        if rank in totals:
            totals[rank]["distinction"][0] += ok
            totals[rank]["distinction"][1] += n

    def cell(ok, n):
        return {"ok": ok, "n": n, "accuracy": ok / n if n else None}

    return [{"rank": r, **{k: cell(*v) for k, v in totals[r].items()}} for r in RANKS]


def player_report(player):
    """Everything the performance page shows about one player."""
    from . import game

    skills = skills_for(player)
    proven = [s for s in skills if s.proven]

    # Skill branches come from the reference labels of scored items, so listing every
    # one would tell the player what a beetle they got wrong really was (and which
    # items were scored). Only show progress in taxa the player has named themselves,
    # once there's enough of it that a single item can't be singled out.
    claimed = {"subfamily": {""}, "tribe": set(), "genus": set(), "species": set()}
    for subfamily, tribe, genus in GameAnswer.objects.filter(
        player=player, mode="classify", skipped=False
    ).values_list("subfamily", "tribe", "genus").distinct():
        claimed["tribe"].add(subfamily.lower())
        claimed["genus"].add(tribe.lower())
        claimed["species"].add(genus.lower())
    for rank, group in GameAnswer.objects.filter(player=player, mode="select", skipped=False).values_list(
            "grid_rank", "grid_group"):   # a Find Them All grid names its group's branch to the player (#543)
        if BRANCH_OF.get(rank) and (group or {}).get(BRANCH_OF[rank]):
            claimed[rank].add(group[BRANCH_OF[rank]].lower())
    min_shown = game_setting("GAME_REPORT_MIN_JUDGED", 5)
    progressing = sorted(
        (s for s in skills if not s.proven and s.judged >= min_shown and s.branch.lower() in claimed[s.rank]),
        key=lambda s: (-(s.covered / s.required if s.required else 0) * s.lower_bound, s.rank),
    )
    for s in skills:
        s.progress = min(1.0, s.covered / s.required) if s.required else 0.0
        s.accuracy = s.correct / s.judged if s.judged else None
        s.children_unit = CHILDREN_UNIT[s.rank]

    since = timezone.now() - timedelta(days=183)
    monthly = []
    rows = (
        GameAnswer.objects.filter(player=player, answered_at__gte=since)
        .annotate(month=TruncMonth("answered_at"))
        .values("month")
        .annotate(
            labelled=Count("id", filter=Q(skipped=False)),
            **{k: v for k, v in game._rank_counts().items()},
        )
        .order_by("month")
    )
    for row in rows:
        accuracy, judged = game._accuracy(row)
        monthly.append({"month": row["month"], "labelled": row["labelled"], "accuracy": accuracy, "judged": judged})

    by_rank = accuracy_by_rank(skills, apart_counts(player))
    return {
        "summary": game.player_summary(player),
        "rounds": player.game_rounds.filter(finished_at__isnull=False).count(),
        "challenge": game.target_difficulty(player),
        "by_rank": by_rank,
        # hide the Distinction column when nobody has told these beetles apart yet (#618 rep-empty-col), rather
        # than a table of nothing but dashes
        "by_rank_has_distinction": any(row["distinction"]["n"] for row in by_rank),
        "proven": proven,
        "progressing": progressing[:12],
        "monthly": monthly,
        "recent_rounds": list(
            player.game_rounds.filter(finished_at__isnull=False)
            .annotate(labelled=Count("answers", filter=Q(answers__skipped=False)))
            .order_by("-finished_at")[:10]
        ),
        "per_species": per_species(),
        "children_share": children_share(),
        "min_accuracy": min_accuracy(),
        "siblings": game_setting("GAME_TRUST_SIBLINGS", 2),
    }


# ---------------------------------------------------------------------------
# Experts' labels straight into the database
# ---------------------------------------------------------------------------
def auto_apply_expert_labels(roi_ids=None):
    """
    Write the species that proven experts agree on onto beetles that have no name yet, without waiting for a
    curator. This is the strictest rule in the game. Each expert counted must be *proven directly* on the species
    of the label's genus (enough validated images of most of its species, see coverage, and at least
    GAME_TRUST_MIN_ACCURACY right), be proven or reliable in its tribe, subfamily and overall, and be among the most
    reliable players overall (elite_players). Proof in neighbouring genera, which is enough for a suggestion to
    curators, is not enough here. GAME_AUTO_APPLY_MIN_EXPERTS (2) such experts must give the same species, no
    proven player may disagree, the beetle must have no species label, not be validated, and no curator has
    reviewed a game label for it before. The beetle stays unvalidated so a curator still confirms it; a
    LabelReview with no reviewer records that it was automatic. Returns the ids of the beetles labelled.
    """
    from django.db import transaction

    from . import game
    from .models import LabelReview

    if not game_setting("GAME_AUTO_APPLY_EXPERT_LABELS", True):
        return []
    min_experts = game_setting("GAME_AUTO_APPLY_MIN_EXPERTS", 2)
    reviewed = LabelReview.objects.all()
    if roi_ids is not None:
        reviewed = reviewed.filter(roi_id__in=list(roi_ids))
    reviewed = set(reviewed.values_list("roi_id", flat=True))
    entries = [
        e for e in game.consensus(roi_ids=roi_ids)
        if not (e["roi"].id in reviewed or e["roi"].bbox_is_validated or e["roi"].is_deleted
                or e["roi"].depicts_valid_name_id or e["trusted_rank"] != "species" or e["taxon"] is None)
    ]
    if not entries:
        return []
    trust = TrustContext({pid for e in entries for pid, _ in e["votes"]})
    applied = []
    for entry in entries:
        roi, taxon = entry["roi"], entry["taxon"]
        if direct_experts(entry, trust) < min_experts:
            continue
        with transaction.atomic():
            roi.depicts_valid_name_id = taxon.valid_species_id
            roi.save(update_fields=["depicts_valid_name_id"])
            LabelReview.objects.create(
                roi=roi, decision=LabelReview.Decision.ACCEPTED, subfamily=taxon.subfamily or "", tribe=taxon.tribe or "",
                genus=taxon.genus or "", species=taxon.species or "", taxon=taxon, trusted_rank="species",
                answers=entry["answers"], reviewed_by=None,
            )
        applied.append(roi.id)
    return applied


def direct_experts(entry, trust):
    """Players who give the consensus species (and every rank above it) and are directly proven for all of it."""
    winners = {r: (entry["ranks"][r] or {}).get("value", "").lower() for r in RANKS}
    count = set()
    for pid, labels in entry["votes"]:
        labels = complete_labels(labels, trust.index)
        if all((labels.get(r) or "").lower() == winners[r] for r in RANKS) and trust.trusted_through(
                pid, "species", labels, direct_only=True):
            count.add(pid)
    return len(count)


# ---------------------------------------------------------------------------
# The expertise tree a player sees
# ---------------------------------------------------------------------------
EXPERTISE_FLOOR = 0.5
# Accuracy bands on the site's one scale (game_scale.py, #572): an empty grey marker for too few answers yet, red
# under 50%, then three equal steps from 50% up to what an expert needs (orange, yellow, green). An expert is deep
# green: a Naming expert's dot glows like the top level; a Distinction expert's triangle doesn't, since it unlocks
# nothing (#498, #539).
EXPERTISE_TIERS = ("decent", "good", "great")


def expertise_bands():
    """[(status, lowest accuracy)] from the top band down, e.g. great 0.7, good 0.6, decent 0.5 (expert at 80%)."""
    top = max(min_accuracy(), EXPERTISE_FLOOR + 0.04)
    step = (top - EXPERTISE_FLOOR) / len(EXPERTISE_TIERS)
    return [(tier, round(EXPERTISE_FLOOR + i * step, 4)) for i, tier in reversed(list(enumerate(EXPERTISE_TIERS)))]


def accuracy_status(ok, n, min_shown):
    """unknown (fewer than ``min_shown`` judged), or fair .. great by accuracy."""
    if n < min_shown:
        return "unknown"
    for tier, lowest in expertise_bands():
        if ok / n >= lowest:
            return tier
    return "fair"


def node_status(skill, min_shown):
    """How naming in a branch is shown: unknown (too few answers yet), fair .. great by accuracy, or expert."""
    if skill is None or skill.judged < min_shown:
        return "unknown"
    if skill.proven:
        return "expert"
    return accuracy_status(skill.correct, skill.judged, min_shown)


def apart_status(ok, n, cover, min_shown):
    """How telling a branch apart is shown: like node_status, with expert for a Distinction expert (#498)."""
    if n >= min_shown and is_distinction_expert(ok, n, cover):
        return "expert"
    return accuracy_status(ok, n, min_shown)


def apart_counts(player):
    """
    {(rank, branch_lower): [correct, judged, branch_display, {child: answers}]}, shaped like skill_counts: how well
    the player tells a taxon's children apart, from Similarity and Odd One Out answers judged against validated
    beetles (#381); Find Them All counts as naming instead (skill_counts, #543). ("genus", "xyleborini") is telling
    Xyleborini's genera apart. A Similarity answer judges each rank whose parent the two beetles share (both in Xyleborini: did they say rightly whether the genus
    is the same?), and shows both beetles' children. An Odd One Out grid judges its own rank within its group's parent
    (a pick outside the group), and shows the group's child, and the odd one's when it has the same parent. Coverage
    counts the judged answers that showed each child, as naming counts the images named.
    """
    out = {}

    def judge(rank, branch, ok, children):
        row = out.setdefault((rank, branch.lower()), [0, 0, branch, defaultdict(int)])
        row[0] += int(ok)
        row[1] += 1
        for child in {c for c in children if c}:
            row[3][child] += 1

    def child(a, side, rank):
        return child_at(rank, a[f"{side}__taxon__genus"], a[f"{side}__taxon__species"], a[f"{side}__taxon__{rank}"])

    answers = GameAnswer.objects.filter(player=player, is_retry=False, seen_before=False, skipped=False,
                                        score_hold=False)
    correct = [f"correct_{r}" for r in RANKS]
    sides = [f"{side}__taxon__{r}" for side in ("roi", "roi_b") for r in RANKS]
    for a in answers.filter(mode="pair").values(*sides, *correct):
        for r in RANKS:
            parent = BRANCH_OF[r]
            branch = (a[f"roi__taxon__{parent}"] or "") if parent else ""
            if parent and (not branch or branch.lower() != (a[f"roi_b__taxon__{parent}"] or "").lower()):
                break   # different above this rank: nothing inside one taxon to tell apart
            if a[f"correct_{r}"] is not None:
                judge(r, branch, a[f"correct_{r}"], [child(a, "roi", r), child(a, "roi_b", r)])
    odd_one = [f"roi_b__taxon__{r}" for r in RANKS]
    for a in answers.filter(mode="odd", grid_rank__in=RANKS).values("grid_rank", "grid_group", *odd_one, *correct):
        r, ok = a["grid_rank"], a[f"correct_{a['grid_rank']}"]
        parent, group = BRANCH_OF[r], a["grid_group"] or {}
        branch = (group.get(parent) or "") if parent else ""
        if ok is None or (parent and not branch):
            continue
        shown = [(group.get(r) or "").lower()]   # species groups are "Genus species", as child_at keys them
        if not parent or (a[f"roi_b__taxon__{parent}"] or "").lower() == branch.lower():
            shown.append(child(a, "roi_b", r))
        judge(r, branch, ok, shown)
    return out


def distinction_experts(player):
    """[(rank, branch)] where the player is a Distinction expert (#498): worked out when asked, never stored."""
    available = children_available()
    return sorted(
        (rank, display) for (rank, branch), (ok, n, display, shown) in apart_counts(player).items()
        if is_distinction_expert(ok, n, coverage(available.get((rank, branch), {}), shown))
    )


def expertise_legend():
    """
    The tree's colour bands: (status, word, accuracy range) from lowest to highest, e.g. ("good", "Good", "60\u201370%");
    the key adds the experts itself.
    """
    bands = list(reversed(expertise_bands()))
    pct = lambda x: f"{round(x * 100)}%"   # noqa: E731
    legend = [("fair", WORDS["fair"], f"under {pct(EXPERTISE_FLOOR)}")]
    for i, (tier, lowest) in enumerate(bands):
        upper = bands[i + 1][1] if i + 1 < len(bands) else None
        legend.append((tier, WORDS[tier], f"{round(lowest * 100)}\u2013{pct(upper)}" if upper else f"{pct(lowest)}+"))
    return legend


def expertise_tree(player):
    """
    The taxonomy as subfamily > tribe > genus, each branch with how well the player identifies what is inside it:
    a subfamily shows their tribe calls within it, a tribe their genus calls, a genus their species calls; and how
    well they tell those apart in Similarity and Odd One Out (apart_counts). ``status`` is expert for a Naming expert,
    ``apart_status`` for a Distinction expert. Branches they have not played are counted but
    not listed one by one.
    """
    min_shown = game_setting("GAME_REPORT_MIN_JUDGED", 5)
    skills = {(s.rank, s.branch.lower()): s for s in skills_for(player)}
    apart = apart_counts(player)
    available = children_available()

    def node(rank, name):
        key = (rank, name.lower())
        skill = skills.get(key)
        apart_ok, apart_n, _, shown = apart.get(key, (0, 0, name, {}))
        apart_cover = coverage(available.get(key, {}), shown)
        return {
            "apart_correct": apart_ok, "apart_judged": apart_n,
            "apart_status": apart_status(apart_ok, apart_n, apart_cover, min_shown),
            "apart_children_total": apart_cover["children_total"], "apart_children_done": apart_cover["children_done"],
            "apart_children_needed": apart_cover["children_needed"],
            "name": name, "status": node_status(skill, min_shown),
            "judged": skill.judged if skill else 0, "correct": skill.correct if skill else 0,
            "accuracy": (skill.correct / skill.judged) if skill and skill.judged else None,
            "proven": bool(skill and skill.proven),
            "required": skill.required if skill else 0, "covered": skill.covered if skill else 0,
            "children_total": skill.children_total if skill else 0, "children_done": skill.children_done if skill else 0,
            "children_needed": skill.children_needed if skill else 0, "unit": CHILDREN_UNIT[rank],
        }

    layout = defaultdict(lambda: defaultdict(set))
    for subfamily, tribe, genus in Taxon.objects.filter(COMPLETE_TAXON).values_list("subfamily", "tribe", "genus").distinct():
        layout[subfamily][tribe or "(no tribe)"].add(genus)
    tree = []
    for subfamily in sorted(layout):
        sub = node("tribe", subfamily)
        sub["tribes"], sub["hidden"] = [], 0
        for tribe in sorted(layout[subfamily]):
            t = node("genus", tribe)
            genera = [node("species", g) for g in sorted(layout[subfamily][tribe])]
            t["genera"] = [g for g in genera if g["status"] != "unknown" or g["apart_status"] != "unknown"]
            t["hidden"] = len(genera) - len(t["genera"])
            if t["status"] != "unknown" or t["apart_status"] != "unknown" or t["genera"]:
                sub["tribes"].append(t)
            else:
                sub["hidden"] += 1
        tree.append(sub)
    root = node("subfamily", "")
    return {"root": root, "subfamilies": tree, "min_shown": min_shown, "per_species": per_species(),
            "children_share": children_share(), "min_accuracy": min_accuracy(), "legend": expertise_legend(),
            "min_judged": game_setting("GAME_TRUST_MIN_JUDGED", 10),
            "experts": sum(1 for s in skills.values() if s.proven)}
