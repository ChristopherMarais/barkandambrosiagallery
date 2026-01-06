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
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib.auth.views import LoginView as DjangoLoginView, LogoutView
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.files.storage import default_storage

from . import species_ref
from .models import Beetles, UploadBatch, DownloadJob, UpdateBatch
from .schema import REQUIRED_COLS, MAX_ROWS
from .forms import TailwindUserCreationForm, ProfileForm, PasswordChangeFormStyled, ValidSpeciesUploadForm, UpdateBatchUploadForm

import pandas as pd

def landing(request):
    # 1. Total number of images
    total_images = Beetles.objects.count()

    # 2. Number of unique species (based on the valid name ID)
    # We filter out nulls to get an accurate count of identified species
    total_species = Beetles.objects.exclude(depicts_valid_name_id__isnull=True)\
                                   .values('depicts_valid_name_id')\
                                   .distinct().count()

    # 3. Count of images with a Type Status
    # This checks for fields that are neither null nor empty strings
    type_status_count = Beetles.objects.exclude(specimen_type_status__isnull=True)\
                                       .exclude(specimen_type_status="")\
                                       .count()

    context = {
        'total_images': total_images,
        'total_species': total_species,
        'type_status_count': type_status_count,
    }
    return render(request, 'landing.html', context)


@login_required
def my_account(request):
    user = request.user

    if request.method == "POST":
        # Which form was submitted?
        if "profile_submit" in request.POST:
            pform = ProfileForm(request.POST, instance=user)
            cform = PasswordChangeFormStyled(user=user)  # keep password form empty for render
            if pform.is_valid():
                pform.save()
                messages.success(request, "Profile updated.")
                return redirect("my_account")
        elif "password_submit" in request.POST:
            pform = ProfileForm(instance=user)  # show current values
            cform = PasswordChangeFormStyled(user=user, data=request.POST)
            if cform.is_valid():
                user = cform.save()
                update_session_auth_hash(request, user)  # keep them logged in
                messages.success(request, "Password changed.")
                return redirect("my_account")
        else:
            # Unknown submit; re-render both forms
            pform = ProfileForm(instance=user)
            cform = PasswordChangeFormStyled(user=user)
    else:
        pform = ProfileForm(instance=user)
        cform = PasswordChangeFormStyled(user=user)

    return render(
        request,
        "accounts/my_account.html",
        {"profile_form": pform, "password_form": cform},
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
    """
    Renders the marketing/landing page (templates/landing.html).
    """
    return render(request, 'landing.html')


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
    batch.size_bytes = batch.file.size or 0
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
    manage_py = os.path.join(settings.BASE_DIR, "manage.py")
    try:
        proc = subprocess.Popen(
            [
                sys.executable,  # use the same Python/venv as this Django process
                manage_py,
                "process_single_upload",
                "--id",
                str(batch.id),
            ],
            cwd=settings.BASE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
         )
    
        print(f"Spawned process_single_upload PID={proc.pid} for batch {batch.id}", flush=True)

    except Exception as e:
        # Don't break the user request if worker fails to start; just log it.
        print(f"ERROR: failed to start process_single_upload for batch {batch.id}: {e}", flush=True)

    messages.success(request, "Files received and passed quick checks. Track upload status on My Files page under My Uploads. You may leave this page.")
    return redirect("upload")


# User-visible header-model field map (searchable fields only)
FIELD_MAP = {
    # IDs / identifiers (iexact)
    "name id": "depicts_valid_name_id",
    "described name id": "depicts_described_name_id",

    # Short text (icontains)
    "alternative id": "alternative_id",
    "image institution": "image_institution",
    "photographer": "photographer",
    "email": "image_email",
    "photo usage": "photo_usage_statement",
    "aspect": "aspect",
    "specimen": "depicts_specimen",
    "name (verbatim)": "depicts_name_verbatim",
    "country": "collection_country",
    "state/province": "collection_stateProvince",
    "type status": "specimen_type_status",
    
    # New searchable fields
    "specimen notes": "specimen_notes",
    "image notes": "image_notes",

    # Special handling fields
    "sex": "specimen_sex",                                     # normalized m/f
    "multiple individuals": "image_has_multiple_individuals",  # boolean yes/no
    "image date": "image_date_taken",                          # YYYY / YYYY-MM / YYYY-MM-DD
    "resolution": "resolution_in_ppmm",                        # numeric with operators
}

# CSV-backed fields (reference lookups)
REF_FIELD_LABELS = {"scientific name", "genus", "species"}

# Operator precedence (no parentheses): NOT > AND > OR
OP_PRECEDENCE = {"NOT": 3, "AND": 2, "OR": 1}
OPERATORS = set(OP_PRECEDENCE.keys())

# Fields to search when user provides "free text" (not Field:Value)
FREE_TEXT_FIELDS = [
    "alternative_id", "image_institution", "photographer", "image_email", 
    "photo_usage_statement", "image_notes", "depicts_specimen", 
    "depicts_valid_name_id", "depicts_described_name_id", 
    "depicts_name_verbatim", "collection_country", "collection_stateProvince", 
    "specimen_type_status", "specimen_notes",
    "aspect", "specimen_sex"
]


#----------------------
# Query Parser Helpers
#----------------------

def _normalize_header(h: str) -> str:
    return (h or "").strip().lower()

def _tokenize_query(qs: str):
    """
    Split respecting quotes. Allow field names with spaces:
    e.g., 'Name ID:123', 'Type Status:male'.
    Strategy:
      - shlex.split keeps quoted values together.
      - Then scan left-to-right accumulating header parts until token that contains ':' or '='.
      - That token marks the end of the header; the part after ':'/'=' is the value (may be empty).
    Returns a list of either:
      - dict(field='Name ID', value='123')
      - dict(op='AND'/'OR'/'NOT')
      - dict(free_text='...') for words not part of a Field:Value pair
    """
    if not qs:
        return []

    try:
        raw = shlex.split(qs, posix=True)
    except ValueError:
        # Fallback for unbalanced quotes
        raw = qs.split()

    out = []
    acc = []  # accumulating header words that include spaces

    def flush_free_text(tokens):
        if tokens:
            out.append({"free_text": " ".join(tokens)})

    i = 0
    while i < len(raw):
        t = raw[i]
        U = t.upper()
        
        # If we hit an operator and haven't accumulated a header, it's an operator
        if not acc and U in OPERATORS:
            out.append({"op": U})
            i += 1
            continue

        # Look for a token that contains a delimiter
        if ":" in t or "=" in t:
            # split on the first delimiter
            delim = ":" if ":" in t else "="
            left, _, right = t.partition(delim)
            header_words = acc + [left]
            acc = []  # reset for the next clause
            header = " ".join(header_words).strip()
            
            # If the header part is empty (e.g. ":value"), treat as free text
            if not header:
                flush_free_text(header_words)  # Should handle [left] if left is empty?
                # Actually if left is empty, header_words is empty or just spaces
                # Treat 'right' as value? Without header we can't do Field search.
                # Treat the whole thing as free text.
                flush_free_text([t])
                i += 1
                continue
                
            value = right.strip()
            if value == "":
                # If value is empty, but next token exists and is not an operator, treat next token as value
                if i + 1 < len(raw) and raw[i + 1].upper() not in OPERATORS:
                    value = raw[i + 1]
                    i += 1  # consume it
            out.append({"field": header, "value": value})
            i += 1
            continue

        # No operator, no delimiter -> part of a header OR free text
        acc.append(t)
        i += 1

    # Trailing tokens without value/delimiter -> treat as free text
    if acc:
        flush_free_text(acc)

    return out

def _to_rpn(parts):
    """
    Shunting-yard (no parentheses):
      - Outputs a list where ops come after their operands.
    """
    output = []
    stack = []
    for p in parts:
        if "op" in p:
            op = p["op"]
            while stack and stack[-1] in OPERATORS and OP_PRECEDENCE[stack[-1]] >= OP_PRECEDENCE[op]:
                output.append({"op": stack.pop()})
            stack.append(op)
        else:
            output.append(p)
    while stack:
        output.append({"op": stack.pop()})
    return output

_NUM_OP_RE = re.compile(r"^\s*(<=|>=|<|>|=)?\s*([+-]?\d+(?:\.\d+)?)\s*$")

def _parse_numeric(value: str):
    """
    Returns (op, number) where op in {'lt','lte','gt','gte','exact'}.
    Accepts value like '>=10', '< 5.2', '12.0', '=7'.
    """
    m = _NUM_OP_RE.match(value or "")
    if not m:
        return None, None
    raw_op, num_s = m.groups()
    if raw_op in (None, "", "="):
        op = "exact"
    elif raw_op == "<":
        op = "lt"
    elif raw_op == "<=":
        op = "lte"
    elif raw_op == ">":
        op = "gt"
    elif raw_op == ">=":
        op = "gte"
    else:
        return None, None
    try:
        num = float(num_s)
    except Exception:
        return None, None
    return op, num

def _parse_date_prefix(v: str):
    """
    Accept 'YYYY', 'YYYY-MM', or 'YYYY-MM-DD'.
    Returns (start_date, end_date_exclusive) for ranges,
    or (exact_date, None) for exact day.
    """
    s = (v or "").strip()
    # YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        y, m, d = map(int, s.split("-"))
        return date(y, m, d), None
    # YYYY-MM
    if re.fullmatch(r"\d{4}-\d{2}", s):
        y, m = map(int, s.split("-"))
        start = date(y, m, 1)
        # next month
        if m == 12:
            end = date(y + 1, 1, 1)
        else:
            end = date(y, m + 1, 1)
        return start, end
    # YYYY
    if re.fullmatch(r"\d{4}", s):
        y = int(s)
        start = date(y, 1, 1)
        end = date(y + 1, 1, 1)
        return start, end
    return None, None

def _normalize_bool(v: str):
    """
    Accept yes/no, true/false, 1/0 (case-insensitive).
    Returns True/False or None if unrecognized.
    """
    s = (v or "").strip().lower()
    if s in {"1", "true", "yes", "y"}:
        return True
    if s in {"0", "false", "no", "n"}:
        return False
    return None

def _normalize_sex(v: str):
    """
    Map 'male'/'m' -> 'm', 'female'/'f' -> 'f'.
    Return the stored one-letter code, or None if unknown.
    """
    s = (v or "").strip().lower()
    if s in {"m", "male"}:
        return "m"
    if s in {"f", "female"}:
        return "f"
    return None

def _clause_to_q(field_label: str, value: str, ignored):
    """
    Build a Q() for a single field:value clause.
    """
    if not field_label:
        ignored.append("empty field")
        return None

    norm = _normalize_header(field_label)

    # --- CSV-backed fields ---
    if norm in REF_FIELD_LABELS:
        # If user searches 'genus:N/A', return records where valid_name_id is null
        if value and value.strip().upper() == "N/A":
            return Q(depicts_valid_name_id__isnull=True)
            
        ids = species_ref.ids_for(field_label, value)
        if ids is None:
            ignored.append(f"reference unavailable for '{field_label}'")
            return None
        if not ids:
            return Q(pk__in=[])
        return Q(depicts_valid_name_id__in=list(ids))

    # --- DB-backed fields ---
    model_field = FIELD_MAP.get(norm)
    if not model_field:
        ignored.append(f"unknown field '{field_label}'")
        return None

    # Handle "N/A" or empty values
    if value is None or value == "" or value.strip().upper() == "N/A":
        if model_field == "image_date_taken":
            # Prevents: django.core.exceptions.ValidationError: ['“” value has an invalid date format.']
            return Q(image_date_taken__isnull=True)
        
        return (
            Q(**{f"{model_field}__isnull": True}) |
            Q(**{f"{model_field}": ""})
        )

    # Special case: "Name ID:" with no value → Name ID is blank (NULL or empty string)
    if value is None or value == "":
        if model_field == "depicts_valid_name_id":
            # Blank = NULL (and, for safety, also empty string if any ever appear)
            return (
                Q(depicts_valid_name_id__isnull=True) |
                Q(depicts_valid_name_id__exact="")
            )
        # For all other fields, treat empty value as an error as before
        ignored.append(f"empty value for '{field_label}'")
        return None

    # IDs: iexact match
    if model_field in {"depicts_valid_name_id", "depicts_described_name_id"}:
        return Q(**{f"{model_field}__iexact": value})

    # Sex normalization
    if model_field == "specimen_sex":
        code = _normalize_sex(value)
        if code is None:
            ignored.append(f"unknown sex value '{value}'")
            return None
        return Q(specimen_sex__iexact=code)

    # Boolean
    if model_field == "image_has_multiple_individuals":
        b = _normalize_bool(value)
        if b is None:
            ignored.append(f"invalid boolean '{value}' (use yes/no/true/false/1/0)")
            return None
        return Q(image_has_multiple_individuals=b)

    # Date prefixes
    if model_field == "image_date_taken":
        start, end = _parse_date_prefix(value)
        if start and end is None:
            # exact day
            return Q(image_date_taken=start)
        if start and end:
            return Q(image_date_taken__gte=start, image_date_taken__lt=end)
        ignored.append(f"invalid date '{value}' (YYYY or YYYY-MM or YYYY-MM-DD)")
        return None

    # Numeric with operators
    if model_field == "resolution_in_ppmm":
        op, num = _parse_numeric(value)
        if op is None:
            ignored.append(f"invalid numeric '{value}' (try >=10, < 5.5, =12)")
            return None
        lookup = {
            "exact": "",
            "lt": "__lt",
            "lte": "__lte",
            "gt": "__gt",
            "gte": "__gte",
        }[op]
        return Q(**{f"{model_field}{lookup}": num})

    # Short text: icontains (including email, aspect, institution, etc.)
    return Q(**{f"{model_field}__icontains": value})

def build_query_q(user_qs: str):
    """
    Full pipeline: tokenize -> RPN -> evaluate to Q
    Returns (Q_object, ignored_tokens_list)
    """
    parts = _tokenize_query(user_qs or "")
    ignored = []  # We don't use 'error' key anymore, but check for weird states if needed

    # Filter only valid parts/ops for RPN
    linear = parts # All parts are now valid (either field, op, or free_text)

    rpn = _to_rpn(linear)

    stack = []
    for node in rpn:
        if "op" in node:
            op = node["op"]
            if op == "NOT":
                if not stack:
                    ignored.append("dangling NOT")
                    continue
                a = stack.pop()
                stack.append(~a)
            else:
                # binary
                if len(stack) < 2:
                    ignored.append(f"dangling {op}")
                    continue
                b = stack.pop()
                a = stack.pop()
                stack.append((a & b) if op == "AND" else (a | b))

        elif "free_text" in node:
            # --- MODIFIED LOGIC START ---
            val = node["free_text"]
            q_any = Q()
            
            # 1. Search DB text fields (requires FREE_TEXT_FIELDS to be updated globally)
            for f in FREE_TEXT_FIELDS:
                q_any |= Q(**{f"{f}__icontains": val})
            
            # 2. Search Reference/Taxonomy CSV (e.g. Subfamily, Genus, Authority)
            # This calls the new helper function in species_ref.py
            matching_ids = species_ref.find_ids_matching_text(val)
            if matching_ids:
                # Add any image whose 'depicts_valid_name_id' matches the found taxonomy
                q_any |= Q(depicts_valid_name_id__in=matching_ids)
            
            stack.append(q_any)
            # --- MODIFIED LOGIC END ---

        else:
            q = _clause_to_q(node.get("field", ""), node.get("value", ""), ignored)
            if q is not None:
                stack.append(q)

    # Combine any remaining Qs with AND (conservative)
    if not stack:
        return Q(), ignored
    q = stack[0]
    for extra in stack[1:]:
        q &= extra
    return q, ignored


# --- 2. GALLERY VIEW (The Database Browser) ---
# --- Configuration for Faceted Search ---
# Grouped by category for display.
FILTERS_CONFIG = [
    # --- TAXONOMY ---
    {"category": "Taxonomy", "param": "subfamily", "type": "ref", "field": "subfamily", "label": "Subfamily"},
    {"category": "Taxonomy", "param": "tribe", "type": "ref", "field": "tribe", "label": "Tribe"},
    {"category": "Taxonomy", "param": "subtribe", "type": "ref", "field": "subtribe", "label": "Subtribe"},
    {"category": "Taxonomy", "param": "genus", "type": "ref", "field": "genus", "label": "Genus"},
    {"category": "Taxonomy", "param": "species", "type": "ref", "field": "species", "label": "Species"},
    {"category": "Taxonomy", "param": "subspecies", "type": "ref", "field": "subspecies", "label": "Subspecies"},
    {"category": "Taxonomy", "param": "authority", "type": "ref", "field": "authority", "label": "Authority"},
    {"category": "Taxonomy", "param": "authority_year", "type": "ref", "field": "authorityYear", "label": "Authority Year"},
    {"category": "Taxonomy", "param": "original_genus", "type": "ref", "field": "originalGenus", "label": "Original Genus"},

    # --- COLLECTION ---
    {"category": "Collection", "param": "country", "type": "db", "field": "collection_country", "label": "Country"},
    {"category": "Collection", "param": "state", "type": "db", "field": "collection_stateProvince", "label": "State/Province"},
    {"category": "Collection", "param": "sex", "type": "db", "field": "specimen_sex", "label": "Sex"},

    # --- IMAGE DETAILS ---
    {"category": "Image Details", "param": "institution", "type": "db", "field": "image_institution", "label": "Institution"},
    {"category": "Image Details", "param": "photographer", "type": "db", "field": "photographer", "label": "Photographer"},
    {"category": "Image Details", "param": "usage", "type": "db", "field": "photo_usage_statement", "label": "Photo Usage"},
    {"category": "Image Details", "param": "aspect", "type": "db", "field": "aspect", "label": "Aspect"},
    {"category": "Image Details", "param": "date_taken", "type": "db", "field": "image_date_taken", "label": "Image Date"},
    {"category": "Image Details", "param": "multiple", "type": "bool", "field": "image_has_multiple_individuals", "label": "Multiple Individuals"},
]

# In beetlesgallery/beetles_app/views.py

def gallery(request):
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

    # Helper: Apply filters (Updated for Lists)
    def apply_filters(qs, filters_dict, exclude_param=None):
        # Apply Size Filter
        if size_min:
            try:
                qs = qs.filter(image_size_bytes__gte=float(size_min) * 1024 * 1024)
            except ValueError: pass
        if size_max:
            try:
                qs = qs.filter(image_size_bytes__lte=float(size_max) * 1024 * 1024)
            except ValueError: pass

        # Apply Resolution Filter
        if res_min:
            try:
                qs = qs.filter(resolution_in_ppmm__gte=float(res_min))
            except ValueError: pass
        if res_max:
            try:
                qs = qs.filter(resolution_in_ppmm__lte=float(res_max))
            except ValueError: pass

        for param, vals in filters_dict.items():
            if param == exclude_param: continue 
            
            cfg = next((c for c in FILTERS_CONFIG if c["param"] == param), None)
            if not cfg: continue

            # Separate "N/A" from real values
            has_na = NA in vals
            real_vals = [v for v in vals if v != NA]

            if cfg["type"] == "db":
                q_part = Q()
                if real_vals:
                    q_part |= Q(**{f"{cfg['field']}__in": real_vals})
                
                if has_na:
                    if cfg["field"] == "image_date_taken":
                        # Only use isnull for DateFields
                        q_part |= Q(**{f"{cfg['field']}__isnull": True})
                    else:
                        q_part |= Q(**{f"{cfg['field']}__isnull": True}) | Q(**{f"{cfg['field']}": ""})

                if q_part:
                    qs = qs.filter(q_part)

            elif cfg["type"] == "bool":
                # If both Yes and No are selected, it effectively means "All", so ignore.
                # If only one is selected, filter by it.
                bool_vals = set()
                for v in vals:
                    b = _normalize_bool(v)
                    if b is not None:
                        bool_vals.add(b)
                if len(bool_vals) == 1:
                    qs = qs.filter(**{cfg['field']: list(bool_vals)[0]})
            elif cfg["type"] == "ref":
                q_ref = Q()
                if real_vals:
                    all_ids = []
                    for v in real_vals:
                        ids = species_ref.ids_for(cfg['field'], v)
                        if ids: all_ids.extend(ids)
                    if all_ids:
                        q_ref |= Q(depicts_valid_name_id__in=all_ids)
                
                if has_na:
                    q_ref |= Q(depicts_valid_name_id__isnull=True)

                if q_ref:
                    qs = qs.filter(q_ref)
                elif vals: # If filters were selected but no IDs matched
                    return qs.none()
        return qs

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
            if cfg["field"] == "image_date_taken":
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
        b.warn_large = (b.image_size_bytes or 0) >= WARN_IMAGE_SIZE_BYTES

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
    # If user is not logged in, redirect to login with a message
    if not request.user.is_authenticated:
        login_url = reverse("login")
        # Preserve the page they were trying to access
        return redirect(f"{login_url}?next={request.path}")

    beetle = (
        Beetles.objects
        .all()
        .get(pk=beetle_id)
    )

    # CSV-based enrichment for detail page (all fields) with "Unknown" fallback
    raw_vid = beetle.depicts_valid_name_id
    norm_vid = _normalize_valid_id_for_lookup(raw_vid)

    ref_species = species_ref.resolve(norm_vid) if norm_vid is not None else None
    ref_version = species_ref.get_version()

    return render(
        request,
        "beetles/detail.html",
        {"beetle": beetle, "ref_species": ref_species, "ref_version": ref_version},
    )


def signup(request):
    if request.method == "POST":
        form = TailwindUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            # save email if you included it in the template
            email = request.POST.get("email", "").strip()
            if email:
                user.email = email
                user.save(update_fields=["email"])
            login(request, user)
            return redirect("upload")
    else:
        form = TailwindUserCreationForm()
    return render(request, "accounts/signup.html", {"form": form})

@login_required
@require_POST
def start_batch_download(request):
    """
    Create a DownloadJob for either:
      - selection_mode=ids: comma-separated UUIDs in 'selected_ids'
      - selection_mode=query: current q string in 'q' and a 'total_matches' hint
    This records intent only; files are built later by a worker/command.
    """
    mode = (request.POST.get("selection_mode") or "").strip()
    q_str = (request.POST.get("q") or "").strip()
    total = request.POST.get("total_matches")
    try:
        total = int(total) if total is not None else 0
    except ValueError:
        total = 0

    # --- DEBUG: dump POST keys & sample values (visible in runserver console) ---
    try:
        post_snapshot = {
            k: request.POST.getlist(k)[:5]  # first few values per key
            for k in request.POST.keys()
        }
        print("[start_batch_download] POST keys snapshot:", list(request.POST.keys()))
        print("[start_batch_download] POST sample values:", post_snapshot)
    except Exception as _e:
        print("[start_batch_download] POST debug failed:", _e)

    if mode not in ("ids", "query"):
        messages.error(request, "Invalid selection mode.")
        return redirect("home")

    job = DownloadJob.objects.create(
        requested_by=request.user,
        selection_mode=mode,
        query_string=q_str if mode == "query" else "",
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

        print(
            f"[start_batch_download] mode=ids user={request.user.pk} "
            f"raw_ids_str_len={len(raw_ids_str)} "
            f"counts={{'selected_ids': {len(raw_ids_list)}, "
            f"'selected_ids[]': {len(request.POST.getlist('selected_ids[]'))}, "
            f"'selected': {len(request.POST.getlist('selected'))}}}"
        )

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
            if ids:
                print(f"[start_batch_download] rescued {len(ids)} UUID(s) from arbitrary POST fields")

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


    if mode == "query" and (not q_str):
        messages.warning(request, "You requested a download of the entire database. This may be large.")

    manage_py = os.path.join(settings.BASE_DIR, "manage.py")
    try:
        proc = subprocess.Popen(
            [
                sys.executable,      # use the same Python/venv as this Django process
                manage_py,
                "build_downloads",
                "--job",
                str(job.id),
                "--limit",
                "1",
            ],
            cwd=settings.BASE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(
            f"[start_batch_download] Spawned build_downloads for job {job.id} (PID={proc.pid})",
            flush=True,
        )
    except Exception as e:
        # Log the failure and mark the job as failed so the user sees it on My Files.
        print(
            f"[start_batch_download] ERROR spawning build_downloads for job {job.id}: {e}",
            flush=True,
        )
        job.status = DownloadJob.Status.FAILED
        job.error_message = "Internal error starting download build. Please try again later."
        job.save(update_fields=["status", "error_message"])
        messages.error(request, "There was a problem starting the download job. Please try again later.")
        return redirect("my_uploads")

    messages.success(request, "Batch download request created. You can track status on My Files -> My Downloads.")
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

@staff_member_required
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
@staff_member_required
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
    manage_py = os.path.join(settings.BASE_DIR, "manage.py")
    python = sys.executable or "python3"
    cmd = [python, manage_py, "process_single_update", "--id", str(batch.id)]

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=settings.BASE_DIR,
        )
    except Exception as e:
        # If we can't start the worker, leave batch in STAGING and
        # tell the user how to run it manually.
        messages.warning(
            request,
            (
                "Update file received and passed quick checks, but "
                "background validation/apply could not be started "
                f"(error: {e}). You can run the pipeline manually:\n"
                f"  python manage.py validate_updates --id {batch.id}\n"
                f"  python manage.py apply_updates --id {batch.id}"
            ),
        )
        return redirect("update_upload")

    messages.success(
        request,
        "Update file received and passed quick checks. "
        "Full validation and apply will now run in the background; "
        "you can track status on My Files → My Updates."
    )
    # Send them where they can see the batch status
    return redirect("my_uploads")