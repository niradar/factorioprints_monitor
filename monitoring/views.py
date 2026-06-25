# views.py
import logging
import threading

from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings as django_settings
from django.core.mail import send_mail
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from .models import UserSnapshot, BlueprintSnapshot, SnapshotRun, CommentStatus, Blueprint, UserSettings
from .utils import (
    take_snapshot,
    get_inbox_queryset,
    get_inbox_counts,
    user_blueprint_ids,
    get_blueprints_overview,
    get_blueprint_comments,
    get_blueprint_series,
)
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Max
from django.http import HttpResponseRedirect, JsonResponse, HttpResponseBadRequest, Http404
from django.urls import reverse
from django.views.decorators.http import require_POST
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
        # Close this thread's DB connection - it isn't managed by the request cycle.
        connection.close()


def start_snapshot_async(user_url, run_id):
    """Run take_snapshot in a background daemon thread so the request doesn't block."""
    thread = threading.Thread(target=_run_snapshot, args=(user_url, run_id), daemon=True)
    thread.start()
    return thread


def extract_fp_user_id(user_url):
    # You may want better validation here
    return user_url.rstrip('/').split('/')[-1]


def landing(request):
    """Entry point (`/`). New install (no users) → onboarding form; otherwise
    → the most recently active user's inbox. `?add=1` forces the form so you can
    add another user even when one already exists."""
    if request.method == 'POST':
        user_url = request.POST.get('user_url', '').strip()
        if user_url:
            return redirect('inbox', fp_user_id=extract_fp_user_id(user_url))
        error = "Paste your factorioprints user URL."
    else:
        error = None
        if 'add' not in request.GET:
            last = UserSnapshot.objects.order_by('-snapshot_ts').first()
            if last:
                return redirect('inbox', fp_user_id=extract_fp_user_id(last.user_url))

    last = UserSnapshot.objects.order_by('-snapshot_ts').first()
    context = {'error': error}
    if last:
        last_settings = UserSettings.objects.filter(user_url=last.user_url).first()
        context['last_user'] = extract_fp_user_id(last.user_url)
        context['last_user_name'] = (
            last_settings.display_name if last_settings and last_settings.display_name
            else extract_fp_user_id(last.user_url)
        )
    return render(request, 'monitoring/landing.html', context)


def take_snapshot_view(request, fp_user_id):
    user_url = f"https://factorioprints.com/user/{fp_user_id}"
    # Run the scrape in a background thread (non-blocking); skip if busy / in cooldown.
    if is_snapshot_running(user_url):
        started, level, msg = False, messages.WARNING, "A snapshot is already running - please wait for it to finish."
    elif is_in_cooldown(user_url):
        started, level, msg = False, messages.WARNING, "Snapshot skipped - one was already taken within the last 10 minutes."
    else:
        run = SnapshotRun.objects.create(user_url=user_url, status=SnapshotRun.RUNNING)
        start_snapshot_async(user_url, run.id)
        started, level, msg = True, messages.INFO, "Snapshot started in the background. It can take a few minutes."

    # AJAX path (new shell): the poller takes over; no navigation.
    if request.headers.get('X-Requested-With') == 'fetch':
        return JsonResponse({'started': started, 'running': is_snapshot_running(user_url), 'message': msg})

    # No-JS fallback: flash a message and return to where we came from.
    messages.add_message(request, level, msg)
    return HttpResponseRedirect(request.POST.get('next') or reverse('inbox', args=[fp_user_id]))


def snapshot_status(request, fp_user_id):
    """Lightweight JSON for the client-side poller: is a snapshot running, and
    since when. When it stops, the client reloads and the server re-renders the
    final state (last scan / failure / cooldown)."""
    user_url = f"https://factorioprints.com/user/{fp_user_id}"
    latest_run = SnapshotRun.objects.filter(user_url=user_url).first()
    running = is_snapshot_running(user_url)
    return JsonResponse({
        'running': running,
        'started_at': latest_run.started_at.isoformat() if latest_run and running else None,
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
    settings_obj = UserSettings.objects.filter(user_url=user_url).first()
    # the friendly name the user chose (falls back to the raw id if unset)
    owner_display = settings_obj.display_name if settings_obj else ''
    display_name = owner_display or fp_user_id
    # their Disqus name, lowercased, used to spot their own comments → "(you)"
    owner_disqus = (settings_obj.disqus_name.strip().lower() if settings_obj and settings_obj.disqus_name else '')

    last_snapshot = UserSnapshot.objects.filter(user_url=user_url).order_by('-snapshot_ts').first()
    latest_run = SnapshotRun.objects.filter(user_url=user_url).first()
    blueprint_count = (
        BlueprintSnapshot.objects.filter(snapshot_ts=last_snapshot.snapshot_ts).count()
        if last_snapshot else 0
    )
    # each monitored user shows its own display name (if set), not just the current one
    display_by_url = dict(
        UserSettings.objects.exclude(display_name='').values_list('user_url', 'display_name')
    )
    monitored_users = [
        {
            'fp_user_id': (uid := extract_fp_user_id(row['user_url'])),
            'name': display_by_url.get(row['user_url']) or uid,
            'is_current': uid == fp_user_id,
        }
        for row in UserSnapshot.objects.values('user_url').annotate(latest=Max('snapshot_ts')).order_by('-latest')
    ]
    return {
        'active_nav': active,
        'fp_user_id': fp_user_id,
        'user_url': user_url,
        'display_name': display_name,
        'owner_display': owner_display,
        'owner_disqus': owner_disqus,
        'last_snapshot_ts': last_snapshot.snapshot_ts if last_snapshot else None,
        'latest_run': latest_run,
        'snapshot_running': is_snapshot_running(user_url),
        'snapshot_recent': is_in_cooldown(user_url),
        'blueprint_count': blueprint_count,
        'monitored_users': monitored_users,
        'awaiting_count': awaiting_count,
    }


def _mark_ownership(comments, owner_disqus, owner_display):
    """Tag each comment: is it the owner's, and what name to show. The owner's
    own Disqus name is masked behind their chosen display name (privacy)."""
    for c in comments:
        c.is_mine = bool(owner_disqus and (c.author or '').strip().lower() == owner_disqus)
        c.display_author = owner_display if (c.is_mine and owner_display) else c.author
    return comments


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

    shell = shell_context(fp_user_id, user_url, active='inbox', awaiting_count=counts['needs'])
    page_obj.object_list = _mark_ownership(list(page_obj.object_list), shell['owner_disqus'], shell['owner_display'])

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
    context.update(shell)
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
    current filter/search - always the full outstanding set)."""
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
    # None (no baseline for the window) sorts below every real delta.
    'fav_delta': lambda r: r['fav_delta'] if r['fav_delta'] is not None else float('-inf'),
    'comments': lambda r: r['comments'] or 0,
    'awaiting': lambda r: r['awaiting'] or 0,
    'last': lambda r: r['last_comment_ts'] or _MIN_DT,
}

# Δ window options for the Blueprints "new favourites" switcher: label -> days.
BLUEPRINT_WINDOWS = {'today': 1, '7d': 7, '30d': 30}
# Column index of each sort key in the rendered table, so the client-side pager
# can pick up the server's initial sort instead of hard-coding its own default.
BLUEPRINT_SORT_COL = {'name': 0, 'favourites': 1, 'fav_delta': 2, 'comments': 3, 'awaiting': 4, 'last': 5}


def blueprints_list(request, fp_user_id):
    user_url = f"https://factorioprints.com/user/{fp_user_id}"

    # "New favourites" window: which past snapshot the Δ column is measured against.
    window = request.GET.get('window', '7d')
    if window not in BLUEPRINT_WINDOWS:
        window = '7d'
    rows, baseline_ts = get_blueprints_overview(user_url, baseline_days=BLUEPRINT_WINDOWS[window])

    # Server sort gives the initial order (and a no-JS fallback). The page renders
    # every blueprint, so the browser re-sorts instantly on header click - no
    # round-trip, no pagination needed at this scale.
    sort = request.GET.get('sort', 'last')
    if sort not in BLUEPRINT_SORTS:
        sort = 'last'
    direction = 'asc' if request.GET.get('dir') == 'asc' else 'desc'
    rows.sort(key=BLUEPRINT_SORTS[sort], reverse=(direction == 'desc'))

    # One-line "what you gained" summary for the chosen window.
    gained = sum(r['fav_delta'] for r in rows if r['fav_delta'] and r['fav_delta'] > 0)
    movers = sum(1 for r in rows if r['fav_delta'] and r['fav_delta'] > 0)

    counts = get_inbox_counts(user_url)
    context = {
        'rows': rows,
        'total': len(rows),
        'sort': sort,
        'dir': direction,
        'window': window,
        'baseline_ts': baseline_ts,
        'gained': gained,
        'movers': movers,
        'sort_col': BLUEPRINT_SORT_COL[sort],
        'sort_asc': direction == 'asc',
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

    # Chart data (ms timestamps; json_script handles escaping). Favourites has no
    # real history - only what each snapshot saw - so it's plotted per snapshot.
    # Comments DO have a real post date, so they're plotted cumulatively by when
    # each was posted; this is correct even from a single snapshot.
    fav_series = [
        {'t': int(p['snapshot_ts'].timestamp() * 1000), 'v': p['favourites']}
        for p in series
    ]
    com_series = [
        {'t': int(c.created_utc.timestamp() * 1000), 'v': i + 1}
        for i, c in enumerate(sorted(comments, key=lambda c: c.created_utc))
    ]
    chart_data = {'fav': fav_series, 'com': com_series}

    counts = get_inbox_counts(user_url)
    shell = shell_context(fp_user_id, user_url, active='blueprints', awaiting_count=counts['needs'])
    _mark_ownership(comments, shell['owner_disqus'], shell['owner_display'])

    context = {
        'bp': bp,
        'favourites': favourites,
        'comments_total': comments_total,
        'fav_delta': fav_delta,
        'awaiting': awaiting,
        'comments': comments,
        'chart_data': chart_data,
    }
    context.update(shell)
    return render(request, 'monitoring/blueprint_detail.html', context)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def settings_page(request, fp_user_id):
    user_url = f"https://factorioprints.com/user/{fp_user_id}"
    obj, _ = UserSettings.objects.get_or_create(user_url=user_url)

    if request.method == 'POST':
        obj.display_name = request.POST.get('display_name', '').strip()
        obj.disqus_name = request.POST.get('disqus_name', '').strip()
        obj.alerts_enabled = request.POST.get('alerts_enabled') == 'on'
        obj.alert_email = request.POST.get('alert_email', '').strip()

        action = request.POST.get('action', 'save')  # "save" or "test"

        error = None
        if obj.alerts_enabled and not obj.alert_email:
            error = "Enter an email address to receive alerts."
        elif obj.alert_email:
            try:
                validate_email(obj.alert_email)
            except ValidationError:
                error = "That email address doesn't look valid."
        if not error and action == 'test' and not obj.alert_email:
            error = "Enter an email address to send a test."

        if error:
            # re-render with the submitted (unsaved) values so nothing is lost
            messages.error(request, error)
        else:
            # Test saves first (so the email you're testing is the one kept) and
            # then sends - which is why unsaved fields no longer get cleared.
            obj.save()
            if action == 'test':
                try:
                    send_mail(
                        subject="FP Monitor - test alert",
                        message=f"This is a test alert for {fp_user_id}. If you got this, email alerts are wired up.",
                        from_email=None,  # uses DEFAULT_FROM_EMAIL
                        recipient_list=[obj.alert_email],
                    )
                    messages.success(request, f"Settings saved. Test email sent to {obj.alert_email} (dev: printed to the server console).")
                except Exception as exc:
                    messages.error(request, f"Settings saved, but the test email failed: {exc}")
            else:
                messages.success(request, "Settings saved.")
            return redirect('settings', fp_user_id=fp_user_id)

    counts = get_inbox_counts(user_url)
    context = {
        'settings_obj': obj,
        'project_dir': str(django_settings.BASE_DIR),
    }
    context.update(shell_context(fp_user_id, user_url, active='settings', awaiting_count=counts['needs']))
    return render(request, 'monitoring/settings.html', context)


def remove_account(request, fp_user_id):
    """Stop monitoring an account: delete all of its data, then go to the
    landing route (which routes to another account, or onboarding if none)."""
    user_url = f"https://factorioprints.com/user/{fp_user_id}"
    if request.method != 'POST':
        return redirect('settings', fp_user_id=fp_user_id)
    if is_snapshot_running(user_url):
        messages.warning(request, "A snapshot is running for this account - wait for it to finish, then remove.")
        return redirect('settings', fp_user_id=fp_user_id)

    from .utils import delete_user_account
    delete_user_account(user_url)
    messages.success(request, f"Removed {fp_user_id} and all of its data.")
    return redirect('home')


def about(request, fp_user_id):
    user_url = f"https://factorioprints.com/user/{fp_user_id}"
    counts = get_inbox_counts(user_url)
    context = {'repo_url': 'https://github.com/niradar/factorioprints_monitor'}
    context.update(shell_context(fp_user_id, user_url, active='about', awaiting_count=counts['needs']))
    return render(request, 'monitoring/about.html', context)
