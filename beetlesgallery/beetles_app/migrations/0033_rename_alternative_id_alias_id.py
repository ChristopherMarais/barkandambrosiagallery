# alternative_id is renamed alias_id (#379): a rename, so the stored IDs are kept.
from django.db import migrations, models

HELP = (
    "Your own ID for this record, e.g. a catalogue number or file name from your database. Optional; we keep it "
    "unchanged and include it in every download so you can link our records back to yours. (Was called alternative_id.)"
)


class Migration(migrations.Migration):

    dependencies = [
        ("beetles_app", "0032_modelprediction_rank_confidence"),
    ]

    operations = [
        migrations.RenameField(model_name="beetles", old_name="alternative_id", new_name="alias_id"),
        migrations.RenameField(model_name="historicalbeetles", old_name="alternative_id", new_name="alias_id"),
        migrations.AlterField(
            model_name="beetles", name="alias_id",
            field=models.CharField(blank=True, help_text=HELP, max_length=255, null=True),
        ),
        migrations.AlterField(
            model_name="historicalbeetles", name="alias_id",
            field=models.CharField(blank=True, help_text=HELP, max_length=255, null=True),
        ),
    ]
