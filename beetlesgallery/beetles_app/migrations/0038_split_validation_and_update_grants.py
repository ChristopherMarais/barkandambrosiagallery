"""
Validation, AI recommendations and metadata updates became their own areas (areas.py). So that nobody loses what they
could do: whoever could edit names and records ("annotate", which used to include validating and the AI) now also has
"validate" and "ai_recommend", and whoever could upload ("upload", which used to include updates) now also has
"update". Bulk validation, model predictions and the site notice are new: only superusers have them until granted.
"""
from django.db import migrations

ADDED = {"annotate": ["validate", "ai_recommend"], "upload": ["update"]}


def grant(apps, schema_editor):
    AreaGrant = apps.get_model("beetles_app", "AreaGrant")
    have = set(AreaGrant.objects.values_list("user_id", "area"))
    rows = []
    for user_id, area in list(have):
        for extra in ADDED.get(area, []):
            if (user_id, extra) not in have:
                have.add((user_id, extra))
                rows.append(AreaGrant(user_id=user_id, area=extra))
    AreaGrant.objects.bulk_create(rows)


class Migration(migrations.Migration):
    DATA_MIGRATION_REVIEWED = ("Adds AreaGrant rows only (never removes): holders of 'annotate' get 'validate' and "
                               "'ai_recommend', holders of 'upload' get 'update', so what each person can do is unchanged.")

    dependencies = [("beetles_app", "0037_area_grants_for_existing_accounts")]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
