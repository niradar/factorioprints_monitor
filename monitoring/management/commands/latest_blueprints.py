# monitoring/management/commands/latest_blueprints.py

from django.core.management.base import BaseCommand
from monitoring.utils import get_latest_blueprints

class Command(BaseCommand):
    help = "Get all blueprints for the latest snapshot of a user."

    def add_arguments(self, parser):
        parser.add_argument('--user-url', required=True, help='FactorioPrints user URL')

    def handle(self, *args, **options):
        user_url = options['user_url']
        blueprints = get_latest_blueprints(user_url)
        for bp_snapshot in blueprints:
            self.stdout.write(bp_snapshot.blueprint.url)
