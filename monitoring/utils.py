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

    # Best-effort: email the user about comments new in this snapshot (no-op if
    # alerts are off or this is the first snapshot). Never breaks the snapshot.
    from .alerts import send_new_comment_alert
    send_new_comment_alert(user_url, snapshot_ts)
    return snapshot_ts


def monitored_user_urls():
    """Every FactorioPrints account this install monitors, as a sorted list of
    user_urls. The union of accounts that have at least one snapshot (what the
    switcher shows) and accounts configured in Settings (a UserSettings row),
    so a freshly-configured account is scanned even before its first snapshot.
    Used by the `snapshot_all` command that drives scheduled scans."""
    from .models import UserSettings

    urls = set(UserSnapshot.objects.values_list('user_url', flat=True).distinct())
    urls |= set(UserSettings.objects.values_list('user_url', flat=True))
    return sorted(urls)


def delete_user_account(user_url):
    """Remove every trace of one monitored account and return deleted counts.

    Deletes the account's snapshots, snapshot-run history and settings. Its
    blueprints are removed only when no other account references them (in
    practice a blueprint belongs to a single user); the CASCADE on Blueprint
    then takes their BlueprintSnapshot/CommentSnapshot rows and handled flags
    (CommentStatus) with them. For any blueprint shared with another account,
    only this account's own snapshot rows are removed, leaving the blueprint
    and the other account's data intact. Runs in a single transaction.
    """
    from .models import BlueprintSnapshot, CommentSnapshot, SnapshotRun, UserSettings

    with transaction.atomic():
        snap_ts = list(
            UserSnapshot.objects.filter(user_url=user_url).values_list('snapshot_ts', flat=True)
        )
        my_bp = set(
            BlueprintSnapshot.objects.filter(snapshot_ts__in=snap_ts)
            .values_list('blueprint_id', flat=True)
        )
        # blueprints also captured under some OTHER account's snapshots
        shared = set(
            BlueprintSnapshot.objects.filter(blueprint_id__in=my_bp)
            .exclude(snapshot_ts__in=snap_ts)
            .values_list('blueprint_id', flat=True)
        )
        exclusive = my_bp - shared

        counts = {}
        if shared:
            counts['comment_snapshots'] = CommentSnapshot.objects.filter(
                snapshot_ts__in=snap_ts, blueprint_id__in=shared).delete()[0]
            counts['blueprint_snapshots'] = BlueprintSnapshot.objects.filter(
                snapshot_ts__in=snap_ts, blueprint_id__in=shared).delete()[0]
        # Exclusive blueprints: deleting the canonical row cascades to its
        # snapshots, comment snapshots and handled flags.
        counts['blueprints'] = Blueprint.objects.filter(id__in=exclusive).delete()[0]
        counts['user_snapshots'] = UserSnapshot.objects.filter(user_url=user_url).delete()[0]
        counts['snapshot_runs'] = SnapshotRun.objects.filter(user_url=user_url).delete()[0]
        counts['settings'] = UserSettings.objects.filter(user_url=user_url).delete()[0]
    return counts


def user_blueprint_ids(user_url):
    """Blueprint ids that appear in any of this user's snapshots (a queryset of
    ids). Shared by the recent-comments and inbox queries."""
    return BlueprintSnapshot.objects.filter(
        snapshot_ts__in=UserSnapshot.objects.filter(user_url=user_url).values('snapshot_ts')
    ).values_list('blueprint_id', flat=True)


def get_recent_unique_comments(user_url, limit=None):
    """Return the latest unique (blueprint, comment_id) CommentSnapshots for a
    user's blueprints, newest first. The same comment is captured in every
    snapshot, so we keep only the most recent row per (blueprint, comment_id).

    `limit` caps the number of rows (None returns all). Returns a queryset.
    """
    from django.db.models import Max

    latest_per_comment = (
        CommentSnapshot.objects.filter(blueprint_id__in=user_blueprint_ids(user_url))
        .values('blueprint_id', 'comment_id')
        .annotate(latest_id=Max('id'))
        .values_list('latest_id', flat=True)
    )
    qs = (
        CommentSnapshot.objects.filter(id__in=latest_per_comment)
        .select_related('blueprint')
        .order_by('-created_utc')
    )
    if limit is not None:
        qs = qs[:limit]
    return qs


def _handled_subquery():
    """Exists() subquery: is the outer CommentSnapshot's comment marked handled?
    Correlated on (blueprint, comment_id) - the stable comment identity."""
    from django.db.models import OuterRef
    from .models import CommentStatus

    return CommentStatus.objects.filter(
        blueprint_id=OuterRef('blueprint_id'),
        comment_id=OuterRef('comment_id'),
        handled=True,
    )


def get_inbox_queryset(user_url, status='all', query=''):
    """Latest unique comments for the inbox, annotated with `is_handled` and
    filtered by status ('all' | 'needs' | 'done') and a free-text query."""
    from django.db.models import Exists, Q

    qs = get_recent_unique_comments(user_url).annotate(is_handled=Exists(_handled_subquery()))
    if status == 'needs':
        qs = qs.filter(is_handled=False)
    elif status == 'done':
        qs = qs.filter(is_handled=True)
    if query:
        qs = qs.filter(
            Q(message_text__icontains=query)
            | Q(author__icontains=query)
            | Q(blueprint__name__icontains=query)
        )
    return qs


def get_inbox_counts(user_url):
    """Counts for the filter pills / nav badge: {'all', 'needs', 'done'}."""
    from django.db.models import Exists

    base = get_recent_unique_comments(user_url).annotate(is_handled=Exists(_handled_subquery()))
    total = base.count()
    done = base.filter(is_handled=True).count()
    return {'all': total, 'needs': total - done, 'done': done}


def get_blueprint_comments(blueprint_id):
    """Latest unique comments for ONE blueprint, annotated `is_handled`, newest first."""
    from django.db.models import Max, Exists

    latest = (
        CommentSnapshot.objects.filter(blueprint_id=blueprint_id)
        .values('comment_id').annotate(latest_id=Max('id')).values_list('latest_id', flat=True)
    )
    return (
        CommentSnapshot.objects.filter(id__in=latest)
        .select_related('blueprint')
        .annotate(is_handled=Exists(_handled_subquery()))
        .order_by('-created_utc')
    )


def get_blueprint_series(blueprint_id):
    """Per-snapshot (ts, favourites, total_comments) for one blueprint - chart data."""
    return list(
        BlueprintSnapshot.objects.filter(blueprint_id=blueprint_id)
        .order_by('snapshot_ts')
        .values('snapshot_ts', 'favourites', 'total_comments')
    )


def get_blueprints_overview(user_url, baseline_days=30):
    """Per-blueprint rows for the Blueprints list, from the user's latest snapshot.

    Each row: blueprint_id, name, url, favourites, fav_delta (vs ~baseline_days
    ago, or None), comments (Disqus total), awaiting (unhandled captured), and
    last_comment_ts.

    A blueprint that first appears *within* the window (its earliest snapshot is
    newer than the baseline snapshot) is treated as new: its baseline is 0, so
    all its current favourites count as gained in the window. This only fires
    when a baseline snapshot exists - on a brand-new account (no snapshot old
    enough) every delta is None, since the first scan is the baseline, not a gain.

    Returns ``(rows, baseline_ts)`` where baseline_ts is the snapshot the deltas
    were actually measured against (so the UI can show "since <date>"), or None
    when there is no snapshot old enough for the window. Returns ``([], None)``
    if the user has no snapshots at all.
    """
    from datetime import timedelta
    from django.db.models import Exists, Min

    latest = UserSnapshot.objects.filter(user_url=user_url).order_by('-snapshot_ts').first()
    if not latest:
        return [], None
    latest_ts = latest.snapshot_ts

    current = list(BlueprintSnapshot.objects.filter(snapshot_ts=latest_ts).select_related('blueprint'))

    # baseline favourites ~baseline_days ago (nearest user snapshot at/before then)
    baseline_user = (
        UserSnapshot.objects
        .filter(user_url=user_url, snapshot_ts__lte=latest_ts - timedelta(days=baseline_days))
        .order_by('-snapshot_ts').first()
    )
    baseline_fav = {}
    first_seen = {}
    if baseline_user:
        baseline_fav = dict(
            BlueprintSnapshot.objects.filter(snapshot_ts=baseline_user.snapshot_ts)
            .values_list('blueprint_id', 'favourites')
        )
        # Earliest snapshot per current blueprint, to spot ones that first
        # appeared after the baseline (genuinely new, so baseline favs = 0).
        first_seen = dict(
            BlueprintSnapshot.objects
            .filter(blueprint_id__in=[bs.blueprint_id for bs in current])
            .values('blueprint_id').annotate(f=Min('snapshot_ts'))
            .values_list('blueprint_id', 'f')
        )

    # comment aggregates per blueprint, from the latest-unique comments (one query)
    agg = {}
    comments = get_recent_unique_comments(user_url).annotate(is_handled=Exists(_handled_subquery()))
    for c in comments.values('blueprint_id', 'is_handled', 'created_utc'):
        a = agg.setdefault(c['blueprint_id'], {'awaiting': 0, 'last_ts': None})
        if not c['is_handled']:
            a['awaiting'] += 1
        if a['last_ts'] is None or c['created_utc'] > a['last_ts']:
            a['last_ts'] = c['created_utc']

    rows = []
    for bs in current:
        a = agg.get(bs.blueprint_id, {'awaiting': 0, 'last_ts': None})
        base = baseline_fav.get(bs.blueprint_id)
        if base is None and baseline_user is not None:
            fs = first_seen.get(bs.blueprint_id)
            if fs is not None and fs > baseline_user.snapshot_ts:
                base = 0  # first appeared within the window; all favs are new
        rows.append({
            'blueprint_id': bs.blueprint_id,
            'name': bs.name,
            'url': bs.blueprint.url,
            'favourites': bs.favourites,
            'fav_delta': (bs.favourites - base) if base is not None else None,
            'comments': bs.total_comments,
            'awaiting': a['awaiting'],
            'last_comment_ts': a['last_ts'],
        })
    return rows, (baseline_user.snapshot_ts if baseline_user else None)


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


def blueprints_with_new_comments(user_url: str, start_date: str, end_date: str, allow_nearest: bool = True) -> str:
    """
    Returns a CSV string of all blueprints for a user that received new comments between two dates.
    Each row: blueprint_url, blueprint_name, num_of_new_comments, comments_num_on_end_date
    If allow_nearest is True, will use the closest snapshot within the range if an exact date match is missing.
    """
    # Parse as date (ignore hour)
    start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()

    qs = UserSnapshot.objects.filter(user_url=user_url)
    if not qs.exists():
        return f"No snapshots found for user {user_url}."

    # End boundary: the snapshot state as of end_date (latest on or before it).
    end_snapshot_ts = None
    exact_end = qs.filter(snapshot_ts__date=end_date_obj).order_by('-snapshot_ts').first()
    if exact_end:
        end_snapshot_ts = exact_end.snapshot_ts
    elif allow_nearest:
        nearest_end = qs.filter(snapshot_ts__date__lte=end_date_obj).order_by('-snapshot_ts').first()
        if nearest_end:
            end_snapshot_ts = nearest_end.snapshot_ts
    if not end_snapshot_ts:
        return f"No snapshots found for end date {end_date}."

    # Start boundary: the baseline state as of start_date (latest on or before it).
    # With allow_nearest, if there is no snapshot at/before start_date the baseline
    # is empty (count 0), so all comments present at the end count as new.
    start_snapshot_ts = None
    exact_start = qs.filter(snapshot_ts__date=start_date_obj).order_by('-snapshot_ts').first()
    if exact_start:
        start_snapshot_ts = exact_start.snapshot_ts
    elif allow_nearest:
        nearest_start = qs.filter(snapshot_ts__date__lte=start_date_obj).order_by('-snapshot_ts').first()
        if nearest_start:
            start_snapshot_ts = nearest_start.snapshot_ts
    else:
        return f"No snapshots found for start date {start_date}."

    # Get blueprints that existed at the end date
    end_blueprints = BlueprintSnapshot.objects.filter(snapshot_ts=end_snapshot_ts)
    result_rows = []
    for bp_snap in end_blueprints:
        # Count comments at the baseline (0 if there's no baseline snapshot) and at end
        comments_at_start = 0 if start_snapshot_ts is None else CommentSnapshot.objects.filter(
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
