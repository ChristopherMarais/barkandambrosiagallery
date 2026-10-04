"""
Access is now granted area by area to everyone, staff included (areas.py). So that nobody loses anything, every
existing account is given exactly what its role gave it before: curators (staff) every area they had (not the
species tables, which were for superusers), members the specimen pages and downloads. Superusers need nothing.
New accounts after this start as Basic.
"""
from django.conf import settings
from django.db import migrations, models

CURATOR_AREAS = ["details", "download", "boxes", "annotate", "upload", "interactions"]
MEMBER_AREAS = ["details", "download"]


def grant(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    AreaGrant = apps.get_model("beetles_app", "AreaGrant")
    rows = []
    for user in User.objects.filter(is_superuser=False).only("id", "is_staff", "is_active", "last_login"):
        if not user.is_active and user.last_login is None:
            continue   # an account waiting on an access request: it starts as Basic like any new one
        have = set(AreaGrant.objects.filter(user_id=user.id).values_list("area", flat=True))
        for area in (CURATOR_AREAS if user.is_staff else MEMBER_AREAS):
            if area not in have:
                rows.append(AreaGrant(user_id=user.id, area=area))
    AreaGrant.objects.bulk_create(rows)


class Migration(migrations.Migration):
    DATA_MIGRATION_REVIEWED = ("Adds AreaGrant rows only (never removes): each existing non-superuser account gets "
                               "the areas its role already gave it, so access is unchanged.")

    dependencies = [
        ("beetles_app", "0036_proposals_notice_seen"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="accessrequest", name="granted_areas",
            field=models.JSONField(blank=True, default=list, help_text="The areas granted when it was decided (areas.py keys)."),
        ),
        migrations.AlterField(
            model_name="accessrequest", name="granted_role",
            field=models.CharField(blank=True, help_text="basic, or areas (see granted_areas), once approved. Older requests: member or curator.", max_length=10),
        ),
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
