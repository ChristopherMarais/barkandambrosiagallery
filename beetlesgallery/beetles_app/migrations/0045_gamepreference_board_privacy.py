# Leaderboard privacy (#394): show as "A player" to others, or don't show the boards to me.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("beetles_app", "0044_playerskill_children"),
    ]

    operations = [
        migrations.AddField(
            model_name="gamepreference",
            name="hide_name",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="gamepreference",
            name="hide_boards",
            field=models.BooleanField(default=False),
        ),
    ]
