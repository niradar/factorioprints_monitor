# monitoring/management/commands/snapshot_all.py

import logging
import sys

from django.core.management.base import BaseCommand

from monitoring.utils import monitored_user_urls, take_snapshot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Take a snapshot for every monitored FactorioPrints account. Intended "
        "for a single scheduled task - accounts are scanned sequentially and one "
        "account failing does not stop the others."
    )

    def handle(self, *args, **options):
        urls = monitored_user_urls()
        if not urls:
            self.stdout.write(self.style.WARNING("No monitored accounts found - nothing to do."))
            return

        self.stdout.write(f"Scanning {len(urls)} account(s): {', '.join(urls)}")
        failures = []
        for user_url in urls:
            try:
                snapshot_ts = take_snapshot(user_url)
                self.stdout.write(self.style.SUCCESS(
                    f"OK  {user_url} -> snapshot {snapshot_ts.isoformat()}"
                ))
            except Exception as exc:  # one bad account must not abort the rest
                logger.exception("Snapshot failed for %s", user_url)
                failures.append((user_url, exc))
                self.stderr.write(self.style.ERROR(f"FAIL {user_url}: {exc}"))

        if failures:
            self.stderr.write(self.style.ERROR(
                f"{len(failures)} of {len(urls)} account(s) failed."
            ))
            sys.exit(1)  # non-zero so Task Scheduler's "Last Run Result" flags it
        self.stdout.write(self.style.SUCCESS(f"All {len(urls)} account(s) snapshotted."))
