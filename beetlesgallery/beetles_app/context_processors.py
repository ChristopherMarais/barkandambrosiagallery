from django.conf import settings
from beetlesgallery.beetles_app.models import Taxon

def species_ref_status(request):
    """
    Expose the taxonomy reference status dict site-wide natively from Postgres:
    {
      "version": "<version string>",
      "label": "<UTC label or None>",
      "updating": <bool>
    }
    """
    try:
        latest_taxon = Taxon.objects.order_by("-updated_at").first()
        if latest_taxon:
            t_label = f"Database Managed (Last Sync: {latest_taxon.updated_at.strftime('%Y-%m-%d %H:%M UTC')})"
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