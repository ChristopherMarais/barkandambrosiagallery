"""Cache-Control rules for static files (WhiteNoise) and uploaded media.

Kept free of Django imports so settings.py can use it.
"""

import re

ONE_YEAR = 365 * 24 * 60 * 60
FIVE_MINUTES = 5 * 60

# WhiteNoise's ManifestStaticFilesStorage names: "css/style.3f2a9c1b8d7e.css"
HASHED_STATIC_NAME = re.compile(r"\.[0-9a-f]{12}\.")

# Media folders whose file names are the SHA-256 of their content, so a path never changes what it holds.
# Everything else under MEDIA_ROOT (exports, uploads, game crops) keeps the shorter 30-day cache.
CONTENT_ADDRESSED_MEDIA = ("originals/", "thumbnails/", "display/")
MEDIA_IMAGE_CACHE = f"public, max-age={ONE_YEAR}, immutable"
MEDIA_OTHER_CACHE = "public, max-age=2592000, immutable"


def media_cache_control(path: str) -> str:
    """The Cache-Control value for a file served from MEDIA_ROOT, given its path under /media/."""
    if path.lstrip("/").startswith(CONTENT_ADDRESSED_MEDIA):
        return MEDIA_IMAGE_CACHE
    return MEDIA_OTHER_CACHE


def whitenoise_add_headers(headers, path, url):
    """WhiteNoise's WHITENOISE_ADD_HEADERS_FUNCTION: hashed files for a year, everything else for five minutes.

    A hashed name changes whenever the file does, so a year (and immutable) is safe. Other static files get a
    short cache so a redeploy shows up quickly.
    """
    if HASHED_STATIC_NAME.search(url):
        headers["Cache-Control"] = f"public, max-age={ONE_YEAR}, immutable"
    else:
        headers["Cache-Control"] = f"public, max-age={FIVE_MINUTES}"
