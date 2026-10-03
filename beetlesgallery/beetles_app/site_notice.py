"""
A site-wide notice a superuser can switch on and off, e.g. "We're stress-testing the site" during a load test or a
club session (issue #383). Read through the cache, so it costs nothing per page while the site is busy.
"""
from django.core.cache import cache
from django.shortcuts import redirect, render

from .models import SiteNotice
from .views import superuser_required

CACHE_KEY = "site_notice:v1"
CACHE_SECONDS = 60
DEFAULT_TEXT = "We're stress-testing the site: things may be slow for a while. Thanks for playing!"


def current():
    """The notice text to show, or "" when it is off."""
    text = cache.get(CACHE_KEY)
    if text is None:
        row = SiteNotice.objects.filter(pk=1).first()
        text = row.text.strip() if row and row.active else ""
        cache.set(CACHE_KEY, text, CACHE_SECONDS)
    return text


def context(request):
    """Template context processor: {{ site_notice }} on every page."""
    try:
        return {"site_notice": current()}
    except Exception:   # e.g. before the table exists
        return {"site_notice": ""}


@superuser_required
def edit(request):
    notice, _ = SiteNotice.objects.get_or_create(pk=1, defaults={"text": DEFAULT_TEXT})
    if request.method == "POST":
        notice.text = (request.POST.get("text") or "").strip()[:300]
        notice.active = request.POST.get("active") == "on" and bool(notice.text)
        notice.updated_by = request.user
        notice.save()
        cache.delete(CACHE_KEY)
        return redirect("site_notice")
    return render(request, "beetles/site_notice.html", {"notice": notice})
