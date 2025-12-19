# Required columns for metadata table
REQUIRED_COLS = {
    "full_path_at_import",
}

# Safety cap for very large uploads
MAX_ROWS = 20000

# File types accepted inside ZIP (used by validate_uploads)
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp")

# Name of the manifest we write after validation
MANIFEST_NAME = "manifest.json"

# Optional for backward compatibility if manifest structure is changed
MANIFEST_VERSION = 1
