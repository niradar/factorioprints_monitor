@echo off
REM Take a snapshot for every monitored FactorioPrints account.
REM No arguments - the account list comes from the database.
REM Intended to be invoked by Windows Task Scheduler for automatic daily scans.

cd /d "%~dp0"
if not exist "logs" mkdir "logs"

set DJANGO_SETTINGS_MODULE=factorioprints_monitor.settings

echo ====== %date% %time% : snapshot_all start ======>> "logs\snapshot.log"
".venv\Scripts\python.exe" manage.py snapshot_all >> "logs\snapshot.log" 2>&1
echo ====== %date% %time% : snapshot_all exit %ERRORLEVEL% ======>> "logs\snapshot.log"
exit /b %ERRORLEVEL%
