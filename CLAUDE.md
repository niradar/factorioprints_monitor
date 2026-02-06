# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django web app that monitors user blueprints on factorioprints.com and tracks new comments. Uses Playwright for headless browser scraping, Celery + RabbitMQ for async task execution, and SQLite for storage.

## Common Commands

```bash
# Install dependencies
uv sync
uv sync --extra dev          # includes pytest, pytest-mock
playwright install            # install browser binaries for Playwright

# Run Django dev server (port 8129)
run_server.bat                # Windows
python manage.py runserver 0.0.0.0:8129   # manual

# Run Celery worker
run_celery.bat                # Windows (uses `python manage.py celery`)
celery -A factorioprints_monitor worker -l info   # manual

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

RabbitMQ prerequisite: `docker run -d --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:management`

## Architecture

### Django Project Layout

- `factorioprints_monitor/` — Django project config (settings, urls, celery, wsgi)
- `monitoring/` — Single Django app containing all application logic

### Data Model (Snapshot-based design)

All data is captured as point-in-time snapshots to track changes over time:

- **Blueprint** — Canonical blueprint record (`url` unique). Referenced by snapshots.
- **UserSnapshot** — Records when a user's data was scraped. Unique on `(snapshot_ts, user_url)`.
- **BlueprintSnapshot** — Per-blueprint data at a snapshot time (name, favourites, total_comments). Unique on `(snapshot_ts, blueprint)`.
- **CommentSnapshot** — Individual comment captured at snapshot time. Unique on `(snapshot_ts, blueprint, comment_id)`.

### Scraping Pipeline

1. `blueprints_scraper.py` — Playwright sync API. Scrolls the user's page to lazy-load all blueprint cards, extracts name/url/favorites.
2. `comments_scraper.py` — Playwright async API. Loads each blueprint page, waits for Disqus iframe, extracts `threadData` JSON. Has `get_comments_async()` for concurrent use.
3. `utils.py:take_snapshot()` — Orchestrates the full pipeline: scrapes blueprints, fetches comments concurrently (capped by `SNAPSHOT_MAX_CONCURRENCY`, default 6 via `asyncio.Semaphore`), then stores everything in a single `transaction.atomic()` block.

### Async Task Flow

The web UI triggers snapshots asynchronously:
1. User clicks "Take New Snapshot" → `views.take_snapshot_view()` calls `take_snapshot_task.delay(user_url)`
2. Celery worker picks up the task from RabbitMQ and runs `take_snapshot()` from `utils.py`
3. Dashboard has a 1-hour cooldown between snapshots per user

### URL Routes

All routes are under `monitoring/urls.py`:
- `/` — Home page with user URL input
- `/user/<fp_user_id>/` — User dashboard (blueprints table, recent comments)
- `/user/<fp_user_id>/snapshot/` — Trigger async snapshot
- `/user/<fp_user_id>/comments/` — CSV of blueprints with new comments between dates
- `/user/<fp_user_id>/snapshots/` — All snapshots for a user

### Templates

Templates are in `monitoring/templates/monitoring/`. `base.html` provides shared layout and a `makeTableSortable()` JS utility used across pages.
