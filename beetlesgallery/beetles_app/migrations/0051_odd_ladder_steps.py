"""
Odd One Out's ladder grows from 12 steps to 24 (#540): at each rank, after the grids reach 16 beetles, they hide two
and then three odd ones before the next rank. A player's saved step is moved to the same grid on the new ladder (the
same size and rank, one odd one), so nobody jumps a rank up or down. Select all keeps its twelve steps. Odd One Out
answers now keep their picks too (GameAnswer.picks, only its help text changes).
"""
from django.db import migrations, models

SIZES = 3       # 4, 9 and 16 beetles: the old ladder's steps per rank
NEW_PER_RANK = 6   # 4·1, 9·1, 16·1, 9·2, 16·2, 16·3


def old_to_new(step):
    rank, size = divmod(step - 1, SIZES)
    return rank * NEW_PER_RANK + size + 1


def new_to_old(step):
    rank, shape = divmod(step - 1, NEW_PER_RANK)
    return rank * SIZES + min(shape, SIZES - 1) + 1   # two or three odd ones in 16: back to 16 with one


def remap(apps, convert):
    GridStep = apps.get_model("beetles_app", "GridStep")
    for row in GridStep.objects.filter(game="odd"):
        row.step = convert(row.step)
        row.save(update_fields=["step"])


def forwards(apps, schema_editor):
    remap(apps, old_to_new)


def backwards(apps, schema_editor):
    remap(apps, new_to_old)


class Migration(migrations.Migration):
    DATA_MIGRATION_REVIEWED = (
        "Reads and rewrites game_grid_step.step for Odd One Out rows only (game='odd'): the same grid size and rank on "
        "the new 24-step ladder (old step s -> 6 * ((s - 1) // 3) + (s - 1) % 3 + 1). No rows are added or removed; "
        "reversible."
    )

    dependencies = [
        ("beetles_app", "0050_game_names"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="gameanswer",
            name="picks",
            field=models.JSONField(blank=True, default=list, help_text="Grid games: the places in tiles the player tapped (Select all) or picked (Odd One Out, since #540)."),
        ),
    ]
