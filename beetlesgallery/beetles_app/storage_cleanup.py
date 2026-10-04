"""
Free disk space held by files the site no longer needs (run nightly by `manage.py cleanup_storage`).

Only ever deletes inside downloads/, uploads/ and tmp_uploads/ under MEDIA_ROOT, never the image set itself
(originals/, display/, thumbnails/) or the species tables (reference/):

* Downloads: a finished download's CSV/ZIP once it expires (DOWNLOAD_RETENTION_DAYS after it was built), leftovers
  of builds that crashed, and result files no download points to any more. Any download can be built again.
* Uploads: an imported upload's ZIP once every image in it is in the gallery (checked by content hash, and the
  gallery's copy must be on disk), UPLOAD_ZIP_KEEP_DAYS after the import. The CSV, manifest and error logs are kept:
  they are small and record what came in. ZIPs of uploads that were rejected, failed or never finished go after
  FAILED_UPLOAD_KEEP_DAYS; whoever uploaded them has had the error report since.
* Temporary files of interrupted uploads (tmp_uploads/) after TEMP_FILE_KEEP_HOURS.

Every function takes dry_run and returns a Freed tally, so `--dry-run` shows exactly what a real run would remove.
"""
import hashlib
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from django.utils import timezone

from .models import DownloadJob, ImageAsset, UploadBatch
from .schema import IMAGE_EXTENSIONS

# The only folders (relative to MEDIA_ROOT) anything is ever deleted from
DELETABLE = ("downloads/", "uploads/", "tmp_uploads/")


@dataclass
class Freed:
    files: int = 0
    bytes: int = 0
    kept: list = field(default_factory=list)   # (what, why) for things deliberately left alone

    def add(self, other):
        self.files += other.files
        self.bytes += other.bytes
        self.kept += other.kept


def media_root():
    return Path(settings.MEDIA_ROOT)


def _deletable(rel):
    rel = str(rel).replace("\\", "/")
    return rel.startswith(DELETABLE) and ".." not in rel.split("/")


def _size(path):
    try:
        if path.is_dir():
            return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
        return path.stat().st_size
    except OSError:
        return 0


def delete_path(rel, freed, dry_run):
    """Delete one file or folder under MEDIA_ROOT, but only inside DELETABLE. Missing files count as nothing."""
    if not _deletable(rel):
        raise ValueError(f"refusing to delete outside {DELETABLE}: {rel}")
    path = media_root() / rel
    if not path.exists():
        return
    freed.files += sum(1 for p in path.rglob("*") if p.is_file()) if path.is_dir() else 1
    freed.bytes += _size(path)
    if dry_run:
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _setting(name, default):
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


# --- Downloads ---------------------------------------------------------------------------------------------------

def expire_downloads(dry_run=False, now=None):
    """Remove the files of finished downloads past their expiry, and mark them expired."""
    now = now or timezone.now()
    freed = Freed()
    jobs = DownloadJob.objects.filter(
        status__in=[DownloadJob.Status.READY, DownloadJob.Status.FAILED], expires_at__isnull=False, expires_at__lt=now)
    # Failed builds never get an expiry: their partial files go after the same retention, from when they stopped
    failed_cutoff = now - timedelta(days=_setting("DOWNLOAD_RETENTION_DAYS", 14))
    failed = DownloadJob.objects.filter(status=DownloadJob.Status.FAILED, expires_at__isnull=True,
                                        finished_at__lt=failed_cutoff)
    for job in list(jobs) + list(failed):
        had_files = bool(job.csv_file or job.zip_file)
        for f in (job.csv_file, job.zip_file):
            if f:
                delete_path(f.name, freed, dry_run)
        if dry_run or (not had_files and job.status != DownloadJob.Status.READY):
            continue
        job.csv_file = ""
        job.zip_file = ""
        if job.status == DownloadJob.Status.READY:
            job.status = DownloadJob.Status.EXPIRED
            job.error_message = job.error_message or "Files expired and removed after retention period."
        job.finished_at = job.finished_at or now
        job.save(update_fields=["csv_file", "zip_file", "status", "error_message", "finished_at"])
    return freed


def sweep_download_leftovers(dry_run=False, now=None):
    """Folders left in downloads/tmp by builds that crashed, and result files no download points to."""
    freed = Freed()
    cutoff = time.time() - _setting("TEMP_FILE_KEEP_HOURS", 24) * 3600
    tmp = media_root() / "downloads" / "tmp"
    building = {str(i) for i in DownloadJob.objects.filter(
        status__in=[DownloadJob.Status.PENDING, DownloadJob.Status.BUILDING]).values_list("id", flat=True)}
    if tmp.is_dir():
        for child in tmp.iterdir():
            if child.name not in building and child.stat().st_mtime < cutoff:
                delete_path(f"downloads/tmp/{child.name}", freed, dry_run)

    results = media_root() / "downloads" / "results"
    if results.is_dir():
        known = set()
        for csv_name, zip_name in DownloadJob.objects.values_list("csv_file", "zip_file"):
            known.update(n for n in (csv_name, zip_name) if n)
        result_cutoff = time.time() - _setting("DOWNLOAD_RETENTION_DAYS", 14) * 86400
        for path in results.rglob("*"):
            rel = path.relative_to(media_root()).as_posix()
            if path.is_file() and rel not in known and path.stat().st_mtime < result_cutoff:
                delete_path(rel, freed, dry_run)
    return freed


# --- Uploads -----------------------------------------------------------------------------------------------------

def _image_members(zf):
    for info in zf.infolist():
        name = info.filename
        base = name.replace("\\", "/").rsplit("/", 1)[-1]
        if info.is_dir() or name.startswith("__MACOSX/") or base.startswith("._") or not base:
            continue
        if base.lower().endswith(IMAGE_EXTENSIONS):
            yield name


def _hash_member(zf, name):
    h = hashlib.sha256()
    with zf.open(name, "r") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def missing_from_gallery(zip_path):
    """Images in the ZIP that are not in the gallery with their file on disk (by content hash). [] when all are."""
    with zipfile.ZipFile(zip_path) as zf:
        hashes = {name: _hash_member(zf, name) for name in _image_members(zf)}
    in_gallery = dict(ImageAsset.objects.filter(image_sha256__in=set(hashes.values()))
                      .values_list("image_sha256", "image_file"))
    return [name for name, sha in hashes.items()
            if not in_gallery.get(sha) or not default_storage.exists(in_gallery[sha])]


def clear_imported_upload_zips(dry_run=False, now=None):
    """The ZIP of an imported upload, once every image in it is safely in the gallery."""
    now = now or timezone.now()
    freed = Freed()
    cutoff = now - timedelta(days=_setting("UPLOAD_ZIP_KEEP_DAYS", 7))
    batches = UploadBatch.objects.filter(status=UploadBatch.Status.IMPORTED, imported_at__lt=cutoff).exclude(zip_file="")
    for batch in batches:
        path = media_root() / batch.zip_file.name
        if path.exists():
            try:
                missing = missing_from_gallery(path)
            except (zipfile.BadZipFile, OSError) as e:
                freed.kept.append((batch.zip_file.name, f"could not be read ({e})"))
                continue
            if missing:
                freed.kept.append((batch.zip_file.name, f"{len(missing)} image(s) not in the gallery, e.g. {missing[0]}"))
                continue
            delete_path(batch.zip_file.name, freed, dry_run)
        if not dry_run:
            UploadBatch.objects.filter(pk=batch.pk).update(zip_file="")
    return freed


def clear_unfinished_upload_zips(dry_run=False, now=None):
    """ZIPs of uploads that were rejected, failed to import or never finished, after FAILED_UPLOAD_KEEP_DAYS."""
    now = now or timezone.now()
    freed = Freed()
    cutoff = now - timedelta(days=_setting("FAILED_UPLOAD_KEEP_DAYS", 30))
    batches = (UploadBatch.objects.exclude(status=UploadBatch.Status.IMPORTED).exclude(zip_file="")
               .filter(updated_at__lt=cutoff))
    for batch in batches:
        delete_path(batch.zip_file.name, freed, dry_run)
        if not dry_run:
            UploadBatch.objects.filter(pk=batch.pk).update(zip_file="")
    return freed


def sweep_upload_temp_files(dry_run=False):
    """Files Django spooled to tmp_uploads/ for uploads that were interrupted."""
    freed = Freed()
    folder = media_root() / "tmp_uploads"   # settings.FILE_UPLOAD_TEMP_DIR
    cutoff = time.time() - _setting("TEMP_FILE_KEEP_HOURS", 24) * 3600
    if folder.is_dir():
        for path in folder.iterdir():
            if path.is_file() and path.stat().st_mtime < cutoff:
                delete_path(f"tmp_uploads/{path.name}", freed, dry_run)
    return freed


STEPS = [
    ("Expired downloads", expire_downloads),
    ("Download leftovers", sweep_download_leftovers),
    ("Imported upload ZIPs (images already in the gallery)", clear_imported_upload_zips),
    ("Rejected or unfinished upload ZIPs", clear_unfinished_upload_zips),
    ("Interrupted upload temp files", sweep_upload_temp_files),
]
