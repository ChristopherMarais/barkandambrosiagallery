"""Select all (#370): a fourth game, at level 3. Its answers keep which of the grid's beetles the player tapped (``picks``)."""
from django.db import migrations, models

MODES = [("classify", "Classify"), ("pair", "Compare pairs"), ("odd", "Odd One Out"), ("select", "Select all"),
         ("mixed", "Mixed")]


class Migration(migrations.Migration):
    dependencies = [
        ("beetles_app", "0041_odd_one_out"),
    ]

    operations = [
        migrations.AlterField(
            model_name="gameround", name="mode",
            field=models.CharField(choices=MODES, db_index=True, max_length=10),
        ),
        migrations.AlterField(
            model_name="gameanswer", name="mode",
            field=models.CharField(choices=MODES, db_index=True, max_length=10),
        ),
        migrations.AlterField(
            model_name="gamepreference", name="play_mode",
            field=models.CharField(choices=[("both", "Both"), ("classify", "Name That Beetle"), ("pair", "Family Ties"),
                                            ("odd", "Odd One Out"), ("select", "Select all")], default="both", max_length=10),
        ),
        migrations.AddField(
            model_name="gameanswer", name="picks",
            field=models.JSONField(blank=True, default=list, help_text="Select all: the places in tiles the player tapped."),
        ),
        migrations.AlterField(
            model_name="gameanswer", name="tiles",
            field=models.JSONField(blank=True, default=list, help_text="Grid games (Odd One Out, Select all): the regions shown, in order."),
        ),
    ]
