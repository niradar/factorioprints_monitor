from django.core.management.base import BaseCommand, CommandError
from monitoring.models import UserSnapshot, BlueprintSnapshot, CommentSnapshot
from django.db import transaction
from datetime import datetime, timezone

class Command(BaseCommand):
    help = "Delete a snapshot and all its associated data (by timestamp, in ISO-8601 format)"

    def add_arguments(self, parser):
        parser.add_argument('--timestamp', required=True, help='Snapshot timestamp to delete (ISO-8601, e.g. 2025-06-05T08:00:00+00:00)')

    def handle(self, *args, **options):
        ts_str = options['timestamp']
        try:
            snapshot_ts = datetime.fromisoformat(ts_str)
            if snapshot_ts.tzinfo is None:
                snapshot_ts = snapshot_ts.replace(tzinfo=timezone.utc)
        except Exception:
            raise CommandError("Invalid timestamp format. Use ISO-8601 (e.g. 2025-06-05T08:00:00+00:00)")

        with transaction.atomic():
            deleted_comments = CommentSnapshot.objects.filter(snapshot_ts=snapshot_ts).delete()[0]
            deleted_blueprints = BlueprintSnapshot.objects.filter(snapshot_ts=snapshot_ts).delete()[0]
            deleted_users = UserSnapshot.objects.filter(snapshot_ts=snapshot_ts).delete()[0]

        self.stdout.write(self.style.SUCCESS(
            f"Deleted snapshot at {snapshot_ts.isoformat()}: "
            f"{deleted_comments} comments, {deleted_blueprints} blueprints, {deleted_users} users"
        ))
