import re
import shlex
import uuid
import math
from datetime import date
from django.db.models import Q

# User-visible header-model field map (searchable fields only)
FIELD_MAP = {
    # IDs / identifiers (iexact)
    "name id": "depicts_valid_name_id",
    "described name id": "depicts_described_name_id",

    # Short text (icontains)
    "alternative id": "alternative_id",
    "image institution": "image_asset__image_institution",
    "photographer": "image_asset__photographer",
    "email": "image_asset__image_email",
    "photo usage": "image_asset__photo_usage_statement",
    "aspect": "aspect",
    "specimen": "depicts_specimen",
    "name (verbatim)": "depicts_name_verbatim",
    "country": "collection_country",
    "state/province": "collection_stateProvince",
    "type status": "specimen_type_status",
    
    # New searchable fields
    "specimen notes": "specimen_notes",
    "image notes": "image_asset__image_notes",

    # Special handling fields
    "sex": "specimen_sex",                                     
    "multiple individuals": "image_asset__image_has_multiple_individuals",
    "image date": "image_asset__image_date_taken",
    "resolution": "image_asset__resolution_in_ppmm",
}

# CSV-backed fields (reference lookups)
REF_FIELD_LABELS = {"scientific name", "genus", "species"}

# Operator precedence (no parentheses): NOT > AND > OR
OP_PRECEDENCE = {"NOT": 3, "AND": 2, "OR": 1}
OPERATORS = set(OP_PRECEDENCE.keys())

# Fields to search when user provides "free text" (not Field:Value)
FREE_TEXT_FIELDS = [
    "alternative_id", 
    "image_asset__image_institution",
    "image_asset__photographer",
    "image_asset__image_email",
    "image_asset__photo_usage_statement",
    "image_asset__image_notes",
    "depicts_specimen", 
    "depicts_valid_name_id", 
    "depicts_described_name_id", 
    "depicts_name_verbatim", 
    "collection_country", 
    "collection_stateProvince", 
    "specimen_type_status", 
    "specimen_notes",
    "aspect", 
    "specimen_sex"
]

# --- Query Parser Helpers ---

def _normalize_header(h: str) -> str:
    return (h or "").strip().lower()

def _tokenize_query(qs: str):
    if not qs:
        return []

    try:
        raw = shlex.split(qs, posix=True)
    except ValueError:
        raw = qs.split()

    out = []
    acc = []

    def flush_free_text(tokens):
        if tokens:
            out.append({"free_text": " ".join(tokens)})

    i = 0
    while i < len(raw):
        t = raw[i]
        U = t.upper()
        if not acc and U in OPERATORS:
            out.append({"op": U})
            i += 1
            continue

        if ":" in t or "=" in t:
            delim = ":" if ":" in t else "="
            left, _, right = t.partition(delim)
            header_words = acc + [left]
            acc = []
            header = " ".join(header_words).strip()
            
            if not header:
                flush_free_text(header_words)
                flush_free_text([t])
                i += 1
                continue
                
            value = right.strip()
            if value == "":
                if i + 1 < len(raw) and raw[i + 1].upper() not in OPERATORS:
                    value = raw[i + 1]
                    i += 1
            out.append({"field": header, "value": value})
            i += 1
            continue

        acc.append(t)
        i += 1

    if acc:
        flush_free_text(acc)

    return out

def _to_rpn(parts):
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
    s = (v or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        y, m, d = map(int, s.split("-"))
        return date(y, m, d), None
    if re.fullmatch(r"\d{4}-\d{2}", s):
        y, m = map(int, s.split("-"))
        start = date(y, m, 1)
        if m == 12:
            end = date(y + 1, 1, 1)
        else:
            end = date(y, m + 1, 1)
        return start, end
    if re.fullmatch(r"\d{4}", s):
        y = int(s)
        start = date(y, 1, 1)
        end = date(y + 1, 1, 1)
        return start, end
    return None, None

def _normalize_bool(v: str):
    s = (v or "").strip().lower()
    if s in {"1", "true", "yes", "y"}:
        return True
    if s in {"0", "false", "no", "n"}:
        return False
    return None

def _normalize_sex(v: str):
    s = (v or "").strip().lower()
    if s in {"m", "male"}:
        return "m"
    if s in {"f", "female"}:
        return "f"
    return None

def _clause_to_q(field_label: str, value: str, ignored):
    from . import species_ref
    if not field_label:
        ignored.append("empty field")
        return None

    norm = _normalize_header(field_label)

    if norm in REF_FIELD_LABELS:
        if value and value.strip().upper() == "N/A":
            return Q(depicts_valid_name_id__isnull=True)
        ids = species_ref.ids_for(field_label, value)
        if ids is None:
            ignored.append(f"reference unavailable for '{field_label}'")
            return None
        if not ids:
            return Q(pk__in=[])
        return Q(depicts_valid_name_id__in=list(ids))

    model_field = FIELD_MAP.get(norm)
    if not model_field:
        ignored.append(f"unknown field '{field_label}'")
        return None

    if value is None or value == "" or value.strip().upper() == "N/A":
        if model_field == "image_asset__image_date_taken":
            return Q(**{f"{model_field}__isnull": True})
        return (Q(**{f"{model_field}__isnull": True}) | Q(**{f"{model_field}": ""}))

    if value is None or value == "":
        if model_field == "depicts_valid_name_id":
            return (Q(depicts_valid_name_id__isnull=True) | Q(depicts_valid_name_id__exact=""))
        ignored.append(f"empty value for '{field_label}'")
        return None

    if model_field in {"depicts_valid_name_id", "depicts_described_name_id"}:
        return Q(**{f"{model_field}__iexact": value})

    if model_field == "specimen_sex":
        code = _normalize_sex(value)
        if code is None:
            ignored.append(f"unknown sex value '{value}'")
            return None
        return Q(specimen_sex__iexact=code)

    if model_field == "image_asset__image_has_multiple_individuals":
        b = _normalize_bool(value)
        if b is None:
            ignored.append(f"invalid boolean '{value}' (use yes/no/true/false/1/0)")
            return None
        return Q(**{model_field: b})

    if model_field == "image_asset__image_date_taken":
        start, end = _parse_date_prefix(value)
        if start and end is None:
            return Q(**{model_field: start})
        if start and end:
            return Q(**{f"{model_field}__gte": start, f"{model_field}__lt": end})
        ignored.append(f"invalid date '{value}' (YYYY or YYYY-MM or YYYY-MM-DD)")
        return None

    if model_field == "image_asset__resolution_in_ppmm":
        op, num = _parse_numeric(value)
        if op is None:
            ignored.append(f"invalid numeric '{value}' (try >=10, < 5.5, =12)")
            return None
        lookup = {"exact": "", "lt": "__lt", "lte": "__lte", "gt": "__gt", "gte": "__gte"}[op]
        return Q(**{f"{model_field}{lookup}": num})

    return Q(**{f"{model_field}__icontains": value})

def build_query_q(user_qs: str):
    from . import species_ref
    parts = _tokenize_query(user_qs or "")

    # Exact Record ID (UUID) Check
    # If valid UUID, return exact match on 'id' immediately.
    clean_q = (user_qs or "").strip()
    try:
        uuid_val = uuid.UUID(clean_q)
        return Q(id=uuid_val), []
    except (ValueError, TypeError):
        # Not a valid UUID, proceed to standard tokenization logic
        pass

    ignored = []
    rpn = _to_rpn(parts)
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
                if len(stack) < 2:
                    ignored.append(f"dangling {op}")
                    continue
                b = stack.pop()
                a = stack.pop()
                stack.append((a & b) if op == "AND" else (a | b))

        elif "free_text" in node:
            val = node["free_text"]
            q_any = Q()
            for f in FREE_TEXT_FIELDS:
                q_any |= Q(**{f"{f}__icontains": val})
            matching_ids = species_ref.find_ids_matching_text(val)
            if matching_ids:
                q_any |= Q(depicts_valid_name_id__in=matching_ids)
            stack.append(q_any)

        else:
            q = _clause_to_q(node.get("field", ""), node.get("value", ""), ignored)
            if q is not None:
                stack.append(q)

    if not stack:
        return Q(), ignored
    q = stack[0]
    for extra in stack[1:]:
        q &= extra
    return q, ignored

# --- Configuration for Faceted Search ---
FILTERS_CONFIG = [
    {"category": "Taxonomy", "param": "subfamily", "type": "ref", "field": "subfamily", "label": "Subfamily"},
    {"category": "Taxonomy", "param": "tribe", "type": "ref", "field": "tribe", "label": "Tribe"},
    {"category": "Taxonomy", "param": "subtribe", "type": "ref", "field": "subtribe", "label": "Subtribe"},
    {"category": "Taxonomy", "param": "genus", "type": "ref", "field": "genus", "label": "Genus"},
    {"category": "Taxonomy", "param": "species", "type": "ref", "field": "species", "label": "Species"},
    {"category": "Taxonomy", "param": "subspecies", "type": "ref", "field": "subspecies", "label": "Subspecies"},
    {"category": "Taxonomy", "param": "authority", "type": "ref", "field": "authority", "label": "Authority"},
    {"category": "Taxonomy", "param": "authority_year", "type": "ref", "field": "authorityYear", "label": "Authority Year"},
    {"category": "Taxonomy", "param": "original_genus", "type": "ref", "field": "originalGenus", "label": "Original Genus"},
    {"category": "Collection", "param": "country", "type": "db", "field": "collection_country", "label": "Country"},
    {"category": "Collection", "param": "state", "type": "db", "field": "collection_stateProvince", "label": "State/Province"},
    {"category": "Collection", "param": "sex", "type": "db", "field": "specimen_sex", "label": "Sex"},
    {"category": "Collection", "param": "type_status", "type": "db", "field": "specimen_type_status", "label": "Type Status"},
    {"category": "Image Details", "param": "institution", "type": "db", "field": "image_asset__image_institution", "label": "Institution"},
    {"category": "Image Details", "param": "photographer", "type": "db", "field": "image_asset__photographer", "label": "Photographer"},
    {"category": "Image Details", "param": "usage", "type": "db", "field": "image_asset__photo_usage_statement", "label": "Photo Usage"},
    {"category": "Image Details", "param": "aspect", "type": "db", "field": "aspect", "label": "Aspect"},
    {"category": "Image Details", "param": "date_taken", "type": "db", "field": "image_asset__image_date_taken", "label": "Image Date"},
    {"category": "Image Details", "param": "multiple", "type": "bool", "field": "image_asset__image_has_multiple_individuals", "label": "Multiple Individuals"},
]

def filter_beetles_queryset(qs, filters_dict, size_min=None, size_max=None, res_min=None, res_max=None, exclude_param=None):
    from . import species_ref
    NA = "N/A"
    if size_min:
        try:
            qs = qs.filter(image_asset__image_size_bytes__gte=float(size_min) * 1024 * 1024)
        except ValueError: pass
    if size_max:
        try:
            qs = qs.filter(image_asset__image_size_bytes__lte=float(size_max) * 1024 * 1024)
        except ValueError: pass
    if res_min:
        try:
            qs = qs.filter(image_asset__resolution_in_ppmm__gte=float(res_min))
        except ValueError: pass
    if res_max:
        try:
            qs = qs.filter(image_asset__resolution_in_ppmm__lte=float(res_max))
        except ValueError: pass

    for param, vals in filters_dict.items():
        if param == exclude_param: continue 
        cfg = next((c for c in FILTERS_CONFIG if c["param"] == param), None)
        if not cfg: continue
        has_na = NA in vals
        real_vals = [v for v in vals if v != NA]

        if cfg["type"] == "db":
            q_part = Q()
            if real_vals:
                q_part |= Q(**{f"{cfg['field']}__in": real_vals})
            if has_na:
                if cfg["field"] == "image_asset__image_date_taken":
                    q_part |= Q(**{f"{cfg['field']}__isnull": True})
                else:
                    q_part |= Q(**{f"{cfg['field']}__isnull": True}) | Q(**{f"{cfg['field']}": ""})
            if q_part:
                qs = qs.filter(q_part)

        elif cfg["type"] == "bool":
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
            elif vals:
                return qs.none()
    return qs

def get_system_user(username: str = "admin"):
    from django.contrib.auth import get_user_model
    """
    Return the user used for attribution in import jobs when uploaded_by is missing.
    Prefers an existing account named `username`. If not found, creates a minimal,
    inactive service account so attribution never fails.
    """
    User = get_user_model()
    try:
        return User.objects.get(username=username)
    except User.DoesNotExist:
        # Create a minimal, non-loginable service user.
        u = User(
            username=username,
            is_staff=True,        # can view admin if you later enable it
            is_superuser=False,   # keep it limited; change if you want
            is_active=False,      # cannot log in; just used for attribution
        )
        u.set_unusable_password()
        u.save()
        return u
