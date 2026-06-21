# views.py
import logging
import threading

from django.shortcuts import render, redirect
from .models import UserSnapshot, BlueprintSnapshot, SnapshotRun
from .utils import take_snapshot, blueprints_with_new_comments, get_recent_unique_comments
from urllib.parse import urlparse

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import connection
from django.http import HttpResponseRedirect
from django.urls import reverse
import csv
from io import StringIO
from datetime import timedelta
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
