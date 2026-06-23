@echo off
REM Take a single snapshot for the given FactorioPrints user URL.
REM Usage: run_snapshot.bat "https://factorioprints.com/user/<USER_ID>"
REM Intended to be invoked by Windows Task Scheduler for automatic snapshots.

cd /d "%~dp0"
if not exist "logs" mkdir "logs"

set DJANGO_SETTINGS_MODULE=factorioprints_monitor.settings

echo ====== %date% %time% : take_snapshot %1 start ======>> "logs\snapshot.log"
".venv\Scripts\python.exe" manage.py take_snapshot --user-url %1 >> "logs\snapshot.log" 2>&1
echo ====== %date% %time% : take_snapshot exit %ERRORLEVEL% ======>> "logs\snapshot.log"
exit /b %ERRORLEVEL%
