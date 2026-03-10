import os
import re
import zipfile
import shlex
import uuid, json
import subprocess
import sys
import os
import math
import requests
import time
from datetime import date, timedelta
from io import BytesIO

from django.db.models import Q, Count
from django.utils import timezone
from django.urls import reverse
from django.conf import settings
from django.contrib import messages
from django.utils.http import http_date
from django.http import HttpResponseNotAllowed, FileResponse, HttpResponse, Http404, JsonResponse, StreamingHttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth import login, update_session_auth_hash, get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_POST
from django.contrib.auth.views import LoginView as DjangoLoginView, LogoutView
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.files.storage import default_storage

from . import species_ref, described_names_ref
from .models import Beetles, UploadBatch, DownloadJob, UpdateBatch, ImageAsset
from .schema import REQUIRED_COLS, MAX_ROWS
from .forms import TailwindUserCreationForm, ProfileForm, PasswordChangeFormStyled, ValidSpeciesUploadForm, DescribedNamesUploadForm, UpdateBatchUploadForm
from .tasks import process_upload_task, process_update_task, build_downloads_task

import pandas as pd
from io import BytesIO, StringIO

MODAL_API_URL = "https://christophermarais--ibbi-api-fastapi-app.modal.run/analyze"

# Custom decorator for superuser-only views
def superuser_required(view_func):
    """
    Decorator that requires the user to be a superuser.
    Redirects to login if not authenticated, raises 403 if not superuser.
    """
    decorated_view = user_passes_test(
        lambda u: u.is_superuser,
        login_url='login'
    )(view_func)
    return decorated_view

@login_required
def my_account(request):
    user = request.user

    # Default: Initialize empty forms
    password_form = PasswordChangeFormStyled(user=user)
    create_user_form = TailwindUserCreationForm()
    active_modal = None

    if request.method == "POST":
        # --- CASE 1: Change Password ---
        if "action_change_password" in request.POST:
            password_form = PasswordChangeFormStyled(user=user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)  # Keep user logged in
                messages.success(request, "Password changed successfully.")
                return redirect("my_account")
            else:
                active_modal = "modal-password"
                messages.error(request, "Please correct the errors in the password form.")

        # --- CASE 2: Create User (Staff Only) ---
        elif "action_create_user" in request.POST:
            if not user.is_staff:
                messages.error(request, "You do not have permission to create users.")
                return redirect("my_account")

            create_user_form = TailwindUserCreationForm(request.POST)
            if create_user_form.is_valid():
                new_user = create_user_form.save()
                messages.success(request, f"User '{new_user.username}' created successfully.")
                return redirect("my_account")
            else:
                active_modal = "modal-create-user"
                messages.error(request, "Please correct the errors in the user creation form.")

        # --- CASE 3: Edit User (Staff Only) ---
        elif "action_edit_user" in request.POST:
            if not user.is_staff:
                messages.error(request, "Permission denied.")
                return redirect("my_account")
            
            target_id = request.POST.get("user_id")
            target_user = get_object_or_404(get_user_model(), pk=target_id)
            
            # 1. Update Username
            new_username = request.POST.get("username", "").strip()
            if new_username and new_username != target_user.username:
                if get_user_model().objects.filter(username=new_username).exists():
                    messages.error(request, f"Username '{new_username}' is already taken.")
                    return redirect("my_account")
                target_user.username = new_username

            # 2. Update Role
            role = request.POST.get("role")
            if role == "superuser":
                target_user.is_staff = True
                target_user.is_superuser = True
            elif role == "staff":
                target_user.is_staff = True
                target_user.is_superuser = False
            else: # standard
                target_user.is_staff = False
                target_user.is_superuser = False

            # 3. Update Status (Active/Inactive)
            # Checkbox sends 'on' if checked; if unchecked, it sends nothing (None)
            target_user.is_active = (request.POST.get("is_active") == "on")

            # 4. Update Password (Optional)
            new_pw = request.POST.get("new_password", "").strip()
            if new_pw:
                target_user.set_password(new_pw)
                messages.info(request, f"Password for {target_user.username} has been reset.")

            target_user.save()
            messages.success(request, f"User '{target_user.username}' updated successfully.")
            return redirect("my_account")
        
    # --- Fetch User List (Staff Only) ---
    users_list = []
    if user.is_staff:
        User = get_user_model()
        users_list = User.objects.all().order_by('-date_joined')

    return render(
        request,
        "accounts/my_account.html",
        {
            "password_form": password_form,
            "create_user_form": create_user_form,
            "active_modal": active_modal,
            "users_list": users_list,
        },
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

@staff_member_required
def upload_file(request):
    print("DEBUG: entered upload_file view", flush=True)
    if request.method != "POST":
        return redirect("my_uploads")

    # --- DEBUGGING LOGS  ---
    print(f"DEBUG: POST keys: {list(request.POST.keys())}", flush=True)
    print(f"DEBUG: FILES keys: {list(request.FILES.keys())}", flush=True)
    # ------------------------

    # --- require both files present ---
    csv_file = request.FILES.get("csv_file") or request.FILES.get("csv")
    zipf = request.FILES.get("zip")

    print(f"DEBUG: Resolved csv_file: {csv_file}, zipf: {zipf}", flush=True)

    if not csv_file or not zipf:
        messages.error(request, "Please attach both a .csv metadata file and a .zip of images.")
        return redirect("my_uploads")

    # --- allowlist + size guard ---
    ext_x = os.path.splitext(csv_file.name)[1].lower()
    ext_z = os.path.splitext(zipf.name)[1].lower()

    if ext_x != ".csv":
        messages.error(request, "The metadata file must be a .csv.")
        return redirect("my_uploads")
    if ext_z != ".zip":
        messages.error(request, "The images archive must be a .zip.")
        return redirect("my_uploads")

    CSV_MAX = getattr(settings, "MAX_UPLOAD_SIZE_CSV", 10 * 1024 * 1024)        # 10 MB
    ZIP_MAX  = getattr(settings, "MAX_UPLOAD_SIZE_ZIP",  1024 * 1024 * 1024)      # 1 GB

    if csv_file.size and csv_file.size > CSV_MAX:
        messages.error(request, f"Metadata .csv is too large (> {CSV_MAX // (1024*1024)} MB).")
        return redirect("my_uploads")

    if zipf.size and zipf.size > ZIP_MAX:
        messages.error(request, f"Images .zip is too large (> {ZIP_MAX // (1024*1024)} MB).")
        return redirect("my_uploads")
    # --- Quick check that ZIP is valid and contains at least one entry ---
    try:
        with zipfile.ZipFile(zipf) as z:
            names = z.namelist()
            if not names:
                messages.error(request, "The images ZIP is empty.")
                return redirect("my_uploads")
    except zipfile.BadZipFile:
        messages.error(request, "The images ZIP is corrupt or not a valid ZIP.")
        return redirect("my_uploads")

    # Reset pointer
    try:
        zipf.seek(0)
    except Exception:
        pass

    # --- create batch row first (to get a UUID id for both file names) ---
    batch = UploadBatch.objects.create(
        uploaded_by=request.user,
        original_filename=csv_file.name,     # keep CSV name for display
        status=UploadBatch.Status.STAGING,
    )

    # Saving will use your upload_to=staging_upload_path_csv/zip and name them <batch-id>.(csv|zip)
    batch.file.save(csv_file.name, csv_file, save=False)
    batch.zip_file.save(zipf.name, zipf, save=False)
    batch.size_bytes = csv_file.size or 0
    # Compute checksum of the CSV (used by your existing admin display)
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
        return redirect("my_upload")

    errors = []
    try:
        df = pd.read_csv(batch.file.path)
        df.columns = [c.strip() for c in df.columns]
    except Exception as e:
        batch.mark_rejected_and_move(f"Cannot open CSV: {e}")
        messages.error(request, "Upload rejected: cannot open CSV.")
        return redirect("my_upload")

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
        return redirect("my_upload")

    # Pass preflight; full validator will hash images, check 1:1 mapping, etc.
    # Kick off background processing for this batch (validate + import)
    process_upload_task.delay(batch.id)
    print(f"Queued process_single_upload for batch {batch.id}", flush=True)

    messages.success(request, "Files received. Track the upload status below in the Activity Logs.")
    return redirect("my_uploads")

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
        messages.info(request, "Account creation is restricted. Please email to request an account.")
        return redirect("login")
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
            messages.success(request, "Username created successfully.")
            return redirect("signup")
    else:
        form = TailwindUserCreationForm()
    return render(request, "accounts/signup.html", {"form": form})

@login_required
def start_batch_download(request):
    # --- Handle GET requests gracefully (Fixes Login Redirect 405) ---
    if request.method != "POST":
        # If a user arrives here via GET (e.g. after logging in), 
        # redirect them to the gallery to try again.
        messages.info(request, "Please select items to download.")
        return redirect("beetles_home")
    # ----------------------------------------------------------------------

    from .utils import FILTERS_CONFIG

    """
    Create a DownloadJob for either:
      - selection_mode=ids: comma-separated UUIDs in 'selected_ids'
      - selection_mode=query: current q string in 'q' PLUS any advanced filters in POST
    This records intent only; files are built later by a worker/command.
    """
    mode = (request.POST.get("selection_mode") or "").strip()
    q_str = (request.POST.get("q") or "").strip()

    include_images = True
    # Only staff can opt-out of images
    if request.user.is_staff:
        # Front-end will send value="metadata_only" if the checkbox is unchecked
        if request.POST.get("download_type") == "metadata_only":
            include_images = False

    total = request.POST.get("total_matches")
    try:
        total = int(total) if total is not None else 0
    except ValueError:
        total = 0

    if mode not in ("ids", "query"):
        messages.error(request, "Invalid selection mode.")
        return redirect("beetles_home")

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
        include_images=include_images,
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
    messages.success(request, "Download started. Track progress below in the Activity Logs.")
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

    # Superuser-only taxonomy archives
    valid_species_archives_json = "[]"
    described_names_archives_json = "[]"
    current_valid_species_json = "null"
    current_described_names_json = "null"
    species_ref_status = {}
    described_names_ref_status = {}
    initial_archives = []
    initial_current = None

    if request.user.is_superuser:
        import json
        from django.core.serializers.json import DjangoJSONEncoder

        species_ref_status = species_ref.status()
        described_names_ref_status = described_names_ref.status()

        valid_species_archives = species_ref.list_archived_versions()
        described_names_archives = described_names_ref.list_archived_versions()

        # Get current version info
        valid_species_path = getattr(settings, "VALID_SPECIES_PATH", "reference/valid_species.csv")
        described_names_path = getattr(settings, "DESCRIBED_NAMES_PATH", "reference/described_names.csv")

        current_valid_species = None
        current_described_names = None

        if default_storage.exists(valid_species_path):
            mtime = default_storage.get_modified_time(valid_species_path)
            current_valid_species = {
                'filename': 'valid_species.csv',
                'timestamp': mtime,
                'label': species_ref.status().get('label', 'Current version')
            }

        if default_storage.exists(described_names_path):
            mtime = default_storage.get_modified_time(described_names_path)
            current_described_names = {
                'filename': 'described_names.csv',
                'timestamp': mtime,
                'label': described_names_ref.status().get('label', 'Current version')
            }

        initial_archives = valid_species_archives
        initial_current = current_valid_species

        # Convert to JSON-serializable format
        valid_species_archives_json = json.dumps(valid_species_archives, cls=DjangoJSONEncoder)
        described_names_archives_json = json.dumps(described_names_archives, cls=DjangoJSONEncoder)
        current_valid_species_json = json.dumps(current_valid_species, cls=DjangoJSONEncoder)
        current_described_names_json = json.dumps(current_described_names, cls=DjangoJSONEncoder)

    return render(
        request,
        "beetles/my_uploads.html",
        {
        "batches": batches,
        "download_jobs": download_jobs,
        "update_batches": update_batches,
        "valid_species_archives": valid_species_archives_json,
        "described_names_archives": described_names_archives_json,
        "current_valid_species": current_valid_species_json,
        "current_described_names": current_described_names_json,
        "species_ref_status": species_ref_status,
        "described_names_ref_status": described_names_ref_status,
        "initial_archives": initial_archives,
        "initial_current": initial_current,
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
def download_described_names_ref(request):
    """
    Stream the latest described_names.csv to a logged-in user with a stable, UTC-stamped filename.
    """
    storage_key = getattr(settings, "DESCRIBED_NAMES_PATH", "reference/described_names.csv")

    try:
        f = default_storage.open(storage_key, "rb")
    except Exception:
        raise Http404("Described names reference file is not available.")

    filename = described_names_ref.build_download_filename()
    etag = described_names_ref.get_version()
    quoted_etag = f'"{etag}"' if etag else None

    inm = request.META.get("HTTP_IF_NONE_MATCH", "")
    if quoted_etag and quoted_etag in inm:
        try:
            f.close()
        except Exception:
            pass
        resp = HttpResponse(status=304)
        resp["ETag"] = quoted_etag
        resp["Cache-Control"] = "public, max-age=0, must-revalidate"
        try:
            lm = default_storage.get_modified_time(storage_key)
            resp["Last-Modified"] = http_date(lm.timestamp())
        except Exception:
            pass
        return resp

    resp = FileResponse(f, content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    if quoted_etag:
        resp["ETag"] = quoted_etag
    resp["Cache-Control"] = "public, max-age=0, must-revalidate"
    try:
        lm = default_storage.get_modified_time(storage_key)
        resp["Last-Modified"] = http_date(lm.timestamp())
    except Exception:
        pass

    return resp

@superuser_required
def download_taxonomy_archive(request, ref_type, filename):
    """
    Download an archived version of a taxonomy reference CSV.
    ref_type: 'valid_species' or 'described_names'
    filename: the archived filename
    """
    # Validate ref_type
    if ref_type not in ['valid_species', 'described_names']:
        raise Http404("Invalid reference type")

    # Construct path
    archive_path = f"reference/archive/{ref_type}/{filename}"

    # Security: ensure filename doesn't contain path traversal
    if '..' in filename or '/' in filename:
        raise Http404("Invalid filename")

    # Ensure file exists
    if not default_storage.exists(archive_path):
        raise Http404("Archive file not found")

    # Open and stream the file
    try:
        f = default_storage.open(archive_path, "rb")
    except Exception:
        raise Http404("Could not open archive file")

    resp = FileResponse(f, content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'

    return resp

@superuser_required
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
                success_msg = f"Published valid_species.csv ({rows} rows)."
                messages.success(request, success_msg)

                # If AJAX request, return JSON
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': True, 'message': success_msg})

                # After publish, refresh status for display
                current_status = species_ref.status()
            except Exception as e:
                error_msg = f"Publish failed: {e}"
                messages.error(request, error_msg)

                # If AJAX request, return JSON error
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': False, 'error': error_msg}, status=400)
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

@superuser_required
def admin_described_names(request):
    """
    Minimal admin page to publish a new described_names.csv into default_storage
    and set the user-facing label.
    """
    current_status = described_names_ref.status()
    if request.method == "POST":
        form = DescribedNamesUploadForm(request.POST, request.FILES)
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
                rows, version = described_names_ref.publish_from_file(tmp_path, label=label)
                success_msg = f"Published described_names.csv ({rows} rows)."
                messages.success(request, success_msg)

                # If AJAX request, return JSON
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': True, 'message': success_msg})

                # After publish, refresh status for display
                current_status = described_names_ref.status()
            except Exception as e:
                error_msg = f"Publish failed: {e}"
                messages.error(request, error_msg)

                # If AJAX request, return JSON error
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': False, 'error': error_msg}, status=400)
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

            return redirect("admin_described_names")
    else:
        form = DescribedNamesUploadForm()

    return render(
        request,
        "admin/tools_described_names.html",
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


@staff_member_required
def update_upload(request):
    """
    Staff-only portal to submit a CSV of metadata updates by Record ID (UUID).
    Quick preflight:
      - .csv present and within size limit
      - headers match exactly (required set + optional 'update_notes', no extras)
      - pandas can open CSV
    Creates an UpdateBatch in 'staging'. Full validation/diff/apply comes next steps.
    """
    if request.method != "POST":
        return redirect("my_uploads")

    csv_file = request.FILES.get("csv_file") or request.FILES.get("csv")
    if not csv_file:
        messages.error(request, "Please attach a .csv file.")
        return redirect("my_uploads")

    ext = os.path.splitext(csv_file.name)[1].lower()
    if ext != ".csv":
        messages.error(request, "The update file must be a .csv.")
        return redirect("my_uploads")

    CSV_MAX = getattr(settings, "MAX_UPLOAD_SIZE_CSV", 10 * 1024 * 1024)  # 10 MB default
    if csv_file.size and csv_file.size > CSV_MAX:
        messages.error(request, f"Update .csv is too large (> {CSV_MAX // (1024*1024)} MB).")
        return redirect("my_uploads")

    # Create the UpdateBatch row so we have an ID + on-disk path
    batch = UpdateBatch.objects.create(
        uploaded_by=request.user,
        original_filename=csv_file.name,
        status=UpdateBatch.Status.STAGING,
    )
    batch.file.save(csv_file.name, csv_file, save=False)
    batch.size_bytes = batch.file.size or 0
    # Best-effort checksum for idempotency later
    try:
        batch.compute_sha256_from_disk()
    except Exception:
        pass
    batch.save()

    # Quick preflight with pandas (header checks only)
    if pd is None:
        messages.warning(request, "File received. Note: server missing pandas; skipping quick checks.")
        return redirect("my_uploads")

    errors = []
    try:
        df = pd.read_csv(batch.file.path)
        # normalize headers (strip)
        df.columns = [str(c).strip() for c in df.columns]
    except Exception as e:
        batch.mark_rejected_and_move(f"Cannot open CSV: {e}")
        messages.error(request, "Update rejected: cannot open CSV.")
        return redirect("my_uploads")

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
        return redirect("my_uploads")

    # -- Starting background validate+apply for this update batch --
    process_update_task.delay(batch.id)

    messages.success(
        request,
            "Update file received. Full validation will run in the background; "
            "track the status below in the Activity Logs."
    )
    # Send them where they can see the batch status
    return redirect("my_uploads")

@staff_member_required
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

    _run_update_batch(request, row_data, f"single_edit_{beetle.id}.csv")
    messages.success(request, "Update queued successfully. Changes will appear shortly.")
    return redirect("beetle_detail", beetle_id=beetle_id)


@staff_member_required
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

    _run_update_batch(request, row_data, f"add_specimen_{image_asset.id.hex[:8]}.csv")
    
    messages.success(request, "Update queued successfully. Changes will appear shortly.")
    return redirect(request.META.get('HTTP_REFERER', 'home'))


def _run_update_batch(request, row_data, filename):
    """Helper to package row_data into an XLSX and spawn the processor."""
    df = pd.DataFrame([row_data])
    
    s_buf = StringIO()
    df.to_csv(s_buf, index=False)
    csv_content = s_buf.getvalue().encode('utf-8-sig')
    
    batch = UpdateBatch.objects.create(
        uploaded_by=request.user,
        original_filename=filename,
        status=UpdateBatch.Status.STAGING,
    )
    
    from django.core.files.base import ContentFile
    batch.file.save(filename, ContentFile(csv_content), save=False)
    batch.size_bytes = batch.file.size
    batch.save()

    # Trigger
    process_update_task.delay(batch.id)


# @login_required
def tool_classify(request):
    """
    Proxies image upload to Modal GPU API.
    Returns JSON for AJAX requests, renders template for GET.
    """
    if request.method == 'POST' and request.FILES.get('image'):
        try:
            # 1. Prepare Data
            image_file = request.FILES['image']
            
            # Extract form data
            payload = {
                'architecture': request.POST.get('architecture', 'rtdetr'),
                'box_threshold': request.POST.get('box_threshold', 0.25),
            }
            
            # Prepare file for upload
            files = {
                'image': (image_file.name, image_file.read(), image_file.content_type)
            }

            # 2. Call Modal API
            response = requests.post(MODAL_API_URL, data=payload, files=files, timeout=300)
            
            if response.status_code == 200:
                return JsonResponse(response.json())
            else:
                return JsonResponse({
                    "status": "error", 
                    "message": f"AI Service Error: {response.status_code}"
                }, status=500)

        except requests.exceptions.Timeout:
            return JsonResponse({
                "status": "error", 
                "message": "The AI model is waking up (Cold Start). Please try again in 1 minute."
            }, status=504)
        except Exception as e:
            return JsonResponse({
                "status": "error", 
                "message": f"Processing failed: {str(e)}"
            }, status=500)

    # GET request: Render the page
    return render(request, 'beetles/tool_classify.html', {})

@login_required
def stream_updates(request):
    """
    Server-Sent Events (SSE) stream.
    Optimized to only fetch active jobs + those that finished in the last 10 seconds.
    """
    def event_stream():
        # 1. Define final states
        # Matches models choices for UploadBatch, UpdateBatch, and DownloadJob
        FINAL_STATES = {
            'imported', 'rejected', 'import_failed', 
            'applied', 'apply_failed', 
            'ready', 'failed', 'expired'
        }

        while True:
            data = {}
            has_updates = False

            # 2. Define "Recently" 
            # Keep fetching finished jobs for 10s so the UI has time to update to Green/Red.
            now = timezone.now()
            recent_cutoff = now - timedelta(seconds=10)

            # --- A. DOWNLOAD JOBS ---
            # Logic: Fetch if (Status is NOT Final) OR (Finished recently)
            downloads = DownloadJob.objects.filter(requested_by=request.user).filter(
                ~Q(status__in=FINAL_STATES) | 
                Q(finished_at__gte=recent_cutoff)
            ).order_by('-created_at')

            for job in downloads:
                data[f"download_{job.id}"] = {
                    "status": job.status,
                    "status_display": job.get_status_display(),
                    "csv_url": job.csv_file.url if job.csv_file else None,
                    "zip_url": job.zip_file.url if job.zip_file else None,
                    "error_message": job.error_message,
                }
                has_updates = True

            # --- B. UPLOAD BATCHES ---
            # Logic: Fetch if (Status is NOT Final) OR (Updated recently)
            uploads = UploadBatch.objects.filter(uploaded_by=request.user).filter(
                ~Q(status__in=FINAL_STATES) | 
                Q(updated_at__gte=recent_cutoff)
            ).order_by('-created_at')

            for batch in uploads:
                data[f"upload_{batch.id}"] = {
                    "status": batch.status,
                    "status_display": batch.get_status_display(),
                    "error_log_url": batch.error_report_file.url if batch.error_report_file else None,
                    "error_message": batch.error_message,
                }
                has_updates = True

            # --- C. UPDATE BATCHES ---
            if request.user.is_staff:
                updates = UpdateBatch.objects.filter(uploaded_by=request.user).filter(
                    ~Q(status__in=FINAL_STATES) | 
                    Q(updated_at__gte=recent_cutoff)
                ).order_by('-created_at')

                for batch in updates:
                    data[f"update_{batch.id}"] = {
                        "status": batch.status,
                        "status_display": batch.get_status_display(),
                        "report_url": batch.report_file.url if batch.report_file else None,
                        "error_message": batch.error_message,
                    }
                    has_updates = True

            if has_updates:
                yield f"data: {json.dumps(data)}\n\n"

            time.sleep(3)

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response

@login_required
def taxonomy_browser(request):
    """
    Display the taxonomy browser page.
    Passes the pre-built taxonomy tree JSON and a flat lookup of
    valid_species rows (keyed by valid_species_id) so the frontend
    can show species details without a round-trip.
    """
    from . import taxonomy_tree

    tree = taxonomy_tree.get_tree() or []

    # Build a flat map: valid_species_id -> taxonomy fields
    # Only load once; species_ref keeps it in memory after first call.
    species_map = {}
    try:
        species_ref._ensure_loaded()
        if species_ref._MAP:
            species_map = species_ref._MAP
    except Exception:
        pass

    return render(request, 'beetles/taxonomy_browser.html', {
        'taxonomy_tree_json': json.dumps(tree, ensure_ascii=False),
        'species_map_json': json.dumps(species_map, ensure_ascii=False),
    })


def described_names_for_species(request):
    """
    AJAX endpoint: return all described (synonym) names for a given valid_species_id.
    GET /taxonomy/described-names/?species_id=<valid_species_id>
    Returns: { "names": [ { ...row fields... }, … ] }
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    species_id = (request.GET.get("species_id") or "").strip()
    if not species_id:
        return JsonResponse({"error": "species_id is required"}, status=400)

    # Use the reverse index to find all name_ids linked to this valid_species_id
    name_ids = described_names_ref.ids_for("valid species id", species_id)
    if not name_ids:
        return JsonResponse({"names": []})

    # Bulk-fetch the rows
    rows = described_names_ref.bulk_lookup(name_ids)

    # Return as a list, adding name_id back into each row for the frontend
    names = [{"name_id": nid, **fields} for nid, fields in rows.items()]
    return JsonResponse({"names": names})


def species_images(request):
    """
    AJAX endpoint: return thumbnail URLs for all images tagged to a species.
    GET /taxonomy/species-images/?species_id=<valid_species_id>
    Returns: { "images": [ { "beetle_id", "thumb_url", "detail_url" }, … ] }
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    species_id = (request.GET.get("species_id") or "").strip()
    if not species_id:
        return JsonResponse({"error": "species_id is required"}, status=400)

    # Same dedup pattern as the gallery: one row per unique ImageAsset
    beetles = (
        Beetles.objects
        .filter(depicts_valid_name_id=species_id)
        .select_related("image_asset")
        .order_by("image_asset", "id")
        .distinct("image_asset")
    )

    images = []
    for b in beetles:
        asset = b.image_asset
        if not asset:
            continue
        thumb_url = asset.thumb_small.url if asset.thumb_small else None
        images.append({
            "beetle_id": str(b.id),
            "thumb_url": thumb_url,
            "detail_url": reverse("beetle_detail", kwargs={"beetle_id": b.id}),
        })

    return JsonResponse({"images": images})


def taxonomy_search(request):
    """
    AJAX endpoint: search by original genus or described scientific name.
    GET /taxonomy/search/?field=<original_genus|described_name>&query=<term>
    Returns: { "species_ids": [...], "matches": [...] }
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    field = (request.GET.get("field") or "").strip()
    query = (request.GET.get("query") or "").strip()

    if not field or not query:
        return JsonResponse({"error": "field and query are required"}, status=400)

    from . import species_ref, described_names_ref

    species_ids = set()
    matches = []

    if field == "original_genus":
        # Search originalGenus in species_ref (case-insensitive substring)
        species_ref._ensure_reverse_index()
        idx = species_ref._rev_index.get("originalGenus", {})
        q_lower = query.lower()

        for genus_lower, ids in idx.items():
            if q_lower in genus_lower:
                species_ids.update(ids)
                matches.append(genus_lower.capitalize())

    elif field == "described_name":
        # Search describedScientificName in described_names_ref (case-insensitive substring)
        described_names_ref._ensure_reverse_index()
        idx = described_names_ref._rev_index.get("describedScientificName", {})
        q_lower = query.lower()

        name_ids_found = set()
        for name_lower, nids in idx.items():
            if q_lower in name_lower:
                name_ids_found.update(nids)
                matches.append(name_lower)

        # Map name_ids → valid_species_ids
        rows = described_names_ref._load_all_rows()
        for row in rows:
            nid = str(row.get("name_id", "")).strip()
            if nid in name_ids_found:
                vid = str(row.get("name_valid_species_id", "")).strip()
                if vid:
                    species_ids.add(vid)

    else:
        return JsonResponse({"error": "invalid field"}, status=400)

    return JsonResponse({
        "species_ids": sorted(species_ids),
        "matches": sorted(set(matches))[:20]  # limit autocomplete suggestions
    })


@staff_member_required
def tool_annotate(request):
    """
    Data annotation tool page (staff only).
    """
    return render(request, 'beetles/tool_annotate.html', {})


@staff_member_required
def export_annotations(request):
    """
    Export all bounding box annotations in YOLO or COCO format.

    GET /api/v1/export-annotations/?format=yolo
    GET /api/v1/export-annotations/?format=coco
    """
    export_format = request.GET.get('format', 'yolo').lower()

    if export_format not in ['yolo', 'coco']:
        return JsonResponse({'error': 'Invalid format. Must be "yolo" or "coco".'}, status=400)

    # Get all images with bbox annotations (Beetles with bbox_x not NULL)
    image_asset_ids = Beetles.objects.exclude(bbox_x__isnull=True).values_list('image_asset_id', flat=True).distinct()

    # Load category mapping
    from django.conf import settings
    from pathlib import Path

    category_map = {}
    mapping_path = Path(settings.MEDIA_ROOT) / 'reference' / 'category_mapping.json'
    if mapping_path.exists():
        try:
            with open(mapping_path, 'r', encoding='utf-8') as f:
                mapping_data = json.load(f)
                for cat in mapping_data.get('categories', []):
                    category_map[str(cat['id'])] = cat
        except Exception:
            pass

    try:
        if export_format == 'coco':
            # COCO: Export all annotations in a single JSON file
            data = {
                'images': [],'annotations': [],
                'categories': []
            }

            annotation_id = 1
            processed_images = set()
            used_category_ids = set()

            for image_id in image_asset_ids:
                try:
                    image_asset = ImageAsset.objects.get(pk=image_id)

                    # Skip if we've already processed this image
                    image_id_str = str(image_asset.id)
                    if image_id_str in processed_images:
                        continue
                    processed_images.add(image_id_str)

                    # Get all beetle bbox annotations for this image
                    beetles_with_bbox = Beetles.objects.filter(
                        image_asset=image_asset
                    ).exclude(bbox_x__isnull=True)

                    if not beetles_with_bbox.exists():
                        continue

                    # Use image_asset ID for consistency
                    image_id = str(image_asset.id)

                    # Add image info
                    data['images'].append({
                        'id': image_id,
                        'width': image_asset.image_width or 0,
                        'height': image_asset.image_height or 0,
                        'file_name': f'{image_id}.jpg'
                    })

                    # Add annotations for this image
                    for beetle in beetles_with_bbox:
                        coco_ann = beetle.to_coco(
                            image_asset.image_width or 1920,
                            image_asset.image_height or 1080
                        )
                        if not coco_ann:  # Skip if no bbox
                            continue

                        coco_ann['id'] = annotation_id
                        coco_ann['image_id'] = image_id

                        # Convert category_id to int
                        label = beetle.bbox_label.strip() if beetle.bbox_label else '0'
                        try:
                            coco_ann['category_id'] = int(label)
                        except ValueError:
                            coco_ann['category_id'] = 0

                        data['annotations'].append(coco_ann)
                        annotation_id += 1

                        # Track used categories
                        if label and label not in ['unknown', '']:
                            used_category_ids.add(label)

                except ImageAsset.DoesNotExist:
                    continue

            # Add categories to COCO format
            for cat_id in sorted(used_category_ids, key=lambda x: int(x) if x.lstrip('-').isdigit() else 0):
                cat_info = category_map.get(cat_id)
                if cat_info:
                    data['categories'].append({
                        'id': int(cat_id),
                        'name': cat_info.get('full_name') or cat_info.get('name', f'class_{cat_id}'),
                        'supercategory': cat_info.get('type', 'beetle')
                    })
                else:
                    # Fallback if category not found
                    try:
                        data['categories'].append({
                            'id': int(cat_id),
                            'name': f'class_{cat_id}',
                            'supercategory': 'beetle'
                        })
                    except ValueError:
                        pass

            content = json.dumps(data, indent=2)

            # Create ZIP with single JSON file
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('annotations.json', content)

            zip_buffer.seek(0)
            response = HttpResponse(
                zip_buffer.getvalue(),
                content_type='application/zip'
            )
            response['Content-Disposition'] = 'attachment; filename="annotations_coco.zip"'
            return response

        else:
            # YOLO: Export separate .txt file per image
            zip_buffer = BytesIO()

            # Track processed images to avoid duplicates
            processed_images = set()
            used_category_ids = set()

            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for image_id in image_asset_ids:
                    try:
                        image_asset = ImageAsset.objects.get(pk=image_id)

                        # Get all beetle bbox annotations for this image
                        beetles_with_bbox = Beetles.objects.filter(
                            image_asset=image_asset
                        ).exclude(bbox_x__isnull=True)

                        if not beetles_with_bbox.exists():
                            continue

                        # Use image_asset ID for filename consistency
                        image_id_str = str(image_asset.id)

                        # Skip if we've already processed this image
                        if image_id_str in processed_images:
                            continue
                        processed_images.add(image_id_str)

                        filename = f"{image_asset.id}.txt"

                        # Export YOLO format - all boxes for this image in one file
                        lines = []
                        for beetle_bbox in beetles_with_bbox:
                            line = beetle_bbox.to_yolo()
                            if line:  # Skip empty lines
                                lines.append(line)

                                # Track used category IDs
                                label = beetle_bbox.bbox_label.strip() if beetle_bbox.bbox_label else '0'
                                if label and label not in ['unknown', '']:
                                    used_category_ids.add(label)

                        content = '\n'.join(lines)
                        zf.writestr(filename, content)

                    except ImageAsset.DoesNotExist:
                        continue

                # Generate and add labels.txt mapping file (only for used categories)
                if category_map and used_category_ids:
                    label_ids = sorted([int(k) for k in used_category_ids if k.lstrip('-').isdigit()],
                                      key=lambda x: x)
                    label_lines = []
                    for label_id in label_ids:
                        cat = category_map.get(str(label_id))
                        if cat:
                            name = cat.get('full_name') or cat.get('name', f'class_{label_id}')
                            label_lines.append(f"{label_id}: {name}")
                    if label_lines:
                        labels_content = '\n'.join(label_lines)
                        zf.writestr('labels.txt', labels_content)

            zip_buffer.seek(0)

            response = HttpResponse(
                zip_buffer.getvalue(),
                content_type='application/zip'
            )
            response['Content-Disposition'] = 'attachment; filename="annotations_yolo.zip"'

            return response

    except Exception as e:
        return JsonResponse({'error': f'Export failed: {str(e)}'}, status=500)