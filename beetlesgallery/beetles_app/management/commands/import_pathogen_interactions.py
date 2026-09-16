import json
import os
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings
from beetlesgallery.beetles_app.models import PathogenInteraction


class Command(BaseCommand):
    help = "Imports reported bark & ambrosia beetle pathogen interactions from JSON into the standalone database table."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            help="Path to bark_beetle_pathogens_master.json (defaults to static/data/bark_beetle_pathogens_master.json)",
            default=None,
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing pathogen interactions before importing",
        )

    def handle(self, *args, **options):
        file_path = options["file"]
        if not file_path:
            file_path = settings.BASE_DIR / "beetlesgallery" / "static" / "data" / "bark_beetle_pathogens_master.json"
            if not os.path.exists(file_path):
                file_path = Path("F:/notion_data/bark_beetle_pathogens_master.json")

        file_path = Path(file_path)
        if not file_path.exists():
            self.stderr.write(self.style.ERROR(f"Data file not found at: {file_path}"))
            return

        self.stdout.write(f"Reading records from {file_path}...")
        with open(file_path, "r", encoding="utf-8") as f:
            records = json.load(f)

        if options["clear"]:
            deleted_count, _ = PathogenInteraction.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Cleared {deleted_count} existing records."))

        objects_to_create = []
        for r in records:
            obj = PathogenInteraction(
                record_block_id=r.get("Record Block ID") or None,
                record_number=str(r.get("Records ID") or ""),
                beetle_host=r.get("Beetle Host") or "Unknown Host",
                beetle_host_id=r.get("Beetle Host IDs") or None,
                pathogen=r.get("pathogens") or "Unknown Pathogen",
                category=r.get("categories") or "Unknown",
                organism_source=r.get("organism source") or None,
                infection_site=r.get("infection site") or None,
                ecological_relationship=r.get("ecological relationship") or None,
                identification_method=r.get("identification method") or None,
                validation_type=r.get("validation type") or None,
                experimental_conditions=r.get("experimental conditions") or None,
                country_or_region=r.get("country or region") or None,
                year=str(r.get("year") or ""),
                source=r.get("source") or None,
                title=r.get("title") or None,
                doi_or_full_text=r.get("doi or full text") or None,
                full_text_status=r.get("full text") or None,
            )
            objects_to_create.append(obj)

        created = PathogenInteraction.objects.bulk_create(objects_to_create, batch_size=500)
        self.stdout.write(self.style.SUCCESS(f"Successfully imported {len(created)} pathogen interaction records!"))
