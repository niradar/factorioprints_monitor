# views.py
import logging
import threading

from django.shortcuts import render, redirect, get_object_or_404
from .models import UserSnapshot, BlueprintSnapshot, SnapshotRun, CommentStatus, Blueprint
from .utils import (
    take_snapshot,
    blueprints_with_new_comments,
    get_recent_unique_comments,
    get_inbox_queryset,
    get_inbox_counts,
    user_blueprint_ids,
    get_blueprints_overview,
    get_blueprint_comments,
    get_blueprint_series,
)
from urllib.parse import urlparse, urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Max
from django.http import HttpResponseRedirect, JsonResponse, HttpResponseBadRequest, Http404
from django.urls import reverse
from django.views.decorators.http import require_POST
import csv
from io import StringIO
from datetime import timedelta, datetime, timezone as dt_timezone
from django.utils import timezone

logger = logging.getLogger(__name__)

# Minimum time between snapshots for the same user (also enforced in the UI).
SNAPSHOT_COOLDOWN = timedelta(minutes=10)
# A RUNNING snapshot older than this is treated as stale (the process likely
# died), so it can't permanently block new snapshots. Kept generous because a
# real snapshot may take several minutes.
SNAPSHOT_STALE_AFTER = timedelta(minutes=30)


def is_in_cooldown(user_url):
    """True if a snapshot for this user was taken within SNAPSHOT_COOLDOWN."""
    latest = (
        UserSnapshot.objects.filter(user_url=user_url)
        .order_by('-snapshot_ts')
        .first()
    )
    return bool(latest and latest.snapshot_ts > timezone.now() - SNAPSHOT_COOLDOWN)


def is_snapshot_running(user_url):
    """True if a snapshot for this user is currently running.

    Runs older than SNAPSHOT_COOLDOWN are treated as stale (e.g. the process
    died mid-run) so a permanently-RUNNING row can't block future snapshots.
    """
    cutoff = timezone.now() - SNAPSHOT_STALE_AFTER
    return SnapshotRun.objects.filter(
        user_url=user_url, status=SnapshotRun.RUNNING, started_at__gt=cutoff
    ).exists()


def _run_snapshot(user_url, run_id):
    """Thread body: run the snapshot and record its outcome on the SnapshotRun row."""
    try:
        snapshot_ts = take_snapshot(user_url)
    except Exception as exc:
        logger.exception("Snapshot failed for %s", user_url)
        SnapshotRun.objects.filter(id=run_id).update(
            status=SnapshotRun.FAILED,
            finished_at=timezone.now(),
            error=(str(exc) or repr(exc)),
        )
    else:
        SnapshotRun.objects.filter(id=run_id).update(
            status=SnapshotRun.SUCCESS,
            finished_at=timezone.now(),
            snapshot_ts=snapshot_ts,
        )
    finally:
        # Close this thread's DB connection — it isn't managed by the request cycle.
        connection.close()


def start_snapshot_async(user_url, run_id):
    """Run take_snapshot in a background daemon thread so the request doesn't block."""
    thread = threading.Thread(target=_run_snapshot, args=(user_url, run_id), daemon=True)
    thread.start()
    return thread


def extract_fp_user_id(user_url):
    # You may want better validation here
    return user_url.rstrip('/').split('/')[-1]

def home(request):
    if request.method == "POST":
        user_url = request.POST.get('user_url')
        fp_user_id = extract_fp_user_id(user_url)
        return redirect('user_dashboard', fp_user_id=fp_user_id)

    # Get recent user_urls from UserSnapshot, ordered by latest snapshot
    # Use distinct user_url, order by latest snapshot_ts
    from django.db.models import Max
    recent_users = (
        UserSnapshot.objects.values('user_url')
        .annotate(latest_ts=Max('snapshot_ts'))
        .order_by('-latest_ts')[:5]
    )
    # Prepare for template: list of dicts with user_url, fp_user_id, latest_ts
    recent_user_infos = [
        {
            'user_url': u['user_url'],
            'fp_user_id': extract_fp_user_id(u['user_url']),
            'latest_ts': u['latest_ts'],
        }
        for u in recent_users
    ]
    return render(request, 'monitoring/home.html', {'recent_user_infos': recent_user_infos})



def user_dashboard(request, fp_user_id):
    user_url = f"https://factorioprints.com/user/{fp_user_id}"
    snapshots = UserSnapshot.objects.filter(user_url=user_url).order_by('-snapshot_ts')
    # Button is disabled while a snapshot was taken within the cooldown window
    snapshot_recent = is_in_cooldown(user_url)
    # Most recent run (running/success/failed) drives the status banner
    latest_run = SnapshotRun.objects.filter(user_url=user_url).first()
    snapshot_running = is_snapshot_running(user_url)

    # NEW: Get blueprints from latest snapshot
    blueprint_snapshots = []
    if snapshots:
        latest_snapshot = snapshots[0]
        blueprint_snapshots = BlueprintSnapshot.objects.filter(
            snapshot_ts=latest_snapshot.snapshot_ts
        ).order_by('name')

    # Last 10 unique comments (the full list lives on the recent_comments page)
    unique_comments = list(get_recent_unique_comments(user_url, limit=10))

    return render(request, 'monitoring/user_dashboard.html', {
        'fp_user_id': fp_user_id,
        'snapshots': snapshots,
        'user_url': user_url,
        'snapshot_recent': snapshot_recent,
        'latest_run': latest_run,
        'snapshot_running': snapshot_running,
        'blueprint_snapshots': blueprint_snapshots,
        'recent_comments': unique_comments,  # Pass unique comments to template
    })


def take_snapshot_view(request, fp_user_id):
    user_url = f"https://factorioprints.com/user/{fp_user_id}"
    # Run the scrape in a background thread (non-blocking); skip if busy / in cooldown.
    if is_snapshot_running(user_url):
        messages.warning(
            request,
            "A snapshot is already running — please wait for it to finish.",
        )
    elif is_in_cooldown(user_url):
        messages.warning(
            request,
            "Snapshot skipped — one was already taken within the last 10 minutes.",
        )
    else:
        run = SnapshotRun.objects.create(user_url=user_url, status=SnapshotRun.RUNNING)
        start_snapshot_async(user_url, run.id)
        messages.info(
            request,
            "Snapshot started in the background. It can take a few minutes — "
            "this page refreshes automatically while it runs.",
        )
    return HttpResponseRedirect(reverse('user_dashboard', args=[fp_user_id]))

def parse_csv_table(csv_string):
    """Parses CSV into list of dicts (header->value). Returns [] if error or no data."""
    if not csv_string or csv_string.startswith("No "):
        return []
    f = StringIO(csv_string)
    reader = csv.DictReader(f)
    return list(reader)

# ... in your comments_between view:
def comments_between(request, fp_user_id):
    user_url = f"https://factorioprints.com/user/{fp_user_id}"
    start = request.GET.get('start_date')
    end = request.GET.get('end_date')
    csv_result, table_rows, error_msg = None, [], None
    # If no end date, default to today
    if not end:
        from datetime import date
        end = date.today().isoformat()
    # Only call if both start and end are set
    if start and end:
        csv_result = blueprints_with_new_comments(user_url, start, end)
        if csv_result.startswith("No snapshots") or csv_result.startswith("No blueprints"):
            error_msg = csv_result
        else:
            table_rows = parse_csv_table(csv_result)
    return render(request, 'monitoring/comments_between.html', {
        'fp_user_id': fp_user_id,
        'csv_result': csv_result,
        'table_rows': table_rows,
        'error_msg': error_msg,
        'start': start,
        'end': end,
    })

def user_snapshots(request, fp_user_id):
    user_url = f"https://factorioprints.com/user/{fp_user_id}"
    snapshots = UserSnapshot.objects.filter(user_url=user_url).order_by('-snapshot_ts')
    return render(request, 'monitoring/user_snapshots.html', {
        'fp_user_id': fp_user_id,
        'snapshots': snapshots,
    })

# Page size for the full recent-comments list
RECENT_COMMENTS_PAGE_SIZE = 50

def recent_comments(request, fp_user_id):
    user_url = f"https://factorioprints.com/user/{fp_user_id}"
    comments = get_recent_unique_comments(user_url)
    paginator = Paginator(comments, RECENT_COMMENTS_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'monitoring/recent_comments.html', {
        'fp_user_id': fp_user_id,
        'page_obj': page_obj,
    })


# ---------------------------------------------------------------------------
# Inbox (new design-system pages)
# ---------------------------------------------------------------------------

INBOX_FILTERS = ('all', 'needs', 'done')
PER_PAGE_OPTIONS = (10, 25, 50)
DEFAULT_PER_PAGE = 10


def _per_page(request):
    """Validated page size from ?per_page, falling back to the default."""
    try:
        value = int(request.GET.get('per_page', DEFAULT_PER_PAGE))
    except (TypeError, ValueError):
        return DEFAULT_PER_PAGE
    return value if value in PER_PAGE_OPTIONS else DEFAULT_PER_PAGE


def shell_context(fp_user_id, user_url, active, awaiting_count=None):
    """Context shared by every app-shell page (sidebar, switcher, top bar).

    Kept in one place so new pages don't re-derive the chrome. `awaiting_count`
    is passed in by callers that already computed inbox counts, to avoid running
    that query twice.
    """
    last_snapshot = UserSnapshot.objects.filter(user_url=user_url).order_by('-snapshot_ts').first()
    latest_run = SnapshotRun.objects.filter(user_url=user_url).first()
    blueprint_count = (
        BlueprintSnapshot.objects.filter(snapshot_ts=last_snapshot.snapshot_ts).count()
        if last_snapshot else 0
    )
    monitored_users = [
        {'fp_user_id': (uid := extract_fp_user_id(row['user_url'])), 'name': uid, 'is_current': uid == fp_user_id}
        for row in UserSnapshot.objects.values('user_url').annotate(latest=Max('snapshot_ts')).order_by('-latest')
    ]
    return {
        'active_nav': active,
        'fp_user_id': fp_user_id,
        'user_url': user_url,
        'display_name': fp_user_id,  # TODO: capture the real Disqus/display name when scraping
        'last_snapshot_ts': last_snapshot.snapshot_ts if last_snapshot else None,
        'latest_run': latest_run,
        'snapshot_running': is_snapshot_running(user_url),
        'blueprint_count': blueprint_count,
        'monitored_users': monitored_users,
        'awaiting_count': awaiting_count,
    }


def inbox(request, fp_user_id):
    user_url = f"https://factorioprints.com/user/{fp_user_id}"

    status = request.GET.get('status', 'all')
    if status not in INBOX_FILTERS:
        status = 'all'
    query = request.GET.get('q', '').strip()
    per_page = _per_page(request)

    comments = get_inbox_queryset(user_url, status=status, query=query)
    paginator = Paginator(comments, per_page)
    page_obj = paginator.get_page(request.GET.get('page'))
    counts = get_inbox_counts(user_url)

    # pager links keep status/q/per_page; the per-page form keeps status/q (page resets)
    pager_hidden = {'status': status}
    if query:
        pager_hidden['q'] = query
    qs = urlencode({**pager_hidden, 'per_page': per_page})

    context = {
        'page_obj': page_obj,
        'page_range': paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
        'qs': qs,
        'pager_hidden': pager_hidden,
        'counts': counts,
        'status': status,
        'query': query,
        'per_page': per_page,
        'per_page_options': PER_PAGE_OPTIONS,
    }
    context.update(shell_context(fp_user_id, user_url, active='inbox', awaiting_count=counts['needs']))
    return render(request, 'monitoring/inbox.html', context)


@require_POST
def toggle_handled(request, fp_user_id, blueprint_id, comment_id):
    """Flip a comment's handled state. Works as a plain form POST (redirects
    back) and as a fetch() call (returns JSON) for in-place updates."""
    user_url = f"https://factorioprints.com/user/{fp_user_id}"
    # Only let a user toggle comments on their own monitored blueprints.
    if blueprint_id not in set(user_blueprint_ids(user_url)):
        return HttpResponseBadRequest("Unknown blueprint for this user.")

    status_obj, _ = CommentStatus.objects.get_or_create(blueprint_id=blueprint_id, comment_id=comment_id)
    status_obj.handled = not status_obj.handled
    status_obj.handled_at = timezone.now() if status_obj.handled else None
    status_obj.save(update_fields=['handled', 'handled_at'])

    if request.headers.get('X-Requested-With') == 'fetch':
        return JsonResponse({'handled': status_obj.handled})
    return redirect(request.POST.get('next') or reverse('inbox', args=[fp_user_id]))


@require_POST
def mark_all_done(request, fp_user_id):
    """Mark every awaiting-reply comment for this user as done (ignores the
    current filter/search — always the full outstanding set)."""
    user_url = f"https://factorioprints.com/user/{fp_user_id}"
    now = timezone.now()
    pairs = list(get_inbox_queryset(user_url, status='needs').values_list('blueprint_id', 'comment_id'))
    # Personal-scale data set, so a per-comment upsert is fine and clearer than
    # a hand-rolled bulk merge over (blueprint, comment_id) tuples.
    with transaction.atomic():
        for blueprint_id, comment_id in pairs:
            CommentStatus.objects.update_or_create(
                blueprint_id=blueprint_id,
                comment_id=comment_id,
                defaults={'handled': True, 'handled_at': now},
            )
    if pairs:
        plural = '' if len(pairs) == 1 else 's'
        messages.success(request, f"Marked {len(pairs)} comment{plural} as done.")
    return redirect(request.POST.get('next') or reverse('inbox', args=[fp_user_id]))


# ---------------------------------------------------------------------------
# Blueprints list + detail
# ---------------------------------------------------------------------------

# sort key -> how to read it off an overview row. Built so a missing/None value
# sorts low (blueprints with no comments sink under "last comment, newest first").
_MIN_DT = datetime.min.replace(tzinfo=dt_timezone.utc)
BLUEPRINT_SORTS = {
    'name': lambda r: (r['name'] or '').lower(),
    'favourites': lambda r: r['favourites'] or 0,
    'comments': lambda r: r['comments'] or 0,
    'awaiting': lambda r: r['awaiting'] or 0,
    'last': lambda r: r['last_comment_ts'] or _MIN_DT,
}


def blueprints_list(request, fp_user_id):
    user_url = f"https://factorioprints.com/user/{fp_user_id}"
    rows = get_blueprints_overview(user_url)

    sort = request.GET.get('sort', 'last')
    if sort not in BLUEPRINT_SORTS:
        sort = 'last'
    direction = 'asc' if request.GET.get('dir') == 'asc' else 'desc'
    rows.sort(key=BLUEPRINT_SORTS[sort], reverse=(direction == 'desc'))

    per_page = _per_page(request)
    paginator = Paginator(rows, per_page)
    page_obj = paginator.get_page(request.GET.get('page'))
    counts = get_inbox_counts(user_url)

    pager_hidden = {'sort': sort, 'dir': direction}
    qs = urlencode({**pager_hidden, 'per_page': per_page})

    context = {
        'page_obj': page_obj,
        'page_range': paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1),
        'qs': qs,
        'pager_hidden': pager_hidden,
        'total': len(rows),
        'sort': sort,
        'dir': direction,
        'per_page': per_page,
        'per_page_options': PER_PAGE_OPTIONS,
    }
    context.update(shell_context(fp_user_id, user_url, active='blueprints', awaiting_count=counts['needs']))
    return render(request, 'monitoring/blueprints.html', context)


def blueprint_detail(request, fp_user_id, blueprint_id):
    user_url = f"https://factorioprints.com/user/{fp_user_id}"
    if blueprint_id not in set(user_blueprint_ids(user_url)):
        raise Http404("Blueprint is not monitored for this user.")
    bp = get_object_or_404(Blueprint, id=blueprint_id)

    series = get_blueprint_series(blueprint_id)
    comments = list(get_blueprint_comments(blueprint_id))
    awaiting = sum(1 for c in comments if not c.is_handled)

    favourites = series[-1]['favourites'] if series else 0
    comments_total = series[-1]['total_comments'] if series else 0
    fav_delta = None
    if series:
        cutoff = series[-1]['snapshot_ts'] - timedelta(days=30)
        baseline = next((p for p in reversed(series) if p['snapshot_ts'] <= cutoff), None)
        if baseline:
            fav_delta = favourites - baseline['favourites']

    # ms timestamps for the client-side chart; json_script handles escaping
    chart_series = [
        {'t': int(p['snapshot_ts'].timestamp() * 1000), 'fav': p['favourites'], 'com': p['total_comments']}
        for p in series
    ]

    counts = get_inbox_counts(user_url)
    context = {
        'bp': bp,
        'favourites': favourites,
        'comments_total': comments_total,
        'fav_delta': fav_delta,
        'awaiting': awaiting,
        'comments': comments,
        'chart_series': chart_series,
    }
    context.update(shell_context(fp_user_id, user_url, active='blueprints', awaiting_count=counts['needs']))
    return render(request, 'monitoring/blueprint_detail.html', context)
