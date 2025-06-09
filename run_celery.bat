.venv\Scripts\activate
celery -A factorioprints_monitor worker -l info --concurrency=1
