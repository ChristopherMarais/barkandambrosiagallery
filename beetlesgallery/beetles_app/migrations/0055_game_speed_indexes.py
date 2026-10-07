# Indexes for the game's hot queries: the same-specimen lookup on Beetles, a player's answers by day, and the
# grid answers that show a beetle (a JSON containment test on tiles).

import django.contrib.postgres.indexes
import django.db.models.functions.text
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('beetles_app', '0054_grid_ladder_25'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='beetles',
            index=models.Index(django.db.models.functions.text.Lower(django.db.models.functions.text.Trim('depicts_specimen')), name='beetles_lower_specimen_idx'),
        ),
        migrations.AddIndex(
            model_name='gameanswer',
            index=models.Index(fields=['player', 'answered_at'], name='game_answer_player_day_idx'),
        ),
        migrations.AddIndex(
            model_name='gameanswer',
            index=django.contrib.postgres.indexes.GinIndex(fields=['tiles'], name='game_answer_tiles_gin', opclasses=['jsonb_path_ops']),
        ),
    ]
