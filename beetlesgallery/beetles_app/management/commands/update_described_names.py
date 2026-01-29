from __future__ import annotations
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from beetlesgallery.beetles_app import described_names_ref


class Command(BaseCommand):
    help = "Publish/replace the described_names.csv reference and update caches."

    def add_arguments(self, parser):
        parser.add_argument(
            "--src",
            required=True,
            help="Path to the described_names.csv to publish",
        )
        parser.add_argument(
            "--label",
            help="Optional human-friendly label (e.g., '2025-10-14 16:22 UTC'). "
                 "If omitted, current UTC timestamp is used.",
        )

    def handle(self, *args, **opts):
        src = Path(opts["src"]).expanduser().resolve()
        if not src.exists():
            raise CommandError(f"File not found: {src}")

        label = opts.get("label") or timezone.now().strftime("%Y-%m-%d %H:%M UTC")

        try:
            rows, version = described_names_ref.publish_from_file(str(src), label=label)
        except Exception as e:
            raise CommandError(f"Publish failed: {e}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Published described_names.csv ({rows} rows), "
                f"label='{label}', version={version[:12]}…"
            )
        )
