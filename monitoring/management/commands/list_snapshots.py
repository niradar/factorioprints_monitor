# monitoring/management/commands/list_snapshots.py

from django.core.management.base import BaseCommand
from monitoring.utils import list_snapshots

class Command(BaseCommand):
    help = "List all available snapshot timestamps (optionally filtered by user URL)."

    def add_arguments(self, parser):
        parser.add_argument('--user-url', help='(Optional) Filter by user URL')

    def handle(self, *args, **options):
        user_url = options.get('user_url')
        times = list_snapshots(user_url)
        for ts in times:
            self.stdout.write(ts.isoformat())
