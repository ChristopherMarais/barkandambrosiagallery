# Required columns for metadata table
REQUIRED_COLS = {
    "full_path_at_import",
}

# Safety cap for very large uploads
MAX_ROWS = 20000

# File types accepted inside ZIP (used by validate_uploads)
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp")

# Files kept next to an upload batch's CSV, one of each per batch: the manifest written after validation (CSV row ->
# image hash and ZIP member) and the archive record written after import. They used to be plain "manifest.json" and
# "archive.json", shared by every batch moved to the same month's folder, so the batch validated last overwrote the
# others' manifest and an import could read the wrong one (#350). A batch validated before that may still have one.
LEGACY_MANIFEST_NAME = "manifest.json"


def manifest_name(batch_id):
    return f"manifest_{batch_id}.json"


def archive_name(batch_id):
    return f"archive_{batch_id}.json"


# Optional for backward compatibility if manifest structure is changed
MANIFEST_VERSION = 1
