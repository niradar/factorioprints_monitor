@echo off
REM Take a single snapshot for the given FactorioPrints user URL.
REM Usage: run_snapshot.bat "https://factorioprints.com/user/<USER_ID>"
REM Intended to be invoked by Windows Task Scheduler for automatic snapshots.

cd /d "%~dp0"
call .venv\Scripts\activate

set DJANGO_SETTINGS_MODULE=factorioprints_monitor.settings

python manage.py take_snapshot --user-url %1
