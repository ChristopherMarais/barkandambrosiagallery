"""
The grid games grow to 25 beetles (5×5). Find Them All's ladder goes from 12 steps to 16 (4, 9, 16, 25 at each rank)
and Odd One Out's from 24 to 40 (game_grid_ladder.ODD_SHAPES). A player's saved step is moved to the same grid on the
new ladder (the same size, rank and number of odd ones), so nobody jumps a rank or a grid size. The ladders are written
out here, as they were and are, so this stays right whatever the code's ladders become.
"""
from django.db import migrations

RANKS = ("subfamily", "tribe", "genus", "species")
OLD = {
    "odd": [(size, rank, odds) for rank in RANKS for size, odds in ((4, 1), (9, 1), (16, 1), (9, 2), (16, 2), (16, 3))],
    "select": [(size, rank, 1) for rank in RANKS for size in (4, 9, 16)],
}
NEW = {
    "odd": [(size, rank, odds) for rank in RANKS for size, odds in
            ((4, 1), (9, 1), (16, 1), (25, 1), (9, 2), (16, 2), (25, 2), (16, 3), (25, 3), (25, 4))],
    "select": [(size, rank, 1) for rank in RANKS for size in (4, 9, 16, 25)],
}


def old_to_new(game, step):
    """The same grid on the new ladder (every old grid is on it)."""
    old = OLD[game]
    return NEW[game].index(old[min(max(step, 1), len(old)) - 1]) + 1


def new_to_old(game, step):
    """Back: a grid of 25 becomes one of 16, with at most three odd ones."""
    new = NEW[game]
    size, rank, odds = new[min(max(step, 1), len(new)) - 1]
    size = min(size, 16)
    shape = (size, rank, min(odds, 3) if game == "odd" else 1)
    while shape not in OLD[game]:   # (9, 3) and the like don't exist: fewer odd ones
        shape = (size, rank, shape[2] - 1)
    return OLD[game].index(shape) + 1


def remap(apps, convert):
    GridStep = apps.get_model("beetles_app", "GridStep")
    for row in GridStep.objects.filter(game__in=("odd", "select")):
        step = convert(row.game, row.step)
        if step != row.step:
            row.step = step
            row.save(update_fields=["step"])


def forwards(apps, schema_editor):
    remap(apps, old_to_new)


def backwards(apps, schema_editor):
    remap(apps, new_to_old)


class Migration(migrations.Migration):
    DATA_MIGRATION_REVIEWED = (
        "Reads and rewrites game_grid_step.step only (Odd One Out and Find Them All rows): each step moves to the same "
        "grid size, rank and number of odd ones on the new ladders (Find Them All 12 -> 16 steps, Odd One Out 24 -> 40). "
        "No rows are added or removed; reversible (grids of 25 go back to 16)."
    )

    dependencies = [
        ("beetles_app", "0053_game_names_naming"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
