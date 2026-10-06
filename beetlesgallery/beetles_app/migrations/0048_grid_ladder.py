# The grid games grow with the player (#489): each player's step on the ladder of Odd One Out and Select all, and on
# every grid answer the step it was built at and the photos flagged before answering. Only adds; nothing is changed.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('beetles_app', '0045_gamepreference_board_privacy'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='gameanswer',
            name='flagged',
            field=models.JSONField(blank=True, default=list, help_text='Grid games: the places in tiles the player flagged as a bad photo; left out of scoring and votes.'),
        ),
        migrations.AddField(
            model_name='gameanswer',
            name='grid_step',
            field=models.PositiveSmallIntegerField(blank=True, help_text="Grid games: the player's step on the grid ladder (1-12) when the grid was built.", null=True),
        ),
        migrations.CreateModel(
            name='GridStep',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('game', models.CharField(choices=[('odd', 'Odd One Out'), ('select', 'Select all')], max_length=10)),
                ('step', models.PositiveSmallIntegerField(default=1)),
                ('good_run', models.PositiveSmallIntegerField(default=0, help_text='Good grids in a row since the step last moved.')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('last_answer', models.ForeignKey(blank=True, help_text='The last answer that moved the ladder, so no answer ever counts twice.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='beetles_app.gameanswer')),
                ('player', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='grid_steps', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'game_grid_step',
            },
        ),
        migrations.AddConstraint(
            model_name='gridstep',
            constraint=models.UniqueConstraint(fields=('player', 'game'), name='game_grid_step_player_game_uniq'),
        ),
    ]
