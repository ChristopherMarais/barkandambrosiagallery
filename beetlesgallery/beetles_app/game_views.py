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
from django.db.models import Max
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import game, game_board, game_checked, game_discoveries, game_feedback, game_queue, game_levels, game_rewards, game_scoring, game_tips, game_trust
from . import game_taxa as taxa_tree
from .areas import ANNOTATE, area_required
from .models import Beetles, GameAnswer, GameReport, GameRound, ImageLock, LabelReview, PlayerScore, Taxon
from .predictions import suggestions_for

MODES = {m.value: m.label for m in GameRound.Mode}
# What players see. (The model keeps its own plain labels; changing those would need a migration.)
GAME_NAMES = {"classify": "Name That Beetle", "pair": "Similarity", "mixed": "Beetle ID"}
GAME_TAGLINES = {
    "classify": "One beetle, four guesses: subfamily, tribe, genus, species. Go as deep as you dare.",
    "pair": "Two beetles. How close is the family? From total strangers to the very same species.",
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
    return render(request, "beetles/game_home.html", {
        "checked": checked, "checked_new": checked_new,
        "discoveries": game_discoveries.pop_unseen(request.user),
        "score": game_scoring.score_for(request.user),
        "rewards": game_rewards.progress(request.user),
        "board": board, "board_period": board_period,
        "standing": game_board.accuracy_standing(request.user),
        "goal_floor": game_rewards.daily_goal(),
        "games": game_board.mode_stats([request.user.id])[request.user.id],
        # the public address in production (SITE_URL), this server's own when developing
        "share_url": (request.build_absolute_uri(reverse("game_home")) if settings.DEBUG
                      else settings.SITE_URL.rstrip("/") + reverse("game_home")),
        "discussions": discussions_url(),
    })


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
                  points=Sum("answers__points__points"))
        .filter(labelled__gt=0).order_by("-finished_at")
    )
    checked = game_checked.items(request.user, limit=500)
    sessions = Paginator(rounds, HISTORY_PER_PAGE).get_page(request.GET.get("page") if tab == "sessions" else 1)
    checked_page = Paginator(checked, HISTORY_PER_PAGE).get_page(request.GET.get("page") if tab == "checked" else 1)
    return render(request, "beetles/game_history.html", {
        "tab": tab, "sessions": sessions, "checked": checked_page, "checked_total": len(checked),
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
    })


@login_required
def game_play(request, mode):
    if mode not in MODES:
        raise Http404("Unknown game mode")
    return render(request, "beetles/game_play.html", {
        "discussions": discussions_url(),
        # short, one line each, for the little report menu in the full-image view
        "report_reasons": [("wrong_label", "Wrong name"), ("bad_box", "Box doesn't fit"),
                           ("bad_image", "Bad photo"), ("other", "Something else")],
        "mode": mode,
        "mode_label": GAME_NAMES[mode],
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
    if roi_id not in {str(answer.roi_id), str(answer.roi_b_id)}:
        return JsonResponse({"error": "Unknown image."}, status=400)
    reason = body.get("reason")
    if reason not in GameReport.Reason.values:
        return JsonResponse({"error": "Please choose a reason."}, status=400)
    roi = answer.roi if str(answer.roi_id) == roi_id else answer.roi_b
    report = game_feedback.create_report(request.user, roi, reason, str(body.get("note") or ""), answer)
    return JsonResponse({"status": report.status, "reason": report.get_reason_display()})


@login_required
@require_POST
def game_report_item(request):
    """
    A player reports a photo straight from the feed (the cog in the full-image view): the beetle goes to the
    curators on the Image Annotation page and stays out of the game until they deal with it.
    Body: {"round", "index", "image": 0 or 1 (A or B, as shown), "reason", "note"}.
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
    shown = [a] if b is None else ([b, a] if rnd.items[index].get("flip") else [a, b])
    which = body.get("image", 0)
    if not isinstance(which, int) or not 0 <= which < len(shown):
        return JsonResponse({"error": "Unknown image."}, status=400)
    reason = body.get("reason")
    if reason not in GameReport.Reason.values:
        return JsonResponse({"error": "Please choose a reason."}, status=400)
    report = game_feedback.create_report(request.user, shown[which], reason, str(body.get("note") or ""))
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


def _item_rois(item):
    """The Beetles rows of a round item, (a, b) with b None for classify. None if any is gone."""
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
    """
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
    return {
        "level": info["level"],
        "play_mode": game.play_mode(player),
        "choose_game": game_levels.CHOOSE_GAME in info["perks"],
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
    Change the game (both / Name That Beetle / Family Ties) or the focus from the feed. Each only if unlocked.
    Body: {"play_mode": ...} and/or {"focus_rank": ..., "focus_value": ...} (focus_rank "" clears the focus).
    """
    from .models import GamePreference

    body = _json_body(request) or {}
    info = game_levels.for_player(request.user)
    pref, _ = GamePreference.objects.get_or_create(player=request.user)
    if "play_mode" in body:
        mode = body.get("play_mode")
        if mode not in GamePreference.PlayMode.values:
            return JsonResponse({"error": "Unknown game."}, status=400)
        if mode != "pair" and game_levels.CHOOSE_GAME not in info["perks"]:
            level = game_levels.perk_level(game_levels.CHOOSE_GAME)
            return JsonResponse({"error": f"Identification unlocks at level {level}."}, status=403)
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
    chip = {k: state[k] for k in ("today", "goal", "goal_met", "streak", "level", "score", "next_at", "level_progress")}
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
            for r, v in answer.items():
                setattr(record, r, v)
            if not record.is_check and record.genus and record.species:
                record.new_species = game_discoveries.is_new_species(record.genus, record.species)
            if record.is_check:
                scores = game.score_classification(answer, roi_a.taxon)
        else:
            choice = body.get("pair_answer")
            if choice not in dict(PAIR_CHOICES):
                return JsonResponse({"error": "Please choose an answer."}, status=400)
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
        "events": game_rewards.play_events(request.user, before),
        "chip": _chip(request.user),
    }
    if any(e["kind"] == "level" for e in extra["events"]):
        # A new level's unlocks apply at once: the toolbar learns about them, and when the level opens a new game
        # the rest of this batch (picked under the old rules) is set aside for a fresh one.
        extra["prefs"] = _prefs(request.user)
        if extra["prefs"]["choose_game"] and game_levels.CHOOSE_GAME not in before["perks"]:
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


def _community(record):
    """
    What other players said about the beetle just answered (Name That Beetle): the most common name at the most
    specific rank most of them gave, and whether this player agrees. Their latest answer each, never the truth.
    """
    if record.mode != GameRound.Mode.CLASSIFY:
        return None
    latest = {}
    for ans in (GameAnswer.objects.filter(roi=record.roi, skipped=False).exclude(player=record.player)
                .order_by("answered_at")):
        latest[ans.player_id] = ans
    if not latest:
        return {"players": 0}
    mine = game.answer_values({r: getattr(record, r) for r in game.RANKS})
    for rank in reversed(game.RANKS):
        names = {}
        for ans in latest.values():
            value = game.answer_values({r: getattr(ans, r) for r in game.RANKS})[rank]
            if value:
                display = f"{ans.genus} {ans.species}" if rank == "species" else getattr(ans, rank)
                names.setdefault(value, [display, 0])[1] += 1
        answered = sum(n for _, n in names.values())
        if answered >= max(1, (len(latest) + 1) // 2):   # most of them went at least this far
            value, (display, count) = max(names.items(), key=lambda kv: kv[1][1])
            return {
                "players": len(latest), "rank": rank, "name": display, "count": count, "of": answered,
                "agree": (mine[rank] == value) if mine[rank] else None,
            }
    return {"players": len(latest)}


def _worth_celebrating(record, scores):
    """
    Confetti for a scored item the player got right: the species, or a pair with every judged claim right.
    It says nothing on other items, so it is the only hint that an item was scored, and only when they won.
    """
    if not record.is_check or record.skipped:
        return False
    if record.mode == GameRound.Mode.CLASSIFY:
        return scores.get("species") is True
    judged = [ok for ok in scores.values() if ok is not None]
    return bool(judged) and all(judged)


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
    return JsonResponse({"proposals": proposals, "reports": reports, "tips": tips, "ai": ai})


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
        rel = reliability.get(pid) or {m: game.default_weight() for m in ("classify", "pair", "all")}
        rows.append({
            "id": pid,
            "username": users[pid].username if pid in users else "?",
            "labelled": labelled.get(pid, 0),
            "classify": [rel["classify"][r] for r in game.RANKS],
            "pair": [rel["pair"][r] for r in game.RANKS],
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
        for mode in ("classify", "pair"):
            for r in game.RANKS:
                header += [f"{mode}_{r}_correct", f"{mode}_{r}_judged", f"{mode}_{r}_accuracy"]
        header.append("proven_skills")
        writer.writerow(header)
        for p in _player_rows():
            row = [p["username"], p["labelled"]]
            for mode in ("classify", "pair"):
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
