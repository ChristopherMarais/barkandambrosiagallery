from celery import shared_task
from django.core.management import call_command

@shared_task
def process_upload_task(batch_id):
    call_command('process_single_upload', id=batch_id)

@shared_task
def process_update_task(batch_id):
    call_command('process_single_update', id=batch_id)

@shared_task
def build_downloads_task(job_id):
    call_command('build_downloads', job=job_id, limit=1)

@shared_task
def recompute_game_players_task(player_ids):
    """Re-score these players in the background (see game.finish_round)."""
    from beetlesgallery.beetles_app.game_scoring import recompute
    recompute(player_ids)

@shared_task
def import_predictions_task(job_id):
    """Check and save a model predictions CSV uploaded on Data Management (predictions.run_upload)."""
    from beetlesgallery.beetles_app.predictions import run_upload
    run_upload(job_id)

@shared_task
def recompute_all_scores_task():
    """Everyone's scores with the current scoring settings (the Scoring page's "Re-score everyone")."""
    call_command("recompute_game_scores")

@shared_task
def finish_game_round_task(round_id):
    """Refresh what derives from a closed batch's answers in the background (see game.finish_round_later)."""
    from beetlesgallery.beetles_app import game
    from beetlesgallery.beetles_app.models import GameRound
    rnd = GameRound.objects.filter(id=round_id).first()
    if rnd is not None:
        game.refresh_round(rnd)

@shared_task(ignore_result=True)
def prepare_game_crops_task(round_id):
    """Cut a new batch's crops before the feed asks for them (game_crops.prepare)."""
    from beetlesgallery.beetles_app.game_crops import prepare
    prepare(round_id)

@shared_task(ignore_result=True)
def build_game_batch_ahead_task(round_id):
    """Build the batch that follows one in play, from its middle on (game_views.build_ahead_later)."""
    from beetlesgallery.beetles_app.game_views import build_ahead_now
    build_ahead_now(round_id)

@shared_task(ignore_result=True)
def warm_game_batches_task(player_id):
    """Build a batch for each game the player might switch to (game_warm.warm_later)."""
    from django.contrib.auth import get_user_model
    from beetlesgallery.beetles_app.game_warm import build
    player = get_user_model().objects.filter(pk=player_id).first()
    if player is not None:
        build(player)

@shared_task(ignore_result=True)
def grow_game_round_task(round_id):
    """Grow a batch that was started small to its full size (game_grow.start_round)."""
    from beetlesgallery.beetles_app.game_grow import grow_now
    grow_now(round_id)


@shared_task(ignore_result=True)
def refresh_game_ratings_task():
    """The game's ratings table, worked out over all answers and stored (game_scoring.refresh_ratings), off the web path."""
    from beetlesgallery.beetles_app.game_scoring import refresh_ratings
    refresh_ratings()
