"""Test configuration (``ENVIRONMENT=test``).

Used by pytest (see ``pytest.ini``, which points ``DJANGO_SETTINGS_MODULE``
straight at this module) and available for manual runs via
``ENVIRONMENT=test python manage.py ...``.

Differences from ``base``:

* ``DEBUG`` is off so tests exercise the same code paths as production, without
  the debug toolbar installed.
* Password hashing is switched to a fast, insecure hasher — test logins must not
  spend seconds in PBKDF2.
* Email is captured in memory instead of being sent.
* ``testserver`` is allowed so Django's test client works.

The database itself is the same PostgreSQL engine as every other configuration.
pytest-django creates (and drops) a separate ``test_<POSTGRES_DB>`` database, so
the development data is never touched.
"""

from .base import *  # noqa: F403

DEBUG = False

SECRET_KEY = "django-insecure-test-only-orgplatform"

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

# Fast, deliberately insecure hasher. Never reuse this outside the test suite.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Keep the test run quiet and deterministic.
LOGGING = {  # noqa: F405
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"], "level": "CRITICAL"},
}
