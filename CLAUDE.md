# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django web app that monitors user blueprints on factorioprints.com and tracks new comments. Uses Playwright for headless browser scraping and SQLite for storage. Snapshots triggered from the web UI run in a background thread; scheduled snapshots are driven externally (e.g. Windows Task Scheduler) via the `take_snapshot` management command.

The primary web UI is a single-user, design-system app centered on a comment **inbox** (find new comments, reply on factorioprints, mark done), plus a blueprints list, per-blueprint trend charts, settings, an About page, and an onboarding landing screen. An older dashboard/template set (`home`, `user_dashboard`, `comments_between`, `user_snapshots`, `base.html`) still exists but is **legacy** — new work happens on the design-system shell described below.

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

Two models hold mutable, **non-snapshot** state (one row per identity, not per scrape):

- **CommentStatus** — The inbox "handled" flag, keyed on `(blueprint, comment_id)` — the stable comment identity, *not* a snapshot row, so it survives re-scrapes. Fields: `handled`, `handled_at`. A row exists only once a comment is toggled; absence means not handled. Queried via an `Exists()` annotation in `utils.get_inbox_queryset()`.
- **UserSettings** — Per-user preferences, one row per `user_url`: `display_name` (shown across the app, and substituted for the user's own comment author as "Name (you)"), `disqus_name` (matches the user's own comments), `alerts_enabled` / `alert_email` (email-alert config; sending is not built yet).

### Scraping Pipeline

1. `blueprints_scraper.py` — Playwright sync API. Scrolls the user's page to lazy-load all blueprint cards, extracts name/url/favorites.
2. `comments_scraper.py` — Playwright async API. Loads each blueprint page, waits for Disqus iframe, extracts `threadData` JSON. Has `get_comments_async()` for concurrent use.
3. `utils.py:take_snapshot()` — Orchestrates the full pipeline: scrapes blueprints, fetches comments concurrently (capped by `SNAPSHOT_MAX_CONCURRENCY`, default 6 via `asyncio.Semaphore`), then stores everything in a single `transaction.atomic()` block.

### Async Task Flow

The web UI triggers snapshots in a background thread:
1. The top-bar "Take snapshot" button (and the empty-inbox CTA) submits the form. `views.take_snapshot_view()` checks `is_snapshot_running()` and `is_in_cooldown()`; if clear it creates a `SnapshotRun` (status=running) and calls `start_snapshot_async(user_url, run.id)`
2. `start_snapshot_async` runs `take_snapshot()` (from `utils.py`) in a daemon thread (`_run_snapshot`) so the request returns immediately; on completion it sets the `SnapshotRun` to success (+`snapshot_ts`) or failed (+`error`), and logs failures
3. **Server-side protection:** a 10-minute cooldown (`SNAPSHOT_COOLDOWN` in `views.py`, based on the last successful `UserSnapshot`) and an overlap block via `is_snapshot_running`. A `RUNNING` row older than `SNAPSHOT_STALE_AFTER` (30 min) is treated as stale so a crashed run can't block forever.
4. **Client-side (design-system shell):** `take_snapshot_view` returns JSON when called via `fetch` (header `X-Requested-With: fetch`); `static/monitoring/js/snapshot.js` then polls `snapshot_status` every ~3s, shows live "scanning…" + elapsed time with no navigation, and reloads once when the run finishes. Without JS, the form falls back to a normal POST that redirects to `next` (the current page). `shell_context()` exposes `snapshot_running` / `snapshot_recent` so the button disables on running **or** cooldown.
5. Only the web path records `SnapshotRun` rows; the `take_snapshot` management command (used by Task Scheduler) just runs the scrape directly.

### URL Routes

All routes are under `monitoring/urls.py`.

Design-system app (current):
- `/` — Landing/onboarding (`views.landing`). No users → URL-input form; otherwise redirects to the most recent user's inbox. `?add=1` forces the form (used by the switcher's "Add user").
- `/user/<fp_user_id>/inbox/` — Inbox: latest comments with All/Needs-reply/Done filter, search, pagination, per-comment Done toggle
- `/user/<fp_user_id>/comment/<blueprint_id>/<comment_id>/toggle/` — Toggle a comment's handled state (JSON for fetch, redirect for no-JS)
- `/user/<fp_user_id>/inbox/mark-all-done/` — Mark all awaiting comments done
- `/user/<fp_user_id>/blueprints/` — Sortable blueprints list (sort + pagination are client-side)
- `/user/<fp_user_id>/blueprint/<blueprint_id>/` — Blueprint detail: stat cards, favourites/comments trend chart, that blueprint's comments
- `/user/<fp_user_id>/settings/` — Settings (display name, Disqus name, email alerts, auto-scan setup); `…/settings/test-email/` sends a test
- `/user/<fp_user_id>/about/` — About page
- `/user/<fp_user_id>/snapshot/` — Trigger async snapshot (JSON for fetch, else redirect to `next`)
- `/user/<fp_user_id>/snapshot/status/` — JSON snapshot status for the client-side poller

Legacy (kept, not the primary UI): `/user/<fp_user_id>/` (dashboard), `/user/<fp_user_id>/comments/` (CSV between dates), `/user/<fp_user_id>/recent-comments/`, `/user/<fp_user_id>/snapshots/`.

### Frontend (design system)

The design-system UI is an app shell, not the legacy `base.html`:
- `templates/monitoring/app_base.html` — shared shell (sidebar nav, user switcher, top bar with search + snapshot button). New pages `{% extends %}` it and fill `{% block content %}`. The content heading lives *in* the content area, not the top bar.
- `static/monitoring/css/app.css` — the whole design system (tokens for dark + light themes, all components). Single source of truth.
- `static/monitoring/js/` — `app.js` (theme toggle + persistence, user switcher, Done-toggle fetch), `snapshot.js` (AJAX trigger + status poller), `table-pager.js` (client-side sort + pagination for the blueprints table), `chart.js` (dependency-free SVG trend chart; favourites by snapshot date, comments by real `created_utc`).
- Partials: `_comment_row.html` (shared by inbox + detail; `hide_blueprint` flag), `_pager.html` (shared pagination).
- `templatetags/inbox_extras.py` — `smart_time` filter (relative for fresh, absolute date for old, year only for past years).
- `views.shell_context()` centralizes the chrome context (nav, switcher users, display name, snapshot state) for every shell page.
- Design direction and component rules are documented in `.interface-design/system.md`; static mockups live in `mockups/`.

The dev email backend is the console backend (`settings.py`), so `send_test_email` and future alerts print to the server console until real SMTP is configured.

### Templates

Templates are in `monitoring/templates/monitoring/`. The legacy pages use `base.html` (which provides `makeTableSortable()`); the current app uses `app_base.html` + the design system above.
