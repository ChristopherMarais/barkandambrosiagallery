"""
Odd One Out (#369): a third game, between Similarity and Identification. Its answers keep the regions shown
(``tiles``), the rank at which one differs (``grid_rank``) and the names the others share (``grid_group``).

Identification moves from level 2 to level 4. Nobody loses it: every player who could play it before (level 2, that
is 50 points, or anyone who has played it) keeps it (GamePreference.kept_perks), and a superuser's earlier grant of
"choose_game", which used to mean Identification, now grants Identification as well.
"""
from django.db import migrations, models

KEEP_FROM_POINTS = 50   # level 2 before this change (game_levels.LEVELS)


def keep_identification(apps, schema_editor):
    GameAnswer = apps.get_model("beetles_app", "GameAnswer")
    GamePreference = apps.get_model("beetles_app", "GamePreference")
    PlayerScore = apps.get_model("beetles_app", "PlayerScore")

    keep = set(PlayerScore.objects.filter(score__gte=KEEP_FROM_POINTS).values_list("player_id", flat=True))
    keep |= set(GameAnswer.objects.filter(mode="classify").values_list("player_id", flat=True).distinct())
    for player_id in keep:
        pref, _ = GamePreference.objects.get_or_create(player_id=player_id)
        if "identification" not in (pref.kept_perks or []):
            pref.kept_perks = list(pref.kept_perks or []) + ["identification"]
            pref.save(update_fields=["kept_perks"])
    for pref in GamePreference.objects.exclude(granted_perks=[]):
        perks = list(pref.granted_perks or [])
        if "choose_game" in perks and "identification" not in perks:
            pref.granted_perks = perks + ["identification"]
            pref.save(update_fields=["granted_perks"])


class Migration(migrations.Migration):
    DATA_MIGRATION_REVIEWED = (
        "Adds only: GamePreference.kept_perks gets 'identification' for players with at least 50 points or any "
        "Identification answer, and granted_perks that contain 'choose_game' get 'identification' too. Nothing is "
        "removed; reversing leaves the new fields to be dropped with the schema."
    )

    dependencies = [
        ("beetles_app", "0040_merge_permissions_and_tiers"),
    ]

    operations = [
        migrations.AlterField(
            model_name="gameround", name="mode",
            field=models.CharField(choices=[("classify", "Classify"), ("pair", "Compare pairs"), ("odd", "Odd One Out"), ("mixed", "Mixed")], db_index=True, max_length=10),
        ),
        migrations.AlterField(
            model_name="gameanswer", name="mode",
            field=models.CharField(choices=[("classify", "Classify"), ("pair", "Compare pairs"), ("odd", "Odd One Out"), ("mixed", "Mixed")], db_index=True, max_length=10),
        ),
        migrations.AlterField(
            model_name="gamepreference", name="play_mode",
            field=models.CharField(choices=[("both", "Both"), ("classify", "Name That Beetle"), ("pair", "Family Ties"), ("odd", "Odd One Out")], default="both", max_length=10),
        ),
        migrations.AddField(
            model_name="gameanswer", name="tiles",
            field=models.JSONField(blank=True, default=list, help_text="Grid games (Odd One Out): the regions shown, in order."),
        ),
        migrations.AddField(
            model_name="gameanswer", name="grid_rank",
            field=models.CharField(blank=True, help_text="Grid games: the rank of the group (in Odd One Out, where one region differs).", max_length=10),
        ),
        migrations.AddField(
            model_name="gameanswer", name="grid_group",
            field=models.JSONField(blank=True, default=dict, help_text='Grid games: the names of the group, down to grid_rank, e.g. {"subfamily": "Scolytinae", "tribe": "Xyleborini"}.'),
        ),
        migrations.AddField(
            model_name="gamepreference", name="kept_perks",
            field=models.JSONField(blank=True, default=list, help_text="Unlocks the player keeps from before the levels changed (game_levels.PERKS keys), e.g. Identification for players who had it when it moved from level 2 to level 4."),
        ),
        migrations.RunPython(keep_identification, migrations.RunPython.noop),
    ]
