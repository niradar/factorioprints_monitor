# monitoring/utils.py
from .models import Blueprint, BlueprintSnapshot, CommentSnapshot, UserSnapshot
from django.db import transaction
from datetime import datetime, timezone
import logging
import asyncio

from .comments_scraper import get_comments_async
from .blueprints_scraper import scrape_user_blueprints

# Limit concurrent comment-scraping browser instances
try:
    from django.conf import settings
    MAX_INSTANCES = getattr(settings, "SNAPSHOT_MAX_CONCURRENCY", 6)
except Exception:
    MAX_INSTANCES = 6


def _fetch_all_comments_concurrent(blueprints, logger):
    """Run async comment fetching with concurrency cap, return {url: data}."""
    async def _run():
        sem = asyncio.Semaphore(MAX_INSTANCES)
        results = {}

        async def fetch(bp):
            async with sem:
                url = bp['url']
                logger.info(f"Scraping comments for blueprint: {url}")
                data = await get_comments_async(url)
                logger.info(f"Found {len(data.get('comments', []))} comments for blueprint: {url}")
                results[url] = data

        await asyncio.gather(*(fetch(bp) for bp in blueprints))
        return results

    return asyncio.run(_run())


def take_snapshot(user_url: str) -> datetime:
    logger = logging.getLogger(__name__)
    snapshot_ts = datetime.now(timezone.utc)
    logger.info(f"Starting snapshot for user: {user_url} at {snapshot_ts}")

    # Scrape everything FIRST (no transaction yet)
    blueprints = scrape_user_blueprints(user_url)
    logger.info(f"Found {len(blueprints)} blueprints for user {user_url}")

    # Fetch comments concurrently with a cap on running instances
    comments_data = _fetch_all_comments_concurrent(blueprints, logger)

    # Now, store everything in a single, short transaction:
    with transaction.atomic():
        UserSnapshot.objects.create(snapshot_ts=snapshot_ts, user_url=user_url)
        logger.info(f"Created UserSnapshot for {user_url} at {snapshot_ts}")
        for bp in blueprints:
            blueprint_obj, _ = Blueprint.objects.get_or_create(
                url=bp['url'],
                defaults={'name': bp.get('name', 'Unknown')}
            )
            c_info = comments_data.get(bp['url'], {"total_comments": 0, "comments": []})
            BlueprintSnapshot.objects.create(
                snapshot_ts=snapshot_ts,
                blueprint=blueprint_obj,
                name=bp.get('name', 'Unknown'),
                favourites=bp.get('favorites', 0),
                total_comments=c_info.get('total_comments', 0)
            )
            logger.info(f"Created BlueprintSnapshot for {bp['url']} at {snapshot_ts}")
            for c in c_info.get('comments', []):
                CommentSnapshot.objects.create(
                    snapshot_ts=snapshot_ts,
                    blueprint=blueprint_obj,
                    comment_id=c.get('id', 'missing_id'),
                    author=c.get('author', 'unknown'),
                    created_utc=c.get('created_utc', snapshot_ts),
                    message_text=c.get('message_text', '')
                )
            logger.info(f"Created {len(c_info.get('comments', []))} CommentSnapshots for blueprint {bp['url']} at {snapshot_ts}")
    logger.info(f"Snapshot complete for user: {user_url} at {snapshot_ts}")
    return snapshot_ts


def list_snapshots(user_url=None):
    """Return all snapshot timestamps (optionally filtered by user_url)"""
    qs = UserSnapshot.objects.all()
    if user_url:
        qs = qs.filter(user_url=user_url)
    return qs.order_by('snapshot_ts').values_list('snapshot_ts', flat=True)


def get_latest_blueprints(user_url):
    """Return all blueprints for the latest snapshot of a user (as queryset)"""
    qs = UserSnapshot.objects.filter(user_url=user_url)
    if not qs.exists():
        return []
    latest_ts = qs.latest('snapshot_ts').snapshot_ts
    return BlueprintSnapshot.objects.filter(
        snapshot_ts=latest_ts
    ).select_related('blueprint')


def blueprints_with_new_comments(user_url: str, start_date: str, end_date: str) -> str:
    """
    Returns a CSV string of all blueprints for a user that received new comments between two dates.
    Each row: blueprint_url, blueprint_name, num_of_new_comments, comments_num_on_end_date
    """
    # Parse as date (ignore hour)
    start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()

    # Find the latest snapshot for each date (if available)
    start_snapshot_qs = UserSnapshot.objects.filter(user_url=user_url, snapshot_ts__date=start_date_obj).order_by('-snapshot_ts')
    end_snapshot_qs = UserSnapshot.objects.filter(user_url=user_url, snapshot_ts__date=end_date_obj).order_by('-snapshot_ts')

    if not start_snapshot_qs.exists():
        return f"No snapshots found for start date {start_date}."
    if not end_snapshot_qs.exists():
        return f"No snapshots found for end date {end_date}."

    start_snapshot_ts = start_snapshot_qs.first().snapshot_ts
    end_snapshot_ts = end_snapshot_qs.first().snapshot_ts

    # Get blueprints that existed at the end date
    end_blueprints = BlueprintSnapshot.objects.filter(snapshot_ts=end_snapshot_ts)
    result_rows = []
    for bp_snap in end_blueprints:
        # Count comments at start and end
        comments_at_start = CommentSnapshot.objects.filter(
            snapshot_ts=start_snapshot_ts,
            blueprint=bp_snap.blueprint
        ).count()
        comments_at_end = CommentSnapshot.objects.filter(
            snapshot_ts=end_snapshot_ts,
            blueprint=bp_snap.blueprint
        ).count()
        num_new_comments = comments_at_end - comments_at_start
        if num_new_comments > 0:
            result_rows.append([
                bp_snap.blueprint.url,
                bp_snap.name,
                str(num_new_comments),
                str(comments_at_end)
            ])
    if not result_rows:
        return "No blueprints received new comments in this period."
    out = ["blueprint_url,blueprint_name,num_of_new_comments,comments_num_on_end_date"]
    for row in result_rows:
        # Escape blueprint_name if needed
        row[1] = '"' + row[1].replace('"', '""') + '"' if ',' in row[1] else row[1]
        out.append(",".join(row))
    return "\n".join(out)
