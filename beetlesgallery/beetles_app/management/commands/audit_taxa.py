"""
Read-only report on the species list (the Taxon table) for curators, issue #420:

    python manage.py audit_taxa                       # oddities the game's pickers work around
    python manage.py audit_taxa --csv valid_species_20261003.csv   # also: where the table and the ground truth differ

Nothing is judged by how a name is spelt or what it ends in; only by where the list itself puts it. Placeholder rows
("Ipini sp. undetermined": identified only to Ipini) are expected and not reported.
"""
from django.core.management.base import BaseCommand

from beetlesgallery.beetles_app import game_taxa

TITLES = {
    "names_at_several_ranks": "Names used at more than one rank (placeholder rows aside)",
    "genera_with_several_parents": "Genera under more than one (subfamily, tribe)",
    "tribes_in_several_subfamilies": "Tribes under more than one subfamily",
    "rows_missing_a_rank": "Rows with a lower rank but no higher one (subfamily / tribe / subtribe / genus / species)",
}
CSV_TITLES = {
    "only_in_csv": "In the ground-truth CSV but not in the database",
    "only_in_database": "In the database but not in the ground-truth CSV",
    "different": "Names that differ (database -> CSV)",
}


class Command(BaseCommand):
    help = "Report oddities in the species list, and (with --csv) differences from the ground-truth species CSV."

    def add_arguments(self, parser):
        parser.add_argument("--csv", help="The ground-truth valid_species CSV to compare the database with")

    def handle(self, *args, **options):
        self.section(game_taxa.audit(), TITLES)
        if options["csv"]:
            self.section(game_taxa.compare_with_csv(options["csv"]), CSV_TITLES)

    def section(self, report, titles):
        for key, title in titles.items():
            items = report[key]
            self.stdout.write(f"\n{title}: {len(items)}")
            for item in items[:200]:
                if isinstance(item, tuple):
                    name, detail = item
                    self.stdout.write(f"  {name}: {'; '.join(' / '.join(d) if isinstance(d, tuple) else d for d in detail)}")
                else:
                    self.stdout.write(f"  {item}")
            if len(items) > 200:
                self.stdout.write(f"  ... and {len(items) - 200} more")
