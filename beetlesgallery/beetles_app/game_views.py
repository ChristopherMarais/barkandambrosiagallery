"""
Pages and JSON endpoints for the Beetle ID game. The game logic is in game.py and
expertise / trusted labels in game_trust.py.

Item payloads carry only an image URL and a bounding box: never the ROI id, its
label, or whether the item is a check, so the player cannot tell which answers are scored.
"""
import contextvars
import csv
import json
import logging
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import game, game_board, game_checked, game_discoveries, game_feedback, game_queue, game_levels, game_rewards, game_scoring, game_tips, game_trust
from . import game_answer_review, game_applied, game_crops
from . import game_grid_ladder, game_warm
from . import game_taxa as taxa_tree
from .areas import ANNOTATE, BOXES, VALIDATE, area_required, has_area
from .models import Beetles, GameAnswer, GameReport, GameRound, ImageLock, LabelReview, PlayerScore, RetroCredit, Taxon
from .predictions import suggestions_for

logger = logging.getLogger(__name__)

MODES = {m.value: m.label for m in GameRound.Mode}
# What players see. (The model keeps its own plain labels; changing those would need a migration.)
GAME_NAMES = {"classify": "Naming", "pair": "Similarity", "odd": "Odd One Out", "select": "Find Them All",
              "mixed": settings.GAME_DISPLAY_NAME}
GAME_TAGLINES = {
    "classify": "One beetle, four guesses: subfamily, tribe, genus, species. Go as deep as you dare.",
    "pair": "Two beetles. How close is the family? From total strangers to the very same species.",
    "odd": "Four beetles, one doesn't belong. Spot it.",
    "select": "Nine beetles. Select every one of a group.",
    "mixed": "Name beetles and spot family ties.",
}
PAIR_CHOICES = [(c.value, c.label) for c in GameAnswer.PairAnswer]
# Family Ties: the ladder from strangers (top) to the same species (bottom). "Not sure" is its own button.
RUNGS = [
    ("different", "Different subfamily", "strangers"),
    ("subfamily", "Same subfamily", "distant kin"),
    ("tribe", "Same tribe", "cousins"),
    ("genus", "Same genus", "siblings"),
    ("species", "Same species", "twins"),
]
MAX_RESPONSE_MS = 60 * 60 * 1000
DISCUSSIONS_URL = "https://github.com/ChristopherMarais/barkandambrosiagallery/discussions/categories/beetle-id-game"

# Reporting a photo from the feed, before answering (a wrong name is reported from the round review instead)
FEED_REPORT_REASONS = [("bad_box", "Box doesn't fit"), ("bad_image", "Bad photo"), ("other", "Something else")]
# A short line under a reason in that menu (#360): a photo must show a good part of the beetle (#498)
FEED_REPORT_HINTS = {"bad_box": "Misses the beetle or frames the label",
                     "bad_image": "Blurry, dark, too little of the beetle, or not a beetle"}


def discussions_url():
    """Where players report bugs and suggest ideas (GitHub Discussions)."""
    return game.game_setting("GAME_DISCUSSIONS_URL", DISCUSSIONS_URL)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@login_required
def game_home(request):
    game.close_idle_rounds(request.user)   # anything they left open counts now
    game_discoveries.find([request.user.id])
    checked, checked_new, checked_change = game_checked.pop_unseen(request.user)
    # this week's top players (#497); while the week is empty, the home says so and shows last week's top three
    board = game_board.board(limit=5)
    rewards = game_rewards.progress(request.user)
    return render(request, "beetles/game_home.html", {
        "checked": checked, "checked_new": checked_new, "checked_change": checked_change,
        "proposals_notice": rewards["proposals"] and _first_sight_of_proposals(request.user),
        "discoveries": game_discoveries.pop_unseen(request.user),
        "score": game_scoring.score_for(request.user),
        "rewards": rewards,
        "board": board, "last_week": [] if board else game_board.last_week_top(),
        "standing": game_board.accuracy_standing(request.user),
        "goal_floor": game_rewards.daily_goal(),
        "games": game_board.mode_stats([request.user.id])[request.user.id],
        # the public address in production (SITE_URL), this server's own when developing
        "share_url": (request.build_absolute_uri(reverse("game_home")) if settings.DEBUG
                      else settings.SITE_URL.rstrip("/") + reverse("game_home")),
        "discussions": discussions_url(),
    })


def _first_sight_of_proposals(player):
    """True the first time a player whose labels now go to the curators sees the game home (the notice shows once)."""
    from .models import GamePreference

    pref, _ = GamePreference.objects.get_or_create(player=player)
    if pref.proposals_notice_seen_at:
        return False
    GamePreference.objects.filter(pk=pref.pk).update(proposals_notice_seen_at=timezone.now())
    return True


@login_required
def game_staff_unlocks(request):
    """
    Superusers only: grant any player any unlock (or all of them), whatever their level, for people who need the
    features and for testing. Stored in GamePreference.granted_perks.
    """
    from .models import GamePreference

    if not request.user.is_superuser:
        raise Http404("Not found")
    users = get_user_model().objects.all()
    if request.method == "POST":
        player = get_object_or_404(users, id=request.POST.get("player"))
        perks = ["all"] if request.POST.get("all") else [p for p in request.POST.getlist("perks") if p in game_levels.PERKS]
        pref, _ = GamePreference.objects.get_or_create(player=player)
        pref.granted_perks = perks
        pref.save(update_fields=["granted_perks", "updated_at"])
        q = (request.POST.get("q") or "").strip()[:50]
        query = urlencode({"q": q})
        return redirect(f"{reverse('game_staff_unlocks')}?{query}#p{player.id}")
    q = (request.GET.get("q") or "").strip()[:50]
    if q:
        users = users.filter(username__icontains=q)
    else:   # people with grants first, then the most recent players
        users = users.filter(id__in=GamePreference.objects.exclude(granted_perks=[]).values("player_id")) | users.filter(
            id__in=GameAnswer.objects.values("player_id"))
    grants = dict(GamePreference.objects.filter(player__in=users).values_list("player_id", "granted_perks"))
    rows = []
    for u in users.distinct().order_by("username")[:100]:
        info = game_levels.describe(*(
            PlayerScore.objects.filter(player=u).values_list("score", "rating").first() or (0.0, 0.0)))
        mine = grants.get(u.id) or []
        rows.append({"user": u, "level": info["level"], "name": info["name"], "all": "all" in mine,
                     "perks": [{"key": k, "title": t, "level": game_levels.perk_level(k), "on": "all" in mine or k in mine,
                                "earned": k in info["perks"]} for k, (t, _) in game_levels.PERKS.items()]})
    return render(request, "beetles/game_staff_unlocks.html", {"rows": rows, "q": q})


@login_required
def game_checked_page(request):
    """Old address of the checked beetles: they now live on the History page."""
    return redirect(reverse("game_history") + "?tab=checked")


HISTORY_PER_PAGE = 20


@login_required
def game_history(request):
    """
    A player's history: every session (batch) they played, newest first, with what it earned and a link to its
    answers; and every beetle a curator checked after they answered it.
    """
    from django.core.paginator import Paginator
    from django.db.models import Count, Q, Sum

    tab = "checked" if request.GET.get("tab") == "checked" else "sessions"
    rounds = (
        GameRound.objects.filter(player=request.user, finished_at__isnull=False)
        .annotate(labelled=Count("answers", filter=Q(answers__skipped=False), distinct=True),
                  identified=Count("answers", filter=Q(answers__skipped=False, answers__mode="classify"), distinct=True),
                  compared=Count("answers", filter=Q(answers__skipped=False, answers__mode="pair"), distinct=True),
                  spotted=Count("answers", filter=Q(answers__skipped=False, answers__mode="odd"), distinct=True),
                  selected=Count("answers", filter=Q(answers__skipped=False, answers__mode="select"), distinct=True),
                  points=Sum("answers__points__points"))
        .filter(labelled__gt=0).order_by("-finished_at")
    )
    checked = game_checked.items(request.user, limit=500)
    # points that came later, new since they last looked: a small pop on each, and beetles if they gained (#425)
    new_gain = sum(c["change"] for c in checked if c["new"] and c["change"] > 0)
    if tab == "checked" and any(c["new"] for c in checked):   # seen once the Checked later tab is open
        RetroCredit.objects.filter(player=request.user, seen_at__isnull=True).update(seen_at=timezone.now())
    sessions = Paginator(rounds, HISTORY_PER_PAGE).get_page(request.GET.get("page") if tab == "sessions" else 1)
    for r in sessions:   # which games a session was: one by name, or how many
        played = [name for name, n in (("Naming", r.identified), ("Similarity", r.compared),
                                       ("Odd One Out", r.spotted), ("Find Them All", r.selected)) if n]
        r.games_label = played[0] if len(played) == 1 else f"{len(played)} games"
    checked_page = Paginator(checked, HISTORY_PER_PAGE).get_page(request.GET.get("page") if tab == "checked" else 1)
    return render(request, "beetles/game_history.html", {
        "tab": tab, "sessions": sessions, "checked": checked_page, "checked_total": len(checked),
        "new_gain": round(new_gain, 1), "new_checked": sum(1 for c in checked if c["new"]),
    })


@login_required
def game_leaderboard(request):
    """The full leaderboard: by score, accuracy or beetles seen, this week (default), this month or all time,
    searchable, and per branch."""
    sort = {"accuracy": "identification"}.get(request.GET.get("sort"), request.GET.get("sort"))   # old links
    sort = sort if sort in game_board.SORTS else "score"
    period = request.GET.get("period") if request.GET.get("period") in game_board.PERIODS else "week"
    q = (request.GET.get("q") or "").strip()[:50]
    branch_rank = request.GET.get("rank") if request.GET.get("rank") in game_board.BRANCH_SKILL else ""
    branch_value = (request.GET.get("branch") or "").strip()[:100]
    return render(request, "beetles/game_leaderboard.html", {
        "rows": game_board.board(sort=sort, period=period, q=q, limit=100),
        "branch_rows": game_board.branch_board(branch_rank, branch_value) if branch_rank and branch_value else None,
        "sort": sort, "period": period, "q": q, "sorts": game_board.SORTS, "periods": game_board.PERIODS,
        "resets_at": game_board.period_end(period),
        "last_week": game_board.last_week_top(),
        "branch_rank": branch_rank, "branch_value": branch_value,
    })


@login_required
def game_profile(request, user_id):
    """A player's public game profile."""
    player = get_object_or_404(get_user_model(), id=user_id)
    return render(request, "beetles/game_profile.html", {
        "player": player, "is_self": player == request.user, "p": game_board.profile(player),
    })


@login_required
def game_unlocks(request):
    """The level ladder, what each level unlocks, and the focus a player can choose with what they have unlocked."""
    from .models import GamePreference

    info = game_levels.for_player(request.user)
    pref, _ = GamePreference.objects.get_or_create(player=request.user)
    error = ""
    # only the focus form posts here: a post without its fields (say, the removed Leaderboards card) changes nothing
    if request.method == "POST" and "focus_rank" not in request.POST:
        return redirect("game_unlocks")
    if request.method == "POST":
        rank = request.POST.get("focus_rank", "")
        value = (request.POST.get("focus_value") or "").strip()[:100]
        if not rank:
            pref.focus_rank, pref.focus_value = "", ""
            pref.save()
            return redirect("game_unlocks")
        if rank not in game_levels.FOCUS_PERK or game_levels.FOCUS_PERK[rank] not in info["perks"]:
            error = "That focus is not unlocked yet."
        elif not value or not Taxon.objects.filter(game.COMPLETE_TAXON, **{f"{rank}__iexact": value}).exists():
            error = f"Choose a {rank} from the list."
        else:
            canonical = Taxon.objects.filter(**{f"{rank}__iexact": value}).values_list(rank, flat=True).first()
            pref.focus_rank, pref.focus_value = rank, canonical
            pref.save()
            return redirect("game_unlocks")
    return render(request, "beetles/game_unlocks.html", {
        "info": info, "ladder": game_levels.table(), "pref": pref, "error": error,
        "focus_active": game.player_focus(request.user),
        "focus_ranks": [(r, label, game_levels.FOCUS_PERK[r] in info["perks"]) for r, label in
                        (("subfamily", "Subfamily"), ("tribe", "Tribe"), ("genus", "Genus"))],
        "proposal_level": game_levels.proposal_level(),
        "per_species": game_trust.per_species(), "children_share": game_trust.children_share(),
        "trust_accuracy": game_trust.min_accuracy(),
        "min_experts": game.game_setting("GAME_AUTO_APPLY_MIN_EXPERTS", 2),
        "ranks": game_levels.rank_for(request.user),
        # opened from the "your labels now go to the curators" notice: the box lights up briefly, then looks normal
        "labels_new": bool(info["proposals"]) and request.GET.get("new") == "labels",
    })


@login_required
def game_expertise(request, user_id=None):
    """The taxonomy tree coloured by how well the player identifies each branch."""
    player = request.user if user_id is None else get_object_or_404(get_user_model(), id=user_id)
    return render(request, "beetles/game_expertise.html", {
        "player": player, "is_self": player == request.user, "tree": game_trust.expertise_tree(player),
        "info": game_levels.for_player(player),
    })


def _weight_label(weight):
    return int(weight) if float(weight).is_integer() else weight


def _classify_examples():
    """The worked examples on the How it works page, with today's settings."""
    w = game_scoring.classify_weight()

    def points(right, named=4):
        results = {r: i < right for i, r in enumerate(game.RANKS[:named])}
        return sum(game_scoring.classify_points(results).values()) * w

    nums = {"overreach": points(3), "genus": points(3, named=3), "species": points(4)}
    return {k: _weight_label(round(v, 1)) for k, v in nums.items()}


@login_required
def game_how(request):
    """How the game works and how it is scored, in plain words."""
    return render(request, "beetles/game_how.html", {
        "discussions": discussions_url(), "levels": game_levels.table(), "goal_floor": game_rewards.daily_goal(),
        "proposal_level": game_levels.proposal_level(),
        "min_experts": game.game_setting("GAME_AUTO_APPLY_MIN_EXPERTS", 2),
        "per_species": game_trust.per_species(), "children_share": game_trust.children_share(),
        "trust_accuracy": game_trust.min_accuracy(),
        "classify_weight": _weight_label(game_scoring.classify_weight()),
        "rank_points": {r: p * game_scoring.classify_weight() for r, p in game_scoring.RANK_POINTS.items()},
        "classify_examples": _classify_examples(), "pair_points": [
            (game_scoring.DEPTH_NAME[d], p) for d, p in sorted(game_scoring.PAIR_POINTS.items())],
        "confidence": round(game_scoring.confidence() * 100),
        "wrong_cost": _weight_label(round(game_scoring.wrong_cost(), 1)),   # what a wrong claim costs, × its points
        "cap": int(game.game_setting("GAME_POINTS_CONSENSUS_CAP", 0.6) * 100),
        "unsure": game.game_setting("GAME_POINTS_UNSURE", 0.25),
        "rank_steps": game_levels.rank_steps(), "ranks_all_level": game_levels.RANKS_ALL_FROM_LEVEL,
        "odd_level": game_levels.game_level("odd"), "identify_level": game_levels.game_level("classify"),
        "select_level": game_levels.game_level("select"),
        "odd_weight": _weight_label(game.game_setting("GAME_POINTS_ODD_WEIGHT", 1.5)),
        "odd_skip": game.game_setting("GAME_POINTS_ODD_SKIP", 0.25),
        "difficulty_spread": round(game_scoring.difficulty_spread() * 100),
        "reveal_hours": _weight_label(game.game_setting("GAME_REVEAL_COOLDOWN_HOURS", 2)),
        "recall_days": _weight_label(game.game_setting("GAME_EXPERTISE_RECALL_DAYS", 30)),
    })


@login_required
def game_play(request, mode):
    if mode not in MODES:
        raise Http404("Unknown game mode")
    return render(request, "beetles/game_play.html", {
        "discussions": discussions_url(),
        # short, one line each, for the little report menu in the full-image view. No "Wrong name" here: that is
        # for after answering (the round review), so the menu never hints at the answer.
        "report_reasons": [(value, label, FEED_REPORT_HINTS.get(value, "")) for value, label in FEED_REPORT_REASONS],
        "mode": mode,
        "mode_label": GAME_NAMES[mode],
        "break_minutes": game.game_setting("GAME_BREAK_NUDGE_MINUTES", 60),   # 0 turns the break nudge off
        "ranks": [(r, r.capitalize()) for r in game.RANKS],
        "rungs": RUNGS,
        "last_review": _last_review_url(request.user),
        **_onboarding(request),
    })


def _last_review_url(player):
    """Where Back finds the review of the player's latest answer after a reload ("" before their first answer)."""
    last = GameAnswer.objects.filter(player=player).order_by("-answered_at").values_list("round_id", "index").first()
    return reverse("game_past_review", args=last) if last else ""


def _onboarding(request):
    """
    Help for new players: a walkthrough of every button the first time they play (or when asked for with
    ?tour=1), and for their first few days a reminder of how to report a photo that looks wrong.
    """
    played = GameAnswer.objects.filter(player=request.user, skipped=False).exists()
    days = len(game_rewards.active_days(request.user)) if played else 0
    return {
        "tour": request.GET.get("tour") == "1" or not played,
        "report_tip": days <= game.game_setting("GAME_REPORT_TIP_DAYS", 3),
    }


def _render_report(request, player):
    since = timezone.now() - timedelta(days=30)
    recent = GameAnswer.objects.filter(player=player, answered_at__gte=since).select_related("points")
    return render(request, "beetles/game_report.html", {
        "player": player,
        "is_self": player == request.user,
        "report": game_trust.player_report(player),
        "losses": game_feedback.loss_summary(recent),
    })


@login_required
def game_report(request):
    return _render_report(request, request.user)


@staff_member_required
def game_player_report(request, user_id):
    return _render_report(request, get_object_or_404(get_user_model(), id=user_id))


@login_required
def game_round_review(request, round_id):
    """Feedback on a finished round: each answer next to what the database says."""
    rnd = get_object_or_404(GameRound, id=round_id)
    if rnd.player != request.user and not request.user.is_staff:
        raise Http404("No such round")
    if rnd.finished_at is None:
        return redirect("game_play", mode=rnd.mode)
    feedback = game_feedback.round_feedback(rnd)
    return render(request, "beetles/game_round_review.html", {
        "round": rnd,
        "feedback": feedback,
        "feedback_json": feedback["items"],
        "is_self": rnd.player == request.user,
        "reasons": GameReport.Reason.choices,
        # curators who may validate can open a verified beetle in the annotator, to un-validate or correct it (#380)
        "can_revoke": has_area(request.user, BOXES) and has_area(request.user, VALIDATE),
    })


@login_required
@require_POST
def game_report_roi(request):
    """A player reports an ROI from one of their finished rounds as looking wrong."""
    body = _json_body(request) or {}
    rnd = GameRound.objects.filter(id=body.get("round"), player=request.user).first() if _is_uuid(body.get("round")) else None
    if rnd is None or rnd.finished_at is None:
        return JsonResponse({"error": "You can report images from your finished rounds."}, status=404)
    answer = rnd.answers.filter(index=body.get("index")).first() if isinstance(body.get("index"), int) else None
    if answer is None:
        return JsonResponse({"error": "Unknown item."}, status=404)
    roi_id = str(body.get("roi") or "")
    if roi_id not in {str(answer.roi_id), str(answer.roi_b_id), *map(str, answer.tiles or [])}:
        return JsonResponse({"error": "Unknown image."}, status=400)
    reason = body.get("reason")
    if reason not in GameReport.Reason.values:
        return JsonResponse({"error": "Please choose a reason."}, status=400)
    roi = answer.roi if str(answer.roi_id) == roi_id else (
        answer.roi_b if str(answer.roi_b_id) == roi_id else get_object_or_404(Beetles, id=roi_id))
    report = game_feedback.create_report(request.user, roi, reason, str(body.get("note") or ""), answer)
    return JsonResponse({"status": report.status, "reason": report.get_reason_display()})


@login_required
@require_POST
def game_report_item(request):
    """
    A player reports a photo straight from the feed (the cog in the full-image view): the beetle goes to the
    curators on the Image Annotation page and stays out of the game until they deal with it.
    Body: {"round", "index", "image": 0 or 1 (A or B, as shown; in a grid the beetle's place in it), "reason",
    "note"}, and "photo": n to report the beetle's n-th other photo (the "More photos" gallery, 1 = its first) instead
    of the one in play. In a grid the player carries on without the flagged photo: the reply lists the places flagged so
    far ("flagged") and says whether that ends the grid ("end", see _grid_over).
    """
    body = _json_body(request) or {}
    rnd = GameRound.objects.filter(id=body.get("round"), player=request.user).first() if _is_uuid(body.get("round")) else None
    index = body.get("index")
    if rnd is None or not isinstance(index, int) or not 0 <= index < len(rnd.items):
        return JsonResponse({"error": "Unknown beetle."}, status=404)
    rois = _item_rois(rnd.items[index])
    if rois is None:
        return JsonResponse({"error": "Unknown beetle."}, status=404)
    a, b = rois
    if rnd.items[index].get("tiles"):
        shown = _item_tiles(rnd.items[index])
    else:
        shown = [a] if b is None else ([b, a] if rnd.items[index].get("flip") else [a, b])
    which = body.get("image", 0)
    if not isinstance(which, int) or not 0 <= which < len(shown):
        return JsonResponse({"error": "Unknown image."}, status=400)
    reason = body.get("reason")
    if reason not in dict(FEED_REPORT_REASONS):
        return JsonResponse({"error": "Please choose a reason."}, status=400)
    roi = shown[which]
    photo = body.get("photo") or 0
    if photo:
        others = game.specimen_photos(roi) if isinstance(photo, int) else []
        if not 1 <= photo <= len(others):
            return JsonResponse({"error": "Unknown photo."}, status=400)
        roi = others[photo - 1]
    report = game_feedback.create_report(request.user, roi, reason, str(body.get("note") or ""))
    out = {"status": report.status, "reason": report.get_reason_display()}
    if rnd.items[index].get("tiles"):
        out["flagged"] = _flagged_places(shown, request.user)
        out["end"] = _grid_over(rnd.items[index], _item_mode(rnd, index), out["flagged"])
    return JsonResponse(out)


def _is_uuid(value):
    import uuid as _uuid

    try:
        _uuid.UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Round API
# ---------------------------------------------------------------------------
def _json_body(request):
    try:
        body = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _box(roi):
    return [roi.bbox_x, roi.bbox_y, roi.bbox_width, roi.bbox_height]


# The Beetles rows a feed request has looked up, by id (None: not found). Several steps of one request look up the
# same item's beetles (its place in the batch, its photos, the next ones to prefetch), so each is fetched once (#542).
_rows = contextvars.ContextVar("game_rows", default=None)


def _beetles(ids):
    """{id: Beetles row} for these ids (strings) that exist, with their photo and taxon; remembered in a timed request."""
    memo = _rows.get()
    if memo is None:
        return {str(k): v for k, v in Beetles.objects.select_related("image_asset", "taxon").in_bulk(ids).items()}
    wanted = [str(i) for i in ids if str(i) not in memo]
    if wanted:
        found = {str(k): v for k, v in Beetles.objects.select_related("image_asset", "taxon").in_bulk(wanted).items()}
        memo.update({i: found.get(i) for i in wanted})
    return {str(i): memo[str(i)] for i in ids if memo[str(i)] is not None}


def _item_tiles(item):
    """The Beetles rows of a grid item (Odd One Out, Select all), in the order shown. None if any is gone."""
    ids = item.get("tiles") or []
    found = _beetles(ids)
    if not ids or any(i not in found or not found[i].has_bbox() for i in ids):
        return None
    return [found[i] for i in ids]


def _flagged_places(tiles, player):
    """The places in a grid whose photo this player has an open report on: the ones they flagged (#489)."""
    reported = set(GameReport.objects.filter(reporter=player, status=GameReport.Status.OPEN,
                                             roi_id__in=[t.id for t in tiles]).values_list("roi_id", flat=True))
    return [i for i, t in enumerate(tiles) if t.id in reported]


def _grid_over(item, mode, flagged):
    """
    Whether flags end a grid, unscored like a reported photo (#489): once half its photos are flagged, or in Odd One Out
    an odd one is, since without it there are not enough to find.
    """
    tiles = item.get("tiles") or []
    odds = {tiles.index(t) for t in _odd_ones(item) if t in tiles} if mode == GameRound.Mode.ODD else set()
    return 2 * len(flagged) >= len(tiles) or bool(odds & set(flagged))


def _odd_ones(item):
    """An Odd One Out item's odd ones (ids): ``odds``, or just ``a`` for a grid built before several (#540)."""
    return item.get("odds") or [item["a"]]


def _item_rois(item):
    """
    The Beetles rows of a round item, (a, b) with b None for classify and Odd One Out (whose a is the odd one, and
    whose other beetles must all still be there too). None if any is gone.
    """
    if item.get("tiles"):
        tiles = _item_tiles(item)
        return None if tiles is None else (next(t for t in tiles if str(t.id) == item["a"]), None)
    ids = [item["a"]] + ([item["b"]] if item.get("b") else [])
    found = _beetles(ids)
    if any(i not in found or not found[i].has_bbox() for i in ids):
        return None
    return found[item["a"]], found.get(item.get("b"))


def _next_index(rnd, start=None):
    """First item at or after ``start`` (default: after the last answer) whose ROIs still exist."""
    if start is None:
        last = rnd.answers.aggregate(m=Max("index"))["m"]
        start = 0 if last is None else last + 1
    for i in range(start, len(rnd.items)):
        if _item_rois(rnd.items[i]) is not None:
            return i
    return None


def _shown_rois(item):
    """The Beetles rows of an item in the order shown (A then B, or the grid's tiles). None if any is gone."""
    if item.get("tiles"):
        return _item_tiles(item)
    rois = _item_rois(item)
    if rois is None:
        return None
    a, b = rois
    return [a] if b is None else ([b, a] if item.get("flip") else [a, b])


def _crop_url(rnd, index, image, roi, size):
    """Where the feed gets a beetle's crop (game_crop); ``v`` changes with the box, so the browser may keep it for good."""
    url = reverse("game_crop", args=[rnd.id, index, image, size])
    return f"{url}?v={game_crops.crop_key(roi)}"


def _item_images(rnd, index, extras=False):
    """
    The photos of one item: the whole photo ("url", for the whole-photo view), its box, and the crop the feed shows
    ("small" at once, "large" swapped in when it arrives; #494). With ``extras``, each also says how many other photos
    there are of that same beetle ("more"), and lists them ("photos") once the player has unlocked them
    (game_levels.SPECIMEN_PHOTOS). Odd One Out shows its beetles in a grid, each on its own (no other photos of them).
    """
    rois = _shown_rois(rnd.items[index])
    images = [{"url": r.display_url, "box": _box(r), "small": _crop_url(rnd, index, i, r, "small"),
               "large": _crop_url(rnd, index, i, r, "large")} for i, r in enumerate(rois)]
    if rnd.items[index].get("tiles"):
        return images
    if extras:
        unlocked = game_levels.SPECIMEN_PHOTOS in game_levels.for_player(rnd.player)["perks"]
        for image, roi in zip(images, rois):
            more = game.specimen_photos(roi)
            if more:
                image["more"] = len(more)
                if unlocked:
                    image["photos"] = [{"url": m.display_url, "box": _box(m), "aspect": m.aspect or ""} for m in more]
    return images


def _item_mode(rnd, index):
    """The game of one item: a mixed feed stores it per item, a single-game round has one for all."""
    return rnd.items[index].get("mode") or rnd.mode


def _item_payload(rnd, index):
    game_grid_ladder.restep(rnd, index)   # grids picked before the player's step moved are built again at the new one
    payload = {
        "index": index,
        "mode": _item_mode(rnd, index),
        "position": rnd.answers.count() + 1,
        "total": len(rnd.items),
        "images": _item_images(rnd, index, extras=True),
        "prefetch": [],
    }
    if any(im.get("more") for im in payload["images"]):
        payload["more_level"] = game_levels.perk_level(game_levels.SPECIMEN_PHOTOS)
    if rnd.items[index].get("retry"):
        payload["again"] = True   # a beetle they got wrong before, shown again so they can learn it
    if payload["mode"] in (GameRound.Mode.ODD, GameRound.Mode.SELECT):
        grid = rnd.items[index]
        # Odd One Out: all but one share a name at this rank; the grid's size, and the player's step when it was built
        payload.update(rank=grid["rank"], size=len(grid["tiles"]), step=grid.get("step"))
        if payload["mode"] == GameRound.Mode.ODD:   # how many odd ones to find (#540); which ones stays on the server
            payload["odds"] = len(_odd_ones(grid))
        if payload["mode"] == GameRound.Mode.SELECT:   # "Tap every <target>"
            payload["target"] = grid["group"][grid["rank"]]
    if payload["mode"] == GameRound.Mode.CLASSIFY:
        others = (GameAnswer.objects.filter(roi_id=rnd.items[index]["a"], skipped=False)
                  .exclude(player=rnd.player).values("player").distinct().count())
        if others:
            payload["others"] = others   # how many other players named it (not what they said, until you answer)
    # Let the browser start downloading the next crops while this item is answered: the next beetle's small then
    # large ones, and the small ones of the beetle after it (#542). From the middle of a batch on, the next batch is
    # built on the worker; on its last item, if it isn't there yet, it is built now so the batch can end without a
    # wait, and its first beetle comes next (#494).
    if rnd.finished_at is None and 2 * index >= len(rnd.items) - 1:
        build_ahead_later(rnd)
    coming = []
    following = _next_index(rnd, index + 1)
    if following is not None:
        coming.append((rnd, following))
        after = _next_index(rnd, following + 1)
        if after is not None:
            coming.append((rnd, after))
    elif rnd.finished_at is None:
        ahead = _batch_ahead(rnd) or (None if _ahead_on_the_worker(rnd) else _build_ahead(rnd, index))
        first = _next_index(ahead, 0) if ahead else None
        if first is not None:
            coming.append((ahead, first))
    for n, (batch, at) in enumerate(coming):
        upcoming = _item_images(batch, at)
        payload["prefetch"] += [im["small"] for im in upcoming] + ([im["large"] for im in upcoming] if n == 0 else [])
    return payload


# ---------------------------------------------------------------------------
# Speed (#494): the next batch is built while the last item of a batch is on screen, the end of a batch is
# refreshed on the worker, and each beetle comes as a crop cut on the server.
# ---------------------------------------------------------------------------
def _batch_ahead(rnd):
    """The batch built to follow ``rnd`` (_build_ahead), if there is one: a later unfinished one with no answers."""
    return (GameRound.objects.filter(player_id=rnd.player_id, mode=rnd.mode, finished_at__isnull=True,
                                     started_at__gt=rnd.started_at, answers__isnull=True)
            .exclude(id=rnd.id).order_by("started_at").first())


def _build_ahead(rnd, index):
    """
    Build the batch that follows ``rnd`` while its last item (``index``) is still being answered, so the feed can
    prefetch its first beetle and the batch can end without a wait. Beetles still to come in ``rnd`` are left out of
    it. None when there is nothing new to build.
    """
    fresh = game.start_round(rnd.player, rnd.mode, fresh_only=True)
    if fresh is None:
        return None
    # built from the middle of a batch (#542): the rest of it, and every other photo of those specimens, will have
    # been named in its reviews by then (#541)
    coming = {str(i) for i in game.same_specimen(set().union(*(game._item_ids(item) for item in rnd.items[index:])))}
    items = [item for item in fresh.items if coming.isdisjoint(game._item_ids(item))]
    if not items:
        fresh.delete()
        return None
    if len(items) < len(fresh.items):
        fresh.items = items
        fresh.save(update_fields=["items"])
    if fresh.notice:   # not saved with the batch: kept until the feed reaches it (_next_batch)
        from django.core.cache import cache

        cache.set(AHEAD_NOTICE.format(fresh.id), fresh.notice, 60 * 60 * 24)
    return fresh


AHEAD_LOCK = "game:ahead-building:{}"   # a batch's next batch is queued for, or being built on, the worker
AHEAD_LOCK_SECONDS = 120                 # after which the feed builds it itself (a worker that's down or far behind)


def _ahead_on_the_worker(rnd):
    return bool(cache.get(AHEAD_LOCK.format(rnd.id)))


def build_ahead_later(rnd):
    """
    From the middle of a batch on: have the worker build the batch that follows it (build_ahead_now), once, so it is
    ready well before the last item instead of being built while the player waits on it (#542). Where game work stays
    in the request, or the queue can't be reached, the last item builds it as before.
    """
    if not game.game_setting("GAME_RECOMPUTE_IN_BACKGROUND", False) or _ahead_on_the_worker(rnd):
        return
    from .tasks import build_game_batch_ahead_task

    def queue():
        if not cache.add(AHEAD_LOCK.format(rnd.id), 1, AHEAD_LOCK_SECONDS):
            return
        if _batch_ahead(rnd) is not None:
            cache.delete(AHEAD_LOCK.format(rnd.id))
            return
        try:
            build_game_batch_ahead_task.apply_async(args=[str(rnd.id)], retry=False)
        except Exception:
            cache.delete(AHEAD_LOCK.format(rnd.id))
            logger.info("Batch after %s not queued: it is built on its last item", rnd.id)

    transaction.on_commit(queue)


def build_ahead_now(round_id):
    """The worker's part of build_ahead_later: the next batch, unless the feed has moved on or has one already."""
    try:
        rnd = GameRound.objects.filter(id=round_id).first()
        if rnd is None or rnd.finished_at is not None or _batch_ahead(rnd) is not None:
            return None
        index = _next_index(rnd)
        if index is None:
            return None
        return _build_ahead(rnd, index)
    finally:
        cache.delete(AHEAD_LOCK.format(round_id))


def _drop_ahead(rnd):
    """A batch built ahead under rules that no longer apply (a new level opened a game): it goes, unanswered."""
    ahead = _batch_ahead(rnd)
    if ahead is not None:
        ahead.delete()


AHEAD_NOTICE = "game:ahead-notice:{}"


def _next_batch(rnd):
    """
    The batch that carries the feed on after ``rnd``, and its first item: the one built ahead, or a new one. Either
    has its ``notice`` (game.start_round): a batch built ahead gets it back from the cache, where _build_ahead left it.
    """
    ahead = _batch_ahead(rnd)
    if ahead is not None:
        first = _next_index(ahead, 0)
        if first is not None:
            from django.core.cache import cache

            ahead.notice = cache.get(AHEAD_NOTICE.format(ahead.id)) or ""
            return ahead, first
        ahead.delete()   # its beetles have gone since
    fresh = game.start_round(rnd.player, rnd.mode, fresh_only=True)
    return fresh, (_next_index(fresh, 0) if fresh else None)


LEVEL_SHOWN = "game:level-shown:{}"


def _late_level_events(player, before, events, level):
    """
    A level reached through the work done after a batch (on the worker, between two answers) raises no event on its
    own: play_events compares the moments just before and after one answer. So the feed remembers the last level it
    showed each player, and announces a higher one on the next answer (toast and gold confetti), once. Without the
    cache nothing is announced twice: a level it doesn't know of is simply remembered.
    """
    from django.core.cache import cache

    key = LEVEL_SHOWN.format(player.pk)
    shown = cache.get(key)
    cache.set(key, level, 60 * 60 * 24 * 30)
    if shown is None or level <= shown or any(e["kind"] == "level" for e in events):
        return []
    # what the player had at the level last shown, plus any unlocks granted or kept outside the levels
    extra = set(before["perks"]) - game_levels.unlocked_perks(before["level"] - 1)
    then = dict(before, level=shown, perks=sorted(game_levels.unlocked_perks(shown - 1) | extra))
    return [e for e in game_rewards.play_events(player, then) if e["kind"] in ("level", "proposals")]


def _timed(view):
    """Log how long a feed request took and what it built (one line), so lag shows in the server logs (#494)."""
    @wraps(view)
    def timed(request, *args, **kwargs):
        stats = {"batches": 0, "items": 0, "crops": 0}
        token, rows = game_crops.built.set(stats), _rows.set({})
        started = time.perf_counter()
        try:
            return view(request, *args, **kwargs)
        finally:
            _rows.reset(rows)
            game_crops.built.reset(token)
            logger.info("%s took %d ms: %d new batch(es), %d item(s), %d crop(s) queued", view.__name__,
                        (time.perf_counter() - started) * 1000, stats["batches"], stats["items"], stats["crops"])
    return timed


@login_required
@require_GET
def game_crop(request, round_id, index, image, size):
    """
    One beetle of the player's own batch, as the crop the feed shows (game_crops), cut on first request. The URL names
    the batch, the item and the photo, never the beetle, so it gives away nothing the feed doesn't show.
    """
    size = game_crops.size_name(size)   # from here on, SIZES' own key: the request's string never reaches a path
    if size is None:
        raise Http404("Unknown size.")
    rnd = get_object_or_404(GameRound, id=round_id, player=request.user)
    rois = _shown_rois(rnd.items[index]) if index < len(rnd.items) else None
    if not rois or image >= len(rois) or not game.playable_rois().filter(pk=rois[image].pk).exists():
        raise Http404("Unknown beetle.")
    path = game_crops.ensure(rois[image], size)
    if path is None:
        raise Http404("No crop.")
    response = FileResponse(open(path, "rb"), content_type=game_crops.file_format()[2])
    # the name changes with the box (?v=), so the browser can keep it for good; private, as it needs a login
    response["Cache-Control"] = "private, max-age=31536000, immutable"
    return response


def _finish(rnd):
    game.finish_round(rnd)
    summary = game.player_summary(rnd.player)
    summary["round_labelled"] = rnd.answers.filter(skipped=False).count()
    return {"done": True, "summary": summary, "review_url": reverse("game_round_review", args=[rnd.id]),
            "caught_up": game.nothing_to_play(rnd.player, rnd.mode)}   # why, and whether clearing the focus gives more


@login_required
@require_POST
@_timed
def game_start(request):
    body = _json_body(request)
    mode = (body or {}).get("mode")
    if mode not in MODES:
        return JsonResponse({"error": "Unknown game mode."}, status=400)

    # Pick up where the player left off (e.g. after a reload) before starting afresh. "fresh" (after changing the
    # game or focus) closes what is left of the current batch, and any built ahead of it under the old choice, so the
    # new choice applies straight away; the closing is done on the worker (#542).
    rnd = game.resumable_round(request.user, mode)
    fresh = bool((body or {}).get("fresh"))
    index = _next_index(rnd) if rnd and not fresh else None
    if index is None:
        if rnd is not None:
            _drop_ahead(rnd)
            game.finish_round_later(rnd)
        # after a switch of game, the batch the worker built for it while they played (game_warm), if there is one
        rnd = (fresh and game_warm.take(request.user, mode)) or game.start_round(request.user, mode)
        index = _next_index(rnd, 0) if rnd else None
    if index is None:   # nothing in any of their games: say why (no beetles yet, all seen, their focus, ...)
        return JsonResponse({"error": game.nothing_to_play(request.user, mode)["text"]}, status=404)
    focus = game.player_focus(request.user)
    return JsonResponse({
        "round": str(rnd.id), "item": _item_payload(rnd, index), "chip": _chip(request.user),
        "focus": f"{focus[0].capitalize()}: {focus[1]}" if focus else "",
        "prefs": _prefs(request.user),
        # the game they chose has nothing for them right now, so the feed plays their other games (game.start_round)
        "notice": getattr(rnd, "notice", ""),
    })


def _prefs(player):
    """The game and focus choices for the feed's toolbar: what is chosen, and what is unlocked at which level."""
    from .models import GamePreference

    info = game_levels.for_player(player)
    pref = GamePreference.objects.filter(player=player).first()
    focus = game.player_focus(player)
    available = game_levels.games(info["perks"])
    return {
        "level": info["level"],
        "play_mode": game.play_mode(player, info),
        "games": [{"key": g, "name": game_levels.GAME_NAMES[g], "unlocked": g in available,
                   "level": game_levels.game_level(g)} for g in game_levels.GAMES],
        "choose_game": game_levels.CHOOSE_GAME in info["perks"],
        "light": game_levels.LIGHT in info["perks"],
        "choose_game_level": game_levels.perk_level(game_levels.CHOOSE_GAME),
        "focus": {"rank": focus[0], "value": focus[1]} if focus else None,
        "focus_ranks": [
            {"rank": r, "unlocked": perk in info["perks"], "level": game_levels.perk_level(perk)}
            for r, perk in game_levels.FOCUS_PERK.items()
        ],
        "saved_focus": {"rank": pref.focus_rank, "value": pref.focus_value} if pref and pref.focus_rank else None,
    }


@login_required
@require_POST
@_timed
def game_prefs(request):
    """
    Change the game (All modes / Similarity / Odd One Out / Find Them All / Naming) or the focus from the feed. Each only if
    unlocked. Body: {"play_mode": ...} and/or {"focus_rank": ..., "focus_value": ...} (focus_rank "" clears the focus).
    """
    from .models import GamePreference

    body = _json_body(request) or {}
    info = game_levels.for_player(request.user)
    pref, _ = GamePreference.objects.get_or_create(player=request.user)
    if "play_mode" in body:
        mode = body.get("play_mode")
        if mode not in GamePreference.PlayMode.values:
            return JsonResponse({"error": "Unknown game."}, status=400)
        if mode in game_levels.GAME_PERK and mode not in game_levels.games(info["perks"]):
            return JsonResponse({"error": f"{game_levels.GAME_NAMES[mode]} unlocks at level {game_levels.game_level(mode)}."},
                                status=403)
        if mode != "pair" and game_levels.CHOOSE_GAME not in info["perks"]:
            level = game_levels.perk_level(game_levels.CHOOSE_GAME)
            return JsonResponse({"error": f"Choosing your game unlocks at level {level}."}, status=403)
        pref.play_mode = mode
    if "focus_rank" in body:
        rank = body.get("focus_rank") or ""
        value = str(body.get("focus_value") or "").strip()[:100]
        if not rank:
            pref.focus_rank, pref.focus_value = "", ""
        elif rank not in game_levels.FOCUS_PERK:
            return JsonResponse({"error": "Unknown focus."}, status=400)
        elif game_levels.FOCUS_PERK[rank] not in info["perks"]:
            level = game_levels.perk_level(game_levels.FOCUS_PERK[rank])
            return JsonResponse({"error": f"Focus on a {rank} unlocks at level {level}."}, status=403)
        else:
            canonical = (Taxon.objects.filter(game.COMPLETE_TAXON, **{f"{rank}__iexact": value})
                         .values_list(rank, flat=True).first()) if value else None
            if not canonical:
                return JsonResponse({"error": f"Choose a {rank} from the list."}, status=400)
            pref.focus_rank, pref.focus_value = rank, canonical
    pref.save()
    return JsonResponse({"prefs": _prefs(request.user)})


@login_required
@require_POST
def game_warm_others(request):
    """
    The feed is up: have the worker build a batch for each game the player could switch to (game_warm), so switching
    rarely waits. Body: {"mode": the page's game}. Cheap when they are built already.
    """
    mode = (_json_body(request) or {}).get("mode")
    return JsonResponse({"queued": game_warm.warm_later(request.user, mode)})


def _chip(player):
    """The small counters in the feed's header: today's beetles against the daily goal, and the day streak."""
    state = game_rewards.progress(player)
    chip = {k: state[k] for k in ("today", "goal", "goal_met", "streak", "level", "score", "next_at", "level_progress",
                                  "rank", "rank_next")}
    chip["level_icon"] = game_levels.level_icon(state["level"])
    return chip


def _clean_classification(body):
    """The rank values from a classify answer, or None if they don't match the taxonomy."""
    answer = {r: str(body.get(r) or "").strip()[:100] for r in game.RANKS}
    if answer["species"] and not answer["genus"]:
        return None
    if not any(answer.values()):
        return None
    if not taxa_tree.known(answer):
        return None
    return answer


def _deeper_than(rank, depth):
    """True if a rank depth (subfamily 0 ... species 3) is past the deepest rank the player has open yet."""
    return depth > game_levels.RANK_ORDER.index(rank)


def _response_ms(body):
    try:
        value = int(body.get("elapsed_ms"))
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= MAX_RESPONSE_MS else None


@login_required
@require_POST
@_timed
def game_answer(request, round_id):
    rnd = get_object_or_404(GameRound, id=round_id, player=request.user)
    body = _json_body(request)
    if body is None:
        return JsonResponse({"error": "Invalid request."}, status=400)
    if rnd.finished_at is not None:
        return JsonResponse({"error": "This round is already finished."}, status=409)

    index = _next_index(rnd)
    if index is None:
        return JsonResponse(_finish(rnd))
    if body.get("index") != index:
        return JsonResponse({"error": "Out of step with the round; please reload."}, status=409)

    item = rnd.items[index]
    roi_a, roi_b = _item_rois(item)
    before = game_rewards.progress(request.user)
    record = GameAnswer(
        round=rnd, player=request.user, mode=_item_mode(rnd, index), index=index,
        is_check=bool(item.get("check")), is_retry=bool(item.get("retry")), roi=roi_a, roi_b=roi_b,
        skipped=bool(body.get("skipped") or body.get("reported")), response_ms=_response_ms(body),
        # moving on from a beetle they just reported: no points either way
        score_hold=bool(body.get("reported")),
    )
    tiles = grid = None
    if record.mode in (GameRound.Mode.ODD, GameRound.Mode.SELECT):
        tiles = _item_tiles(item)
        record.tiles, record.grid_rank, record.grid_group = item["tiles"], item["rank"], item["group"]
        record.grid_step = item.get("step")
        record._grid_tiles = tiles
        # Photos flagged before answering (each one reported) are left out; half of them, or the odd one, end the grid
        # unscored like a reported photo (#489)
        flagged = body.get("flagged") or []
        if (not isinstance(flagged, list) or len(set(map(str, flagged))) != len(flagged)
                or any(not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(tiles) for i in flagged)):
            return JsonResponse({"error": "Unknown flagged photo."}, status=400)
        record.flagged = sorted(set(flagged) & set(_flagged_places(tiles, request.user)))
        if _grid_over(item, record.mode, record.flagged):
            record.skipped = record.score_hold = True
    if record.mode == GameRound.Mode.ODD:
        # roi_b: the odd one the round was built around. Only a pick on a validated beetle is scored straight away.
        record.roi_b, record.is_check = roi_a, False
    if record.is_check and roi_a.taxon:
        record.ref_subfamily = roi_a.taxon.subfamily or ""
        record.ref_tribe = roi_a.taxon.tribe or ""
        record.ref_genus = roi_a.taxon.genus or ""
        record.ref_species = roi_a.taxon.species or ""

    scores = {}
    if not record.skipped:
        if record.mode == GameRound.Mode.CLASSIFY:
            answer = _clean_classification(body)
            if answer is None:
                return JsonResponse({"error": "Please choose a name from the lists."}, status=400)
            if any(answer[r] and _deeper_than(before["rank"], game.RANKS.index(r)) for r in game.RANKS):
                return JsonResponse({"error": "That rank isn't open yet."}, status=400)
            for r, v in answer.items():
                setattr(record, r, v)
            if not record.is_check and record.genus and record.species:
                record.new_species = game_discoveries.is_new_species(record.genus, record.species)
            if record.is_check:
                scores = game.score_classification(answer, roi_a.taxon)
        elif record.mode == GameRound.Mode.ODD:
            # As many picks as the grid has odd ones (#540); a lone "pick" is how the feed sent one before
            want = len(_odd_ones(item))
            picks = body.get("picks", [body["pick"]] if "pick" in body else None)
            if (not isinstance(picks, list) or len(picks) != want or len(set(map(str, picks))) != len(picks)
                    or any(not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(tiles) for i in picks)):
                return JsonResponse({"error": "Please pick a beetle." if want == 1 else f"Please pick {want} beetles."},
                                    status=400)
            if set(picks) & set(record.flagged):
                return JsonResponse({"error": "That photo is flagged: pick another beetle."}, status=400)
            record.picks = sorted(picks)
            record.roi = tiles[record.picks[0]]
            right = game.odd_verdict(game.score_odd_grid(tiles, record.picks, record.grid_rank, record.grid_group,
                                                         record.flagged))
            # scored straight away once the truth tells: any pick on one of the rest, or every pick on a validated beetle
            record.is_check = right is not None
            if record.is_check:
                t = next((tiles[i].taxon for i in record.picks if game_scoring.is_truth(tiles[i])), None)
                if t is not None:
                    record.ref_subfamily, record.ref_tribe = t.subfamily or "", t.tribe or ""
                    record.ref_genus, record.ref_species = t.genus or "", t.species or ""
                scores = {record.grid_rank: right}
        elif record.mode == GameRound.Mode.SELECT:
            picks = body.get("picks")
            if (not isinstance(picks, list) or not picks or len(set(map(str, picks))) != len(picks)
                    or any(not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(tiles) for i in picks)):
                return JsonResponse({"error": "Tap the beetles first."}, status=400)
            if set(picks) & set(record.flagged):
                return JsonResponse({"error": "A flagged photo can't be tapped."}, status=400)
            record.picks = sorted(picks)
            grid = game.score_select(tiles, record.picks, record.grid_rank, record.grid_group, record.flagged)
            if grid["members"]:
                scores = {record.grid_rank: grid["perfect"]}   # the rank's "correct": a perfect grid
        else:
            choice = body.get("pair_answer")
            if choice not in dict(PAIR_CHOICES):
                return JsonResponse({"error": "Please choose an answer."}, status=400)
            if choice in game.PAIR_DEPTH and _deeper_than(before["rank"], game.PAIR_DEPTH[choice]):
                return JsonResponse({"error": "That rung isn't open yet."}, status=400)
            record.pair_answer = choice
            if record.is_check:
                scores = game.score_pair(choice, roi_a.taxon, roi_b.taxon)
    for r, ok in scores.items():
        setattr(record, f"correct_{r}", ok)
    if not (record.skipped or record.is_retry):
        # a beetle whose names the player was shown recently (#541, #555): full points, but it doesn't show expertise
        shown = [b.id for b in (record.roi, record.roi_b) if b is not None]
        if record.mode == GameRound.Mode.SELECT:
            shown += [b.id for b in tiles]
        elif record.mode == GameRound.Mode.ODD:   # every odd one and every pick of a grid with several (#540)
            shown += [*_odd_ones(item), *(tiles[i].id for i in record.picks)]
        record.seen_before = game.seen_recently(request.user, shown)
    game_scoring.note_difficulty(record)   # how hard the beetle is now: its points follow it (#492)
    try:
        with transaction.atomic():
            record.save()
    except IntegrityError:
        # The same item was submitted twice (double tap, two tabs).
        return JsonResponse({"error": "That answer was already saved; please reload."}, status=409)

    game_scoring.score_new_answer(record)
    game_grid_ladder.update(record)   # the grid games grow, or shrink, with each grid answered (#489)
    extra = {
        # what the answer earned next to what is known about the beetle, shown before the next one (#488)
        "review": game_answer_review.review(record, item),
        "events": game_rewards.play_events(request.user, before),
        "chip": _chip(request.user),
    }
    extra["events"] += _late_level_events(request.user, before, extra["events"], extra["chip"]["level"])
    if any(e["kind"] == "level" for e in extra["events"]):
        # A new level's unlocks apply at once: the toolbar learns about them, and when the level opens a new game
        # the rest of this batch (picked under the old rules) is set aside for a fresh one.
        extra["prefs"] = _prefs(request.user)
        opened = {g["key"] for g in extra["prefs"]["games"] if g["unlocked"]}
        if opened - set(game_levels.games(before["perks"])):
            _drop_ahead(rnd)
            game.finish_round_later(rnd)
            fresh = game.start_round(request.user, rnd.mode, fresh_only=True)
            first = _next_index(fresh, 0) if fresh else None
            if first is not None:
                return JsonResponse(dict(extra, round=str(fresh.id), item=_item_payload(fresh, first), notice=fresh.notice))
    nxt = _next_index(rnd, index + 1)
    if nxt is None:
        # The feed carries straight on into a new batch (usually built ahead), and the work of closing this one is
        # done on the worker. It only ends when there is nothing new left to show.
        fresh, first = _next_batch(rnd)
        if first is not None:
            game.finish_round_later(rnd)
            return JsonResponse(dict(extra, round=str(fresh.id), item=_item_payload(fresh, first), notice=fresh.notice))
        return JsonResponse(dict(_finish(rnd), **extra))
    return JsonResponse(dict(extra, item=_item_payload(rnd, nxt)))


@login_required
@require_GET
def game_past_review(request, round_id, index):
    """
    The review of one of the player's own answers (its round and place in it), for Back after a reload: the same card
    the feed showed after the answer (game_answer_review). Anyone else's answer is a 404, like one that doesn't exist.
    """
    rnd = GameRound.objects.filter(id=round_id, player=request.user).first()
    card = game_answer_review.past(rnd, index) if rnd else None
    if card is None:
        return JsonResponse({"error": "No such answer."}, status=404)
    return JsonResponse({"review": card})


@login_required
@require_POST
def game_exit(request):
    """The player leaves the feed: close their current batch so their answers count, then go back to the game home."""
    body = _json_body(request) or {}
    rnd = GameRound.objects.filter(id=body.get("round"), player=request.user).first() if _is_uuid(body.get("round")) else None
    if rnd is not None and rnd.finished_at is None:
        game.finish_round_later(rnd)
    # How the sitting went. "since" is when the page was opened (milliseconds since 1970); without it, the last hour.
    try:
        since = datetime.fromtimestamp(int(body["since"]) / 1000, tz=dt_timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        since = timezone.now() - timedelta(hours=1)
    return JsonResponse({"url": reverse("game_home"), "recap": game_rewards.recap(request.user, since)})


# ---------------------------------------------------------------------------
# Taxonomy pickers
# ---------------------------------------------------------------------------
@login_required
@require_GET
def game_taxa(request):
    """
    Options for one picker. ``rank`` is the list wanted; the chosen higher ranks
    narrow it. Genus options also carry their subfamily and tribe so picking a genus
    can fill those in. Only names that belong at that rank are offered (see game_taxa.py).
    """
    rank = request.GET.get("rank")
    if rank not in game.RANKS:
        return JsonResponse({"error": "Unknown rank."}, status=400)
    parents = {r: (request.GET.get(r) or "").strip() for r in game.RANKS[: game.RANKS.index(rank)]}
    if rank == "species" and not parents.get("genus"):
        return JsonResponse({"options": []})

    return JsonResponse({"options": taxa_tree.options(rank, parents)})


# ---------------------------------------------------------------------------
# Label proposals (annotation page)
# ---------------------------------------------------------------------------
def _proposal_json(entry, review):
    taxon = entry["taxon"]
    return {
        "answers": entry["answers"],
        "players": entry["players"],
        "trusted_rank": entry["trusted_rank"],
        "ranks": {
            r: (None if v is None else {
                "value": v["value"], "support": round(v["support"], 3), "votes": v["votes"],
                "trusted": v["trusted"], "trusted_votes": v["trusted_votes"],
            })
            for r, v in entry["ranks"].items()
        },
        "taxon": None if taxon is None else {
            "valid_species_id": taxon.valid_species_id, "scientific_name": taxon.scientific_name,
        },
        "review": None if review is None else {
            "decision": review.decision,
            "reviewed_by": review.reviewed_by.username if review.reviewed_by else "",
            "reviewed_at": review.reviewed_at.isoformat(),
            "answers": review.answers,
        },
    }


@area_required(ANNOTATE)
@require_GET
def game_proposals(request):
    """Game label proposals for the ROIs of one image, keyed by ROI id."""
    image_id = request.GET.get("image_asset")
    if not image_id:
        return JsonResponse({"error": "image_asset is required"}, status=400)
    try:
        roi_ids = list(Beetles.objects.filter(image_asset_id=image_id, is_deleted=False).values_list("id", flat=True))
    except Exception:
        return JsonResponse({"error": "Invalid image id"}, status=400)
    latest = {}
    for review in LabelReview.objects.filter(roi_id__in=roi_ids).select_related("reviewed_by"):
        latest.setdefault(review.roi_id, review)  # ordered newest first
    proposals = {
        str(entry["roi"].id): _proposal_json(entry, latest.get(entry["roi"].id))
        for entry in game.consensus(roi_ids=roi_ids, voters=game_levels.suggestion_voters())
    }
    reports = {}
    for r in GameReport.objects.filter(roi_id__in=roi_ids, status=GameReport.Status.OPEN).select_related("reporter"):
        reports.setdefault(str(r.roi_id), []).append({
            "reason": r.get_reason_display(), "note": r.note, "reporter": r.reporter.username,
            "created_at": r.created_at.isoformat(), "was_validated": r.was_validated,
        })
    tips = {str(roi_id): items for roi_id, items in game_tips.tips(roi_ids).items()}
    # What classifier models said, per rank with their confidence (shown under "AI suggestion")
    rois = Beetles.objects.filter(id__in=roi_ids).select_related("taxon")
    ai = {str(roi_id): items for roi_id, items in suggestions_for(rois).items()}
    # labels the game wrote into the database and a curator can take back (#427)
    applied = {
        str(roi_id): {
            "automatic": review.reviewed_by_id is None,
            "by": review.reviewed_by.username if review.reviewed_by else "",
            "at": review.reviewed_at.isoformat(),
            "name": review.taxon.scientific_name if review.taxon else " ".join(
                v for v in (review.genus, review.species) if v),
            "current": bool(review.taxon) and str(review.roi.depicts_valid_name_id or "") == review.taxon.valid_species_id,
        }
        for roi_id, review in game_applied.applied_for(roi_ids).items()
    }
    return JsonResponse({"proposals": proposals, "reports": reports, "tips": tips, "ai": ai, "applied": applied})


@area_required(ANNOTATE)
@require_POST
def game_applied_revert(request, roi_id):
    """Take back a label the game wrote onto this beetle: its earlier label comes back (game_applied.revert)."""
    roi = get_object_or_404(Beetles, id=roi_id, is_deleted=False)
    lock = ImageLock.objects.filter(image_asset_id=roi.image_asset_id).select_related("locked_by").first()
    if lock and lock.locked_by_id != request.user.id and not lock.is_expired():
        return JsonResponse({"error": f"{lock.locked_by.username} is editing this image."}, status=409)
    try:
        before = game_applied.revert(roi, request.user)
    except game_applied.RevertError as e:
        return JsonResponse({"error": str(e)}, status=409)
    game_queue.forget()
    return JsonResponse({"depicts_valid_name_id": before["depicts_valid_name_id"]})


@area_required(ANNOTATE)
@require_POST
def game_resolve_reports(request, roi_id):
    """Close the open player reports on one ROI (see game_feedback.resolve_reports)."""
    roi = get_object_or_404(Beetles, id=roi_id)
    body = _json_body(request) or {}
    outcome = body.get("outcome")
    if outcome not in (GameReport.Status.CORRECTED, GameReport.Status.CONFIRMED):
        return JsonResponse({"error": "outcome must be corrected or confirmed"}, status=400)
    closed = game_feedback.resolve_reports(roi, outcome, request.user, str(body.get("note") or ""))
    return JsonResponse({"closed": closed})


@area_required(ANNOTATE)
@require_POST
def game_proposal_review(request, roi_id):
    """
    Accept or dismiss ("Reject" on the page) the game proposal for one ROI.

    Accepting sets the ROI's species to the proposal's species (it does not validate the
    ROI; staff still do that as usual). Both decisions are recorded in LabelReview: either way
    the proposal leaves the queue until new answers arrive (game_queue), and the game never
    writes a label onto the ROI by itself afterwards (game_trust.auto_apply_expert_labels).
    """
    roi = get_object_or_404(Beetles, id=roi_id, is_deleted=False)
    body = _json_body(request) or {}
    decision = body.get("decision")
    if decision not in ("accept", "dismiss"):
        return JsonResponse({"error": "decision must be accept or dismiss"}, status=400)

    lock = ImageLock.objects.filter(image_asset_id=roi.image_asset_id).select_related("locked_by").first()
    if lock and lock.locked_by_id != request.user.id and not lock.is_expired():
        return JsonResponse({"error": f"{lock.locked_by.username} is editing this image."}, status=409)

    entries = game.consensus(roi_ids=[roi.id], voters=game_levels.suggestion_voters())
    if not entries:
        return JsonResponse({"error": "There is no game proposal for this ROI."}, status=404)
    entry = entries[0]
    ranks = entry["ranks"]
    review = LabelReview(
        roi=roi, reviewed_by=request.user, answers=entry["answers"],
        trusted_rank=entry["trusted_rank"], taxon=entry["taxon"],
        **{r: (ranks[r]["value"] if ranks[r] else "") for r in game.RANKS},
    )
    if decision == "accept":
        if entry["taxon"] is None:
            return JsonResponse({"error": "Only species-level proposals can be accepted."}, status=400)
        roi.depicts_valid_name_id = entry["taxon"].valid_species_id
        roi.label_source = Beetles.LabelSource.EXPERT   # a curator accepted the game's consensus
        roi.label_source_detail = f"{entry['answers']} game answers, accepted by {request.user.username}"[:255]
        roi.last_updated_by = request.user
        roi._name_by_hand = True   # the curator chose it: it shows even over a Taxonomist ID (identification.py)
        roi.save()
        review.decision = LabelReview.Decision.ACCEPTED
    else:
        review.decision = LabelReview.Decision.DISMISSED
    review.save()
    game_queue.forget()   # the image list's proposal filter and sort
    return JsonResponse({
        "decision": review.decision,
        "depicts_valid_name_id": roi.depicts_valid_name_id,
    })


# ---------------------------------------------------------------------------
# Staff review
# ---------------------------------------------------------------------------
def _player_rows():
    reliability = game.player_reliability()
    labelled = {row["player_id"]: row["labelled"] for row in game.leaderboard(limit=None)}
    proven = {}
    for skill in game_trust.PlayerSkill.objects.filter(proven=True):
        proven.setdefault(skill.player_id, []).append(skill)
    ids = set(reliability) | set(labelled)
    users = get_user_model().objects.in_bulk(ids)
    rows = []
    for pid in ids:
        rel = reliability.get(pid) or {m: game.default_weight() for m in ("classify", "pair", "odd", "select", "all")}
        rows.append({
            "id": pid,
            "username": users[pid].username if pid in users else "?",
            "labelled": labelled.get(pid, 0),
            "classify": [rel["classify"][r] for r in game.RANKS],
            "pair": [rel["pair"][r] for r in game.RANKS],
            "odd": [rel["odd"][r] for r in game.RANKS],
            "proven": sorted(proven.get(pid, []), key=lambda s: (game.RANKS.index(s.rank), s.branch)),
        })
    rows.sort(key=lambda r: (-r["labelled"], r["username"]))
    return rows


def _sort_rows(request, rows, param, keys, default=""):
    """
    Sort ``rows`` by the column named in the query (``?players_sort=labelled``, a leading "-" for descending).
    ``keys`` maps column names to a function giving a row's value; rows without a value go last either way.
    Returns (rows, the sort in use).
    """
    raw = request.GET.get(param) or default
    name = raw.lstrip("-")
    if name not in keys:
        raw, name = default, default.lstrip("-")
    if not name:
        return list(rows), raw
    value = keys[name]
    rows = list(rows)
    present = [r for r in rows if value(r) not in (None, "")]
    missing = [r for r in rows if value(r) in (None, "")]
    present.sort(key=lambda r: (value(r).lower() if isinstance(value(r), str) else value(r)), reverse=raw.startswith("-"))
    return present + missing, raw


def _cell_accuracy(cell):
    return cell["accuracy"] if cell.get("n") else None


@login_required
def game_review(request):
    """Superusers only: open reports, every player's reliability and the label proposals, each paged and sortable."""
    if not request.user.is_superuser:
        raise Http404("Not found")
    only_trusted = request.GET.get("trusted") == "1"
    entries = game.consensus()
    if only_trusted:
        entries = [e for e in entries if e["trusted_rank"]]

    def page(items, param):
        """One page of a table; each table has its own page number, so paging one keeps your place in the others."""
        return Paginator(items, REVIEW_PER_PAGE).get_page(request.GET.get(param))

    reports, reports_sort = _sort_rows(
        request, GameReport.objects.filter(status=GameReport.Status.OPEN).select_related("reporter", "roi__taxon"),
        "reports_sort", {
            "roi": lambda r: str(r.roi_id), "label": lambda r: r.roi.taxon.scientific_name if r.roi.taxon else None,
            "reason": lambda r: r.get_reason_display(), "reporter": lambda r: r.reporter.username,
            "date": lambda r: r.created_at,
        }, default="date")
    player_keys = {"player": lambda r: r["username"], "labelled": lambda r: r["labelled"], "expert": lambda r: len(r["proven"])}
    for i, rank in enumerate(game.RANKS):
        player_keys[f"id_{rank}"] = lambda r, i=i: _cell_accuracy(r["classify"][i])
        player_keys[f"sim_{rank}"] = lambda r, i=i: _cell_accuracy(r["pair"][i])
    players, players_sort = _sort_rows(request, _player_rows(), "players_sort", player_keys, default="-labelled")
    label_keys = {
        "roi": lambda e: str(e["roi"].id), "label": lambda e: e["roi"].taxon.scientific_name if e["roi"].taxon else None,
        "answers": lambda e: e["answers"],
    }
    for rank in game.RANKS:
        label_keys[rank] = lambda e, rank=rank: (e["ranks"].get(rank) or {}).get("value")
    entries, labels_sort = _sort_rows(request, entries, "labels_sort", label_keys)   # default: most answered first

    return render(request, "beetles/game_review.html", {
        "open_reports": page(reports, "reports_page"),
        "reports_sort": reports_sort, "players_sort": players_sort, "labels_sort": labels_sort,
        "ranks": game.RANKS,
        "players": page(players, "players_page"),
        "consensus": page(entries, "labels_page"),
        "consensus_total": len(entries),
        "only_trusted": only_trusted,
        "rounds": GameRound.objects.count(),
        "answers": GameAnswer.objects.count(),
        "per_species": game_trust.per_species(), "children_share": game_trust.children_share(),
    })


REVIEW_PER_PAGE = 25


def _pct(value):
    return "" if value is None else f"{value:.3f}"


@login_required
def game_export(request, kind):
    if not request.user.is_superuser:
        raise Http404("Not found")
    return _game_export(request, kind)


def _game_export(request, kind):
    response = HttpResponse(content_type="text/csv")
    writer = csv.writer(response)
    if kind == "labels":
        response["Content-Disposition"] = 'attachment; filename="game_label_consensus.csv"'
        header = ["roi_id", "image_id", "current_valid_name_id", "answers", "players",
                  "trusted_rank", "proposed_valid_species_id"]
        for r in game.RANKS:
            header += [f"{r}", f"{r}_support", f"{r}_votes", f"{r}_trusted"]
        writer.writerow(header)
        for entry in game.consensus():
            roi = entry["roi"]
            row = [roi.id, roi.image_asset_id, roi.depicts_valid_name_id or "", entry["answers"],
                   entry["players"], entry["trusted_rank"],
                   entry["taxon"].valid_species_id if entry["taxon"] else ""]
            for r in game.RANKS:
                vote = entry["ranks"][r]
                row += ([vote["value"], _pct(vote["support"]), vote["votes"], vote["trusted"]]
                        if vote else ["", "", "", ""])
            writer.writerow(row)
    elif kind == "players":
        response["Content-Disposition"] = 'attachment; filename="game_player_reliability.csv"'
        header = ["username", "labelled"]
        for mode in ("classify", "pair", "odd"):
            for r in game.RANKS:
                header += [f"{mode}_{r}_correct", f"{mode}_{r}_judged", f"{mode}_{r}_accuracy"]
        header.append("proven_skills")
        writer.writerow(header)
        for p in _player_rows():
            row = [p["username"], p["labelled"]]
            for mode in ("classify", "pair", "odd"):
                for cell in p[mode]:
                    row += [cell["ok"], cell["n"], _pct(cell["accuracy"])]
            row.append("; ".join(f"{s.rank}:{s.branch or 'all'}" for s in p["proven"]))
            writer.writerow(row)
    elif kind == "skills":
        response["Content-Disposition"] = 'attachment; filename="game_player_skills.csv"'
        writer.writerow(["username", "rank", "branch", "correct", "judged", "lower_bound", "proven", "proven_at"])
        for s in game_trust.PlayerSkill.objects.select_related("player").order_by("player__username", "rank", "branch"):
            writer.writerow([s.player.username, s.rank, s.branch, s.correct, s.judged,
                             f"{s.lower_bound:.3f}", s.proven, s.proven_at.isoformat() if s.proven_at else ""])
    else:
        raise Http404("Unknown export")
    return response
