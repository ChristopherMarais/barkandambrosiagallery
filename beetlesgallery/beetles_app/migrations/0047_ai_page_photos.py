"""
Photos kept from the AI page (IBBI-AI, #502) say so: their institution is "AI page", and ``added_by`` records who
added a photo (the AI page sets it for a signed-in visitor; uploads may use it later).

Photos the AI page kept before this (full_path_at_import "classifier/...") get the same: the institution where it is
empty, and added_by from the photo's first history record, which names the visitor who sent it when they were signed
in. Nothing else changes.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q

AI_PAGE = "AI page"


def label_ai_page_photos(apps, schema_editor):
    ImageAsset = apps.get_model("beetles_app", "ImageAsset")
    HistoricalImageAsset = apps.get_model("beetles_app", "HistoricalImageAsset")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    photos = ImageAsset.objects.filter(full_path_at_import__startswith="classifier/")
    photos.filter(Q(image_institution__isnull=True) | Q(image_institution="")).update(image_institution=AI_PAGE)
    # Who sent it: the account on the photo's creation record (history keeps the id of a deleted account; skip those)
    senders = dict(HistoricalImageAsset.objects.filter(
        id__in=photos.filter(added_by__isnull=True).values("id"), history_type="+", last_updated_by__isnull=False,
    ).values_list("id", "last_updated_by"))
    accounts = set(User.objects.filter(pk__in=set(senders.values())).values_list("pk", flat=True))
    for photo_id, user_id in senders.items():
        if user_id in accounts:
            ImageAsset.objects.filter(pk=photo_id, added_by__isnull=True).update(added_by_id=user_id)


class Migration(migrations.Migration):
    DATA_MIGRATION_REVIEWED = (
        "Only photos the AI page kept (full_path_at_import starting 'classifier/'): image_institution becomes "
        "'AI page' where it is empty, and the new added_by is set from the photo's creation history record "
        "(last_updated_by, the signed-in visitor who sent it; accounts deleted since are skipped) where added_by is "
        "empty. No other field or row is touched; reversing leaves the values (the new column goes with the schema)."
    )

    dependencies = [
        ('beetles_app', '0045_gamepreference_board_privacy'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='historicalimageasset',
            name='added_by',
            field=models.ForeignKey(blank=True, db_constraint=False, help_text='Who added this photo to the gallery, when known (the AI page sets it for a signed-in visitor).', null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='imageasset',
            name='added_by',
            field=models.ForeignKey(blank=True, help_text='Who added this photo to the gallery, when known (the AI page sets it for a signed-in visitor).', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='added_images', to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(label_ai_page_photos, migrations.RunPython.noop),
    ]
