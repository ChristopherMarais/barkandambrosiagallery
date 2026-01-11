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