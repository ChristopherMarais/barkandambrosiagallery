import os
from django.core.management.base import BaseCommand
from beetlesgallery.beetles_app.models import Beetles
from beetlesgallery.beetles_app.image_pipeline import ensure_display_jpeg

class Command(BaseCommand):
    help = "Scans all records and generates display JPEGs for any TIFF images found."

    def handle(self, *args, **options):
        self.stdout.write("Scanning database for TIFF images...")

        # Find all records with an image
        qs = Beetles.objects.filter(image_file__isnull=False).exclude(image_file="")
        
        count_tiff = 0
        count_generated = 0
        count_skipped = 0

        for beetle in qs.iterator():
            name = beetle.image_file.name.lower()
            
            # Process only TIFFs
            if name.endswith(".tif") or name.endswith(".tiff"):
                count_tiff += 1
                
                # Check/Create the JPEG
                path = ensure_display_jpeg(beetle)
                
                # If path was returned, it means it exists now.
                # We can check if it was newly created by checking if it existed before, 
                # but ensure_display_jpeg returns the path either way.
                # For this output, we'll just say we processed it.
                if path:
                    self.stdout.write(f" [OK] {beetle.id} -> {path}")
                    count_generated += 1
                else:
                    self.stdout.write(self.style.ERROR(f" [FAIL] {beetle.id}"))
            else:
                count_skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done.\n"
            f"Found {count_tiff} TIFFs.\n"
            f"Processed/Verified {count_generated} display JPEGs.\n"
            f"Skipped {count_skipped} non-TIFF files."
        ))