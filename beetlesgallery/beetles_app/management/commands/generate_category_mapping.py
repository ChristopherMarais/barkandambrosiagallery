from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from pathlib import Path
import json

from beetlesgallery.beetles_app import species_ref


class Command(BaseCommand):
    help = "Generate category mapping JSON for bounding box annotations from species reference data."

    def handle(self, *args, **options):
        self.stdout.write("Loading species reference data...")

        # Load all species data
        all_rows = species_ref._load_all_rows()

        # Create category mapping
        categories = []

        # ID 0: Unknown/Generic Beetle
        categories.append({
            "id": 0,
            "name": "Beetle (Unknown)",
            "type": "generic",
            "genus": None,
            "species": None,
            "full_name": "Beetle (Unknown)",
            "valid_species_id": None
        })

        # Collect unique genera and species with their IDs
        genera_set = set()
        species_by_id = {}  # valid_species_id -> full species data

        for row in all_rows:
            genus = (row.get('genus') or '').strip()
            species_epithet = (row.get('species') or '').strip()
            valid_species_id = (row.get('valid_species_id') or '').strip()

            if genus:
                genera_set.add(genus)

            if genus and species_epithet and valid_species_id:
                try:
                    species_id = int(valid_species_id)
                    species_by_id[species_id] = {
                        'genus': genus,
                        'species': species_epithet,
                        'scientific_name': row.get('scientificName', '').strip(),
                        'authority': row.get('scientificNameAuthority', '').strip()
                    }
                except ValueError:
                    self.stderr.write(f"Warning: Invalid species ID '{valid_species_id}' for {genus} {species_epithet}")

        # Sort genera for consistent ordering
        genera_list = sorted(genera_set)

        # Add genus-level categories (negative IDs to avoid collision with valid_species_id)
        self.stdout.write(f"Adding {len(genera_list)} genera with negative IDs (-1 to -{len(genera_list)})...")
        genus_id = -1
        for genus in genera_list:
            categories.append({
                "id": genus_id,
                "name": f"Genus: {genus}",
                "type": "genus",
                "genus": genus,
                "species": None,
                "full_name": genus,
                "valid_species_id": None
            })
            genus_id -= 1

        # Add species-level categories using their actual valid_species_id
        self.stdout.write(f"Adding {len(species_by_id)} species with their valid_species_id...")
        for species_id in sorted(species_by_id.keys()):
            data = species_by_id[species_id]
            categories.append({
                "id": species_id,
                "name": f"{data['genus']} {data['species']}",
                "type": "species",
                "genus": data['genus'],
                "species": data['species'],
                "full_name": data['scientific_name'],
                "valid_species_id": species_id,
                "authority": data['authority']
            })

        self.stdout.write(f"ID ranges: Generic=0, Genera=-1 to {genus_id+1}, Species={min(species_by_id.keys())}-{max(species_by_id.keys())}")

        # Create output structure
        output = {
            "version": "1.0",
            "generated_at": timezone.now().isoformat(),
            "total_categories": len(categories),
            "categories": categories
        }

        # Save to media/reference/category_mapping.json
        media_root = settings.MEDIA_ROOT
        reference_dir = Path(media_root) / 'reference'
        reference_dir.mkdir(parents=True, exist_ok=True)

        output_path = reference_dir / 'category_mapping.json'

        self.stdout.write(f"Writing to {output_path}...")
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        self.stdout.write(self.style.SUCCESS(
            f"\n✓ Successfully generated category mapping!\n"
            f"  - Total categories: {len(categories)}\n"
            f"  - Generic beetles: 1 (ID: 0)\n"
            f"  - Genera: {len(genera_list)} (IDs: -1 to -{len(genera_list)})\n"
            f"  - Species: {len(species_by_id)} (IDs: {min(species_by_id.keys())}-{max(species_by_id.keys())} from valid_species_id)\n"
            f"  - Output: {output_path}"
        ))
