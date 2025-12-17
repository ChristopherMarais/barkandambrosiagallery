from __future__ import annotations
import io, os, hashlib, zipfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.files.base import File
from django.core.files.storage import default_storage
from django.db.models import Q

from beetles_app.models import Beetles
from beetles_app.image_pipeline import write_original_and_thumb96

def sha256_stream(fp, chunk=1024*1024):
    h = hashlib.sha256()
    for chunk_bytes in iter(lambda: fp.read(chunk), b""):
        h.update(chunk_bytes)
    return h.hexdigest()

class Command(BaseCommand):
    help = "Rehydrate missing originals and thumbnails from a ZIP or directory by matching SHA-256."

    def add_arguments(self, parser):
        g = parser.add_mutually_exclusive_group(required=True)
        g.add_argument("--zip", dest="zip_path", help="Path to a ZIP containing images.")
        g.add_argument("--dir", dest="dir_path", help="Path to a directory tree containing images.")
        parser.add_argument("--limit", type=int, default=100000, help="Max files to scan from source.")
        parser.add_argument("--dry-run", action="store_true", help="List what would be restored but do not write.")
        parser.add_argument("--only-missing", action="store_true", help="Restore only when original is missing (default).")
        parser.add_argument("--force-thumbs", action="store_true", help="Rebuild thumb even if original exists.")

    def handle(self, *args, **opts):
        zip_path = opts.get("zip_path")
        dir_path = opts.get("dir_path")
        dry = opts.get("dry_run", False)
        limit = int(opts.get("limit", 100000))
        only_missing = bool(opts.get("only_missing", True))
        force_thumbs = bool(opts.get("force_thumbs", False))

        # Build a quick lookup of shas that need restoring
        needs = {}
        qs = Beetles.objects.only("id","image_file","image_sha256")
        count_missing = 0
        for b in qs.iterator(chunk_size=1000):
            sha = (b.image_sha256 or "").strip()
            if not sha:
                continue
            needs.setdefault(sha, {"row": b, "missing": False})
            # Check if original exists in storage
            exists = False
            try:
                if b.image_file and b.image_file.name:
                    exists = default_storage.exists(b.image_file.name)
            except Exception:
                exists = False
            if not exists:
                needs[sha]["missing"] = True
                count_missing += 1

        if only_missing:
            target_shas = {sha for sha, info in needs.items() if info["missing"]}
        else:
            target_shas = set(needs.keys())

        if not target_shas:
            self.stdout.write(self.style.SUCCESS("Nothing to restore."))
            return

        self.stdout.write(f"Target rows: {len(target_shas)} (missing={count_missing}, only_missing={only_missing})")

        restored = 0
        scanned = 0

        def handle_candidate(name, open_func):
            nonlocal restored, scanned
            if scanned >= limit:
                return
            scanned += 1
            try:
                with open_func() as fp:
                    # compute sha
                    sha = sha256_stream(fp)
                    if sha in target_shas:
                        # re-open fresh stream for writing
                        fp.seek(0)
                        if dry:
                            self.stdout.write(f"DRY: would restore sha={sha} from {name}")
                            return
                        # write original + thumb
                        write_original_and_thumb96(sha, fp)
                        restored += 1
                        self.stdout.write(self.style.SUCCESS(f"Restored sha={sha} from {name}"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Skip {name}: {e}"))

        if zip_path:
            zp = Path(zip_path)
            if not zp.exists():
                raise CommandError(f"ZIP not found: {zip_path}")
            with zipfile.ZipFile(zp, "r") as zf:
                for zi in zf.infolist():
                    if zi.is_dir():
                        continue
                    def opener(zi=zi):
                        return zf.open(zi, "r")
                    handle_candidate(zi.filename, opener)
                    if scanned >= limit:
                        break
        else:
            dp = Path(dir_path)
            if not dp.exists():
                raise CommandError(f"Directory not found: {dir_path}")
            for root, _, files in os.walk(dp):
                for fn in files:
                    full = Path(root) / fn
                    def opener(full=full):
                        return open(full, "rb")
                    handle_candidate(str(full), opener)
                    if scanned >= limit:
                        break

        self.stdout.write(self.style.SUCCESS(f"Done. scanned={scanned}, restored={restored}"))
