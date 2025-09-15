@echo off
call .venv\Scripts\activate

set DJANGO_SETTINGS_MODULE=factorioprints_monitor.settings

python manage.py runserver 0.0.0.0:8123

pause
