"""
The staging site: a copy of the site that runs `main` next to production, on its own database, so changes can be
tried on the server before a release (docs/staging.md). Everything here does nothing unless settings.STAGING is on.

* Only one account can sign in: the shared staging account (stagedtesting / gallerystaging by default). Every page
  but the sign-in page asks for it, so the site is closed to everyone else and to search engines.
* Every page says it is the staging site, and no email leaves it.
"""
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import MiddlewareNotUsed
from django.http import HttpResponse
from django.urls import reverse

ROBOTS = "User-agent: *\nDisallow: /\n"


class StagingGateMiddleware:
    """Ask for the staging account on every page and keep search engines out."""

    def __init__(self, get_response):
        if not getattr(settings, "STAGING", False):
            raise MiddlewareNotUsed
        self.get_response = get_response

    def open_paths(self):
        return (reverse("login"), "/" + settings.STATIC_URL.lstrip("/"), "/favicon.ico")

    def __call__(self, request):
        if request.path == "/robots.txt":
            response = HttpResponse(ROBOTS, content_type="text/plain")
        elif request.user.is_authenticated or request.path.startswith(self.open_paths()):
            response = self.get_response(request)
        else:
            response = redirect_to_login(request.get_full_path())
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response


def context(request):
    """Template context processor: {{ staging }} is True on the staging site."""
    return {"staging": getattr(settings, "STAGING", False)}
