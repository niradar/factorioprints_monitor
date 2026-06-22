"""Email alerts for new comments found by a snapshot.

`send_new_comment_alert()` is called at the end of `utils.take_snapshot()` for
both the web and scheduled paths. It's best-effort: it never raises, so a mail
failure can't break a snapshot.
"""
import logging
from collections import OrderedDict

from django.conf import settings as dj_settings
from django.core.mail import send_mail

from .models import UserSnapshot, CommentSnapshot, UserSettings

logger = logging.getLogger(__name__)


def new_comments_in_snapshot(user_url, snapshot_ts):
    """CommentSnapshots first seen at `snapshot_ts` (vs the previous snapshot),
    excluding the user's own comments, oldest first.

    Empty on the first-ever snapshot - that one is just a baseline, so we don't
    alert on the whole comment history.
    """
    prev = (
        UserSnapshot.objects
        .filter(user_url=user_url, snapshot_ts__lt=snapshot_ts)
        .order_by('-snapshot_ts').first()
    )
    if not prev:
        return []

    prev_keys = set(
        CommentSnapshot.objects.filter(snapshot_ts=prev.snapshot_ts)
        .values_list('blueprint_id', 'comment_id')
    )
    settings_obj = UserSettings.objects.filter(user_url=user_url).first()
    own = settings_obj.disqus_name.strip().lower() if settings_obj and settings_obj.disqus_name else ''

    fresh = []
    for c in CommentSnapshot.objects.filter(snapshot_ts=snapshot_ts).select_related('blueprint'):
        if (c.blueprint_id, c.comment_id) in prev_keys:
            continue
        if own and (c.author or '').strip().lower() == own:
            continue
        fresh.append(c)
    fresh.sort(key=lambda c: c.created_utc)
    return fresh


def _build_email(settings_obj, user_url, fresh):
    n = len(fresh)
    plural = '' if n == 1 else 's'
    who = settings_obj.display_name or 'your blueprints'
    subject = f"{n} new comment{plural} on {who}"

    lines = [f"{n} new comment{plural} on your factorioprints blueprints:"]
    groups = OrderedDict()
    for c in fresh:
        groups.setdefault((c.blueprint.name, c.blueprint.url), []).append(c)
    for (bp_name, bp_url), comments in groups.items():
        lines.append(f"\n{bp_name}  ({bp_url})")
        for c in comments:
            text = ' '.join((c.message_text or '').split())
            if len(text) > 140:
                text = text[:137] + '...'
            lines.append(f"  - {c.author}: {text}")

    fp_user_id = user_url.rstrip('/').split('/')[-1]
    base = getattr(dj_settings, 'ALERT_BASE_URL', 'http://localhost:8129').rstrip('/')
    lines.append(f"\nReply in your inbox: {base}/user/{fp_user_id}/inbox/?status=needs")
    return subject, "\n".join(lines)


def send_new_comment_alert(user_url, snapshot_ts):
    """Email the configured address about comments new in this snapshot, if alerts
    are enabled and there are any. Best-effort - logs and swallows all errors."""
    try:
        settings_obj = UserSettings.objects.filter(user_url=user_url).first()
        if not settings_obj or not settings_obj.alerts_enabled or not settings_obj.alert_email:
            return
        fresh = new_comments_in_snapshot(user_url, snapshot_ts)
        if not fresh:
            return
        subject, body = _build_email(settings_obj, user_url, fresh)
        send_mail(subject, body, None, [settings_obj.alert_email])
        logger.info("Sent new-comment alert (%d) to %s", len(fresh), settings_obj.alert_email)
    except Exception:
        logger.exception("Failed to send new-comment alert for %s", user_url)
