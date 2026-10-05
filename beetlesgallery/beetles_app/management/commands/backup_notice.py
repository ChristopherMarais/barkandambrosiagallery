"""
Switch the site notice on while a backup runs and off when it ends (.github/workflows/backup.yaml).

`backup_notice on` shows BACKUP_TEXT unless someone already has a notice up (theirs stays). `backup_notice off` only
switches off the backup's own notice, so a notice a person put up in the meantime is left alone.
"""
from django.core.cache import cache
from django.core.management.base import BaseCommand

from beetlesgallery.beetles_app.models import SiteNotice
from beetlesgallery.beetles_app.site_notice import CACHE_KEY

BACKUP_TEXT = "A backup is running: the site may be a little slow for a while."


class Command(BaseCommand):
    help = "Show (on) or remove (off) the site notice that says a backup is running."

    def add_arguments(self, parser):
        parser.add_argument("state", choices=["on", "off"])

    def handle(self, *args, state, **opts):
        notice, _ = SiteNotice.objects.get_or_create(pk=1)
        ours = notice.active and notice.text == BACKUP_TEXT
        if state == "on" and not notice.active:
            notice.text, notice.active, notice.updated_by = BACKUP_TEXT, True, None
            notice.save()
            message = "Backup notice on."
        elif state == "off" and ours:
            notice.active, notice.updated_by = False, None
            notice.save()
            message = "Backup notice off."
        else:
            message = "Left the site notice as it is."
        cache.delete(CACHE_KEY)
        self.stdout.write(message)
