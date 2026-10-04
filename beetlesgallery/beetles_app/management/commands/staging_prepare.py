"""
Get the staging site's database ready after a copy of production is loaded into it, and after every staging deploy
(docs/staging.md). Refuses to run anywhere but the staging site.

* Every email address becomes staging-<id>@staging.invalid, so nothing can reach a real person from staging.
* Nobody can sign in with their own password, sessions and API tokens are dropped.
* The shared staging account (STAGING_ACCOUNT / STAGING_PASSWORD) exists, is a superuser and is the only way in.

Safe to run again: it only ever changes the staging database.
"""
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.db import models
from django.db.models.functions import Cast, Concat

SCRUBBED_DOMAIN = "staging.invalid"


def scrub_emails():
    """Replace every non-empty email address in the database. Returns the number of rows changed."""
    changed = 0
    for model in apps.get_models(include_auto_created=True):
        if model._meta.proxy or not model._meta.managed:
            continue
        pk = model._meta.pk.attname
        for field in model._meta.concrete_fields:
            if not isinstance(field, models.EmailField):
                continue
            rows = model._default_manager.exclude(**{f"{field.attname}__isnull": True}).exclude(**{field.attname: ""})
            rows = rows.exclude(**{f"{field.attname}__endswith": "@" + SCRUBBED_DOMAIN})
            changed += rows.update(**{field.attname: Concat(
                models.Value("staging-"), Cast(pk, models.CharField()), models.Value("@" + SCRUBBED_DOMAIN),
                output_field=models.CharField())})
    return changed


class Command(BaseCommand):
    help = "Staging only: scrub the copied production data and set up the shared staging account."

    def handle(self, *args, **options):
        if not getattr(settings, "STAGING", False):
            raise CommandError("This is not the staging site (STAGING=1 is not set): nothing was changed.")

        emails = scrub_emails()
        User = get_user_model()
        name = settings.STAGING_ACCOUNT
        others = User.objects.exclude(username=name).exclude(password__startswith="!")
        locked = others.count()
        for user in others.iterator():
            user.set_unusable_password()
            user.save(update_fields=["password"])
        Session.objects.all().delete()
        if apps.is_installed("rest_framework.authtoken"):
            from rest_framework.authtoken.models import Token
            Token.objects.all().delete()

        account, created = User.objects.get_or_create(username=name)
        account.is_active = account.is_staff = account.is_superuser = True
        account.email = f"{name}@{SCRUBBED_DOMAIN}"
        account.set_password(settings.STAGING_PASSWORD)
        account.save()

        self.stdout.write(self.style.SUCCESS(
            f"Staging ready: {emails} email addresses scrubbed, {locked} accounts locked, "
            f"sign in as {name} ({'created' if created else 'updated'})."))
