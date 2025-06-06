# monitoring/tasks.py

from celery import shared_task
from .utils import take_snapshot

import logging
logger = logging.getLogger(__name__)


@shared_task
def take_snapshot_task(user_url):
    logger.info(f"Taking snapshot for user URL: {user_url}")

    return take_snapshot(user_url)
