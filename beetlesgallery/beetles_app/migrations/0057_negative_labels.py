# Negative labels: what each game answer says a beetle is *not* (game_negatives). A new table only; existing answers
# get their rows from `python manage.py backfill_negative_labels`.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('beetles_app', '0056_alter_model_options_plurals'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='NegativeLabel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mode', models.CharField(choices=[('classify', 'Classify'), ('pair', 'Compare pairs'), ('odd', 'Odd One Out'), ('select', 'Find Them All'), ('mixed', 'All modes')], max_length=10)),
                ('rank', models.CharField(max_length=10)),
                ('value', models.CharField(max_length=201)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('answer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='negatives', to='beetles_app.gameanswer')),
                ('player', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='negative_labels', to=settings.AUTH_USER_MODEL)),
                ('roi', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='negative_labels', to='beetles_app.beetles')),
            ],
            options={
                'verbose_name': 'Negative Label',
                'verbose_name_plural': 'Negative Labels',
                'db_table': 'game_negative_label',
                'indexes': [models.Index(fields=['roi', 'rank'], name='game_negative_label_roi_idx')],
                'constraints': [models.UniqueConstraint(fields=('answer', 'roi', 'rank'), name='game_negative_label_uniq')],
            },
        ),
    ]
