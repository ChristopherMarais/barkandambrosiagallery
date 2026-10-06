"""Views for reviewing proposed interactions and for showing accepted ones on the interactions page."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_GET

from . import interaction_data as data
from . import interaction_review as review
from .areas import INTERACTIONS, area_required
from .models import InteractionProposal

PAGE_SIZE = 20
KEPT_ON_REDIRECT = ("status", "category", "min_score", "q", "page")
STATUS_TABS = [
    (InteractionProposal.Status.PROPOSED, "Waiting"),
    (InteractionProposal.Status.ACCEPTED, "Accepted"),
    (InteractionProposal.Status.REJECTED, "Rejected"),
]


def _filters(params):
    status = params.get("status") or InteractionProposal.Status.PROPOSED
    if status not in InteractionProposal.Status.values:
        status = InteractionProposal.Status.PROPOSED
    try:
        min_score = float(params["min_score"]) if params.get("min_score") else None
    except ValueError:
        min_score = None
    return {
        "status": status, "category": (params.get("category") or "").strip(),
        "min_score": min_score, "q": (params.get("q") or "").strip()[:100],
    }


@area_required(INTERACTIONS)
def interaction_review(request):
    if request.method == "POST":
        try:
            decision, row, changed = review.decide(
                request.POST.get("claim"), request.POST.get("decision"), request.user, request.POST.get("note"),
                request.POST.get("partner_name", ""), request.POST.get("category", ""), request.POST.get("relationship", ""),
            )
        except review.ReviewError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, {
                review.ACCEPT: f"Accepted and published ({changed} source{'s' if changed != 1 else ''}).",
                review.COVERED: "Marked as already in the dataset.",
                review.REJECT: "Rejected. It will not be proposed again.",
                review.REOPEN: "Put back in the waiting list.",
            }[decision])
        target = reverse("interaction_review")
        query = urlencode({k: v for k, v in request.POST.items() if k in KEPT_ON_REDIRECT and v})
        return redirect(f"{target}?{query}" if query else target)

    filters = _filters(request.GET)
    state = {k: v for k, v in request.GET.items() if k in KEPT_ON_REDIRECT and v}   # so a form posts back to the same view
    paginator = Paginator(review.claim_groups(**filters), PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    claims = [review.decorate(c) for c in review.load_claims(page.object_list, **filters)]
    counts = {status: review.claim_groups(status=status).count() for status, _ in STATUS_TABS}
    return render(request, "beetles/interaction_review.html", {
        "claims": claims, "page": page, "filters": filters, "counts": counts,
        "tabs": [(value, label, counts[value]) for value, label in STATUS_TABS],
        "categories": review.CATEGORIES, "relationships": review.RELATIONSHIPS,
        "state": state,
        "querystring": urlencode({k: v for k, v in state.items() if k != "page"}),
    })


@require_GET
def interactions_records(request):
    """Every interaction in the database, for the interactions page's table."""
    return JsonResponse(data.master_records(), safe=False)


@require_GET
def interactions_hosts(request):
    """One line per beetle, for the page's Beetle Hosts tab."""
    return JsonResponse(data.hosts_summary(), safe=False)


@require_GET
def interactions_references(request):
    """The publications cited, for the page's References tab."""
    return JsonResponse(data.references(), safe=False)
