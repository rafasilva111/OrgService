"""Development configuration (``ENVIRONMENT=dev``).

Verbose errors, the debug toolbar, permissive host headers and a non-caching
static file backend. This is the default when no ``ENVIRONMENT`` is set.
"""

from .base import *  # noqa: F403
from .env import get_list, get_str

# Only ever a fallback: production must set DJANGO_SECRET_KEY explicitly.
SECRET_KEY = get_str("DJANGO_SECRET_KEY", "django-insecure-dev-only-orgplatform")

DEBUG = True

ALLOWED_HOSTS = get_list(
    "DJANGO_ALLOWED_HOSTS",
    ["localhost", "127.0.0.1", "0.0.0.0", "[::1]", "testserver"],
)

# Third-party dev tooling: the debug toolbar is only installed in this config.
INSTALLED_APPS = [*INSTALLED_APPS, "debug_toolbar"]  # noqa: F405

# Inserted right after AuthenticationMiddleware so request.user is available to
# the SHOW_TOOLBAR_CALLBACK (we only show it to superusers in dev).
MIDDLEWARE = [  # noqa: F405
    *MIDDLEWARE[: MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware") + 1],  # noqa: F405
    "debug_toolbar.middleware.DebugToolbarMiddleware",
    *MIDDLEWARE[MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware") + 1 :],  # noqa: F405
]

DEBUG_TOOLBAR_CONFIG = {
    "SHOW_TOOLBAR_CALLBACK": lambda request: bool(
        request.user.is_authenticated and request.user.is_superuser
    ),
}

# Email goes to the console instead of a real SMTP server.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
