from django.core.management.base import BaseCommand, CommandError

from beetlesgallery.beetles_app import taxonomy_tree


class Command(BaseCommand):
    help = "Regenerate reference/taxonomy_tree.json from the current valid_species.csv."

    def handle(self, *args, **opts):
        try:
            species_count = taxonomy_tree.rebuild()
        except Exception as e:
            raise CommandError(f"Build failed: {e}")

        self.stdout.write(
            self.style.SUCCESS(
                f"taxonomy_tree.json rebuilt successfully ({species_count} species)."
            )
        )
