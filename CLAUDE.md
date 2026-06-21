# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django web app that monitors user blueprints on factorioprints.com and tracks new comments. Uses Playwright for headless browser scraping and SQLite for storage. Snapshots triggered from the web UI run in a background thread; scheduled snapshots are driven externally (e.g. Windows Task Scheduler) via the `take_snapshot` management command.

## Common Commands

```bash
# Install dependencies
uv sync
uv sync --extra dev          # includes pytest, pytest-mock
playwright install            # install browser binaries for Playwright

# Run Django dev server (port 8129)
run_server.bat                # Windows
python manage.py runserver 0.0.0.0:8129   # manual

# Database
python manage.py migrate
python manage.py makemigrations monitoring

# Tests
python manage.py test monitoring

# Management commands
python manage.py take_snapshot --user-url "https://factorioprints.com/user/<USER_ID>"
python manage.py list_snapshots [--user-url "..."]
python manage.py latest_blueprints --user-url "..."
python manage.py delete_snapshot --timestamp "2025-06-05T08:00:00+00:00"
python manage.py blueprints_with_new_comments --user-url "..." --start-date "2025-06-01" --end-date "2025-06-05"
```

Set `DJANGO_SETTINGS_MODULE=factorioprints_monitor.settings` when running outside batch scripts.

### Scheduled snapshots (Windows Task Scheduler)

`run_snapshot.bat "<user_url>"` takes one snapshot via the management command (no web server required — it writes directly to SQLite). Register it to run on a schedule:

```bat
schtasks /Create /SC DAILY /ST 08:00 /TN "FactorioPrintsSnapshot" ^
  /TR "\"C:\projects\factorioprints_monitor\run_snapshot.bat\" https://factorioprints.com/user/<USER_ID>"
```

In Task Scheduler, enable "Run task as soon as possible after a scheduled start is missed" so a snapshot still runs when the PC was off at the scheduled time.

## Architecture

### Django Project Layout

- `factorioprints_monitor/` — Django project config (settings, urls, wsgi)
- `monitoring/` — Single Django app containing all application logic

### Data Model (Snapshot-based design)

All data is captured as point-in-time snapshots to track changes over time:

- **Blueprint** — Canonical blueprint record (`url` unique). Referenced by snapshots.
- **UserSnapshot** — Records when a user's data was scraped. Unique on `(snapshot_ts, user_url)`. Written only on a successful run.
- **BlueprintSnapshot** — Per-blueprint data at a snapshot time (name, favourites, total_comments). Unique on `(snapshot_ts, blueprint)`.
- **CommentSnapshot** — Individual comment captured at snapshot time. Unique on `(snapshot_ts, blueprint, comment_id)`.
- **SnapshotRun** — Lifecycle of a single snapshot attempt: `status` (running/success/failed), `started_at`, `finished_at`, `snapshot_ts` (set on success), `error`. Makes a still-running or failed run observable in the UI/admin.

### Scraping Pipeline

1. `blueprints_scraper.py` — Playwright sync API. Scrolls the user's page to lazy-load all blueprint cards, extracts name/url/favorites.
2. `comments_scraper.py` — Playwright async API. Loads each blueprint page, waits for Disqus iframe, extracts `threadData` JSON. Has `get_comments_async()` for concurrent use.
3. `utils.py:take_snapshot()` — Orchestrates the full pipeline: scrapes blueprints, fetches comments concurrently (capped by `SNAPSHOT_MAX_CONCURRENCY`, default 6 via `asyncio.Semaphore`), then stores everything in a single `transaction.atomic()` block.

### Async Task Flow

The web UI triggers snapshots in a background thread:
1. User clicks "Take New Snapshot" → `views.take_snapshot_view()` checks `is_snapshot_running()` and `is_in_cooldown()`; if clear it creates a `SnapshotRun` (status=running) and calls `start_snapshot_async(user_url, run.id)`
2. `start_snapshot_async` runs `take_snapshot()` (from `utils.py`) in a daemon thread (`_run_snapshot`) so the request returns immediately; on completion it sets the `SnapshotRun` to success (+`snapshot_ts`) or failed (+`error`), and logs failures
3. A 1-hour cooldown (`SNAPSHOT_COOLDOWN` in `views.py`) is enforced server-side; `is_snapshot_running` also blocks overlapping runs (RUNNING rows older than the cooldown are treated as stale). The dashboard shows a status banner and auto-refreshes every 10s while a run is in progress
4. Only the web path records `SnapshotRun` rows; the `take_snapshot` management command (used by Task Scheduler) just runs the scrape directly

### URL Routes

All routes are under `monitoring/urls.py`:
- `/` — Home page with user URL input
- `/user/<fp_user_id>/` — User dashboard (blueprints table, recent comments)
- `/user/<fp_user_id>/snapshot/` — Trigger async snapshot
- `/user/<fp_user_id>/comments/` — CSV of blueprints with new comments between dates
- `/user/<fp_user_id>/snapshots/` — All snapshots for a user

### Templates

Templates are in `monitoring/templates/monitoring/`. `base.html` provides shared layout and a `makeTableSortable()` JS utility used across pages.
