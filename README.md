# Factorioprints.com Monitor

Track new comments on the blueprints you publish on [factorioprints.com](https://factorioprints.com) — see what still needs a reply at a glance, watch favourites and comments grow over time, and (optionally) get emailed when new comments arrive.

A single-user Django web app that scrapes factorioprints with a headless browser (Playwright) and stores point-in-time **snapshots** in SQLite.

## Features

- **Comment inbox** — every new comment across your blueprints in one list. Filter **All / Needs reply / Done**, search, and mark a comment *done* as you reply on factorioprints. Your own replies show as “(you)”.
- **Blueprints** — a sortable list (favourites + 30‑day change, comments, awaiting replies, last comment) and a per‑blueprint page with a favourites/comments **trend chart** (comments plotted by their real post date).
- **Snapshots** — take one from the app with **live progress** (no page reload), or on a schedule via Windows Task Scheduler.
- **Email alerts** — optionally get emailed when a snapshot finds new comments (your own replies excluded).
- **Settings** — a display name, your Disqus name (powers “(you)” / reply detection), and email‑alert config.
- Dark / light themes; multiple monitored users with a switcher.

## 1. Prerequisites

[uv](https://github.com/astral-sh/uv) — a Python package/venv manager.

## 2. Installation

```bash
git clone https://github.com/niradar/factorioprints_monitor.git
cd factorioprints_monitor
uv sync
playwright install            # browser binaries for Playwright
uv sync --extra dev           # optional: pytest, pytest-mock
python manage.py migrate
```

## 3. Run the web app

```bash
run_server.bat                              # Windows
python manage.py runserver 0.0.0.0:8129     # manual (set DJANGO_SETTINGS_MODULE=factorioprints_monitor.settings)
```

Open <http://localhost:8129/>. On first run, paste your factorioprints **user URL**
(`https://factorioprints.com/user/<USER_ID>`) — that drops you into the inbox, where
**Take snapshot** scrapes your blueprints and comments. After that, the inbox fills in
and you can reply, mark done, browse blueprint trends, and tune Settings.

## 4. Email alerts (optional)

Enable **Email alerts** in Settings and enter an address. After each snapshot, any *new*
comments (vs the previous snapshot, excluding your own replies) are emailed to you.

In development, mail prints to the **server console**. For real delivery, set SMTP env
vars before launching — e.g. Gmail with a 16‑char **App Password** (requires 2‑Step
Verification). Set them as user env vars so both the server and the scheduled task see them:

```bat
setx FPM_EMAIL_HOST     smtp.gmail.com
setx FPM_EMAIL_USER     you@gmail.com
setx FPM_EMAIL_PASSWORD "<app password>"
```

Optional: `FPM_EMAIL_PORT` (default 587), `FPM_EMAIL_TLS` (default 1), `FPM_EMAIL_FROM`,
and `FPM_BASE_URL` (host used in the alert’s inbox link, default `http://localhost:8129`).
Open a new terminal after `setx`, then use **Test** in Settings to verify.

## 5. Scheduled snapshots (Windows Task Scheduler)

`run_snapshot.bat "<user_url>"` takes one snapshot via the management command — no web
server required (it writes straight to SQLite) — and triggers email alerts if enabled.
Register it to run on a schedule:

Replace `<install-dir>` with the folder where you cloned the project:

```bat
schtasks /Create /SC DAILY /ST 08:00 /TN "FactorioPrintsSnapshot" ^
  /TR "\"<install-dir>\run_snapshot.bat\" https://factorioprints.com/user/<USER_ID>"
```

In Task Scheduler, enable **“Run task as soon as possible after a scheduled start is
missed”** so a snapshot still runs if the PC was off at the scheduled time.

## 6. Management commands (CLI)

Run from the project root (`set DJANGO_SETTINGS_MODULE=factorioprints_monitor.settings` if
outside the batch scripts).

### Take a snapshot
Scrapes all blueprints and comments for a user and stores them.
```bash
python manage.py take_snapshot --user-url "https://factorioprints.com/user/<USER_ID>"
```

### List snapshots
All snapshot timestamps (optionally filtered by user).
```bash
python manage.py list_snapshots [--user-url "https://factorioprints.com/user/<USER_ID>"]
```

### Latest blueprints
Blueprints in the most recent snapshot of a user.
```bash
python manage.py latest_blueprints --user-url "https://factorioprints.com/user/<USER_ID>"
```

### Blueprints with new comments (between dates)
CSV of blueprints that gained comments between two dates.
```bash
python manage.py blueprints_with_new_comments --user-url "..." --start-date 2025-06-01 --end-date 2025-06-05
```

### Delete a snapshot
Delete a snapshot and all its data by timestamp (must match `list_snapshots` exactly).
```bash
python manage.py delete_snapshot --timestamp 2025-06-05T08:00:00+00:00
```

### Notes

* Snapshots can take a few minutes depending on the number of blueprints and comments.
* All scraping is server-side; no manual browser interaction is needed.

## Tests

```bash
python manage.py test monitoring
```

## License

[MIT](LICENSE) © Nir Adar
