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
