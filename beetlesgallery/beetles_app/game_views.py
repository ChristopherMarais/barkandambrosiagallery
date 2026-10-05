"""
Pages and JSON endpoints for the Beetle ID game. The game logic is in game.py and
expertise / trusted labels in game_trust.py.

Item payloads carry only an image URL and a bounding box: never the ROI id, its
label, or whether the item is a check, so the player cannot tell which answers are scored.
"""
import csv
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import game, game_board, game_checked, game_discoveries, game_feedback, game_queue, game_levels, game_rewards, game_scoring, game_tips, game_trust
from . import game_applied
from . import game_taxa as taxa_tree
from .areas import ANNOTATE, area_required
from .models import Beetles, GameAnswer, GameReport, GameRound, ImageLock, LabelReview, PlayerScore, RetroCredit, Taxon
from .predictions import suggestions_for

MODES = {m.value: m.label for m in GameRound.Mode}
# What players see. (The model keeps its own plain labels; changing those would need a migration.)
GAME_NAMES = {"classify": "Name That Beetle", "pair": "Similarity", "odd": "Odd One Out", "select": "Select all",
              "mixed": settings.GAME_DISPLAY_NAME}
GAME_TAGLINES = {
    "classify": "One beetle, four guesses: subfamily, tribe, genus, species. Go as deep as you dare.",
    "pair": "Two beetles. How close is the family? From total strangers to the very same species.",
    "odd": "Four beetles, one doesn't belong. Spot it.",
    "select": "Nine beetles. Tap every one of a group.",
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
# A short line under a reason in that menu, so a clear photo that just shows little isn't reported as bad (#360)
FEED_REPORT_HINTS = {"bad_box": "Misses the beetle or frames the label", "bad_image": "Blurry, dark, or not a beetle"}


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
    checked, checked_new = game_checked.pop_unseen(request.user)
    # this week's top players; at the start of a quiet week, all time instead
    board, board_period = game_board.board(limit=5), "week"
    if not board:
        board, board_period = game_board.board(period="all", limit=5), "all"
    rewards = game_rewards.progress(request.user)
    return render(request, "beetles/game_home.html", {
        "checked": checked, "checked_new": checked_new,
        "proposals_notice": rewards["proposals"] and _first_sight_of_proposals(request.user),
        "discoveries": game_discoveries.pop_unseen(request.user),
        "score": game_scoring.score_for(request.user),
        "rewards": rewards,
        "board": board, "board_period": board_period,
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
        played = [name for name, n in (("Identification", r.identified), ("Similarity", r.compared),
                                       ("Odd One Out", r.spotted), ("Select all", r.selected)) if n]
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
    last_week = game_board.weekly_wins()[:1]
    names = dict(get_user_model().objects.filter(id__in=[p for w in last_week for p, _ in w["places"]])
                 .values_list("id", "username"))
    q = (request.GET.get("q") or "").strip()[:50]
    branch_rank = request.GET.get("rank") if request.GET.get("rank") in game_board.BRANCH_SKILL else ""
    branch_value = (request.GET.get("branch") or "").strip()[:100]
    return render(request, "beetles/game_leaderboard.html", {
        "rows": game_board.board(sort=sort, period=period, q=q, limit=100),
        "branch_rows": game_board.branch_board(branch_rank, branch_value) if branch_rank and branch_value else None,
        "sort": sort, "period": period, "q": q, "sorts": game_board.SORTS, "periods": game_board.PERIODS,
        "resets_at": game_board.period_end(period),
        "last_week": [{"position": i, "player_id": p, "username": names.get(p, ""), "points": round(pts)}
                      for i, (p, pts) in enumerate(last_week[0]["places"], start=1)] if last_week else [],
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
        "per_species": game_trust.per_species(),
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
    rp = game_scoring.RANK_POINTS
    over = game.game_setting("GAME_POINTS_OVERREACH", 0.35)
    genus = sum(rp[r] for r in ("subfamily", "tribe", "genus"))
    nums = {"overreach": (genus - rp["species"] * over) * w, "genus": genus * w, "species": sum(rp.values()) * w}
    return {k: _weight_label(round(v, 1)) for k, v in nums.items()}


@login_required
def game_how(request):
    """How the game works and how it is scored, in plain words."""
    return render(request, "beetles/game_how.html", {
        "discussions": discussions_url(), "levels": game_levels.table(), "goal_floor": game_rewards.daily_goal(),
        "proposal_level": game_levels.proposal_level(),
        "min_experts": game.game_setting("GAME_AUTO_APPLY_MIN_EXPERTS", 2),
        "per_species": game_trust.per_species(),
        "trust_accuracy": game_trust.min_accuracy(),
        "classify_weight": _weight_label(game_scoring.classify_weight()),
        "rank_points": {r: p * game_scoring.classify_weight() for r, p in game_scoring.RANK_POINTS.items()},
        "classify_examples": _classify_examples(), "pair_points": [
            (game_scoring.DEPTH_NAME[d], p) for d, p in sorted(game_scoring.PAIR_POINTS.items())],
        "wrong": game.game_setting("GAME_POINTS_WRONG_FACTOR", 0.75),
        "overreach": game.game_setting("GAME_POINTS_OVERREACH", 0.35),
        "cap": int(game.game_setting("GAME_POINTS_CONSENSUS_CAP", 0.6) * 100),
        "unsure": game.game_setting("GAME_POINTS_UNSURE", 0.25),
        "retry_days": game.game_setting("GAME_RETRY_AFTER_DAYS", 2),
        "rank_steps": game_levels.rank_steps(), "ranks_all_level": game_levels.RANKS_ALL_FROM_LEVEL,
        "odd_level": game_levels.game_level("odd"), "identify_level": game_levels.game_level("classify"),
        "select_level": game_levels.game_level("select"),
        "select_wrong": game.game_setting("GAME_POINTS_SELECT_WRONG", 1.5),
        "odd_weight": _weight_label(game.game_setting("GAME_POINTS_ODD_WEIGHT", 1.5)),
        "odd_skip": game.game_setting("GAME_POINTS_ODD_SKIP", 0.25),
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
        **_onboarding(request),
    })


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
    Body: {"round", "index", "image": 0 or 1 (A or B, as shown; in Odd One Out the beetle's place in the grid), "reason",
    "note"}, and "photo": n to report the beetle's n-th other photo (the "More photos" gallery, 1 = its first) instead
    of the one in play.
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
    return JsonResponse({"status": report.status, "reason": report.get_reason_display()})


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


def _item_tiles(item):
    """The Beetles rows of an Odd One Out item, in the order shown. None if any is gone."""
    ids = item.get("tiles") or []
    found = {str(k): v for k, v in Beetles.objects.select_related("image_asset", "taxon").in_bulk(ids).items()}
    if not ids or any(i not in found or not found[i].has_bbox() for i in ids):
        return None
    return [found[i] for i in ids]


def _item_rois(item):
    """
    The Beetles rows of a round item, (a, b) with b None for classify and Odd One Out (whose a is the odd one, and
    whose other beetles must all still be there too). None if any is gone.
    """
    if item.get("tiles"):
        tiles = _item_tiles(item)
        return None if tiles is None else (next(t for t in tiles if str(t.id) == item["a"]), None)
    ids = [item["a"]] + ([item["b"]] if item.get("b") else [])
    found = {str(k): v for k, v in Beetles.objects.select_related("image_asset", "taxon").in_bulk(ids).items()}
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


def _item_images(rnd, index, extras=False):
    """
    The photos of one item. With ``extras``, each also says how many other photos there are of that same beetle
    ("more"), and lists them ("photos") once the player has unlocked them (game_levels.SPECIMEN_PHOTOS).
    Odd One Out shows its beetles in a grid, each on its own (no other photos of them).
    """
    if rnd.items[index].get("tiles"):
        return [{"url": r.display_url, "box": _box(r)} for r in _item_tiles(rnd.items[index])]
    a, b = _item_rois(rnd.items[index])
    rois = [a] if b is None else ([b, a] if rnd.items[index].get("flip") else [a, b])
    images = [{"url": r.display_url, "box": _box(r)} for r in rois]
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
    if payload["mode"] == GameRound.Mode.ODD:
        payload["rank"] = rnd.items[index]["rank"]   # all but one share a name at this rank
    if payload["mode"] == GameRound.Mode.SELECT:   # "Tap every <target>"
        payload["rank"] = rnd.items[index]["rank"]
        payload["target"] = rnd.items[index]["group"][rnd.items[index]["rank"]]
    if payload["mode"] == GameRound.Mode.CLASSIFY:
        others = (GameAnswer.objects.filter(roi_id=rnd.items[index]["a"], skipped=False)
                  .exclude(player=rnd.player).values("player").distinct().count())
        if others:
            payload["others"] = others   # how many other players named it (not what they said, until you answer)
    # Let the browser start downloading the next photos while this item is answered.
    following = _next_index(rnd, index + 1)
    if following is not None:
        payload["prefetch"] = [im["url"] for im in _item_images(rnd, following)]
    return payload


def _finish(rnd):
    game.finish_round(rnd)
    summary = game.player_summary(rnd.player)
    summary["round_labelled"] = rnd.answers.filter(skipped=False).count()
    return {"done": True, "summary": summary, "review_url": reverse("game_round_review", args=[rnd.id])}


@login_required
@require_POST
def game_start(request):
    body = _json_body(request)
    mode = (body or {}).get("mode")
    if mode not in MODES:
        return JsonResponse({"error": "Unknown game mode."}, status=400)

    # Pick up where the player left off (e.g. after a reload) before starting afresh. "fresh" (after changing the
    # game or focus) closes what is left of the current batch so the new choice applies straight away.
    rnd = game.resumable_round(request.user, mode)
    if rnd is not None and (body or {}).get("fresh"):
        game.finish_round(rnd)
        rnd = None
    index = _next_index(rnd) if rnd else None
    if index is None:
        if rnd is not None:
            game.finish_round(rnd)
        rnd = game.start_round(request.user, mode)
        index = _next_index(rnd, 0) if rnd else None
    if index is None:
        return JsonResponse({
            "error": "There are no images ready for this game yet. Please check back later."
        }, status=404)
    focus = game.player_focus(request.user)
    return JsonResponse({
        "round": str(rnd.id), "item": _item_payload(rnd, index), "chip": _chip(request.user),
        "focus": f"{focus[0].capitalize()}: {focus[1]}" if focus else "",
        "prefs": _prefs(request.user),
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
def game_prefs(request):
    """
    Change the game (the mix / Similarity / Odd One Out / Identification) or the focus from the feed. Each only if
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
        record._grid_tiles = tiles
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
            pick = body.get("pick")
            if not isinstance(pick, int) or isinstance(pick, bool) or not 0 <= pick < len(tiles):
                return JsonResponse({"error": "Please pick a beetle."}, status=400)
            record.roi = tiles[pick]
            record.is_check = game_scoring.is_truth(record.roi)
            if record.is_check:
                t = record.roi.taxon
                record.ref_subfamily, record.ref_tribe = t.subfamily or "", t.tribe or ""
                record.ref_genus, record.ref_species = t.genus or "", t.species or ""
                scores = game.score_odd(t, record.grid_rank, record.grid_group)
        elif record.mode == GameRound.Mode.SELECT:
            picks = body.get("picks")
            if (not isinstance(picks, list) or not picks or len(set(map(str, picks))) != len(picks)
                    or any(not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(tiles) for i in picks)):
                return JsonResponse({"error": "Tap the beetles first."}, status=400)
            record.picks = sorted(picks)
            grid = game.score_select(tiles, record.picks, record.grid_rank, record.grid_group)
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
    try:
        with transaction.atomic():
            record.save()
    except IntegrityError:
        # The same item was submitted twice (double tap, two tabs).
        return JsonResponse({"error": "That answer was already saved; please reload."}, status=409)

    game_scoring.score_new_answer(record)
    extra = {
        "community": None if record.skipped else _community(record),
        "celebrate": _worth_celebrating(record, scores),
        "celebrate_size": _celebration_size(record, scores),
        "events": game_rewards.play_events(request.user, before),
        "chip": _chip(request.user),
    }
    if record.mode == GameRound.Mode.ODD:
        extra["reveal"] = _odd_reveal(item, tiles)
    elif record.mode == GameRound.Mode.SELECT:
        # which were members: tapped right, tapped wrong, left out (votes on unchecked beetles stay as they were)
        grid = grid or game.score_select(tiles, [], record.grid_rank, record.grid_group)
        extra["reveal"] = {"rank": item["rank"], "target": item["group"][item["rank"]], "tiles": grid["tiles"],
                           "right": grid["right"], "members": grid["members"], "wrong": grid["wrong"]}
    if any(e["kind"] == "level" for e in extra["events"]):
        # A new level's unlocks apply at once: the toolbar learns about them, and when the level opens a new game
        # the rest of this batch (picked under the old rules) is set aside for a fresh one.
        extra["prefs"] = _prefs(request.user)
        opened = {g["key"] for g in extra["prefs"]["games"] if g["unlocked"]}
        if opened - set(game_levels.games(before["perks"])):
            game.finish_round(rnd)
            fresh = game.start_round(request.user, rnd.mode, fresh_only=True)
            first = _next_index(fresh, 0) if fresh else None
            if first is not None:
                return JsonResponse(dict(extra, round=str(fresh.id), item=_item_payload(fresh, first)))
    nxt = _next_index(rnd, index + 1)
    if nxt is None:
        # The feed carries straight on into a new batch. It only ends when there is nothing new left to show.
        game.finish_round(rnd)
        fresh = game.start_round(request.user, rnd.mode, fresh_only=True)
        first = _next_index(fresh, 0) if fresh else None
        if first is not None:
            return JsonResponse(dict(extra, round=str(fresh.id), item=_item_payload(fresh, first)))
        return JsonResponse(dict(_finish(rnd), **extra))
    return JsonResponse(dict(extra, item=_item_payload(rnd, nxt)))


def _odd_reveal(item, tiles):
    """
    After an Odd One Out answer: which beetle was the odd one, and the names at the round's rank, e.g. {"odd": 2,
    "rank": "tribe", "odd_name": "Ipini", "group": "Xyleborini"}. The odd one is always validated, so this is the truth.
    """
    rank = item["rank"]
    odd = next(i for i, t in enumerate(tiles) if str(t.id) == item["a"])
    values = game.lineage(tiles[odd].taxon, rank) if tiles[odd].taxon else None
    return {"odd": odd, "rank": rank, "odd_name": (values or {}).get(rank, ""), "group": item["group"].get(rank, "")}


def _ahead_of(player):
    """Players ranked above this one: a higher all-time score, or (both rated) a higher overall accuracy."""
    min_judged = game.game_setting("GAME_MIN_JUDGED_FOR_ACCURACY", 10)
    me = PlayerScore.objects.filter(player=player).first()
    my_score, my_acc = (me.score, me.accuracy if me.judged >= min_judged else None) if me else (0.0, None)
    ahead = Q(score__gt=my_score)
    if my_acc is not None:
        ahead |= Q(judged__gte=min_judged, accuracy__gt=my_acc)
    return set(PlayerScore.objects.filter(ahead).exclude(player=player).values_list("player_id", flat=True))


def _community(record):
    """
    What players ranked above this one said about the beetle just answered (Name That Beetle), rank by rank, and
    how far they agree with this player: "Players ahead of you agree with you to tribe; on genus, 3 of 4 said
    Xylosandrus." Only players ahead count, so newcomers learn from better players, not from each other. Their
    latest answer each, never the truth.
    """
    if record.mode != GameRound.Mode.CLASSIFY:
        return None
    latest = {}
    for ans in (GameAnswer.objects.filter(roi=record.roi, skipped=False).exclude(player=record.player)
                .order_by("answered_at")):
        latest[ans.player_id] = ans
    if not latest:
        return {"players": 0}
    ahead = _ahead_of(record.player)
    above = [a for pid, a in latest.items() if pid in ahead]
    out = {"players": len(latest), "ahead": len(above), "ranks": []}
    if not above:
        out["text"] = (f"{len(latest)} other player{'s' if len(latest) != 1 else ''} named it, "
                       "none of them ranked above you yet.")
        return out
    mine = game.answer_values({r: getattr(record, r) for r in game.RANKS})
    for rank in game.RANKS:
        names = {}
        for ans in above:
            value = game.answer_values({r: getattr(ans, r) for r in game.RANKS})[rank]
            if value:
                display = f"{ans.genus} {ans.species}" if rank == "species" else getattr(ans, rank)
                names.setdefault(value, [display, 0])[1] += 1
        named = sum(n for _, n in names.values())
        if not named:
            continue   # nobody ahead named this rank (some name only the genus, say)
        value, (display, count) = max(names.items(), key=lambda kv: kv[1][1])
        majority = count * 2 > named
        out["ranks"].append({
            "rank": rank, "name": display if majority else "", "count": count, "of": named, "split": not majority,
            "agree": (mine[rank] == value) if (mine[rank] and majority) else None,
        })
    if not out["ranks"]:
        out["text"] = f"{len(above)} player{'s' if len(above) != 1 else ''} ahead of you named it."
        return out
    agreed = [r for r in _leading(out["ranks"], lambda r: r["agree"] is True)]
    rest = out["ranks"][len(agreed):]
    who = "Players ahead of you"
    if agreed and not rest:
        out["text"] = f"{who} agree with you to {agreed[-1]['rank']}."
        out["agree"] = True
        return out
    lead = f"{who} agree with you to {agreed[-1]['rank']}; " if agreed else f"{who}: "
    nxt = rest[0]
    if nxt["split"]:
        out["text"] = lead + f"they're split on {nxt['rank']}."
    elif nxt["agree"] is None and agreed:
        out["text"] = lead + f"{nxt['count']} of {nxt['of']} went on to {nxt['rank']} {nxt['name']}."
    else:
        out["text"] = lead + f"on {nxt['rank']}, {nxt['count']} of {nxt['of']} said {nxt['name']}."
    out["agree"] = False if nxt["agree"] is False else None
    return out


def _leading(items, ok):
    """The items from the start for which ok() holds, up to the first that fails."""
    for item in items:
        if not ok(item):
            return
        yield item


def _worth_celebrating(record, scores):
    """
    What to celebrate after an answer (#425): "validated" (beetle confetti) for a checked beetle the player got right,
    the species or a pair with every judged claim right; "partial" (a few grey beetles) for a checked beetle named
    correctly to some rank but not the species; "strong" (ordinary confetti) for an Identification answer
    on an unchecked beetle that proven experts or a trusted model back to genus or species, or that most reliable
    players agree with at species; otherwise False. Validated and strong look different, but neither shows on a
    wrong or weak answer, so it hints at little.
    """
    if record.skipped:
        return False
    if record.mode == GameRound.Mode.SELECT:   # a perfect grid; some found and nothing wrong: a few grey beetles
        grid = game.score_select(record._grid_tiles, record.picks, record.grid_rank, record.grid_group)
        if grid["perfect"]:
            return "validated"
        return "partial" if grid["right"] and not grid["wrong"] else False
    if record.is_check:
        if record.mode == GameRound.Mode.CLASSIFY:
            if scores.get("species") is True:
                return "validated"
            return "partial" if any(ok is True for ok in scores.values()) else False
        judged = [ok for ok in scores.values() if ok is not None]
        return "validated" if judged and all(judged) else False
    strong = record.mode in (GameRound.Mode.CLASSIFY, GameRound.Mode.ODD) and _strong_unvalidated(record)
    return "strong" if strong else False


def _celebration_size(record, scores):
    """
    How big the celebration is, 0.25 to 1: the share of the ranks the player got correct (all of them: 1); in Select
    all, the share of the group found.
    """
    if record.mode == GameRound.Mode.SELECT and not record.skipped:
        grid = game.score_select(record._grid_tiles, record.picks, record.grid_rank, record.grid_group)
        return round(max(0.25, grid["right"] / grid["members"]), 2) if grid["members"] else 1.0
    judged = [ok for ok in scores.values() if ok is not None]
    if record.skipped or not judged:
        return 1.0
    return round(max(0.25, sum(1 for ok in judged if ok) / len(judged)), 2)


def _strong_unvalidated(record):
    """An unchecked beetle's answer that the references (experts, a trusted model) or a clear consensus back."""
    from .models import AnswerPoints

    row = AnswerPoints.objects.filter(answer=record).values_list("detail", flat=True).first() or {}
    reference = row.get("reference") or {}
    if record.mode == GameRound.Mode.ODD:   # experts, a trusted model or most strong players agree it doesn't belong
        rank = record.grid_rank
        return bool(reference.get(rank, {}).get("match")) or \
            (row.get("agreement") or {}).get(rank, 0) >= game.game_setting("GAME_CELEBRATE_AGREEMENT", 0.75)
    if any(reference.get(r, {}).get("match") for r in ("genus", "species")):
        return True
    return (row.get("agreement") or {}).get("species", 0) >= game.game_setting("GAME_CELEBRATE_AGREEMENT", 0.75)


@login_required
@require_POST
def game_exit(request):
    """The player leaves the feed: close their current batch so their answers count, then go back to the game home."""
    body = _json_body(request) or {}
    rnd = GameRound.objects.filter(id=body.get("round"), player=request.user).first() if _is_uuid(body.get("round")) else None
    if rnd is not None and rnd.finished_at is None:
        game.finish_round(rnd)
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
    Accept or dismiss the game proposal for one ROI.

    Accepting sets the ROI's species to the proposal's species (it does not validate the
    ROI; staff still do that as usual). Both decisions are recorded in LabelReview.
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
        "per_species": game_trust.per_species(),
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
