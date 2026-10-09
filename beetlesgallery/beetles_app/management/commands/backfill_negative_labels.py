"""
Write the negative labels (what an answer says a beetle is *not*, game_negatives) of game answers given before they
were written down with each answer. Run once after the deploy that added them:

    manage.py backfill_negative_labels             every Find Them All, Odd One Out and Similarity answer
    manage.py backfill_negative_labels --dry-run   only count them

Safe to run again: an answer's labels are written once (a unique row per answer, beetle and rank).
"""
from django.core.management.base import BaseCommand

from beetlesgallery.beetles_app import game_negatives
from beetlesgallery.beetles_app.models import GameAnswer, NegativeLabel

BATCH = 2000


class Command(BaseCommand):
    help = "Write the negative labels of game answers given before they were recorded."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Count what would be written, write nothing.")

    def handle(self, *args, **options):
        answers = (GameAnswer.objects.filter(mode__in=["odd", "select", "pair"], skipped=False)
                   .select_related("roi_b__taxon").order_by("answered_at"))
        before = NegativeLabel.objects.count()
        seen = said = 0
        rows = []
        for ans in answers.iterator(chunk_size=BATCH):
            seen += 1
            for roi_id, rank, value in game_negatives.derive(ans):
                said += 1
                rows.append(NegativeLabel(answer_id=ans.pk, player_id=ans.player_id, roi_id=roi_id, mode=ans.mode,
                                          rank=rank, value=value[:201]))
            if len(rows) >= BATCH and not options["dry_run"]:
                NegativeLabel.objects.bulk_create(rows, ignore_conflicts=True)
                rows = []
        if rows and not options["dry_run"]:
            NegativeLabel.objects.bulk_create(rows, ignore_conflicts=True)
        added = NegativeLabel.objects.count() - before
        verb = "would say" if options["dry_run"] else "say"
        self.stdout.write(f"{seen:,} answers {verb} {said:,} negative labels; {added:,} new rows written.")
