import csv
import json
import io
import time
from django.core.management.base import BaseCommand
from django.core.files.storage import default_storage
from django.db import transaction, connection
from django.conf import settings
from beetlesgallery.beetles_app.models import Taxon, Synonym, CategoryMapping

class Command(BaseCommand):
    help = 'ETL Pipeline: Hydrates relational taxonomy tables from flat files and links Beetles.'

    def handle(self, *args, **options):
        start_time = time.time()
        self.stdout.write("Initiating Phase 2 ETL Pipeline...")

        with transaction.atomic():
            self._purge_existing_data()
            self._hydrate_category_mapping()
            self._hydrate_taxon_table()
            self._hydrate_synonym_table()
            self._link_beetles_to_taxon()
            
        elapsed = time.time() - start_time
        self.stdout.write(self.style.SUCCESS(f"ETL Pipeline completed successfully in {elapsed:.2f} seconds."))

    def _purge_existing_data(self):
        self.stdout.write("1. Purging existing relational taxonomy data to ensure idempotency...")
        CategoryMapping.objects.all().delete()
        Synonym.objects.all().delete()
        Taxon.objects.all().delete()

    def _hydrate_category_mapping(self):
        self.stdout.write("2. Hydrating CategoryMapping...")
        mapping_path = "reference/category_mapping.json"
        if not default_storage.exists(mapping_path):
            self.stdout.write(self.style.WARNING(f"   -> {mapping_path} not found. Skipping."))
            return

        with default_storage.open(mapping_path, 'r') as f:
            data = json.load(f)
            categories = data.get('categories', [])
            
            objs = [
                CategoryMapping(
                    category_id=int(cat['id']),
                    name=cat.get('name', f"class_{cat['id']}"),
                    full_name=cat.get('full_name', ''),
                    supercategory=cat.get('type', 'beetle')
                ) for cat in categories
            ]
            CategoryMapping.objects.bulk_create(objs)
            self.stdout.write(f"   -> Inserted {len(objs)} category mappings.")

    def _hydrate_taxon_table(self):
        self.stdout.write("3. Hydrating Taxon (valid_species.csv) via raw bulk_create...")
        path = getattr(settings, "VALID_SPECIES_PATH", "reference/valid_species.csv")
        
        if not default_storage.exists(path):
            self.stdout.write(self.style.ERROR(f"   -> {path} not found. Critical failure."))
            return

        # Treebeard Materialized Path base62 encoding algorithm
        alphabet = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
        def get_path(num, steplen=4):
            res = []
            base = len(alphabet)
            while num > 0:
                num, rem = divmod(num, base)
                res.append(alphabet[rem])
            return ''.join(reversed(res)).rjust(steplen, alphabet[0])

        taxa_to_create = []
        path_counter = 1
        
        with default_storage.open(path, "rb") as fb:
            with io.TextIOWrapper(fb, encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                reader.fieldnames = [h.strip() for h in (reader.fieldnames or [])]
                
                for row in reader:
                    vid = (row.get("valid_species_id") or "").strip()
                    if not vid:
                        continue

                    taxa_to_create.append(Taxon(
                        path=get_path(path_counter),
                        depth=1,
                        numchild=0,
                        valid_species_id=vid,
                        scientific_name=row.get("scientificName", "").strip(),
                        scientific_name_authority=row.get("scientificNameAuthority", "").strip(),
                        subfamily=row.get("subfamily", "").strip(),
                        tribe=row.get("tribe", "").strip(),
                        subtribe=row.get("subtribe", "").strip(),
                        genus=row.get("genus", "").strip(),
                        species=row.get("species", "").strip(),
                        subspecies=row.get("subspecies", "").strip(),
                        authority=row.get("authority", "").strip(),
                        authority_year=row.get("authorityYear", "").strip(),
                        original_genus=row.get("originalGenus", "").strip()
                    ))
                    path_counter += 1

        # Execute a single aggregated SQL INSERT statement
        Taxon.objects.bulk_create(taxa_to_create, batch_size=5000)
        self.stdout.write(f"   -> Bulk inserted {len(taxa_to_create)} Taxon records instantly.")

    def _hydrate_synonym_table(self):
        self.stdout.write("4. Hydrating Synonym (described_names.csv)...")
        path = getattr(settings, "DESCRIBED_NAMES_PATH", "reference/described_names.csv")
        
        if not default_storage.exists(path):
            self.stdout.write(self.style.WARNING(f"   -> {path} not found. Skipping."))
            return

        # Pre-fetch taxon map to memory for fast foreign key linking
        taxon_map = {t.valid_species_id: t for t in Taxon.objects.all()}
        synonyms_to_create = []

        with default_storage.open(path, "rb") as fb:
            text = None
            data = fb.read()
            for encoding in ["utf-8-sig", "utf-8", "latin-1", "cp1252"]:
                try:
                    text = data.decode(encoding)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue

            reader = csv.DictReader(io.StringIO(text))
            reader.fieldnames = [h.strip() for h in (reader.fieldnames or [])]
            
            for row in reader:
                name_id = (row.get("name_id") or "").strip()
                valid_id = (row.get("name_valid_species_id") or "").strip()
                
                if not name_id or not valid_id:
                    continue
                
                taxon = taxon_map.get(valid_id)
                if not taxon:
                    continue # Synonym maps to a non-existent Taxon; drop it to maintain integrity.

                synonyms_to_create.append(
                    Synonym(
                        taxon=taxon,
                        name_id=name_id,
                        described_scientific_name=row.get("describedScientificName", "").strip(),
                        described_scientific_name_authority=row.get("describedScientificNameAuthority", "").strip(),
                        genus=row.get("name_genus", "").strip(),
                        species=row.get("name_species", "").strip(),
                        subspecies=row.get("name_subspecies", "").strip(),
                        authority=row.get("name_authority", "").strip(),
                        year=row.get("name_year", "").strip()
                    )
                )

        Synonym.objects.bulk_create(synonyms_to_create, batch_size=2000)
        self.stdout.write(f"   -> Inserted {len(synonyms_to_create)} Synonyms.")

    def _link_beetles_to_taxon(self):
        self.stdout.write("5. Linking Beetles to Taxon via SQL...")
        with connection.cursor() as cursor:
            cursor.execute("""
                UPDATE beetles 
                SET taxon_id = (
                    SELECT t.id FROM taxon t 
                    WHERE t.valid_species_id = beetles.depicts_valid_name_id
                )
                WHERE depicts_valid_name_id IS NOT NULL 
                  AND depicts_valid_name_id != '';
            """)
            updated_count = cursor.rowcount
            self.stdout.write(f"   -> Updated {updated_count} Beetle records.")
            
        # Audit orphaned records
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) FROM beetles 
                WHERE taxon_id IS NULL 
                  AND depicts_valid_name_id IS NOT NULL 
                  AND depicts_valid_name_id != '';
            """)
            orphaned = cursor.fetchone()[0]
            if orphaned > 0:
                self.stdout.write(self.style.WARNING(f"   -> WARNING: {orphaned} Beetle records have a 'depicts_valid_name_id' that does not exist in 'valid_species.csv'."))