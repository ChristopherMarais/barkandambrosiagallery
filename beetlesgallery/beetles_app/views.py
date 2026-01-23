import os
import re
import zipfile
import shlex
import uuid, json
import subprocess
import sys
import os
import math
from datetime import date

from django.db.models import Q, Count
from django.urls import reverse
from django.conf import settings
from django.contrib import messages
from django.utils.http import http_date
from django.http import HttpResponseNotAllowed, FileResponse, HttpResponse, Http404
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_POST
from django.contrib.auth.views import LoginView as DjangoLoginView, LogoutView
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.files.storage import default_storage

from . import species_ref
from .models import Beetles, UploadBatch, DownloadJob, UpdateBatch, ImageAsset
from .schema import REQUIRED_COLS, MAX_ROWS
from .forms import TailwindUserCreationForm, ProfileForm, PasswordChangeFormStyled, ValidSpeciesUploadForm, UpdateBatchUploadForm
from .tasks import process_upload_task, process_update_task, build_downloads_task

import pandas as pd
from io import BytesIO


@login_required
def my_account(request):
    user = request.user

    if request.method == "POST":
        # Only password change is supported now
        if "password_submit" in request.POST:
            cform = PasswordChangeFormStyled(user=user, data=request.POST)
            if cform.is_valid():
                user = cform.save()
                update_session_auth_hash(request, user)  # keep them logged in
                messages.success(request, "Password changed.")
                return redirect("my_account")
        else:
            cform = PasswordChangeFormStyled(user=user)
    else:
        cform = PasswordChangeFormStyled(user=user)

    return render(
        request,
        "accounts/my_account.html",
        {"password_form": cform},
    )

class LoginViewWithRedirectMessage(DjangoLoginView):
    template_name = "accounts/signin.html"

    def get(self, request, *args, **kwargs):
        # self.redirect_field_name is "next" by default
        if request.GET.get(self.redirect_field_name):
            messages.info(request, "Please log in to continue.")
        return super().get(request, *args, **kwargs)

class PostOnlyLogoutView(LogoutView):
    def dispatch(self, request, *args, **kwargs):
        # Only allow POST; reject GET
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        return super().dispatch(request, *args, **kwargs)


def landing(request):
    # 1. Total number of images
    total_images = ImageAsset.objects.count()

    # 2. Get all distinct valid_name_ids associated with actual images
    # We need the actual list of IDs to look up taxonomy (Genera) in the CSV reference
    present_ids_qs = Beetles.objects.exclude(depicts_valid_name_id__isnull=True)\
                                    .exclude(depicts_valid_name_id="")\
                                    .values_list('depicts_valid_name_id', flat=True)\
                                    .distinct()
    
    present_ids = list(present_ids_qs)

    # Count unique species (number of unique valid IDs)
    total_species = len(present_ids)

    # 3. Count unique Genera
    # Use the helper in species_ref to map IDs -> Genera -> Unique Set
    unique_genera_set = species_ref.get_field_values_for_ids(present_ids, "genus")
    total_genera = len(unique_genera_set)

    # 4. Count unique specimen IDs that have a Type Status
    type_status_count = Beetles.objects.exclude(specimen_type_status__isnull=True)\
                                       .exclude(specimen_type_status="")\
                                       .exclude(depicts_specimen__isnull=True)\
                                       .exclude(depicts_specimen="")\
                                       .values('depicts_specimen')\
                                       .distinct()\
                                       .count()

    context = {
        'total_images': total_images,
        'total_species': total_species,
        'total_genera': total_genera,  # Added this
        'type_status_count': type_status_count,
    }
    return render(request, 'landing.html', context)


def _normalize_valid_id_for_lookup(v):
    """
    Normalize depicts_valid_name_id for lookup in species_ref.

    - Blank/None -> None
    - Floats like 123.0 -> "123"
    - Strings like "123.0" -> "123"
    - Otherwise -> stripped string
    """
    if v is None:
        return None

    # Handle blank strings
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
    else:
        s = str(v).strip()

    # Try float interpretation to strip .0 where appropriate
    try:
        f = float(s)
        if math.isnan(f):
            return None
        if f.is_integer():
            return str(int(f))
    except ValueError:
        # Not a float-like string; just return stripped
        return s

    # Non-integer floats (e.g. "123.5") stay as their original string
    return s

@login_required(login_url='login')
def upload_file(request):
    print("DEBUG: entered upload_file view", flush=True)
    if request.method != "POST":
        return render(request, "beetles/upload.html")

    # --- require both files present ---
    xlsx = request.FILES.get("xlsx")
    zipf = request.FILES.get("zip")
    if not xlsx or not zipf:
        messages.error(request, "Please attach both an .xlsx metadata file and a .zip of images.")
        return redirect("upload")

    # --- allowlist + size guard ---
    ext_x = os.path.splitext(xlsx.name)[1].lower()
    ext_z = os.path.splitext(zipf.name)[1].lower()

    if ext_x != ".xlsx":
        messages.error(request, "The metadata file must be a .xlsx.")
        return redirect("upload")
    if ext_z != ".zip":
        messages.error(request, "The images archive must be a .zip.")
        return redirect("upload")

    XLSX_MAX = getattr(settings, "MAX_UPLOAD_SIZE_XLSX", 10 * 1024 * 1024)        # 10 MB
    ZIP_MAX  = getattr(settings, "MAX_UPLOAD_SIZE_ZIP",  1024 * 1024 * 1024)      # 1 GB

    if xlsx.size and xlsx.size > XLSX_MAX:
        messages.error(request, f"Metadata .xlsx is too large (> {XLSX_MAX // (1024*1024)} MB).")
        return redirect("upload")

    if zipf.size and zipf.size > ZIP_MAX:
        messages.error(request, f"Images .zip is too large (> {ZIP_MAX // (1024*1024)} MB).")
        return redirect("upload")

    # --- Quick check that ZIP is valid and contains at least one entry ---
    try:
        with zipfile.ZipFile(zipf) as z:
            names = z.namelist()
            if not names:
                messages.error(request, "The images ZIP is empty.")
                return redirect("upload")
    except zipfile.BadZipFile:
        messages.error(request, "The images ZIP is corrupt or not a valid ZIP.")
        return redirect("upload")

    # Reset pointer
    try:
        zipf.seek(0)
    except Exception:
        pass

    # --- create batch row first (to get a UUID id for both file names) ---
    batch = UploadBatch.objects.create(
        uploaded_by=request.user,
        original_filename=xlsx.name,     # keep XLSX name for display
        status=UploadBatch.Status.STAGING,
    )

    # Saving will use your upload_to=staging_upload_path_xlsx/zip and name them <batch-id>.(xlsx|zip)
    batch.file.save(xlsx.name, xlsx, save=False)
    batch.zip_file.save(zipf.name, zipf, save=False)
    batch.size_bytes = xlsx.size or 0
    # Compute checksum of the XLSX (used by your existing admin display)
    try:
        batch.compute_sha256_from_disk()
    except Exception:
        pass
    batch.save()
    print("DEBUG: after batch.save, batch id=", batch.id, "status=", batch.status, flush=True)

    # --- quick preflight of the XLSX (single sheet) ---
    if pd is None:
        print("DEBUG: pd is None; returning early (no worker spawned)", flush=True)
        messages.warning(request, "Uploaded. Note: server missing pandas; skipping quick XLSX checks.")
        return redirect("upload")

    errors = []
    try:
        df = pd.read_excel(batch.file.path)
        df.columns = [c.strip() for c in df.columns]
    except Exception as e:
        batch.mark_rejected_and_move(f"Cannot open workbook: {e}")
        messages.error(request, "Upload rejected: cannot open workbook.")
        return redirect("upload")

    # Required headers
    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        errors.append(f"Missing required columns: {sorted(missing)}")

    # Optional size cap
    if MAX_ROWS is not None and len(df) > MAX_ROWS:
        errors.append(f"Sheet has {len(df)} rows (max {MAX_ROWS}).")

    print("DEBUG: preflight complete, errors=", errors, flush=True)

    if errors:
        reason = "; ".join(map(str, errors))[:2000]
        batch.mark_rejected_and_move(reason)
        messages.error(request, "Upload rejected: " + reason)
        return redirect("upload")

    # Pass preflight; full validator will hash images, check 1:1 mapping, etc.
    # Kick off background processing for this batch (validate + import)
    process_upload_task.delay(batch.id)
    print(f"Queued process_single_upload for batch {batch.id}", flush=True)

    messages.success(request, "Files received and passed quick checks. Track upload status on My Files page under My Uploads. You may leave this page.")
    return redirect("upload")

def gallery(request):
    from .utils import build_query_q, filter_beetles_queryset, FILTERS_CONFIG
    NA = "N/A"
    try:
        page_size = int(request.GET.get("per_page", 12))
    except (ValueError, TypeError):
        page_size = 12

    WARN_IMAGE_SIZE_BYTES = getattr(settings, "WARN_IMAGE_SIZE_BYTES", 10 * 1024 * 1024)
    base_qs = Beetles.objects.all().order_by("-id")

    # 1. Text Search
    raw_q = request.GET.get("q", "").strip()
    if raw_q:
        q_obj, ignored = build_query_q(raw_q)
        base_search_qs = base_qs.filter(q_obj)
        ignored_tokens = ignored
    else:
        base_search_qs = base_qs
        ignored_tokens = []

    # 2. Capture Active Filters (Updated for Multi-Select)
    active_filters = {}
    for cfg in FILTERS_CONFIG:
        # Use getlist to capture multiple values (e.g. ?country=USA&country=Canada)
        vals = request.GET.getlist(cfg["param"])
        # Clean and filter empty strings
        clean_vals = [v.strip() for v in vals if v.strip()]
        if clean_vals:
            active_filters[cfg["param"]] = clean_vals

    # Capture Range Filters (Size & Resolution)
    size_min = request.GET.get("size_min", "").strip()
    size_max = request.GET.get("size_max", "").strip()
    res_min = request.GET.get("res_min", "").strip()
    res_max = request.GET.get("res_max", "").strip()

    def apply_filters(qs, filters_dict, exclude_param=None):
        return filter_beetles_queryset(qs, filters_dict, size_min, size_max, res_min, res_max, exclude_param)

    # 3. Final Results
    final_qs = apply_filters(base_search_qs, active_filters, exclude_param=None)

    # 4. Build Dynamic Options
    from collections import defaultdict
    grouped_filters = defaultdict(list)
    
    categories = []
    seen_cats = set()
    for cfg in FILTERS_CONFIG:
        if cfg["category"] not in seen_cats:
            categories.append(cfg["category"])
            seen_cats.add(cfg["category"])

    for cfg in FILTERS_CONFIG:
        param = cfg["param"]
        ctx_qs = apply_filters(base_search_qs, active_filters, exclude_param=param)
        
        options = []
        if cfg["type"] == "db":
            # 1. Get real values
            opts_qs = ctx_qs.exclude(**{f"{cfg['field']}__isnull": True})
            
            # Date fields cannot be empty strings, so only exclude nulls for them
            if cfg["field"] == "image_asset__image_date_taken":
                options = list(opts_qs.values_list(cfg['field'], flat=True).distinct().order_by(cfg['field']))
                # Use only isnull check for dates to avoid ValidationError
                na_check = ctx_qs.filter(**{f"{cfg['field']}__isnull": True}).exists()
            else:
                # Standard fields: exclude both null and empty string
                opts_qs = opts_qs.exclude(**{f"{cfg['field']}": ""})
                options = list(opts_qs.values_list(cfg['field'], flat=True).distinct().order_by(cfg['field']))
                na_check = ctx_qs.filter(Q(**{f"{cfg['field']}__isnull": True}) | Q(**{f"{cfg['field']}": ""})).exists()
            
            if na_check:
                options.insert(0, NA)
            
        elif cfg["type"] == "bool":
            options = ["Yes", "No"]
            
        elif cfg["type"] == "ref":
            # 1. Get real values via species_ref
            used_ids = ctx_qs.exclude(depicts_valid_name_id__isnull=True).values_list('depicts_valid_name_id', flat=True).distinct()
            vals = species_ref.get_field_values_for_ids(used_ids, cfg["field"])
            options = sorted(list(vals))

            # 2. Add N/A if unidentified records exist
            if ctx_qs.filter(depicts_valid_name_id__isnull=True).exists():
                options.insert(0, NA)

        # Check if options exist OR if this filter is currently active
        if options or active_filters.get(param):
            grouped_filters[cfg["category"]].append({
                "param": param,
                "label": cfg["label"],
                "options": options,
                "selected": active_filters.get(param, []), # Pass the LIST of selected values
            })

    filter_context = []
    for cat in categories:
        if grouped_filters[cat]:
            filter_context.append((cat, grouped_filters[cat]))

    # 5. Pagination
    final_qs = final_qs.order_by("image_asset", "id").distinct("image_asset")
    final_qs = final_qs.select_related("image_asset").prefetch_related("image_asset__specimens")
    paginator = Paginator(final_qs, page_size)
    page = request.GET.get("page", 1)
    try:
        beetles_page = paginator.page(page)
    except PageNotAnInteger:
        beetles_page = paginator.page(1)
    except EmptyPage:
        beetles_page = paginator.page(paginator.num_pages)

    # Enrichment
    for b in beetles_page.object_list:
        # 1. Resolve Reference for the *representative* beetle
        raw_id = (str(b.depicts_valid_name_id).strip() if b.depicts_valid_name_id else None)
        ref = species_ref.resolve(raw_id)
        
        def clean(val): return val if val and val.lower() != "unknown" else None
        b.ref_scientificName = clean(ref.get("scientificName"))
        b.ref_genus = clean(ref.get("genus"))
        b.ref_species = clean(ref.get("species"))
        b.ref_subfamily = clean(ref.get("subfamily"))
        b.ref_tribe = clean(ref.get("tribe"))
        b.ref_subtribe = clean(ref.get("subtribe"))
        b.ref_subspecies = clean(ref.get("subspecies"))

        # 2. Aggregation Logic
        if b.image_asset:
            siblings = b.image_asset.specimens.all()
            b.siblings_count = len(siblings)
            
            # Helper: Check if there is more than 1 unique value (counting None as a value)
            def check_multiple(attr):
                values = {getattr(s, attr) for s in siblings}
                return len(values) > 1

            b.has_multiple_aspect = check_multiple("aspect")
            b.has_multiple_country = check_multiple("collection_country")
            b.has_multiple_sex = check_multiple("specimen_sex")
            
            # Taxonomy Aggregation
            # Get all Valid IDs involved in this image
            ids = {s.depicts_valid_name_id for s in siblings}
            
            # If we have >1 unique ID (e.g. "123" and None), we might have multiple ranks
            if len(ids) > 1:
                # Resolve all involved IDs to check ranks
                refs = [species_ref.resolve(i) for i in ids]
                
                def check_rank(key):
                    # collect unique values for this rank (e.g. "Platypus", "Crossotarsus")
                    vals = {r.get(key) for r in refs}
                    return len(vals) > 1
                
                b.has_multiple_subfamily = check_rank("subfamily")
                b.has_multiple_tribe = check_rank("tribe")
                b.has_multiple_subtribe = check_rank("subtribe")
                b.has_multiple_genus = check_rank("genus")
                b.has_multiple_species = check_rank("species")
                b.has_multiple_subspecies = check_rank("subspecies")
            else:
                # All beetles have the same ID (or all None), so ranks are identical
                b.has_multiple_subfamily = False
                b.has_multiple_tribe = False
                b.has_multiple_subtribe = False
                b.has_multiple_genus = False
                b.has_multiple_species = False
                b.has_multiple_subspecies = False
            
            b.warn_large = (b.image_asset.image_size_bytes or 0) >= WARN_IMAGE_SIZE_BYTES
        else:
            b.siblings_count = 0
            b.warn_large = False

    return render(
        request,
        "beetles/home.html", 
        {
            "beetles": beetles_page,
            "paginator": paginator,
            "page_obj": beetles_page,
            "is_paginated": beetles_page.has_other_pages(),
            "q": raw_q,
            "ignored_tokens": ignored_tokens,
            "total_matches": final_qs.count(),
            "warn_size_bytes": WARN_IMAGE_SIZE_BYTES,
            "filter_groups": filter_context,
            "selected_filters": active_filters,
            "per_page": page_size,
            "size_min": size_min,
            "size_max": size_max,
            "res_min": res_min,
            "res_max": res_max,
        },
    )


def beetle_detail(request, beetle_id):
    if not request.user.is_authenticated:
        login_url = reverse("login")
        return redirect(f"{login_url}?next={request.path}")

    # Fetch main object
    beetle = get_object_or_404(Beetles, pk=beetle_id)

    # 1. Siblings (Same Image, different specimen records) - For Pagination inside the card
    siblings = []
    if beetle.image_asset:
        siblings = list(
            beetle.image_asset.specimens.all()
            .order_by("id") 
        )

    # Calculate pagination context
    prev_sibling = None
    next_sibling = None
    current_index = 0
    total_siblings = len(siblings)

    if total_siblings > 1:
        for i, s in enumerate(siblings):
            if s.id == beetle.id:
                current_index = i + 1
                if i > 0:
                    prev_sibling = siblings[i - 1]
                if i < total_siblings - 1:
                    next_sibling = siblings[i + 1]
                break

    # 2. Related Specimens (Same Specimen ID, different Images) - For "More images" section
    related_specimens = []
    if beetle.depicts_specimen and beetle.depicts_specimen.strip():
        related_specimens = (
            Beetles.objects
            .filter(depicts_specimen=beetle.depicts_specimen)
            .exclude(pk=beetle.id)  # Exclude current record
            .order_by("image_asset", "id")
            .distinct("image_asset") # One card per image
            .select_related("image_asset")
        )

    # CSV-based enrichment
    raw_vid = beetle.depicts_valid_name_id
    norm_vid = _normalize_valid_id_for_lookup(raw_vid)
    ref_species = species_ref.resolve(norm_vid) if norm_vid is not None else None
    ref_version = species_ref.get_version()

    return render(
        request,
        "beetles/detail.html",
        {
            "beetle": beetle, 
            "ref_species": ref_species, 
            "ref_version": ref_version,
            "siblings": siblings,
            "total_siblings": total_siblings,
            "current_sibling_index": current_index,
            "prev_sibling": prev_sibling,
            "next_sibling": next_sibling,
            "related_specimens": related_specimens,
        },
    )


def signup(request):
    # --- Security Check: Block non-staff users ---
    if not request.user.is_staff:
        messages.error(request, "Account creation is restricted. Please email Jiri Hulcr at hulcr@ufl.edu to request an account.")
        return redirect("beetles_home")
    # ---------------------------------------------
    
    if request.method == "POST":
        form = TailwindUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            # save email if you included it in the template
            email = request.POST.get("email", "").strip()
            if email:
                user.email = email
                user.save(update_fields=["email"])
            
            # Do not log them in automatically; send them to login page with a message
            messages.success(request, "Username created successfully. Please log in.")
            return redirect("login")
    else:
        form = TailwindUserCreationForm()
    return render(request, "accounts/signup.html", {"form": form})

@login_required
@require_POST
def start_batch_download(request):
    from .utils import FILTERS_CONFIG

    """
    Create a DownloadJob for either:
      - selection_mode=ids: comma-separated UUIDs in 'selected_ids'
      - selection_mode=query: current q string in 'q' PLUS any advanced filters in POST
    This records intent only; files are built later by a worker/command.
    """
    mode = (request.POST.get("selection_mode") or "").strip()
    q_str = (request.POST.get("q") or "").strip()
    total = request.POST.get("total_matches")
    try:
        total = int(total) if total is not None else 0
    except ValueError:
        total = 0

    if mode not in ("ids", "query"):
        messages.error(request, "Invalid selection mode.")
        return redirect("home")

    # If query mode, we need to capture ALL filters, not just 'q'.
    # We will serialize them into query_string as JSON.
    final_query_string = ""
    
    if mode == "query":
        # 1. Harvest Facet Filters
        active_filters = {}
        for cfg in FILTERS_CONFIG:
            vals = request.POST.getlist(cfg["param"])
            clean_vals = [v.strip() for v in vals if v.strip()]
            if clean_vals:
                active_filters[cfg["param"]] = clean_vals

        # 2. Harvest Ranges
        ranges = {
            "size_min": request.POST.get("size_min", "").strip(),
            "size_max": request.POST.get("size_max", "").strip(),
            "res_min": request.POST.get("res_min", "").strip(),
            "res_max": request.POST.get("res_max", "").strip(),
        }
        
        # 3. Pack into JSON
        query_payload = {
            "q": q_str,
            "filters": active_filters,
            "ranges": ranges
        }
        final_query_string = json.dumps(query_payload)

    job = DownloadJob.objects.create(
        requested_by=request.user,
        selection_mode=mode,
        query_string=final_query_string if mode == "query" else "",
        total_requested=total if mode == "query" else 0,
        status=DownloadJob.Status.PENDING,
    )

    if mode == "ids":
        # Accept common patterns
        raw_ids_str = (request.POST.get("selected_ids") or "").strip()
        raw_ids_list = request.POST.getlist("selected_ids")

        if not raw_ids_str and raw_ids_list:
            raw_ids_str = ",".join(raw_ids_list)

        if not raw_ids_str:
            alt_list = (
                request.POST.getlist("selected_ids[]")
                or request.POST.getlist("selected")
                or []
            )
            if alt_list:
                raw_ids_str = ",".join(alt_list)

        # Parse UUIDs from CSV string
        ids = []
        seen = set()
        for s in [x.strip() for x in raw_ids_str.split(",") if x.strip()]:
            try:
                uuid.UUID(s)
                if s not in seen:
                    ids.append(s)
                    seen.add(s)
            except Exception:
                pass

        # --- LAST-CHANCE RESCUE ---
        # If still empty, scan ALL POST fields for anything that looks like a UUID.
        if not ids:
            for k in request.POST.keys():
                for v in request.POST.getlist(k):
                    sv = (v or "").strip()
                    try:
                        uuid.UUID(sv)
                        if sv not in seen:
                            ids.append(sv)
                            seen.add(sv)
                    except Exception:
                        continue
        
        if not ids:
            # Keep the job (FAILED) so it appears in My Files for visibility.
            job.status = DownloadJob.Status.FAILED
            job.error_message = "No valid rows were selected (IDs missing or malformed)."
            job.save(update_fields=["status", "error_message"])
            messages.error(request, "No valid rows were selected.")
            return redirect("my_uploads")

        job.set_ids(ids)
        job.total_requested = len(ids)
        job.save(update_fields=["selected_ids_json", "total_requested"])


    build_downloads_task.delay(job.id)
    messages.success(request, "Download started. Track progress in My Files.")
    return redirect("my_uploads")
    

@login_required
def my_uploads(request):
    batches = (
        UploadBatch.objects
        .filter(uploaded_by=request.user)
        .order_by("-created_at")
    )

    download_jobs = (
        DownloadJob.objects
        .filter(requested_by=request.user)
        .order_by("-created_at")
    )

    # Staff-only updates listing
    if request.user.is_staff:
        update_batches = (
            UpdateBatch.objects
            .filter(uploaded_by=request.user)
            .order_by("-created_at")
        )
    else:
        update_batches = []

    return render(
        request, 
        "beetles/my_uploads.html", 
        {
        "batches": batches, 
        "download_jobs": download_jobs,
        "update_batches": update_batches
        }
    )

@login_required(login_url='login')
def download_taxonomy_ref(request):
    """
    Stream the latest valid_species.csv to a logged-in user with a stable, UTC-stamped filename.
    Adds ETag and Cache-Control for client/proxy caching. If the file is missing, 404.
    """
    storage_key = getattr(settings, "VALID_SPECIES_PATH", "reference/valid_species.csv")

    # Ensure the file exists / can be opened
    try:
        f = default_storage.open(storage_key, "rb")
    except Exception:
        raise Http404("Taxonomy reference file is not available.")

    # Build a nice filename based on storage mtime (UTC), per Step 1
    filename = species_ref.build_download_filename()

    # ETag from the published version hash (species_ref publishes this to cache on update)
    etag = species_ref.get_version()
    quoted_etag = f'"{etag}"' if etag else None

    # If-None-Match short-circuit (304)
    inm = request.META.get("HTTP_IF_NONE_MATCH", "")
    if quoted_etag and quoted_etag in inm:
        try:
            f.close()
        except Exception:
            pass
        resp = HttpResponse(status=304)
        resp["ETag"] = quoted_etag
        resp["Cache-Control"] = "public, max-age=0, must-revalidate"
        # Best-effort Last-Modified
        try:
            lm = default_storage.get_modified_time(storage_key)
            resp["Last-Modified"] = http_date(lm.timestamp())
        except Exception:
            pass
        return resp

    # Stream the file
    resp = FileResponse(f, content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    if quoted_etag:
        resp["ETag"] = quoted_etag
    resp["Cache-Control"] = "public, max-age=0, must-revalidate"
    # Best-effort Last-Modified
    try:
        lm = default_storage.get_modified_time(storage_key)
        resp["Last-Modified"] = http_date(lm.timestamp())
    except Exception:
        pass

    return resp

@login_required(login_url='login')
def admin_valid_species(request):
    """
    Minimal admin page to publish a new valid_species.csv into default_storage
    and set the user-facing label.
    """
    current_status = species_ref.status()
    if request.method == "POST":
        form = ValidSpeciesUploadForm(request.POST, request.FILES)
        if form.is_valid():
            f = form.cleaned_data["csv_file"]
            label = form.cleaned_data.get("label") or None

            # Save the upload to a temp file, then call the publisher helper
            from pathlib import Path
            import tempfile

            # use a real temp file (container-/OS-friendly)
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                for chunk in f.chunks():
                    tmp.write(chunk)
                tmp_path = tmp.name

            try:
                rows, version = species_ref.publish_from_file(tmp_path, label=label)
                messages.success(
                    request,
                    f"Published valid_species.csv ({rows} rows)."
                )
                # After publish, refresh status for display
                current_status = species_ref.status()
            except Exception as e:
                messages.error(request, f"Publish failed: {e}")
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

            return redirect("admin_valid_species")
    else:
        form = ValidSpeciesUploadForm()

    return render(
        request,
        "admin/tools_valid_species.html",
        {"form": form, "status": current_status},
    )

# Columns allowed to be overwritten, plus the required Record ID.
UPDATE_ALLOWED_FIELDS = [
    "alternative_id",
    "image_institution",
    "photographer",
    "image_email",
    "photo_usage_statement",
    "aspect",
    "resolution_in_ppmm",
    "image_notes",
    "image_date_taken",
    "image_has_multiple_individuals",
    "depicts_specimen",
    "depicts_valid_name_id",
    "depicts_described_name_id",
    "depicts_name_verbatim",
    "collection_country",
    "collection_stateProvince",
    "specimen_sex",
    "specimen_type_status",
    "specimen_notes",
]

# Required header set for updates:
# - "record_id" (the Beetles primary key) is MANDATORY
# - All updateable fields must be present (all-or-nothing overwrite policy)
# - "update_notes" is optional
UPDATE_REQUIRED_COLS = {"record_id"} | set(UPDATE_ALLOWED_FIELDS)
UPDATE_OPTIONAL_COLS = {"update_notes"}


@login_required
@login_required(login_url='login')
def update_upload(request):
    """
    Staff-only portal to submit an XLSX of metadata updates by Record ID (UUID).
    Quick preflight:
      - .xlsx present and within size limit
      - headers match exactly (required set + optional 'update_notes', no extras)
      - pandas can open workbook
    Creates an UpdateBatch in 'staging'. Full validation/diff/apply comes next steps.
    """
    if request.method != "POST":
        return render(request, "beetles/update_upload.html")

    xlsx = request.FILES.get("xlsx")
    if not xlsx:
        messages.error(request, "Please attach a .xlsx file.")
        return redirect("update_upload")

    ext = os.path.splitext(xlsx.name)[1].lower()
    if ext != ".xlsx":
        messages.error(request, "The update file must be a .xlsx.")
        return redirect("update_upload")

    XLSX_MAX = getattr(settings, "MAX_UPLOAD_SIZE_XLSX", 10 * 1024 * 1024)  # 10 MB default
    if xlsx.size and xlsx.size > XLSX_MAX:
        messages.error(request, f"Update .xlsx is too large (> {XLSX_MAX // (1024*1024)} MB).")
        return redirect("update_upload")

    # Create the UpdateBatch row so we have an ID + on-disk path
    batch = UpdateBatch.objects.create(
        uploaded_by=request.user,
        original_filename=xlsx.name,
        status=UpdateBatch.Status.STAGING,
    )
    batch.file.save(xlsx.name, xlsx, save=False)
    batch.size_bytes = batch.file.size or 0
    # Best-effort checksum for idempotency later
    try:
        batch.compute_sha256_from_disk()
    except Exception:
        pass
    batch.save()

    # Quick preflight with pandas (header checks only)
    if pd is None:
        messages.warning(request, "File received. Note: server missing pandas; skipping quick XLSX checks. Full validation will run later.")
        return redirect("update_upload")

    errors = []
    try:
        df = pd.read_excel(batch.file.path)
        # normalize headers (strip)
        df.columns = [str(c).strip() for c in df.columns]
    except Exception as e:
        batch.mark_rejected_and_move(f"Cannot open workbook: {e}")
        messages.error(request, "Update rejected: cannot open workbook.")
        return redirect("update_upload")

    # Header contract: exact match = required set + optional 'update_notes'; no extras, no missing.
    cols = set(df.columns)

    missing = UPDATE_REQUIRED_COLS - cols
    extras = cols - (UPDATE_REQUIRED_COLS | UPDATE_OPTIONAL_COLS)

    if missing:
        errors.append(f"Missing required columns: {sorted(missing)}")
    if extras:
        errors.append(f"Unexpected extra columns: {sorted(extras)}")

    if "record_id" in cols:
        # light sanity: looks like UUIDs?
        sample_ids = df["record_id"].dropna().astype(str).head(20).tolist()
        badly_formed = []
        for s in sample_ids:
            try:
                uuid.UUID(str(s).strip())
            except Exception:
                badly_formed.append(s)
                if len(badly_formed) >= 3:
                    break
        if badly_formed:
            errors.append(f"'record_id' appears to contain non-UUID values (examples: {badly_formed[:3]})")

    if errors:
        reason = "; ".join(map(str, errors))[:2000]
        batch.mark_rejected_and_move(reason)
        messages.error(request, "Update rejected: " + reason)
        return redirect("update_upload")

    # -- Starting background validate+apply for this update batch --
    process_update_task.delay(batch.id)

    messages.success(
        request,
        "Update file received and passed quick checks. "
        "Full validation and apply will now run in the background; "
        "you can track status on My Files → My Updates."
    )
    # Send them where they can see the batch status
    return redirect("my_uploads")

@login_required
@login_required(login_url='login')
@require_POST
def update_single_beetle(request, beetle_id):
    """
    Receives individual field edits. Handles fields for both Beetles and ImageAsset.
    """
    beetle = get_object_or_404(Beetles, pk=beetle_id)
    
    row_data = {"record_id": str(beetle.id)}
    
    # Combined list of fields form detail.html
    fields = [
        "depicts_specimen", "depicts_valid_name_id", "depicts_described_name_id", 
        "alternative_id", "depicts_name_verbatim", "image_institution", 
        "photographer", "image_email", "photo_usage_statement", "aspect", 
        "resolution_in_ppmm", "image_date_taken", "image_has_multiple_individuals", 
        "collection_country", "collection_stateProvince", "specimen_sex", 
        "specimen_type_status", "image_notes", "specimen_notes"
    ]
    
    for f in fields:
        val = request.POST.get(f)
        if f == "image_has_multiple_individuals":
            if val == "unknown": val = ""
        row_data[f] = val

    _run_update_batch(request, row_data, f"single_edit_{beetle.id}.xlsx")
    messages.success(request, "Update queued successfully. Changes will appear shortly.")
    return redirect("beetle_detail", beetle_id=beetle_id)


@login_required
@login_required(login_url='login')
@require_POST
def create_specimen_for_image(request, image_id):
    """
    Creates a NEW beetle record linked to an existing ImageAsset via 'Plus' button.
    Forces 'image_has_multiple_individuals' to True.
    """
    image_asset = get_object_or_404(ImageAsset, pk=image_id)
    
    # 1. Update flag immediately on the image
    if not image_asset.image_has_multiple_individuals:
        image_asset.image_has_multiple_individuals = True
        image_asset.save(update_fields=['image_has_multiple_individuals'])

    # 2. Prepare payload for UpdateBatch (Special "NEW" mode)
    row_data = {
        "record_id": "NEW",
        "link_image_uuid": str(image_asset.id),
        # Ensure we send 'True' so the new record is consistent
        "image_has_multiple_individuals": "True" 
    }
    
    # Capture only Beetle-specific fields from the form
    beetle_fields = [
        "depicts_specimen", "depicts_valid_name_id", "depicts_described_name_id", 
        "alternative_id", "depicts_name_verbatim", "aspect", 
        "collection_country", "collection_stateProvince", "specimen_sex", 
        "specimen_type_status", "specimen_notes"
    ]
    
    for f in beetle_fields:
        row_data[f] = request.POST.get(f)

    _run_update_batch(request, row_data, f"add_specimen_{image_asset.id.hex[:8]}.xlsx")
    
    messages.success(request, "Update queued successfully. Changes will appear shortly.")
    return redirect(request.META.get('HTTP_REFERER', 'home'))


def _run_update_batch(request, row_data, filename):
    """Helper to package row_data into an XLSX and spawn the processor."""
    df = pd.DataFrame([row_data])
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False)
    buffer.seek(0)
    
    batch = UpdateBatch.objects.create(
        uploaded_by=request.user,
        original_filename=filename,
        status=UpdateBatch.Status.STAGING,
    )
    
    from django.core.files.base import ContentFile
    batch.file.save(filename, ContentFile(buffer.read()), save=False)
    batch.size_bytes = batch.file.size
    batch.save()

    # Trigger
    process_update_task.delay(batch.id)