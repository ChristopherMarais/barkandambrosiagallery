"""
Player expertise and trusted game labels.

Expertise is measured per rank *within a branch* of the taxonomy, from a player's scored
"Name That Beetle" answers (validated ROIs they didn't know were being scored):

    species   within a genus        e.g. species ID in Xyleborus
    genus     within a tribe        e.g. genus ID in Xyleborini
    tribe     within a subfamily
    subfamily overall

A player is *proven* (an expert) at a rank in a taxon once they have covered it: answered at least
GAME_TRUST_IMAGES_PER_SPECIES validated images of every species in it that has validated images (all of them for
a species with fewer), at least GAME_TRUST_MIN_JUDGED answers in total, and at least GAME_TRUST_MIN_ACCURACY of
those answers are right. So a genus with two species needs far
fewer answers than one with forty, and nobody is an expert on a taxon whose species they have never seen.

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

from .game import COMPLETE_TAXON, RANKS, check_rois, game_setting
from .models import GameAnswer, PlayerSkill, Taxon

# The rank whose value names the branch a skill is measured in.
BRANCH_OF = {"subfamily": None, "tribe": "subfamily", "genus": "tribe", "species": "genus"}

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
    return game_setting("GAME_TRUST_IMAGES_PER_SPECIES", 5)


def min_accuracy():
    return game_setting("GAME_TRUST_MIN_ACCURACY", 0.9)


def coverage(available, answered):
    """
    What proof of a taxon needs, and how far a player is.

    ``available``: {species: validated images in the taxon}. ``answered``: {species: the player's first answers
    on those images}. Returns {"required", "covered", "species_total", "species_done", "complete"}, where each
    species needs min(GAME_TRUST_IMAGES_PER_SPECIES, its validated images), and the total needs at least
    GAME_TRUST_MIN_JUDGED. A taxon with fewer validated images than that can't be proven directly at all.
    """
    k = per_species()
    need = {sp: min(k, n) for sp, n in available.items() if n > 0}
    floor = game_setting("GAME_TRUST_MIN_JUDGED", 10)
    required = max(sum(need.values()), floor)
    covered_per = {sp: min(answered.get(sp, 0), n) for sp, n in need.items()}
    done = sum(1 for sp, n in need.items() if covered_per[sp] >= n)
    total_answers = sum(answered.values())
    covered = min(required, max(sum(covered_per.values()), min(total_answers, floor)))
    return {
        "required": required, "covered": covered, "species_total": len(need), "species_done": done,
        "complete": bool(need) and done == len(need) and total_answers >= floor,
    }


def is_proven(ok, n, cover):
    """Covered the taxon (see coverage) and right at least GAME_TRUST_MIN_ACCURACY of the time."""
    return bool(cover["complete"]) and n > 0 and ok / n >= min_accuracy()


def is_reliable(ok, n):
    """Enough answers in a taxon, accurate enough, without the full coverage an expert needs."""
    return n >= game_setting("GAME_TRUST_MIN_JUDGED", 10) and ok / n >= min_accuracy()


def species_key(genus, species):
    return f"{genus or ''} {species or ''}".strip().lower()


def branch_for(rank, labels):
    parent = BRANCH_OF[rank]
    return (labels.get(parent) or "") if parent else ""


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
def skill_counts(player):
    """
    {(rank, branch_lower): [correct, judged, branch_display, {species: answers}]} from scored classify answers.
    Each validated ROI counts once per rank (the first answer), so replayed items can't
    pad a record.
    """
    stats = {}
    seen = set()
    answers = (
        GameAnswer.objects.filter(Q(is_check=True) | Q(validated_later=True), player=player, mode="classify",
                                  is_retry=False, skipped=False, score_hold=False)
        .order_by("answered_at")
        .values("roi_id", "ref_subfamily", "ref_tribe", "ref_genus", "ref_species",
                *[f"correct_{r}" for r in RANKS])
    )
    for a in answers:
        labels = {"subfamily": a["ref_subfamily"], "tribe": a["ref_tribe"], "genus": a["ref_genus"]}
        for r in RANKS:
            ok = a[f"correct_{r}"]
            if ok is None or (r, a["roi_id"]) in seen:
                continue
            seen.add((r, a["roi_id"]))
            branch = branch_for(r, labels)
            if BRANCH_OF[r] and not branch:
                continue
            row = stats.setdefault((r, branch.lower()), [0, 0, branch, defaultdict(int)])
            row[0] += int(ok)
            row[1] += 1
            row[3][species_key(a["ref_genus"], a["ref_species"])] += 1
    return stats


def species_available():
    """{(rank, branch_lower): {species: validated images}}: what proving each skill has to cover."""
    out = defaultdict(lambda: defaultdict(int))
    rows = check_rois().values("taxon__subfamily", "taxon__tribe", "taxon__genus", "taxon__species").annotate(n=Count("id"))
    for row in rows:
        sp = species_key(row["taxon__genus"], row["taxon__species"])
        for rank, field in BRANCH_OF.items():
            branch = (row[f"taxon__{field}"] or "").lower() if field else ""
            if field and not branch:
                continue
            out[(rank, branch)][sp] += row["n"]
    return out


def recompute_skills(player):
    """Refresh the player's PlayerSkill rows from their answers."""
    now = timezone.now()
    existing = {(s.rank, s.branch.lower()): s for s in PlayerSkill.objects.filter(player=player)}
    available = species_available()
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
        skill.species_total, skill.species_done = cover["species_total"], cover["species_done"]
        skill.lower_bound = round(wilson_lower_bound(ok, n), 4)
        skill.updated_at = now
    PlayerSkill.objects.bulk_create(create)
    PlayerSkill.objects.bulk_update(update, [
        "correct", "judged", "proven", "proven_at", "lower_bound", "required", "covered", "species_total",
        "species_done", "updated_at",
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
def player_report(player):
    """Everything the performance page shows about one player."""
    from . import game

    reliability = game.player_reliability([player.id]).get(player.id) or {
        m: game.default_weight() for m in ("classify", "pair", "odd", "select", "all")
    }
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
    min_shown = game_setting("GAME_REPORT_MIN_JUDGED", 5)
    progressing = sorted(
        (s for s in skills if not s.proven and s.judged >= min_shown and s.branch.lower() in claimed[s.rank]),
        key=lambda s: (-(s.covered / s.required if s.required else 0) * s.lower_bound, s.rank),
    )
    for s in skills:
        s.progress = min(1.0, s.covered / s.required) if s.required else 0.0
        s.accuracy = s.correct / s.judged if s.judged else None

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

    return {
        "summary": game.player_summary(player),
        "rounds": player.game_rounds.filter(finished_at__isnull=False).count(),
        "challenge": game.target_difficulty(player),
        "by_rank": [
            {"rank": r, "classify": reliability["classify"][r], "pair": reliability["pair"][r]}
            for r in RANKS
        ],
        "proven": proven,
        "progressing": progressing[:12],
        "monthly": monthly,
        "recent_rounds": list(
            player.game_rounds.filter(finished_at__isnull=False)
            .annotate(labelled=Count("answers", filter=Q(answers__skipped=False)))
            .order_by("-finished_at")[:10]
        ),
        "per_species": per_species(),
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
    of the label's genus (enough validated images of every species in it, see coverage, and at least
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
# Accuracy bands in the levels' rarity colours: under 50% grey, then four equal steps from 50% up to what an expert
# needs (green, blue, purple, orange), and a proven expert glowing gold like the top level.
EXPERTISE_TIERS = ("uncommon", "rare", "epic", "legendary")


def expertise_bands():
    """[(status, lowest accuracy)] from the top band down, e.g. legendary 0.8, epic 0.7, rare 0.6, uncommon 0.5."""
    top = max(min_accuracy(), EXPERTISE_FLOOR + 0.04)
    step = (top - EXPERTISE_FLOOR) / len(EXPERTISE_TIERS)
    return [(tier, round(EXPERTISE_FLOOR + i * step, 4)) for i, tier in reversed(list(enumerate(EXPERTISE_TIERS)))]


def node_status(skill, min_shown):
    """How a branch is shown: unknown (too few answers yet), common .. legendary by accuracy, or expert."""
    if skill is None or skill.judged < min_shown:
        return "unknown"
    if skill.proven:
        return "expert"
    accuracy = skill.correct / skill.judged
    for tier, lowest in expertise_bands():
        if accuracy >= lowest:
            return tier
    return "common"


def expertise_legend():
    """The tree's legend: (status, label) from lowest to highest."""
    bands = list(reversed(expertise_bands()))
    pct = lambda x: f"{round(x * 100)}%"   # noqa: E731
    legend = [("common", f"under {pct(EXPERTISE_FLOOR)}")]
    for i, (tier, lowest) in enumerate(bands):
        upper = bands[i + 1][1] if i + 1 < len(bands) else None
        legend.append((tier, f"{round(lowest * 100)}\u2013{pct(upper)}" if upper else f"{pct(lowest)}+"))
    return legend + [("expert", "proven expert")]


def expertise_tree(player):
    """
    The taxonomy as subfamily > tribe > genus, each branch with how well the player identifies what is inside it:
    a subfamily shows their tribe calls within it, a tribe their genus calls, a genus their species calls.
    Branches they have not played are counted but not listed one by one.
    """
    min_shown = game_setting("GAME_REPORT_MIN_JUDGED", 5)
    skills = {(s.rank, s.branch.lower()): s for s in skills_for(player)}

    def node(rank, name):
        skill = skills.get((rank, name.lower()))
        return {
            "name": name, "status": node_status(skill, min_shown),
            "judged": skill.judged if skill else 0, "correct": skill.correct if skill else 0,
            "accuracy": (skill.correct / skill.judged) if skill and skill.judged else None,
            "proven": bool(skill and skill.proven),
            "required": skill.required if skill else 0, "covered": skill.covered if skill else 0,
            "species_total": skill.species_total if skill else 0, "species_done": skill.species_done if skill else 0,
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
            t["genera"] = [g for g in genera if g["status"] != "unknown"]
            t["hidden"] = len(genera) - len(t["genera"])
            if t["status"] != "unknown" or t["genera"]:
                sub["tribes"].append(t)
            else:
                sub["hidden"] += 1
        tree.append(sub)
    root = node("subfamily", "")
    return {"root": root, "subfamilies": tree, "min_shown": min_shown, "per_species": per_species(),
            "min_accuracy": min_accuracy(), "legend": expertise_legend(),
            "experts": sum(1 for s in skills.values() if s.proven)}
