from django.core.validators import MinValueValidator
from django.db import models

class Blueprint(models.Model):
    url = models.URLField(unique=True)
    name = models.CharField(max_length=255)

    def __str__(self):
        return self.name

class UserSnapshot(models.Model):
    snapshot_ts = models.DateTimeField()
    user_url = models.URLField()

    class Meta:
        unique_together = ('snapshot_ts', 'user_url')

    def __str__(self):
        return f"{self.user_url} at {self.snapshot_ts}"

class BlueprintSnapshot(models.Model):
    snapshot_ts = models.DateTimeField()
    blueprint = models.ForeignKey(Blueprint, on_delete=models.CASCADE, related_name='snapshots')
    name = models.CharField(max_length=255)  # Name at this point in time
    favourites = models.IntegerField(validators=[MinValueValidator(0)])
    total_comments = models.IntegerField(validators=[MinValueValidator(0)])

    class Meta:
        unique_together = ('snapshot_ts', 'blueprint')

    def __str__(self):
        return f"{self.blueprint.name} at {self.snapshot_ts}"

class SnapshotRun(models.Model):
    """Tracks the lifecycle of a single snapshot attempt (status + outcome).

    A UserSnapshot row is only written on success, so this model is what makes
    a still-running or failed run observable in the UI.
    """
    RUNNING = 'running'
    SUCCESS = 'success'
    FAILED = 'failed'
    STATUS_CHOICES = [
        (RUNNING, 'Running'),
        (SUCCESS, 'Success'),
        (FAILED, 'Failed'),
    ]

    user_url = models.URLField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    snapshot_ts = models.DateTimeField(null=True, blank=True)  # set on success
    error = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"SnapshotRun({self.user_url}, {self.status}, started {self.started_at})"

class CommentSnapshot(models.Model):
    snapshot_ts = models.DateTimeField()
    blueprint = models.ForeignKey(Blueprint, on_delete=models.CASCADE, related_name='comment_snapshots')
    comment_id = models.CharField(max_length=50)
    author = models.CharField(max_length=100)
    created_utc = models.DateTimeField()
    message_text = models.TextField()

    class Meta:
        unique_together = ('snapshot_ts', 'blueprint', 'comment_id')

    def __str__(self):
        return f"Comment {self.comment_id} by {self.author} at {self.snapshot_ts}"


class CommentStatus(models.Model):
    """Per-comment 'handled' state for the inbox.

    Handled-ness belongs to a *comment identity* - (blueprint, comment_id) -
    not to any single snapshot. The same comment is re-captured in every
    CommentSnapshot, so storing the flag on a snapshot row would make a handled
    comment reappear as unhandled after the next scrape. This small side table
    keeps the state stable across snapshots. A row exists only once a comment
    has been toggled; absence means "not handled".
    """
    blueprint = models.ForeignKey(Blueprint, on_delete=models.CASCADE, related_name='comment_statuses')
    comment_id = models.CharField(max_length=50)
    handled = models.BooleanField(default=False)
    handled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('blueprint', 'comment_id')
        verbose_name_plural = 'comment statuses'

    def __str__(self):
        return f"CommentStatus({self.blueprint_id}/{self.comment_id}, handled={self.handled})"


class UserSettings(models.Model):
    """Per-user preferences (one row per monitored user_url).

    - disqus_name: the name the user replies under, for future reply-detection.
    - alerts_enabled / alert_email: email-alert configuration (the sending is
      the separate "email alerts" feature; this just stores the config).
    """
    user_url = models.URLField(unique=True)
    display_name = models.CharField(max_length=100, blank=True, default='')  # friendly name shown across the app
    disqus_name = models.CharField(max_length=100, blank=True, default='')   # matches your own comments (for "(you)")
    alerts_enabled = models.BooleanField(default=False)
    alert_email = models.EmailField(blank=True, default='')

    class Meta:
        verbose_name_plural = 'user settings'

    def __str__(self):
        return f"UserSettings({self.user_url})"
