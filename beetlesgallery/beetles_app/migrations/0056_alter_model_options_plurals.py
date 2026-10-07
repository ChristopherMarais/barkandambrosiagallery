# Fixes the Django admin's broken plural names for these models (#618 adm-plurals): "Answer pointss",
# "Roi difficultys" and "Species discoverys" -> proper verbose names.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('beetles_app', '0055_game_speed_indexes'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='answerpoints',
            options={'verbose_name': 'Answer Points', 'verbose_name_plural': 'Answer Points'},
        ),
        migrations.AlterModelOptions(
            name='speciesdiscovery',
            options={'ordering': ['-created_at'], 'verbose_name': 'Species Discovery',
                     'verbose_name_plural': 'Species Discoveries'},
        ),
        migrations.AlterModelOptions(
            name='roidifficulty',
            options={'verbose_name': 'ROI Difficulty', 'verbose_name_plural': 'ROI Difficulties'},
        ),
    ]
