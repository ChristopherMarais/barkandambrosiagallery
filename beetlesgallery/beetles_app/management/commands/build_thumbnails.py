from django.core.management.base import BaseCommand
from django.core.files.storage import default_storage
from beetlesgallery.beetles_app.models import Beetles
from beetlesgallery.beetles_app.image_pipeline import ensure_thumbnail

class Command(BaseCommand):
    help = "Generate missing thumbnails for Beetles.images (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0, help="Optional limit on how many to process.")
        parser.add_argument("--size", type=int, default=96, help="Thumb size in px.")

    def handle(self, *args, **opts):
        limit = opts["limit"]
        size = opts["size"]

        qs = (Beetles.objects
              .only("id", "image_file", "image_sha256", "thumb_small")
              .order_by("id"))

        ok = 0
        miss = 0
        err = 0

        for b in qs.iterator(chunk_size=500):
            try:
                rel = ensure_thumbnail(b, size=size)
                if rel:
                    ok += 1
                else:
                    # rel == "" means no original or no image on this row
                    miss += 1
            except Exception as e:
                err += 1
                self.stderr.write(f"[WARN] {b.id}: {e}")

        self.stdout.write(self.style.SUCCESS(
            f"Done. thumbnails ok={ok}, missing-original={miss}, errors={err}"
        ))