@echo off
call .venv\Scripts\activate

set DJANGO_SETTINGS_MODULE=factorioprints_monitor.settings

python manage.py runserver 127.0.0.1:8129

pause
