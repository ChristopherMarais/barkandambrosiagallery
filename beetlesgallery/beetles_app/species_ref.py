from __future__ import annotations
import csv, io, hashlib, os
from typing import Dict, Iterable, Optional

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.core.files.storage import default_storage

from collections import defaultdict

# Settings
_PATH = getattr(settings, "VALID_SPECIES_PATH", "reference/valid_species.csv")
_VERSION_KEY = getattr(settings, "VALID_SPECIES_VERSION_CACHE_KEY", "valid_species:version")
_UPDATING_KEY = getattr(settings, "VALID_SPECIES_UPDATING_CACHE_KEY", "valid_species:updating")
_LABEL_KEY = getattr(settings, "VALID_SPECIES_VERSION_LABEL_CACHE_KEY", "valid_species:label")

# User-visible label -> CSV column key
_REF_FIELD_LABEL_TO_CSV = {
    "scientific name": "scientificName",
    "scientific name authority": "scientificNameAuthority",
    "genus": "genus",
    "species": "species",
    "subfamily": "subfamily",
    "tribe": "tribe",
    "subtribe": "subtribe",
    "subspecies": "subspecies",
    "authority": "authority",
    "authority year": "authorityYear",
    "original genus": "originalGenus",
}

# In-process reverse index (rebuilt if version changes)
_rev_index = None            # dict: field_key -> dict(lower_value -> set(id))
_rev_index_version = None    # to invalidate on CSV change


# CSV columns (exact header names)
COLUMNS = [
    "valid_species_id",
    "scientificName",
    "scientificNameAuthority",
    "subfamily",
    "tribe",
    "subtribe",
    "genus",
    "species",
    "subspecies",
    "authority",
    "authorityYear",
    "originalGenus",
]

# Module-level cache
_MAP: Optional[Dict[str, Dict[str, str]]] = None
_VERSION: Optional[str] = None  # sha256 of file content

def _load_all_rows():
    """
    Load entire valid_species CSV as a list of dict rows.
    Uses default_storage so it works locally and with S3.
    """
    # open binary, then wrap to control encoding/newline
    with default_storage.open(_PATH, "rb") as fb:
        with io.TextIOWrapper(fb, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            # normalize headers by stripping whitespace (BOM handled by utf-8-sig)
            reader.fieldnames = [h.strip() for h in (reader.fieldnames or [])]
            return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in reader]

def _ensure_reverse_index():
    global _rev_index, _rev_index_version
    version = get_version() or "na"
    if _rev_index is not None and _rev_index_version == version:
        return

    rows = _load_all_rows()
    
    # UPDATE: Index ALL columns we want to filter by
    indexable_cols = [
        "scientificName", "scientificNameAuthority",
        "subfamily", "tribe", "subtribe", 
        "genus", "species", "subspecies",
        "authority", "authorityYear", "originalGenus"
    ]
    
    idx = defaultdict(lambda: defaultdict(set))

    for row in rows:
        vid = str(row.get("valid_species_id", "")).strip()
        if not vid:
            continue
        
        for col in indexable_cols:
            val = (row.get(col) or "").strip()
            if val:
                # Index by lowercase for case-insensitive lookup
                idx[col][val.lower()].add(vid)

    _rev_index = idx
    _rev_index_version = version

def ids_for(field_label: str, value: str):
    """
    Return a set of valid_species_id strings whose <field_label>
    equals <value> (case-insensitive, exact).
    """
    if not field_label or not value:
        return set()
    
    clean_label = field_label.strip().lower()
    csv_key = _REF_FIELD_LABEL_TO_CSV.get(clean_label, clean_label)

    try:
        _ensure_reverse_index()
    except Exception:
        return None

    col_index = _rev_index.get(csv_key)
    if not col_index:
        return set()
        
    return set(col_index.get(value.strip().lower(), set()))

def get_field_values_for_ids(ids: Iterable[object], field_key: str) -> set[str]:
    """
    Given a list of valid_species_ids (e.g. from the filtered search results),
    return the set of distinct values for the specified column (e.g. 'subfamily').
    """
    _ensure_loaded()
    if not _MAP: 
        return set()
    
    res = set()
    field = field_key.strip()
    
    for vid in ids:
        key = str(vid).strip()
        if key in _MAP:
             val = _MAP[key].get(field)
             if val and val.lower() != "unknown":
                 res.add(val)
    return res

def _load_map_from_storage() -> tuple[Dict[str, Dict[str, str]], str]:
    """
    Read the CSV from storage, compute sha256, and build an ID->row map.
    Keeps ALL columns, but the map value excludes the key column for convenience.
    """
    # Read bytes once to compute hash and then parse text
    with default_storage.open(_PATH, "rb") as fb:
        data = fb.read()

    sha = hashlib.sha256(data).hexdigest()
    text = data.decode("utf-8-sig")  # handles BOM safely
    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        raise ValueError("valid_species CSV has no header row.")

    # 🔧 Normalize headers (strip whitespace) to match COLUMNS
    reader.fieldnames = [h.strip() for h in reader.fieldnames]

    missing = set(COLUMNS) - set(reader.fieldnames)
    if missing:
        raise ValueError(f"valid_species CSV missing columns: {sorted(missing)}")

    m: Dict[str, Dict[str, str]] = {}
    seen = set()
    for row in reader:
        # keys in row are now the stripped fieldnames above
        key = (row.get("valid_species_id") or "").strip()
        if not key:
            continue
        if key in seen:
            # last one wins; uploader should guard against this upstream
            pass
        seen.add(key)

        cleaned = {
            col: (row.get(col, "") or "").strip()
            for col in COLUMNS
            if col != "valid_species_id"
        }
        m[key] = cleaned

    return m, sha


def _ensure_loaded() -> None:
    """
    Ensure the in-process cache is hot and matches the latest published version in Django cache.
    """
    global _MAP, _VERSION

    published_version = cache.get(_VERSION_KEY)
    # Load if (a) first time, (b) published version changed, or (c) nothing published yet but map empty
    if _MAP is None or (published_version and _VERSION != published_version):
        try:
            m, sha = _load_map_from_storage()
        except Exception:
            # If we have an existing map, keep serving it; else bubble the error
            if _MAP is None:
                raise
            return
        _MAP, _VERSION = m, sha
        # If no version was published, publish ours; otherwise just align to published_version
        cache.set(_VERSION_KEY, published_version or sha, None)


def get_label():
    # First try cache (set by update_valid_species)
    label = cache.get(_LABEL_KEY)
    if label:
        return label

    # Fallback: derive label from file mtime (works across processes and in prod)
    try:
        path = getattr(settings, "VALID_SPECIES_PATH", "reference/valid_species.csv")
        mtime = default_storage.get_modified_time(path)
        # format a friendly UTC timestamp
        try:
            return mtime.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            # if naive datetime
            return mtime.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return None

def _get_mtime_utc():
    """
    Return the storage mtime of the published valid_species.csv as an aware UTC datetime.
    Fall back to timezone.now() if unavailable.
    """
    try:
        dt = default_storage.get_modified_time(_PATH)
        # Normalize to aware UTC
        try:
            return dt.astimezone(timezone.utc)
        except Exception:
            # If dt is naive, make it aware in UTC
            if timezone.is_naive(dt):
                return timezone.make_aware(dt, timezone.utc)
            return dt
    except Exception:
        # Fallback: current UTC time (should be rare)
        return timezone.now().astimezone(timezone.utc)


def build_download_filename() -> str:
    """
    Filename policy for the public download:
    taxonomy_ref_YYYY-MM-DD_HH-MM_UTC.csv
    """
    ts = _get_mtime_utc()
    stamp = ts.strftime("%Y-%m-%d_%H-%M_UTC")
    return f"taxonomy_ref_{stamp}.csv"

def status() -> Dict[str, object]:
    return {
        "version": cache.get(_VERSION_KEY),          # internal hash
        "label": get_label(),                        # human-friendly with fallback
        "updating": bool(cache.get(_UPDATING_KEY)),
    }

def get_version() -> Optional[str]:
    # Internal control
    _ensure_loaded()
    return _VERSION

def lookup(valid_species_id: object) -> Optional[Dict[str, str]]:
    _ensure_loaded()
    key = (str(valid_species_id) if valid_species_id is not None else "").strip()
    return _MAP.get(key) if _MAP else None


def bulk_lookup(ids: Iterable[object]) -> Dict[str, Dict[str, str]]:
    _ensure_loaded()
    res: Dict[str, Dict[str, str]] = {}
    if not _MAP:
        return res
    for vid in ids:
        key = (str(vid) if vid is not None else "").strip()
        if key in _MAP:
            res[key] = _MAP[key]
    return res

# --- Publisher helper (used by mgmt command and admin view) ---

def publish_from_file(src_path: str, label: Optional[str] = None) -> tuple[int, str]:
    """
    Copy the CSV at src_path to default_storage at _PATH, validate, compute sha,
    and update cache keys (version + label + updating flag). Returns (row_count, version_sha).
    """
    # mark 'updating' so the site-wide banner can show
    cache.set(_UPDATING_KEY, True, 60)  # short TTL; we clear explicitly below
    try:
        # Read source bytes once (works for local/remote paths via Python open)
        with open(src_path, "rb") as f:
            data = f.read()

        # Validate / normalize headers and count rows by reusing parser
        text = data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("valid_species CSV has no header row.")
        reader.fieldnames = [h.strip() for h in reader.fieldnames]
        missing = set(COLUMNS) - set(reader.fieldnames)
        if missing:
            raise ValueError(f"valid_species CSV missing columns: {sorted(missing)}")

        rows = 0
        for _ in reader:
            rows += 1

        # Write to storage atomically-ish: first to a temp key, then rename/overwrite final key
        tmp_key = f"{_PATH}.tmp"
        
        try:
            # If using local storage, we must ensure the folder exists
            full_path = default_storage.path(tmp_key)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
        except NotImplementedError:
            # Cloud storage (S3/Azure) doesn't need folder creation; ignore
            pass

        with default_storage.open(tmp_key, "wb") as out:
            out.write(data)

        # Archive old version before replacing (if it exists)
        if default_storage.exists(_PATH):
            try:
                # Create archive path with timestamp in subfolder
                from django.utils import timezone
                timestamp = timezone.now().strftime("%Y-%m-%d_%H-%M_UTC")
                archive_path = f"reference/archive/valid_species/valid_species_{timestamp}.csv"

                # Ensure archive folder exists (for local storage)
                try:
                    archive_full_path = default_storage.path(archive_path)
                    os.makedirs(os.path.dirname(archive_full_path), exist_ok=True)
                except NotImplementedError:
                    pass

                # Copy current file to archive
                with default_storage.open(_PATH, "rb") as old_file:
                    with default_storage.open(archive_path, "wb") as archive_file:
                        archive_file.write(old_file.read())
            except Exception as e:
                # Log but don't fail the upload if archiving fails
                print(f"Warning: Failed to archive old valid_species.csv: {e}")

        # Move/replace tmp -> final (some storages don't have rename; fall back to copy+delete)
        try:
            default_storage.delete(_PATH)  # ignore errors if it doesn't exist
        except Exception:
            pass
        with default_storage.open(_PATH, "wb") as out:
            out.write(data)
        try:
            default_storage.delete(tmp_key)
        except Exception:
            pass

        # Compute sha on final content (defensive, but keeps logic identical to loader)
        with default_storage.open(_PATH, "rb") as fb:
            final_sha = hashlib.sha256(fb.read()).hexdigest()

        # Publish version + label to cache
        cache.set(_VERSION_KEY, final_sha, None)
        if label:
            cache.set(_LABEL_KEY, str(label), None)
        else:
            cache.delete(_LABEL_KEY)

        # Reset our in-process map next call
        global _MAP, _VERSION, _rev_index, _rev_index_version
        _MAP = None
        _VERSION = None
        _rev_index = None
        _rev_index_version = None

        return rows, final_sha

    finally:
        cache.delete(_UPDATING_KEY)

# -----------------------------
# Centralized "Unknown" resolver
# -----------------------------

# Every taxonomy field we show in templates/exports gets "Unknown" when ID is missing.
UNKNOWN_TAXON = {
    # Core display fields used on home/detail pages
    "scientificName": "Unknown",
    "genus": "Unknown",
    "species": "Unknown",

    # Additional fields used on the detail page
    "scientificNameAuthority": "Unknown",
    "subfamily": "Unknown",
    "tribe": "Unknown",
    "subtribe": "Unknown",
    "subspecies": "Unknown",
    "authority": "Unknown",
    "authorityYear": "Unknown",
    "originalGenus": "Unknown",
}

def resolve(valid_name_id: str | None) -> dict:
    """
    Return a taxonomy dict for the given valid_name_id.
    - If the ID is None/blank, return UNKNOWN_TAXON.
    - If the ID is present but not in the reference, return UNKNOWN_TAXON.
    - Otherwise return the lookup() result (dict of fields).
    """
    vid = (valid_name_id or "").strip()
    if not vid:
        return dict(UNKNOWN_TAXON)

    try:
        row = lookup(vid)  # existing helper in this module
    except Exception:
        row = None

    if not row:
        return dict(UNKNOWN_TAXON)
    return row

def bulk_resolve(ids) -> dict[str, dict]:
    """
    Like bulk_lookup(ids) but guarantees a dict for every input key,
    falling back to UNKNOWN_TAXON when missing/blank.

    Returns: {<id string>: <taxonomy dict>}
    """
    # Normalize keys first to preserve caller's key set
    normed = []
    for x in ids or []:
        s = (str(x).strip() if x is not None else "")
        normed.append(s)

    # Use existing bulk_lookup where possible
    try:
        base = bulk_lookup(set(normed))  # existing helper; may miss blanks
    except Exception:
        base = {}

    out = {}
    for key in normed:
        row = base.get(key)
        out[key] = row if row else dict(UNKNOWN_TAXON)
    return out

def find_ids_matching_text(text: str) -> set[str]:
    """
    Return a set of valid_species_id strings where ANY of the taxonomy fields
    contains the substring 'text' (case-insensitive).
    """
    _ensure_loaded()
    if not _MAP:
        return set()
    
    query = text.strip().lower()
    if not query:
        return set()

    matches = set()
    # Searchable columns in the CSV reference
    searchable_cols = [
        "scientificName", "scientificNameAuthority",
        "subfamily", "tribe", "subtribe",
        "genus", "species", "subspecies",
        "authority", "originalGenus"
    ]

    for vid, data in _MAP.items():
        # check each column
        for col in searchable_cols:
            val = data.get(col, "")
            if val and query in val.lower():
                matches.add(vid)
                break # one match in this row is enough to include the ID

    return matches

def list_archived_versions():
    """
    List all archived versions of valid_species.csv.
    Returns list of dicts with 'filename', 'timestamp', 'path' sorted newest first.
    """
    archive_dir = "reference/archive/valid_species/"
    archived = []

    try:
        # List files in archive directory
        dirs, files = default_storage.listdir(archive_dir)

        for filename in files:
            if filename.endswith('.csv'):
                file_path = f"{archive_dir}{filename}"
                try:
                    # Get modified time
                    mtime = default_storage.get_modified_time(file_path)
                    archived.append({
                        'filename': filename,
                        'timestamp': mtime,
                        'path': file_path
                    })
                except Exception:
                    # If we can't get mtime, skip this file
                    pass

        # Sort by timestamp, newest first
        archived.sort(key=lambda x: x['timestamp'], reverse=True)

    except Exception:
        # Archive directory doesn't exist or can't be read
        pass

    return archived