from django.core.management.base import BaseCommand, CommandError
from monitoring.utils import blueprints_with_new_comments

class Command(BaseCommand):
    help = "List all blueprints for a user that received new comments between two dates, as CSV."

    def add_arguments(self, parser):
        parser.add_argument('--user-url', required=True, help='FactorioPrints user URL')
        parser.add_argument('--start-date', required=True, help='Start date (YYYY-MM-DD)')
        parser.add_argument('--end-date', required=True, help='End date (YYYY-MM-DD)')

    def handle(self, *args, **options):
        user_url = options['user_url']
        start_date = options['start_date']
        end_date = options['end_date']
        try:
            csv_result = blueprints_with_new_comments(user_url, start_date, end_date)
        except Exception as e:
            raise CommandError(f"Error: {e}")
        self.stdout.write(csv_result)
