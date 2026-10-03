"""
Find validated beetles that most reliable players name differently, the same way (likely mislabelled), take them
out of the game and list them for curators (see game_label_check). Also run by recompute_game_scores.
"""
from django.core.management.base import BaseCommand

from beetlesgallery.beetles_app import game_label_check


class Command(BaseCommand):
    help = "Flag validated beetles that players dispute (likely mislabelled) for curators."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="only list them; flag nothing")

    def handle(self, *args, **options):
        found = game_label_check.check(dry_run=options["dry_run"])
        for s in found:
            self.stdout.write(f"{s['roi'].id}  {game_label_check.note(s)}")
        verb = "Would flag" if options["dry_run"] else "Flagged"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(found)} beetle{'s' if len(found) != 1 else ''}."))
