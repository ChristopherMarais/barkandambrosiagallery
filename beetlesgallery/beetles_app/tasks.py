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
