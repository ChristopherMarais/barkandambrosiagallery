"""
What each person may use beyond a Basic account, set per person on the My Account page (and when approving an
access request).

* Basic (every account, approved automatically once the email is confirmed): the image browser (without specimen
  pages), the taxonomy browser, the interactions page, the AI classifier and the Beetle ID game.
* Each area below is granted on its own (AreaGrant), to anyone, staff or not, so two curators can have different
  access. Superusers have everything.

Accounts that existed before this split keep what they had: members got "details" and "download", curators every
area except the species tables (migration 0037).
"""
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import render

DETAILS, DOWNLOAD = "details", "download"
ANNOTATE, BOXES, UPLOAD, INTERACTIONS, SPECIES = "annotate", "boxes", "upload", "interactions", "species_tables"
AREAS = [
    (DETAILS, "Specimen pages", "Open each specimen's page with all its details."),
    (DOWNLOAD, "Downloads", "Download images and their metadata in batches."),
    (BOXES, "Edit bounding boxes", "Draw and adjust boxes on the annotation page (not names or other details)."),
    (ANNOTATE, "Edit names and records", "Change species labels and image and ROI details, and validate records "
                                         "(includes editing boxes)."),
    (UPLOAD, "Upload and update images", "Upload new images and metadata, and update existing records from a CSV."),
    (INTERACTIONS, "Ecological interactions", "Review proposed interactions, and upload or update interactions."),
    (SPECIES, "Species tables", "Upload and download the ground-truth valid species and described names tables "
                                "(Data Management)."),
]
KEYS = [key for key, _, _ in AREAS]
LABELS = {key: label for key, label, _ in AREAS}
# what a curator ("staff") account had before areas were granted one by one
CURATOR_AREAS = [DETAILS, DOWNLOAD, BOXES, ANNOTATE, UPLOAD, INTERACTIONS]
MEMBER_AREAS = [DETAILS, DOWNLOAD]


def granted_areas(user):
    """The areas explicitly granted to this user (not counting their role)."""
    if not getattr(user, "is_authenticated", False):
        return set()
    cached = getattr(user, "_granted_areas", None)
    if cached is None:
        from .models import AreaGrant
        cached = user._granted_areas = set(AreaGrant.objects.filter(user=user).values_list("area", flat=True))
    return cached


def has_area(user, area):
    """Superusers have every area; anyone else (staff included) has the areas granted to them. Editing names and
    records includes editing boxes."""
    if not (getattr(user, "is_authenticated", False) and user.is_active):
        return False
    if user.is_superuser:
        return True
    granted = granted_areas(user)
    return area in granted or (area == BOXES and ANNOTATE in granted)


def area_required(area):
    """
    For a page that needs one area: people not signed in go to the login page; signed-in people without the area
    get a short page saying what it needs and where to ask for it (403).
    """
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if has_area(request.user, area):
                return view(request, *args, **kwargs)
            if not getattr(request.user, "is_authenticated", False):
                return redirect_to_login(request.get_full_path(), "login")
            return render(request, "accounts/needs_access.html", {"area": LABELS.get(area, area)}, status=403)
        return wrapped
    return decorator


def areas_for_templates(request):
    """{'areas': {'annotate': bool, ...}} for templates, e.g. {% if areas.annotate %}."""
    user = getattr(request, "user", None)
    return {"areas": {key: has_area(user, key) for key in KEYS}}
