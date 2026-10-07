"""
Google Analytics (GA4) settings for the templates. See docs/analytics.md.

* Nothing is rendered unless settings.GA_MEASUREMENT_ID is a Measurement ID (G-XXXXXXXXXX). Local, staging and tests
  leave it empty, so no analytics code and no cookie notice appear.
* The page reported to Google is the page's URL pattern from urls.py ("/game/players/:id/"), never the real address:
  no query string, and no beetle, player or other ID in the path.
"""
import re

from django.conf import settings

MEASUREMENT_ID = re.compile(r"^G-[A-Z0-9]{4,20}$")
ID_PLACEHOLDER = re.compile(r"<[^>]*>")


def page_pattern(request):
    """The URL pattern of the page ("/game/rounds/:id/"), or "/not-found" when no page matched."""
    match = getattr(request, "resolver_match", None)
    if match is None:
        return "/not-found"
    route = ID_PLACEHOLDER.sub(":id", match.route or "")
    return "/" + route.lstrip("/")


def context(request):
    """Template context processor: the Measurement ID ("" when off) and the page pattern."""
    measurement_id = getattr(settings, "GA_MEASUREMENT_ID", "") or ""
    if not MEASUREMENT_ID.match(measurement_id):
        measurement_id = ""
    return {"ga_measurement_id": measurement_id, "ga_page": page_pattern(request) if measurement_id else ""}
