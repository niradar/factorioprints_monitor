"""Template helpers for the inbox / app-shell pages."""
from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def smart_time(dt):
    """Hybrid timestamp: relative for fresh comments, absolute date for old ones.

    `2h ago` / `3d ago` within the last week, then `Jun 5`. Written without
    platform-specific strftime codes (no %-d) so it works on Windows too.
    """
    if not dt:
        return ''
    now = timezone.now()
    seconds = (now - dt).total_seconds()
    if seconds < 60:
        return 'just now'
    if seconds < 3600:
        return f'{int(seconds // 60)}m ago'
    if seconds < 86400:
        return f'{int(seconds // 3600)}h ago'
    if seconds < 7 * 86400:
        return f'{int(seconds // 86400)}d ago'
    # older than a week → absolute date; add the year only for past years
    if dt.year == now.year:
        return f'{dt:%b} {dt.day}'
    return f'{dt:%b} {dt.day}, {dt.year}'
