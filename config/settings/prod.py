"""Production configuration (``ENVIRONMENT=prod``).

Selected by setting ``ENVIRONMENT=prod``. Security-relevant values are read from
the environment and the deployment refuses to start without them, so a production
instance cannot silently fall back to development defaults.

Requires at least:

* ``DJANGO_SECRET_KEY`` — must not be the development placeholder.
* ``DJANGO_ALLOWED_HOSTS`` — comma-separated hostnames.

Optional:

* ``DJANGO_SECURE_SSL_REDIRECT`` — set to ``0`` when TLS is terminated upstream
  and the redirect would loop.
* ``DJANGO_USE_X_FORWARDED_PROTO`` — set to ``1`` behind a proxy that sets
  ``X-Forwarded-Proto``.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .env import get_bool, get_list, get_str

DEV_PLACEHOLDER_SECRET_KEY = "django-insecure-dev-only-orgplatform"

SECRET_KEY = get_str("DJANGO_SECRET_KEY", required=True, secret=True)
if SECRET_KEY == DEV_PLACEHOLDER_SECRET_KEY:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY is still the development placeholder. "
        "Generate one with: python -c 'from django.core.management.utils import "
        "get_random_secret_key; print(get_random_secret_key())'"
    )

DEBUG = False

# No default on purpose: a wildcard in production is a header-injection surface.
ALLOWED_HOSTS = get_list("DJANGO_ALLOWED_HOSTS", required=True)

# --- Static files -----------------------------------------------------------
# Cache-busted filenames ("styles.abc123.css").
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
}

# --- Security hardening -----------------------------------------------------
# Safe to leave enabled behind a real web server / proxy that terminates TLS.

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = get_bool("DJANGO_SECURE_SSL_REDIRECT", True)
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

if get_bool("DJANGO_USE_X_FORWARDED_PROTO", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# The debug toolbar is a development-only dependency and is not installed here.
