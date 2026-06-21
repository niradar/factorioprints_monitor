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
