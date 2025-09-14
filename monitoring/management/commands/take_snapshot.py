# monitoring/management/commands/take_snapshot.py

from django.core.management.base import BaseCommand
from monitoring.utils import take_snapshot
import  logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = "Take a snapshot of all blueprints and comments for a FactorioPrints user."

    def add_arguments(self, parser):
        parser.add_argument('--user-url', required=True, help='FactorioPrints user URL')

    def handle(self, *args, **options):
        user_url = options['user_url']
        snapshot_ts = take_snapshot(user_url)
        self.stdout.write(self.style.SUCCESS(f"Snapshot stored at {snapshot_ts.isoformat()}"))
