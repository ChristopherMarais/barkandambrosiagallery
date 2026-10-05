# Expertise covers a share of a taxon's children (its species, genera or tribes), not every species (#381).
# The counts are renamed, not dropped: recompute_game_scores (nightly) refills them under the new rule.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("beetles_app", "0043_game_tuning"),
    ]

    operations = [
        migrations.RenameField(model_name="playerskill", old_name="species_total", new_name="children_total"),
        migrations.RenameField(model_name="playerskill", old_name="species_done", new_name="children_done"),
        migrations.AddField(
            model_name="playerskill",
            name="children_needed",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
