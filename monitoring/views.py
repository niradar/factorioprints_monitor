# views.py
from django.shortcuts import render, redirect
from .models import UserSnapshot, BlueprintSnapshot
from .utils import take_snapshot, blueprints_with_new_comments
from urllib.parse import urlparse

from django.http import HttpResponseRedirect
from django.urls import reverse
import csv
from io import StringIO
from datetime import timedelta
from django.utils import timezone


def extract_fp_user_id(user_url):
    # You may want better validation here
    return user_url.rstrip('/').split('/')[-1]

def home(request):
    if request.method == "POST":
        user_url = request.POST.get('user_url')
        fp_user_id = extract_fp_user_id(user_url)
        # Optionally: save initial UserSnapshot or "watched user" in DB if not exists
        return redirect('user_dashboard', fp_user_id=fp_user_id)
    return render(request, 'monitoring/home.html')



def user_dashboard(request, fp_user_id):
    user_url = f"https://factorioprints.com/user/{fp_user_id}"
    snapshots = UserSnapshot.objects.filter(user_url=user_url).order_by('-snapshot_ts')
    now = timezone.now()
    cutoff_time = now - timedelta(hours=1)
    # Button is disabled if any snapshot newer than cutoff_time exists
    snapshot_recent = snapshots and snapshots[0].snapshot_ts > cutoff_time
    
    # NEW: Get blueprints from latest snapshot
    blueprint_snapshots = []
    if snapshots:
        latest_snapshot = snapshots[0]
        blueprint_snapshots = BlueprintSnapshot.objects.filter(
            snapshot_ts=latest_snapshot.snapshot_ts
        ).order_by('name')
    
    return render(request, 'monitoring/user_dashboard.html', {
        'fp_user_id': fp_user_id,
        'snapshots': snapshots,
        'user_url': user_url,
        'cutoff_time': cutoff_time,
        'snapshot_recent': snapshot_recent,
        'blueprint_snapshots': blueprint_snapshots,  # NEW: Pass to template
    })


from .tasks import take_snapshot_task
def take_snapshot_view(request, fp_user_id):
    user_url = f"https://factorioprints.com/user/{fp_user_id}"
    # Launch async Celery task!
    task = take_snapshot_task.delay(user_url)
    # Optionally: Store the Celery task_id somewhere if you want to poll its status
    return HttpResponseRedirect(reverse('user_dashboard', args=[fp_user_id]))

# Uncomment this if you want to use a synchronous version (not recommended for production)
# def take_snapshot_view(request, fp_user_id):
#     user_url = f"https://factorioprints.com/user/{fp_user_id}"
#     # For MVP, call synchronously; in production, trigger Celery
#     from .utils import take_snapshot as do_snapshot
#     do_snapshot(user_url)
#     return HttpResponseRedirect(reverse('user_dashboard', args=[fp_user_id]))

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
