# Points follow how hard a beetle is (#492): each Identification and Similarity answer keeps the beetle's difficulty
# percentile from the moment it was given. Older answers have none and keep their points.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("beetles_app", "0048_grid_ladder"),
    ]

    operations = [
        migrations.AddField(
            model_name="gameanswer",
            name="difficulty",
            field=models.FloatField(
                blank=True, null=True,
                help_text="Identification and Similarity: how hard the beetle was when answered, as its percentile "
                          "among all playable beetles (0 easiest, 1 hardest). Its points follow it (game_scoring). "
                          "Empty on older answers.",
            ),
        ),
    ]
