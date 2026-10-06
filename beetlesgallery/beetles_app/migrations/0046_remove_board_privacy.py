# Everyone takes part in the leaderboards (#497): the two privacy settings that 0045 added (#394) go again.

from django.db import migrations


class Migration(migrations.Migration):

    DESTRUCTIVE_OK = (
        "hide_name and hide_boards came in 0045 after release v2.1.2 and never reached production; "
        "dropping them only affects staging's test data."
    )

    dependencies = [
        ("beetles_app", "0045_gamepreference_board_privacy"),
    ]

    operations = [
        migrations.RemoveField(model_name="gamepreference", name="hide_name"),
        migrations.RemoveField(model_name="gamepreference", name="hide_boards"),
    ]
