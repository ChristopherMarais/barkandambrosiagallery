"""
Throw-away players for the load test (loadtest/README.md, issue #383).

    LOADTEST_PASSWORD=... python manage.py loadtest_players --create 50
    python manage.py loadtest_players --delete

--create makes loadtest-01 ... loadtest-NN as ordinary, active members, all with the password in LOADTEST_PASSWORD
(never printed). --delete removes every loadtest-* account with everything they did in the game, then re-scores the
real players so nothing the test players answered is left in anyone's consensus or points.
"""
import os

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

PREFIX = "loadtest-"


def loadtest_users():
    return get_user_model().objects.filter(username__startswith=PREFIX)


class Command(BaseCommand):
    help = "Create or delete the throw-away loadtest-NN players used by the load test."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--create", type=int, metavar="N", help="Create loadtest-01 ... loadtest-N")
        group.add_argument("--delete", action="store_true", help="Delete every loadtest-* player and re-score the rest")

    def handle(self, *args, **options):
        if options["create"] is not None:
            self.create(options["create"])
        else:
            self.delete()

    def create(self, n):
        password = os.environ.get("LOADTEST_PASSWORD", "")
        if len(password) < 12:
            raise CommandError("Set LOADTEST_PASSWORD (12+ characters) in the environment first.")
        if not 1 <= n <= 500:
            raise CommandError("Choose between 1 and 500 players.")
        User = get_user_model()
        made = 0
        for i in range(1, n + 1):
            user, created = User.objects.get_or_create(username=f"{PREFIX}{i:02d}",
                                                       defaults={"email": f"{PREFIX}{i:02d}@example.invalid"})
            user.is_active, user.is_staff, user.is_superuser = True, False, False
            user.set_password(password)
            user.save()
            made += created
        self.stdout.write(f"{n} load-test players ready ({made} new).")

    def delete(self):
        users = loadtest_users()
        n = users.count()
        with transaction.atomic():
            users.delete()
        self.stdout.write(f"Deleted {n} load-test players and their game data.")
        if n:
            self.stdout.write("Re-scoring the real players ...")
            call_command("recompute_game_scores", stdout=self.stdout, stderr=self.stderr)
        self.stdout.write("Done.")
