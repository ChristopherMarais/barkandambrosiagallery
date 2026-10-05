from django.conf import settings
from django.utils import timezone
from beetlesgallery.beetles_app.models import Taxon

def species_ref_status(request):
    """
    Expose the taxonomy reference status dict site-wide natively from Postgres:
    {
      "version": "<version string>",
      "label": "<label with last sync in the viewer's timezone>",
      "updating": <bool>
    }
    """
    try:
        latest_taxon = Taxon.objects.order_by("-updated_at").first()
        if latest_taxon:
            t_label = f"Database Managed (Last Sync: {timezone.localtime(latest_taxon.updated_at).strftime('%Y-%m-%d %H:%M %Z')})"
        else:
            t_label = "Database Managed (v2.0)"
    except Exception:
        # Fallback during initial migrations or database unavailability
        t_label = "Database Managed"

    status_dict = {'label': t_label, 'version': 'v2.0', 'updating': False}

    return {
        "species_ref_status": status_dict,
        "described_names_ref_status": status_dict,
        "app_version": settings.APP_VERSION,
        "debug": settings.DEBUG,
    }

def game_player(request):
    """
    The signed-in user's game level and score for the sidebar and the home page, so the game is always one tap
    away. Read from the stored totals only (no recomputing), so it costs one small query per page.
    """
    name = {"game_name": getattr(settings, "GAME_DISPLAY_NAME", "Ambrosia Archive")}
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return name
    try:
        from beetlesgallery.beetles_app.game_levels import describe
        from beetlesgallery.beetles_app.models import PlayerScore

        row = PlayerScore.objects.filter(player=user).values_list("score", "rating").first()
        score, rating = row if row else (0.0, 0.0)
        level = describe(score, rating)
    except Exception:
        return name
    return dict(name, game_player={"level": level["level"], "name": level["name"], "score": round(score)})
