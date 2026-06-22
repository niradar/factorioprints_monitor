from django.contrib import admin

# Register your models here.

from monitoring.models import (
    UserSnapshot, Blueprint, BlueprintSnapshot, CommentSnapshot, SnapshotRun,
    CommentStatus, UserSettings,
)

@admin.register(UserSnapshot)
class UserSnapshotAdmin(admin.ModelAdmin):
    list_display = ('user_url', 'snapshot_ts')
    search_fields = ('user_url',)
    ordering = ('-snapshot_ts',)

@admin.register(Blueprint)
class BlueprintAdmin(admin.ModelAdmin):
    list_display = ('name', 'url')
    search_fields = ('name', 'url')
    ordering = ('name',)

@admin.register(BlueprintSnapshot)
class BlueprintSnapshotAdmin(admin.ModelAdmin):
    list_display = ('blueprint', 'snapshot_ts', 'name', 'favourites', 'total_comments')
    search_fields = ('blueprint__name', 'name')
    ordering = ('-snapshot_ts',)

@admin.register(CommentSnapshot)
class CommentSnapshotAdmin(admin.ModelAdmin):
    list_display = ('blueprint', 'comment_id', 'author', 'created_utc', 'snapshot_ts')
    search_fields = ('blueprint__name', 'author', 'comment_id')
    ordering = ('-snapshot_ts',)

@admin.register(SnapshotRun)
class SnapshotRunAdmin(admin.ModelAdmin):
    list_display = ('user_url', 'status', 'started_at', 'finished_at', 'snapshot_ts')
    list_filter = ('status',)
    search_fields = ('user_url',)
    ordering = ('-started_at',)

@admin.register(UserSettings)
class UserSettingsAdmin(admin.ModelAdmin):
    list_display = ('user_url', 'disqus_name', 'alerts_enabled', 'alert_email')
    list_filter = ('alerts_enabled',)
    search_fields = ('user_url', 'disqus_name', 'alert_email')

@admin.register(CommentStatus)
class CommentStatusAdmin(admin.ModelAdmin):
    list_display = ('blueprint', 'comment_id', 'handled', 'handled_at')
    list_filter = ('handled',)
    search_fields = ('blueprint__name', 'comment_id')
    ordering = ('-handled_at',)
