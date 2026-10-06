"""The Scoring page (superusers): the whole scoring explained with the live numbers, and the settings to tune it."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import game_difficulty, game_scoring, game_tuning
from .models import GameTuning


def _superuser(request):
    if not request.user.is_superuser:
        raise Http404("Not found")


@login_required
def scoring(request):
    _superuser(request)
    if request.method == "POST":
        changes, errors = {}, []
        for key, t in game_tuning.TUNABLES.items():
            if request.POST.get(f"reset-{key}"):
                changes[key] = t["default"]
                continue
            raw = {k: request.POST.get(f"{key}.{k}") for k in t["keys"]} if t["keys"] else request.POST.get(key)
            missing = all(v in (None, "") for v in raw.values()) if t["keys"] else raw is None or str(raw).strip() == ""
            if missing:   # not on the form: left as it is
                continue
            value, error = game_tuning.clean(key, raw)
            if error:
                errors.append(f"{t['label']}: {error}" if not t["keys"] else error)
            else:
                changes[key] = value
        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            changed = game_tuning.save(changes, request.user)
            if changed:
                labels = ", ".join(game_tuning.TUNABLES[k]["label"] for k in changed)
                messages.success(request, f"Saved: {labels}. New answers use it within half a minute; "
                                          "earlier answers follow at the next re-score.")
            else:
                messages.info(request, "Nothing changed.")
        return redirect("game_scoring")

    groups = []
    for title, items in game_tuning.GROUPS:
        rows = []
        for t in items:
            value = game_tuning.current(t["key"])
            rows.append(dict(t, value=value, changed=value != t["default"],
                             parts=[(k, game_tuning.PART_LABELS.get(k, k.capitalize()), value[k], t["default"][k])
                                    for k in t["keys"]] if t["keys"] else None))
        groups.append((title, rows))
    log = (GameTuning.history.select_related("history_user").order_by("-history_date")[:25])
    return render(request, "beetles/game_scoring.html", {
        "groups": groups,
        "examples": game_tuning.examples(),
        "checks": game_tuning.checks(),
        "v": {k: game_tuning.current(k) for k in game_tuning.TUNABLES},
        "log": log,
        "pair_text": "   ".join(f"{label.lower()} {game_tuning.current('GAME_PAIR_POINTS')[k]:g}"
                               for k, label in game_tuning.RUNG_LABELS.items()),
        "difficulty_examples": game_tuning.difficulty_examples(),   # points by how hard the beetle is (#492)
        "thresholds": game_tuning.thresholds(), "play": game_tuning.expected_play(),   # the balance assessment (#530)
        "wrong_cost": game_scoring.wrong_cost(),
        "distributions": game_difficulty.distributions(),
    })


@login_required
@require_POST
def rescore(request):
    """Re-score every answer with the current settings, in the background (the nightly job does the same)."""
    _superuser(request)
    from .tasks import recompute_all_scores_task
    recompute_all_scores_task.delay()
    messages.success(request, "Re-scoring everyone in the background. Scores update in a few minutes.")
    return redirect("game_scoring")
