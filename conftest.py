"""Safety guard for bare ``pytest``.

This project's tests are Django ``TestCase``/``TransactionTestCase`` tests and are
meant to run through the Django test runner, which builds an isolated throwaway
database::

    python manage.py test monitoring

There is no ``pytest-django`` configured, so running the suite with bare ``pytest``
gives the tests no database isolation: ``TransactionTestCase`` commits real rows
(and the snapshot tests spawn background threads that can commit after teardown),
writing fixtures like the ``u1`` user straight into the real ``db.sqlite3``.

To prevent that data leak, this conftest aborts pytest whenever it would run
against the real database. Set ``FPM_ALLOW_PYTEST=1`` to override (e.g. once
``pytest-django`` with an isolated test DB is configured).
"""
import os


def pytest_configure(config):
    if os.environ.get("FPM_ALLOW_PYTEST"):
        return

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "factorioprints_monitor.settings")
    try:
        import django
        from django.conf import settings
        if not settings.configured:
            django.setup()
        db_name = str(settings.DATABASES["default"]["NAME"])
    except Exception:
        db_name = ""

    # Only block the dangerous case: the configured DB is a real on-disk file, not
    # an isolated ``test_*`` DB or an in-memory one.
    base = os.path.basename(db_name).lower()
    is_real = db_name and base != ":memory:" and not base.startswith("test")
    if is_real:
        import pytest
        pytest.exit(
            "Refusing to run bare pytest against the real database:\n"
            f"    {db_name}\n\n"
            "These are Django tests - run them with the isolated test DB instead:\n"
            "    python manage.py test monitoring\n\n"
            "(Set FPM_ALLOW_PYTEST=1 to override once pytest-django is configured.)",
            returncode=2,
        )
