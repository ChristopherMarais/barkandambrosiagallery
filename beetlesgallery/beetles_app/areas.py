"""
What each person may use beyond a Basic account, set per person on the My Account page (and when approving an
access request).

* Basic (every account, approved automatically once the email is confirmed): the image browser with every specimen's
  page, downloads, the taxonomy browser, the interactions page, IBBI-AI and the game (Beetle Detective). Everything
  there is to read or download (owner, 2026-11-18: signing up should be simple, nobody picks permissions).
* Each area below (all of them change something) is granted on its own (AreaGrant), to anyone, staff or not, so two curators can have different
  access. Superusers have everything. "Staff" is only a preset that ticks the usual curator areas (and lets someone
  into Django's own admin pages).
* Validation is separate from editing: VALIDATE for one record at a time, BULK_VALIDATE for many at once. New
  uploads always arrive unvalidated.

Specimen pages ("details") and downloads ("download") used to be granted one by one too; they are now part of every
account (BASIC_AREAS), so old grants of them change nothing. Someone signed in can ask for more with a short note;
an approver picks from REQUESTABLE, which never includes the Admin areas.
"""
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import render

DETAILS, DOWNLOAD = "details", "download"
BOXES, ANNOTATE, VALIDATE, AI_RECOMMEND = "boxes", "annotate", "validate", "ai_recommend"
UPLOAD, UPDATE, BULK_VALIDATE, PREDICTIONS, SPECIES = "upload", "update", "bulk_validate", "predictions", "species_tables"
INTERACTIONS, NOTICE = "interactions", "site_notice"

# Part of every account: nothing to grant (has_area is True for any signed-in, active account)
BASIC_AREAS = [
    (DETAILS, "Specimen pages", "Open each specimen's page with all its details."),
    (DOWNLOAD, "Downloads", "Download images and their metadata in batches."),
]
BASIC_KEYS = {key for key, _, _ in BASIC_AREAS}

# What can be granted, grouped by the page it is used on: (page, [(key, label, description), ...])
PAGES = [
    ("Image annotation", [
        (BOXES, "Edit bounding boxes", "Draw, move and remove boxes (not names or other details)."),
        (ANNOTATE, "Edit names and records", "Change species names and image and ROI details (includes editing boxes)."),
        (VALIDATE, "Validate", "Mark images and ROIs as validated, or take that back, one at a time."),
        (AI_RECOMMEND, "AI recommendations", "Generate AI recommendations: boxes and suggested names."),
    ]),
    ("Data management", [
        (UPLOAD, "Upload new images", "Upload new images with their details (CSV and ZIP). They arrive unvalidated."),
        (UPDATE, "Update metadata", "Change existing records from a CSV (not their validation)."),
        (BULK_VALIDATE, "Bulk validate", "Validate many records at once, from an update CSV or the validation list."),
        (PREDICTIONS, "Model predictions", "Upload AI model predictions for many images."),
        (SPECIES, "Species tables", "Upload and download the accepted species and the synonyms and old names."),
    ]),
    ("Pathogens", [
        (INTERACTIONS, "Pathogen records", "Review proposed records, and upload or update records."),
    ]),
    ("Site", [
        (NOTICE, "Site notice", "Switch the notice at the top of every page on and off."),
    ]),
]
AREAS = [area for _, items in PAGES for area in items]
KEYS = [key for key, _, _ in AREAS]
LABELS = {key: label for key, label, _ in AREAS + BASIC_AREAS}
# The same areas, grouped (acc-groups, #618): what you change (Editing) and site-level configuration (Admin).
GROUP_ORDER = ["Editing", "Admin"]
GROUP_OF = {
    BOXES: "Editing", ANNOTATE: "Editing", VALIDATE: "Editing", AI_RECOMMEND: "Editing",
    UPLOAD: "Editing", UPDATE: "Editing", BULK_VALIDATE: "Editing", INTERACTIONS: "Editing",
    PREDICTIONS: "Admin", SPECIES: "Admin", NOTICE: "Admin",
}
# What an approver can give through an access request: never the Admin areas (owner, 2026-11-18). A superuser can
# still give those to someone by hand on the Account page.
REQUESTABLE = [key for key in KEYS if GROUP_OF[key] != "Admin"]
# Areas that come with another one: editing names and records includes boxes; bulk validation includes validating one at a time
INCLUDED = {BOXES: {ANNOTATE}, VALIDATE: {BULK_VALIDATE}}
# The "Staff" preset on the account page (what a curator usually needs); every box can still be changed one by one.
# Before areas were granted one by one, a curator ("staff") had these.
CURATOR_AREAS = [BOXES, ANNOTATE, VALIDATE, AI_RECOMMEND, UPLOAD, UPDATE, INTERACTIONS]


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
    """Superusers have every area; every account has the BASIC_AREAS; anyone else (staff included) has the areas granted
    to them, plus the ones those include (INCLUDED)."""
    if not (getattr(user, "is_authenticated", False) and user.is_active):
        return False
    if user.is_superuser or area in BASIC_KEYS:
        return True
    granted = granted_areas(user)
    return area in granted or bool(INCLUDED.get(area, set()) & granted)


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
    return {"areas": {key: has_area(user, key) for key in [*BASIC_KEYS, *KEYS]}}


def page_groups(granted=()):
    """For forms: [{"page": ..., "areas": [{"key", "label", "description", "checked"}]}] in PAGES order."""
    granted = set(granted)
    return [{"page": page, "areas": [{"key": k, "label": label, "description": d, "checked": k in granted}
                                     for k, label, d in items]} for page, items in PAGES]


def grouped_areas(wanted=()):
    """The REQUESTABLE areas grouped by GROUP_ORDER (so only Editing), each tagged with whether it was asked for.

    Requested areas are listed first within their group (acc-groups, acc-asked, #618), so a reviewer sees what was
    asked for without hunting through every box.
    """
    wanted = set(wanted)
    by_group = {g: [] for g in GROUP_ORDER}
    for key, label, description in AREAS:
        if key not in REQUESTABLE:
            continue
        by_group[GROUP_OF[key]].append(
            {"key": key, "label": label, "description": description, "wanted": key in wanted}
        )
    for areas_in_group in by_group.values():
        areas_in_group.sort(key=lambda a: not a["wanted"])
    return [{"group": g, "areas": by_group[g]} for g in GROUP_ORDER if by_group[g]]
