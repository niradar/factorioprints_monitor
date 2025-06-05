from django.contrib import admin

# Register your models here.

from monitoring.models import UserSnapshot, Blueprint, BlueprintSnapshot, CommentSnapshot

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
