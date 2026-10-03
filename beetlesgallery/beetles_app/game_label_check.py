"""
Validated beetles that players get wrong more often than not, in the same way: likely mislabelled (#387).

A validated beetle is the game's answer key, so a wrong label punishes everyone who is right. For each validated
beetle with enough Identification answers, each rank is checked top-down. A rank is suspect when, weighting each
player by their reliability (PlayerScore.rating), more than GAME_LABEL_CHECK_WRONG_SHARE of the answers at that rank
disagree with the label, and most of those (GAME_LABEL_CHECK_AGREE_SHARE) agree with each other on one other name.
Scattered wrong answers mean a hard photo, not a wrong label, and are left alone.

A suspect beetle gets a "The name looks wrong" report from the LABEL_CHECK_USER account, which does what any open
report does: the beetle leaves the game and its answers stop counting. Unlike a player's report, every player's
answers on it are held. Curators settle it on the annotation page like any report: "Label was wrong · fixed"
re-scores everyone against the corrected label; "Label is correct" lets the answers count again, and the check
doesn't raise that label again.
"""
from collections import Counter, defaultdict

from django.db import transaction

from . import game
from .models import Beetles, GameAnswer, GameReport, PlayerScore

LABEL_CHECK_USER = "label-check"
RANKS = ("subfamily", "tribe", "genus", "species")


def _settings():
    return {
        "min": game.game_setting("GAME_LABEL_CHECK_MIN_ANSWERS", 5),
        "wrong": game.game_setting("GAME_LABEL_CHECK_WRONG_SHARE", 0.5),
        "agree": game.game_setting("GAME_LABEL_CHECK_AGREE_SHARE", 0.6),
    }


def _name(answer, rank):
    value = (getattr(answer, rank) or "").strip()
    return f"{answer.genus} {value}".strip() if rank == "species" and value else value


def _truth(taxon, rank):
    value = (getattr(taxon, rank, "") or "").strip()
    return f"{taxon.genus} {value}".strip() if rank == "species" and value else value


def suspects(roi_ids=None):
    """
    [{"roi": Beetles, "rank", "label", "alternative", "answers", "wrong_share", "agree_share"}] for validated
    beetles whose label the players dispute, at the highest rank where they do.
    """
    s = _settings()
    rating = dict(PlayerScore.objects.values_list("player_id", "rating"))
    answers = GameAnswer.objects.filter(mode="classify", skipped=False, is_retry=False, is_check=True,
                                        roi__bbox_is_validated=True, roi__is_deleted=False)
    if roi_ids is not None:
        answers = answers.filter(roi_id__in=list(roi_ids))
    by_roi = defaultdict(dict)
    for a in answers.order_by("answered_at"):
        by_roi[a.roi_id][a.player_id] = a          # a player's latest answer counts once

    confirmed = set(GameReport.objects.filter(reporter__username=LABEL_CHECK_USER, status=GameReport.Status.CONFIRMED)
                    .values_list("roi_id", "label_at_report"))
    open_reports = set(GameReport.objects.filter(roi_id__in=list(by_roi), status=GameReport.Status.OPEN)
                       .values_list("roi_id", flat=True))
    rois = {r.id: r for r in Beetles.objects.filter(id__in=list(by_roi)).select_related("taxon")}
    out = []
    for roi_id, per_player in by_roi.items():
        roi = rois.get(roi_id)
        if roi is None or roi.taxon is None or roi_id in open_reports \
                or (roi_id, roi.depicts_valid_name_id or "") in confirmed:
            continue
        for rank in RANKS:
            truth = _truth(roi.taxon, rank)
            named = [(a, _name(a, rank)) for a in per_player.values() if _name(a, rank)]
            if not truth or len(named) < s["min"]:
                break   # too few answers this deep (or no label at this rank): nothing more to say
            weight = {a.player_id: max(rating.get(a.player_id) or 0.0, 0.05) for a, _ in named}
            total = sum(weight.values())
            wrong = Counter()
            for a, value in named:
                if value.lower() != truth.lower():
                    wrong[value] += weight[a.player_id]
            wrong_total = sum(wrong.values())
            if not wrong_total or wrong_total / total <= s["wrong"]:
                continue   # most agree with the label at this rank: look deeper
            alternative, alt_weight = wrong.most_common(1)[0]
            if alt_weight / wrong_total >= s["agree"]:
                out.append({
                    "roi": roi, "rank": rank, "label": truth, "alternative": alternative,
                    "answers": len(named), "wrong_share": wrong_total / total, "agree_share": alt_weight / wrong_total,
                })
            break   # disputed here; deeper ranks follow from it
    return out


def note(s):
    return (f"Most reliable players disagree at {s['rank']}: {round(s['wrong_share'] * 100)}% of {s['answers']} answers "
            f"(weighted by reliability) don't say {s['label']}, and {round(s['agree_share'] * 100)}% of those say "
            f"{s['alternative']}. Checked automatically; the beetle is out of the game until a curator decides.")


@transaction.atomic
def flag(found):
    """Open a report on each suspect beetle and hold everyone's answers on it. Returns the reports made."""
    from .game_feedback import _hold_filter, _refresh
    from .utils import get_system_user

    checker = get_system_user(LABEL_CHECK_USER)
    reports, players, rois = [], set(), []
    for s in found:
        roi = s["roi"]
        reports.append(GameReport.objects.create(
            roi=roi, reporter=checker, reason=GameReport.Reason.WRONG_LABEL, note=note(s)[:1000],
            was_validated=True, label_at_report=roi.depicts_valid_name_id or "",
        ))
        held = GameAnswer.objects.filter(_hold_filter(roi))
        players.update(held.values_list("player_id", flat=True))
        held.update(score_hold=True)
        rois.append(roi.id)
    if reports:
        _refresh(player_ids=players, roi_ids=rois)
    return reports


def check(dry_run=False):
    """Find the disputed validated beetles and (unless dry_run) flag them. Returns what was found."""
    found = suspects()
    if found and not dry_run:
        flag(found)
    return found
