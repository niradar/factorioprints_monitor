# monitoring/utils.py
from .models import Blueprint, BlueprintSnapshot, CommentSnapshot, UserSnapshot
from django.db import transaction
from datetime import datetime, timezone

from .comments_scraper import get_comments
from .blueprints_scraper import scrape_user_blueprints

def take_snapshot(user_url: str) -> datetime:
    snapshot_ts = datetime.now(timezone.utc)
    # Scrape everything FIRST (no transaction yet)
    blueprints = scrape_user_blueprints(user_url)
    comments_data = {}
    for bp in blueprints:
        comments_data[bp['url']] = get_comments(bp['url'])
    # Now, store everything in a single, short transaction:
    with transaction.atomic():
        UserSnapshot.objects.create(snapshot_ts=snapshot_ts, user_url=user_url)
        for bp in blueprints:
            blueprint_obj, _ = Blueprint.objects.get_or_create(
                url=bp['url'],
                defaults={'name': bp.get('name', 'Unknown')}
            )
            c_info = comments_data[bp['url']]
            BlueprintSnapshot.objects.create(
                snapshot_ts=snapshot_ts,
                blueprint=blueprint_obj,
                name=bp.get('name', 'Unknown'),
                favourites=bp.get('favorites', 0),
                total_comments=c_info.get('total_comments', 0)
            )
            for c in c_info.get('comments', []):
                CommentSnapshot.objects.create(
                    snapshot_ts=snapshot_ts,
                    blueprint=blueprint_obj,
                    comment_id=c.get('id', 'missing_id'),
                    author=c.get('author', 'unknown'),
                    created_utc=c.get('created_utc', snapshot_ts),
                    message_text=c.get('message_text', '')
                )
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
