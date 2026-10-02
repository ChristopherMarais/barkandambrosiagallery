"""
Read-only report on the species list: names filed at the wrong rank, genera under more
than one tribe, tribes under more than one subfamily. The game's pickers work around
these (see game_taxa.py), but they should be fixed in the source list.
"""
from django.core.management.base import BaseCommand

from beetlesgallery.beetles_app import game_taxa

TITLES = {
    "names_at_several_ranks": "Names used at more than one rank",
    "bad_subfamilies": "Subfamily values not ending in -inae",
    "bad_tribes": "Tribe values not ending in -ini",
    "bad_genera": "Genus values that do not look like a genus",
    "genera_with_several_parents": "Genera under more than one (subfamily, tribe)",
    "tribes_in_several_subfamilies": "Tribes under more than one subfamily",
}


class Command(BaseCommand):
    help = "Report taxonomy rows that would put names at the wrong rank in the game's pickers."

    def handle(self, *args, **options):
        report = game_taxa.audit()
        for key, title in TITLES.items():
            items = report[key]
            self.stdout.write(f"\n{title}: {len(items)}")
            for item in items:
                if isinstance(item, tuple):
                    name, detail = item
                    self.stdout.write(f"  {name}: {', '.join(' / '.join(d) if isinstance(d, tuple) else d for d in detail)}")
                else:
                    self.stdout.write(f"  {item}")
