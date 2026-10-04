from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from beetlesgallery.beetles_app.predictions import import_predictions


class Command(BaseCommand):
    help = (
        "Load classifier predictions (species suggestions for ROIs) from a CSV. Nothing is saved "
        "if any row is wrong. Columns: record_id, valid_species_id, confidence, model_name, "
        "model_version, top_k. See beetles_app/predictions.py."
    )

    def add_arguments(self, parser):
        parser.add_argument("file", help="Path to the CSV file")
        parser.add_argument("--model", default="", help="model_name for rows that leave it empty")
        parser.add_argument("--model-version", dest="model_version", default="", help="model_version for rows that leave it empty")
        parser.add_argument("--user", default="", help="Username to record as the uploader")
        parser.add_argument("--dry-run", action="store_true", help="Check the file and report, but save nothing")

    def handle(self, *args, **options):
        user = None
        if options["user"]:
            try:
                user = get_user_model().objects.get(username=options["user"])
            except get_user_model().DoesNotExist:
                raise CommandError(f"No user named '{options['user']}'.")
        try:
            handle = open(options["file"], "rb")
        except OSError as e:
            raise CommandError(f"Cannot open {options['file']}: {e}")
        with handle:
            result = import_predictions(
                handle, user=user, default_model=options["model"],
                default_version=options["model_version"], dry_run=options["dry_run"],
            )
        if not result.ok:
            more = result.error_count - len(result.errors)
            tail = f"\n... and {more} more" if more > 0 else ""
            raise CommandError("Nothing was saved.\n" + "\n".join(result.errors) + tail)
        verb = "Would save" if result.dry_run else "Saved"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {result.rows} predictions: {result.created} new, {result.updated} replacing an earlier upload."
            + (f" Boxes: {result.boxes_created} new, {result.boxes_matched} already on the site."
               if result.boxes_created or result.boxes_matched else "")
        ))
