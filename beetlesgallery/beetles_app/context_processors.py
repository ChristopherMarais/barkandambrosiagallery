# beetles_app/context_processors.py
from django.conf import settings
from django.core.cache import cache

from . import species_ref

def species_ref_status(request):
    """
    Expose the taxonomy reference status dict site-wide:
    {
      "version": "<sha256 or None>",
      "label": "<UTC label or None>",
      "updating": <bool>
    }
    """
    return {"species_ref_status": species_ref.status()}