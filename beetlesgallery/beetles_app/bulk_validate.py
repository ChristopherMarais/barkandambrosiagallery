"""
Bulk validation (Data Management): a list of unvalidated images with their boxes and names, for someone granted
"Bulk validate" to check by eye and validate many at once. Validating an image validates every boxed ROI on it
(ImageAsset.validate), exactly as the annotation page's "Validate all" does.
"""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.shortcuts import redirect, render
from django.urls import reverse

from .areas import BULK_VALIDATE, area_required
from .models import Beetles, ImageAsset

PER_PAGE = 48
MAX_AT_ONCE = 500


def unvalidated_images(q="", source="", named_only=False):
    """Unvalidated images that have at least one box (only those can be validated), newest first."""
    rois = Beetles.objects.filter(is_deleted=False)
    images = ImageAsset.objects.filter(is_deleted=False, is_validated=False).filter(
        Exists(rois.filter(image_asset=OuterRef("pk"), bbox_x__isnull=False)))
    if q:
        named = rois.filter(image_asset=OuterRef("pk")).filter(
            Q(taxon__scientific_name__icontains=q) | Q(depicts_name_verbatim__icontains=q))
        images = images.filter(Q(Exists(named)) | Q(image_institution__icontains=q) | Q(full_path_at_import__icontains=q))
    if source:
        images = images.filter(Exists(rois.filter(image_asset=OuterRef("pk"), label_source=source)))
    if named_only:
        images = images.exclude(Exists(rois.filter(image_asset=OuterRef("pk"), taxon__isnull=True)))
    return images.order_by("-created_at", "pk")


@area_required(BULK_VALIDATE)
def bulk_validate(request):
    if request.method == "POST":
        ids = request.POST.getlist("image")[:MAX_AT_ONCE]
        done = 0
        for image in unvalidated_images().filter(pk__in=ids):
            done += bool(image.validate(user=request.user))
        if done:
            messages.success(request, f"Validated {done} image{'s' if done != 1 else ''} and their boxed ROIs.")
        else:
            messages.info(request, "Nothing was selected, so nothing was validated.")
        return redirect(request.POST.get("next") or reverse("bulk_validate"))

    q = (request.GET.get("q") or "").strip()[:100]
    source = request.GET.get("source") or ""
    if source not in Beetles.LabelSource.values:
        source = ""
    named_only = request.GET.get("named") == "1"
    images = unvalidated_images(q, source, named_only).annotate(roi_count=Count("specimens", filter=Q(specimens__is_deleted=False)))
    page = Paginator(images, PER_PAGE).get_page(request.GET.get("page"))
    page.object_list = list(page.object_list.prefetch_related(Prefetch(
        "specimens", queryset=Beetles.objects.filter(is_deleted=False).select_related("taxon").order_by("bbox_y", "bbox_x"),
        to_attr="active_rois")))
    return render(request, "beetles/bulk_validate.html", {
        "page": page, "q": q, "source": source, "named_only": named_only,
        "sources": Beetles.LabelSource.choices, "total": page.paginator.count,
    })
