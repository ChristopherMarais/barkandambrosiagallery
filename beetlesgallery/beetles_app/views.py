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
from django.core.management import call_command

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
    total_images = ImageAsset.objects.filter(is_deleted=False).count()

    # Count unique species and genera via native SQL Joins, bypassing Python memory
    total_species = Beetles.objects.filter(taxon__isnull=False).values('taxon_id').distinct().count()
    total_genera = Beetles.objects.filter(taxon__isnull=False).values('taxon__genus').distinct().count()

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
        'total_genera': total_genera,
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
        return redirect("data_management")

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
        return redirect("data_management")

    # --- allowlist + size guard ---
    ext_x = os.path.splitext(csv_file.name)[1].lower()
    ext_z = os.path.splitext(zipf.name)[1].lower()

    if ext_x != ".csv":
        messages.error(request, "The metadata file must be a .csv.")
        return redirect("data_management")
    if ext_z != ".zip":
        messages.error(request, "The images archive must be a .zip.")
        return redirect("data_management")

    CSV_MAX = getattr(settings, "MAX_UPLOAD_SIZE_CSV", 10 * 1024 * 1024)        # 10 MB
    ZIP_MAX  = getattr(settings, "MAX_UPLOAD_SIZE_ZIP",  1024 * 1024 * 1024)      # 1 GB

    if csv_file.size and csv_file.size > CSV_MAX:
        messages.error(request, f"Metadata .csv is too large (> {CSV_MAX // (1024*1024)} MB).")
        return redirect("data_management")

    if zipf.size and zipf.size > ZIP_MAX:
        messages.error(request, f"Images .zip is too large (> {ZIP_MAX // (1024*1024)} MB).")
        return redirect("data_management")
    # --- Quick check that ZIP is valid and contains at least one entry ---
    try:
        with zipfile.ZipFile(zipf) as z:
            names = z.namelist()
            if not names:
                messages.error(request, "The images ZIP is empty.")
                return redirect("data_management")
    except zipfile.BadZipFile:
        messages.error(request, "The images ZIP is corrupt or not a valid ZIP.")
        return redirect("data_management")

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
    return redirect("data_management")

def gallery(request):
    from .utils import build_query_q, filter_beetles_queryset, FILTERS_CONFIG
    NA = "None"
    try:
        page_size = int(request.GET.get("per_page", 12))
    except (ValueError, TypeError):
        page_size = 12

    WARN_IMAGE_SIZE_BYTES = getattr(settings, "WARN_IMAGE_SIZE_BYTES", 10 * 1024 * 1024)
    base_qs = base_qs = Beetles.objects.filter(is_deleted=False).order_by("-id")

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
    from django.db.models import Q
    
    grouped_filters = defaultdict(list)
    
    categories = []
    seen_cats = set()
    for cfg in FILTERS_CONFIG:
        if cfg["category"] not in seen_cats:
            categories.append(cfg["category"])
            seen_cats.add(cfg["category"])

    for cfg in FILTERS_CONFIG:
        param = cfg["param"]
        ctx_qs = filter_beetles_queryset(base_search_qs, active_filters, exclude_param=param)
        
        options = []
        has_na = False

        if cfg["type"] == "db":
            # 1. Fetch only REAL values, excluding nulls and blanks natively
            field = cfg['field']
            
            # Identify if field is Date or Decimal to avoid passing empty string "" to ORM
            is_strict_type = field in ["image_asset__image_date_taken", "image_asset__resolution_in_ppmm"]
            
            if is_strict_type:
                raw_options = ctx_qs.exclude(**{f"{field}__isnull": True}) \
                                    .values_list(field, flat=True) \
                                    .distinct().order_by(field)
            else:
                raw_options = ctx_qs.exclude(**{f"{field}__isnull": True}) \
                                    .exclude(**{f"{field}": ""}) \
                                    .values_list(field, flat=True) \
                                    .distinct().order_by(field)
            
            for o in raw_options:
                val = o.strftime("%Y-%m-%d") if hasattr(o, "strftime") else str(o).strip()
                if val and val not in options:
                    options.append(val)
                    
            # 2. Hard check the database to see if ANY blank/null records exist
            if is_strict_type:
                has_na = ctx_qs.filter(**{f"{field}__isnull": True}).exists()
            else:
                has_na = ctx_qs.filter(
                    Q(**{f"{field}__isnull": True}) | 
                    Q(**{f"{field}": ""})
                ).exists()

        elif cfg["type"] == "bool":
            options = ["Yes", "No"]
            has_na = ctx_qs.filter(**{f"{cfg['field']}__isnull": True}).exists()
            
        elif cfg["type"] == "ref":
            field_name = f"taxon__{cfg['field']}"
            
            # 1. Fetch only REAL taxonomy values
            raw_options = ctx_qs.exclude(taxon__isnull=True) \
                                .exclude(**{f"{field_name}__isnull": True}) \
                                .exclude(**{f"{field_name}": ""}) \
                                .values_list(field_name, flat=True) \
                                .distinct().order_by(field_name)
                                
            for o in raw_options:
                val = str(o).strip()
                if val and val not in options:
                    options.append(val)

            # 2. Hard check if any ROI is unlinked OR its specific taxon rank is empty
            has_na = ctx_qs.filter(
                Q(taxon__isnull=True) | 
                Q(**{f"{field_name}__isnull": True}) | 
                Q(**{f"{field_name}": ""})
            ).exists()

        # 3. Safely insert None at the top of the list if blanks exist
        if has_na:
            options.insert(0, "None")

        # Check if options exist OR if this filter is currently active
        if options or active_filters.get(param):
            grouped_filters[cfg["category"]].append({
                "param": param,
                "label": cfg["label"],
                "options": options,
                "selected": active_filters.get(param, []),
            })

    filter_context = []
    for cat in categories:
        if grouped_filters[cat]:
            filter_context.append((cat, grouped_filters[cat]))

    # 5. Pagination
    final_qs = final_qs.order_by("image_asset", "id").distinct("image_asset")
    
    # Critical: Fetch taxon in the same SQL call to guarantee O(1) performance
    final_qs = final_qs.select_related("image_asset", "taxon").prefetch_related("image_asset__specimens__taxon")
    
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
        # 1. Resolve Reference natively from the database join
        def clean(val): return val if val and str(val).lower() != "unknown" else None
        
        if b.taxon:
            b.ref_scientificName = clean(b.taxon.scientific_name)
            b.ref_genus = clean(b.taxon.genus)
            b.ref_species = clean(b.taxon.species)
            b.ref_subfamily = clean(b.taxon.subfamily)
            b.ref_tribe = clean(b.taxon.tribe)
            b.ref_subtribe = clean(b.taxon.subtribe)
            b.ref_subspecies = clean(b.taxon.subspecies)
        else:
            b.ref_scientificName = b.ref_genus = b.ref_species = b.ref_subfamily = b.ref_tribe = b.ref_subtribe = b.ref_subspecies = None

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
            
            # Taxonomy Aggregation via DB instances
            # Get all Valid IDs involved in this image using the relational ID
            taxa = {s.taxon_id for s in siblings}
            
            # If we have >1 unique ID, we might have multiple ranks
            if len(taxa) > 1:
                def check_rank(key):
                    # collect unique values for this rank natively from the taxon objects
                    vals = {getattr(s.taxon, key) for s in siblings if s.taxon}
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
        "beetles/image_browser.html", 
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

    # Fetch main object, aggressively joining the taxon relationship to prevent N+1 queries
    beetle = get_object_or_404(Beetles.objects.filter(is_deleted=False).select_related("taxon"), pk=beetle_id)

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

    # Database-based enrichment (Replaces CSV-based species_ref)
    ref_species = None
    if beetle.taxon:
        ref_species = {
            "scientificName": beetle.taxon.scientific_name,
            "scientificNameAuthority": beetle.taxon.scientific_name_authority,
            "subfamily": beetle.taxon.subfamily,
            "tribe": beetle.taxon.tribe,
            "subtribe": beetle.taxon.subtribe,
            "genus": beetle.taxon.genus,
            "species": beetle.taxon.species,
            "subspecies": beetle.taxon.subspecies,
            "authority": beetle.taxon.authority,
            "authorityYear": beetle.taxon.authority_year,
            "originalGenus": beetle.taxon.original_genus,
        }
    # Restore Data Provenance: Extract the latest modification timestamp from the Taxon table
    from beetlesgallery.beetles_app.models import Taxon
    latest_taxon_update = Taxon.objects.order_by("-updated_at").values_list("updated_at", flat=True).first()
    if latest_taxon_update:
        ref_version = f"Database Managed (Last CSV Sync: {latest_taxon_update.strftime('%Y-%m-%d %H:%M UTC')})"
    else:
        ref_version = "Database Managed (No Sync History)"

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
        return redirect("beetles_image_browser")
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
        return redirect("beetles_image_browser")

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
            return redirect("data_management")

        job.set_ids(ids)
        job.total_requested = len(ids)
        job.save(update_fields=["selected_ids_json", "total_requested"])


    build_downloads_task.delay(job.id)
    messages.success(request, "Download started. Track progress below in the Activity Logs.")
    return redirect("data_management")
    

@login_required
def data_management(request):
    batches = UploadBatch.objects.filter(uploaded_by=request.user).order_by("-created_at")
    download_jobs = DownloadJob.objects.filter(requested_by=request.user).order_by("-created_at")

    if request.user.is_staff:
        update_batches = UpdateBatch.objects.filter(uploaded_by=request.user).order_by("-created_at")
    else:
        update_batches = []

    if request.user.is_superuser:
        import json
        from django.utils import timezone
        from django.core.files.storage import default_storage
        
        # Broad try/except to completely prevent 500 errors on dashboard load
        try:
            from beetlesgallery.beetles_app.models import Taxon
            latest_taxon = Taxon.objects.order_by("-updated_at").first()
            if latest_taxon and latest_taxon.updated_at: 
                t_label = f"Database Managed (Last Sync: {latest_taxon.updated_at.strftime('%Y-%m-%d %H:%M UTC')})"
                t_timestamp = latest_taxon.updated_at.isoformat()
            else:
                t_label = "Database Managed (v2.0)"
                t_timestamp = timezone.now().isoformat()
        except Exception as e:
            print(f"Safe error parsing Taxon: {e}")
            t_label = "Database Managed (v2.0)"
            t_timestamp = timezone.now().isoformat()

        species_ref_status = {'label': t_label, 'version': 'v2.0'}
        described_names_ref_status = {'label': t_label, 'version': 'v2.0'}

        current_file_obj = {
            "timestamp": t_timestamp,
            "filename": "Active_Database_Export.csv"
        }
        
        # JSON strings exclusively for the Javascript frontend
        current_valid_species_json = json.dumps(current_file_obj)
        current_described_names_json = json.dumps(current_file_obj)

        def fetch_archives(ref_type):
            archive_dir = f"reference/archive/{ref_type}"
            archives = []
            try:
                if default_storage.exists(archive_dir):
                    _, files = default_storage.listdir(archive_dir)
                    for f in files:
                        if f.endswith('.csv'):
                            try:
                                mtime = default_storage.get_modified_time(f"{archive_dir}/{f}")
                                ts = mtime.isoformat()
                            except Exception:
                                ts = timezone.now().isoformat()
                            archives.append({"filename": f, "timestamp": ts})
                    archives.sort(key=lambda x: x["filename"], reverse=True)
            except Exception as e:
                print(f"Safe error reading storage: {e}")
            return archives

        vs_archives = fetch_archives("valid_species")
        dn_archives = fetch_archives("described_names")

        # JSON strings exclusively for Javascript frontend
        valid_species_archives_json = json.dumps(vs_archives)
        described_names_archives_json = json.dumps(dn_archives)
        
        # NATIVE PYTHON OBJECTS for the Django Template Server-Side Rendering
        initial_archives = vs_archives
        initial_current = current_file_obj
    else:
        valid_species_archives_json = "[]"
        described_names_archives_json = "[]"
        current_valid_species_json = "null"
        current_described_names_json = "null"
        species_ref_status = {}
        described_names_ref_status = {}
        initial_archives = []
        initial_current = None

    return render(
        request,
        "beetles/data_management.html",
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


@staff_member_required
def tool_annotate(request):
    """
    Data annotation tool page (staff only).
    """
    from .utils import FILTERS_CONFIG
    from collections import defaultdict
    from django.db.models import Q
    from .models import Beetles, Taxon
    from django.core.serializers.json import DjangoJSONEncoder
    import json
    
    # Query the base records to discover available filter options
    base_qs = Beetles.objects.filter(is_deleted=False)
    grouped_filters = defaultdict(list)
    
    categories = []
    seen_cats = set()
    for cfg in FILTERS_CONFIG:
        if cfg["category"] not in seen_cats:
            categories.append(cfg["category"])
            seen_cats.add(cfg["category"])

    for cfg in FILTERS_CONFIG:
        param = cfg["param"]
        options = []
        has_na = False

        if cfg["type"] == "db":
            field = cfg['field']
            is_strict_type = field in ["image_asset__image_date_taken", "image_asset__resolution_in_ppmm"]
            
            if is_strict_type:
                raw_options = base_qs.exclude(**{f"{field}__isnull": True}).values_list(field, flat=True).distinct().order_by(field)
                has_na = base_qs.filter(**{f"{field}__isnull": True}).exists()
            else:
                raw_options = base_qs.exclude(**{f"{field}__isnull": True}).exclude(**{f"{field}": ""}).values_list(field, flat=True).distinct().order_by(field)
                has_na = base_qs.filter(Q(**{f"{field}__isnull": True}) | Q(**{f"{field}": ""})).exists()
            
            for o in raw_options:
                val = o.strftime("%Y-%m-%d") if hasattr(o, "strftime") else str(o).strip()
                if val and val not in options:
                    options.append(val)

        elif cfg["type"] in ["bool", "custom_has_rois", "custom_all_rois_val"]:
            options = ["Yes", "No"]
            if cfg["type"] == "bool":
                has_na = base_qs.filter(**{f"{cfg['field']}__isnull": True}).exists()
            else:
                has_na = False
            
        elif cfg["type"] == "ref":
            field_name = f"taxon__{cfg['field']}"
            raw_options = base_qs.exclude(taxon__isnull=True).exclude(**{f"{field_name}__isnull": True}).exclude(**{f"{field_name}": ""}).values_list(field_name, flat=True).distinct().order_by(field_name)
            for o in raw_options:
                val = str(o).strip()
                if val and val not in options:
                    options.append(val)

            has_na = base_qs.filter(Q(taxon__isnull=True) | Q(**{f"{field_name}__isnull": True}) | Q(**{f"{field_name}": ""})).exists()

        if has_na:
            options.insert(0, "None")

        if options:
            grouped_filters[cfg["category"]].append({
                "param": param,
                "label": cfg["label"],
                "options": options,
                "selected": [],
            })

    filter_context = []
    for cat in categories:
        if grouped_filters[cat]:
            filter_context.append((cat, grouped_filters[cat]))

    # --- Inject Taxonomy Tree for Cascading Dropdowns ---
    taxa = Taxon.objects.all()
    tree_dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    species_map = {}

    for t in taxa:
        subf = t.subfamily.strip() if t.subfamily else "Unknown Subfamily"
        tribe = t.tribe.strip() if t.tribe else "Unknown Tribe"
        genus = t.genus.strip() if t.genus else "Unknown Genus"

        tree_dict[subf][tribe][genus].append({
            "name": t.species.strip() if t.species else "sp.",
            "species_id": str(t.valid_species_id),
        })

        species_map[str(t.valid_species_id)] = {
            "subfamily": subf,
            "tribe": tribe,
            "genus": genus,
            "name": t.species.strip() if t.species else "sp."
        }

    # Convert defaultdict to standard dict to prevent silent serialization failures
    def default_to_regular(d):
        if isinstance(d, defaultdict):
            d = {k: default_to_regular(v) for k, v in d.items()}
        return d
    
    tree_dict_clean = default_to_regular(tree_dict)

    return render(request, 'beetles/tool_annotate.html', {
        'filter_groups': filter_context,
        'taxonomy_tree_json': json.dumps(tree_dict_clean, cls=DjangoJSONEncoder, ensure_ascii=False),
        'species_map_json': json.dumps(species_map, cls=DjangoJSONEncoder, ensure_ascii=False),
    })

@login_required(login_url='login')
def download_taxonomy_ref(request):
    storage_key = getattr(settings, "VALID_SPECIES_PATH", "reference/valid_species.csv")
    try:
        f = default_storage.open(storage_key, "rb")
    except Exception:
        raise Http404("Taxonomy reference file is not available.")

    filename = f"valid_species_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
    resp = FileResponse(f, content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp

@login_required(login_url='login')
def download_described_names_ref(request):
    storage_key = getattr(settings, "DESCRIBED_NAMES_PATH", "reference/described_names.csv")
    try:
        f = default_storage.open(storage_key, "rb")
    except Exception:
        raise Http404("Described names reference file is not available.")

    filename = f"described_names_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
    resp = FileResponse(f, content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
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
    current_status = {'label': 'Database Managed (v2.0)', 'version': 'v2.0'}

    if request.method == "POST":
        form = ValidSpeciesUploadForm(request.POST, request.FILES)
        if form.is_valid() and request.FILES:
            # Extract the uploaded file gracefully
            uploaded_file = request.FILES.get('file') or list(request.FILES.values())[0]
            storage_key = getattr(settings, "VALID_SPECIES_PATH", "reference/valid_species.csv")

            # Overwrite the existing file in storage
            if default_storage.exists(storage_key):
                default_storage.delete(storage_key)
            default_storage.save(storage_key, uploaded_file)

            # Synchronously trigger the ETL pipeline
            try:
                call_command('migrate_taxonomy_to_db')
                messages.success(request, "Valid Species uploaded. Postgres database successfully rebuilt and beetles re-linked.")
            except Exception as e:
                messages.error(request, f"File saved, but database rebuild failed: {str(e)}")

            return redirect("admin_valid_species")
        else:
            messages.error(request, "Invalid file submission.")
    else:
        form = ValidSpeciesUploadForm()

    return render(request, "admin/tools_valid_species.html", {"form": form, "status": current_status})


@superuser_required
def admin_described_names(request):
    current_status = {'label': 'Database Managed (v2.0)', 'version': 'v2.0'}

    if request.method == "POST":
        form = DescribedNamesUploadForm(request.POST, request.FILES)
        if form.is_valid() and request.FILES:
            uploaded_file = request.FILES.get('file') or list(request.FILES.values())[0]
            storage_key = getattr(settings, "DESCRIBED_NAMES_PATH", "reference/described_names.csv")

            if default_storage.exists(storage_key):
                default_storage.delete(storage_key)
            default_storage.save(storage_key, uploaded_file)

            try:
                call_command('migrate_taxonomy_to_db')
                messages.success(request, "Described Names uploaded. Postgres database successfully rebuilt and beetles re-linked.")
            except Exception as e:
                messages.error(request, f"File saved, but database rebuild failed: {str(e)}")

            return redirect("admin_described_names")
        else:
            messages.error(request, "Invalid file submission.")
    else:
        form = DescribedNamesUploadForm()

    return render(request, "admin/tools_described_names.html", {"form": form, "status": current_status})
    
UPDATE_ALLOWED_FIELDS = [
    "alternative_id", "image_institution", "photographer", "image_email", 
    "photo_usage_statement", "aspect", "resolution_in_ppmm", "image_notes", 
    "image_date_taken", "image_has_multiple_individuals", "depicts_specimen", 
    "depicts_valid_name_id", "depicts_described_name_id", "depicts_name_verbatim", 
    "collection_country", "collection_stateProvince", "specimen_sex", 
    "specimen_type_status", "specimen_notes",
    "bbox_x", "bbox_y", "bbox_width", "bbox_height"
]
UPDATE_REQUIRED_COLS = {"record_id"} | set(UPDATE_ALLOWED_FIELDS)
UPDATE_OPTIONAL_COLS = {"update_notes"}
UPDATE_IGNORED_COLS = {
    "image_id", "taxonomy_scientific_name", "taxonomy_subfamily", 
    "taxonomy_tribe", "taxonomy_genus", "taxonomy_species"
}

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
        return redirect("data_management")

    csv_file = request.FILES.get("csv_file") or request.FILES.get("csv")
    if not csv_file:
        messages.error(request, "Please attach a .csv file.")
        return redirect("data_management")

    ext = os.path.splitext(csv_file.name)[1].lower()
    if ext != ".csv":
        messages.error(request, "The update file must be a .csv.")
        return redirect("data_management")

    CSV_MAX = getattr(settings, "MAX_UPLOAD_SIZE_CSV", 10 * 1024 * 1024)  # 10 MB default
    if csv_file.size and csv_file.size > CSV_MAX:
        messages.error(request, f"Update .csv is too large (> {CSV_MAX // (1024*1024)} MB).")
        return redirect("data_management")

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
        return redirect("data_management")

    errors = []
    try:
        df = pd.read_csv(batch.file.path)
        # normalize headers (strip)
        df.columns = [str(c).strip() for c in df.columns]
    except Exception as e:
        batch.mark_rejected_and_move(f"Cannot open CSV: {e}")
        messages.error(request, "Update rejected: cannot open CSV.")
        return redirect("data_management")

    # Header contract: exact match = required set + optional 'update_notes'; no extras, no missing.
    cols = set(df.columns)

    missing = UPDATE_REQUIRED_COLS - cols
    extras = cols - (UPDATE_REQUIRED_COLS | UPDATE_OPTIONAL_COLS | UPDATE_IGNORED_COLS)

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
        return redirect("data_management")

    # -- Starting background validate+apply for this update batch --
    process_update_task.delay(batch.id)

    messages.success(
        request,
            "Update file received. Full validation will run in the background; "
            "track the status below in the Activity Logs."
    )
    # Send them where they can see the batch status
    return redirect("data_management")

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
    return redirect(request.META.get('HTTP_REFERER', 'image_browser'))


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
    Display the taxonomy browser page dynamically grouped from flat Postgres records.
    """
    from beetlesgallery.beetles_app.models import Taxon
    from django.core.serializers.json import DjangoJSONEncoder
    import json
    from collections import defaultdict

    # 1. Fetch all flat taxa from DB
    taxa = Taxon.objects.all()

    # 2. Build nested dictionary: Subfamily -> Tribe -> Genus -> list of Species
    tree_dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    species_map = {}

    for t in taxa:
        subf = t.subfamily.strip() if t.subfamily else "Unknown Subfamily"
        tribe = t.tribe.strip() if t.tribe else "Unknown Tribe"
        genus = t.genus.strip() if t.genus else "Unknown Genus"

        # Add to tree
        tree_dict[subf][tribe][genus].append({
            "name": t.species.strip() if t.species else "sp.",
            "level": "species",
            "species_id": t.valid_species_id,
            "scientific_name": t.scientific_name
        })

        # Pre-fetch for the UI detail pane
        species_map[t.valid_species_id] = {
            "scientificName": t.scientific_name,
            "scientificNameAuthority": t.scientific_name_authority,
            "subfamily": t.subfamily,
            "tribe": t.tribe,
            "genus": t.genus,
            "species": t.species,
        }

    # 3. Recursively convert nested dicts to arrays with speciesCount
    def dict_to_tree(d, current_level):
        next_level_map = {
            "subfamily": "tribe",
            "tribe": "genus",
            "genus": "species"
        }
        next_level = next_level_map.get(current_level)

        result = []
        for key, value in sorted(d.items()):
            if current_level == "genus":
                # Value is a list of species
                sorted_species = sorted(value, key=lambda x: x["name"])
                result.append({
                    "name": key,
                    "level": "genus",
                    "speciesCount": len(sorted_species),
                    "children": sorted_species
                })
            else:
                # Value is a dictionary of the next level
                children = dict_to_tree(value, next_level)
                total_count = sum(c.get("speciesCount", 1) for c in children)
                result.append({
                    "name": key,
                    "level": current_level,
                    "speciesCount": total_count,
                    "children": children
                })
        return result

    # Transform the defaultdict into the final nested array expected by JavaScript
    tree_data = dict_to_tree(tree_dict, "subfamily")

    return render(request, 'beetles/taxonomy_browser.html', {
        'taxonomy_tree_json': json.dumps(tree_data, cls=DjangoJSONEncoder, ensure_ascii=False),
        'species_map_json': json.dumps(species_map, cls=DjangoJSONEncoder, ensure_ascii=False),
    })


def described_names_for_species(request):
    """
    AJAX endpoint: return all described (synonym) names natively from Postgres.
    """
    from beetlesgallery.beetles_app.models import Synonym

    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    species_id = (request.GET.get("species_id") or "").strip()
    if not species_id:
        return JsonResponse({"error": "species_id is required"}, status=400)

    # O(1) Indexed Foreign Key lookup
    synonyms = Synonym.objects.filter(taxon__valid_species_id=species_id).values(
        "name_id", 
        "described_scientific_name", 
        "described_scientific_name_authority",
        "genus", 
        "species", 
        "subspecies", 
        "authority", 
        "year"
    )

    return JsonResponse({"names": list(synonyms)})


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
        .filter(depicts_valid_name_id=species_id, is_deleted=False)
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
    AJAX endpoint: search by original genus or described scientific name via database ILIKE queries.
    """
    from beetlesgallery.beetles_app.models import Taxon, Synonym

    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    field = (request.GET.get("field") or "").strip()
    query = (request.GET.get("query") or "").strip()

    if not field or not query:
        return JsonResponse({"error": "field and query are required"}, status=400)

    species_ids = set()
    matches = []

    if field == "original_genus":
        # Native ILIKE substring search on the database index
        qs = Taxon.objects.filter(original_genus__icontains=query).values_list("valid_species_id", "original_genus")
        for vid, genus in qs:
            species_ids.add(vid)
            if genus:
                matches.append(genus.capitalize())

    elif field == "described_name":
        qs = Synonym.objects.filter(described_scientific_name__icontains=query).select_related("taxon")
        for syn in qs:
            if syn.taxon:
                species_ids.add(syn.taxon.valid_species_id)
            matches.append(syn.described_scientific_name)
    else:
        return JsonResponse({"error": "invalid field"}, status=400)

    return JsonResponse({
        "species_ids": sorted(species_ids),
        "matches": sorted(set(matches))[:20] 
    })


@staff_member_required
def tool_annotate(request):
    """
    Data annotation tool page (staff only).
    """
    from .utils import FILTERS_CONFIG
    from collections import defaultdict
    from django.db.models import Q
    from .models import Beetles
    
    # Query the base records to discover available filter options
    base_qs = Beetles.objects.all()
    grouped_filters = defaultdict(list)
    
    categories = []
    seen_cats = set()
    for cfg in FILTERS_CONFIG:
        if cfg["category"] not in seen_cats:
            categories.append(cfg["category"])
            seen_cats.add(cfg["category"])

    for cfg in FILTERS_CONFIG:
        param = cfg["param"]
        options = []
        has_na = False

        if cfg["type"] == "db":
            field = cfg['field']
            is_strict_type = field in ["image_asset__image_date_taken", "image_asset__resolution_in_ppmm"]
            
            if is_strict_type:
                raw_options = base_qs.exclude(**{f"{field}__isnull": True}) \
                                     .values_list(field, flat=True) \
                                     .distinct().order_by(field)
                has_na = base_qs.filter(**{f"{field}__isnull": True}).exists()
            else:
                raw_options = base_qs.exclude(**{f"{field}__isnull": True}) \
                                     .exclude(**{f"{field}": ""}) \
                                     .values_list(field, flat=True) \
                                     .distinct().order_by(field)
                has_na = base_qs.filter(
                    Q(**{f"{field}__isnull": True}) | 
                    Q(**{f"{field}": ""})
                ).exists()
            
            for o in raw_options:
                val = o.strftime("%Y-%m-%d") if hasattr(o, "strftime") else str(o).strip()
                if val and val not in options:
                    options.append(val)

        elif cfg["type"] in ["bool", "custom_has_rois", "custom_all_rois_val"]:
            options = ["Yes", "No"]
            if cfg["type"] == "bool":
                has_na = base_qs.filter(**{f"{cfg['field']}__isnull": True}).exists()
            else:
                has_na = False
            
        elif cfg["type"] == "ref":
            field_name = f"taxon__{cfg['field']}"
            raw_options = base_qs.exclude(taxon__isnull=True) \
                                 .exclude(**{f"{field_name}__isnull": True}) \
                                 .exclude(**{f"{field_name}": ""}) \
                                 .values_list(field_name, flat=True) \
                                 .distinct().order_by(field_name)
            for o in raw_options:
                val = str(o).strip()
                if val and val not in options:
                    options.append(val)

            has_na = base_qs.filter(
                Q(taxon__isnull=True) | 
                Q(**{f"{field_name}__isnull": True}) | 
                Q(**{f"{field_name}": ""})
            ).exists()

        if has_na:
            options.insert(0, "None")

        if options:
            grouped_filters[cfg["category"]].append({
                "param": param,
                "label": cfg["label"],
                "options": options,
                "selected": [], # Default to no filters selected initially
            })

    filter_context = []
    for cat in categories:
        if grouped_filters[cat]:
            filter_context.append((cat, grouped_filters[cat]))

    return render(request, 'beetles/tool_annotate.html', {
        'filter_groups': filter_context
    })

