from django.core.management.base import BaseCommand
from beetlesgallery.beetles_app.models import Beetles, ImageAsset
from django.db import transaction
import sys

class Command(BaseCommand):
    help = "Migrates image data from Beetles table to ImageAsset table based on SHA256 deduplication."

    def handle(self, *args, **options):
        # Fetch all beetles that don't have an image_asset linked yet
        qs = Beetles.objects.filter(image_asset__isnull=True)
        total = qs.count()
        
        self.stdout.write(f"Found {total} beetle records to migrate.")
        
        processed = 0
        created_images = 0
        
        # Process in chunks to handle memory efficiently
        chunk_size = 1000
        
        # We loop until no unlinked beetles remain
        while True:
            # Re-fetch the slice to avoid stale data/memory issues
            batch = list(Beetles.objects.filter(image_asset__isnull=True)[:chunk_size])
            if not batch:
                break

            with transaction.atomic():
                for beetle in batch:
                    sha = beetle.image_sha256
                    
                    # If no SHA, we can't reliably dedupe, but we must migrate.
                    # We'll use the UUID as a fallback 'hash' just to ensure uniqueness if SHA is missing.
                    if not sha:
                        self.stdout.write(self.style.WARNING(f"Beetle {beetle.id} has no SHA256. Using ID as fallback."))
                        sha = f"MISSING_SHA_{beetle.id}"

                    # 1. Get or Create the ImageAsset
                    # We use get_or_create so the first beetle with this SHA defines the image data,
                    # and subsequent beetles just link to it.
                    image_asset, created = ImageAsset.objects.get_or_create(
                        image_sha256=sha,
                        defaults={
                            'full_path_at_import': beetle.full_path_at_import,
                            'image_institution': beetle.image_institution,
                            'photographer': beetle.photographer,
                            'image_email': beetle.image_email,
                            'photo_usage_statement': beetle.photo_usage_statement,
                            'image_date_taken': beetle.image_date_taken,
                            'image_notes': beetle.image_notes,
                            'image_has_multiple_individuals': beetle.image_has_multiple_individuals,
                            'aspect': beetle.aspect,
                            'resolution_in_ppmm': beetle.resolution_in_ppmm,
                            'image_size_bytes': beetle.image_size_bytes,
                            # For FileFields, we can simply assign the existing FieldFile
                            'image_file': beetle.image_file,
                            'thumb_small': beetle.thumb_small,
                            'image_width': beetle.image_width,
                            'image_height': beetle.image_height,
                            'thumb_width': beetle.thumb_width,
                            'thumb_height': beetle.thumb_height,
                        }
                    )

                    if created:
                        created_images += 1

                    # 2. Link the Beetle to this ImageAsset
                    beetle.image_asset = image_asset
                    beetle.save(update_fields=['image_asset'])
                    
                    processed += 1
                    if processed % 100 == 0:
                        sys.stdout.write(f"\rProcessed: {processed}/{total} | New Images: {created_images}")
                        sys.stdout.flush()

        self.stdout.write("\n")
        self.stdout.write(self.style.SUCCESS(f"Migration Complete. Processed {processed} specimens. Created {created_images} unique ImageAssets."))