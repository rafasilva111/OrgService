"""Settings shared by every environment configuration.

This module holds everything that is identical in development, production and
tests: installed apps, middleware, templates, database wiring, i18n, static
files, auth and logging.

Environment-specific values live in ``dev.py``, ``prod.py`` and ``test.py``.
Those modules import everything from here, so this module must not depend on
``DEBUG`` — each configuration decides its own value.

Values are read from the process environment. A ``.env`` file at the repository
root is loaded by :mod:`config.settings` before this module is imported, and real
environment variables always take precedence over ``.env`` entries.
"""

from pathlib import Path

from .env import get_str

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# --- Application definition -------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.postgres",
    "django.contrib.staticfiles",
    # Third party
    "django_extensions",
    # Domain apps
    "apps.access",
    "apps.audit",
    "apps.changes",
    "apps.common",
    "apps.dashboard",
    "apps.events",
    "apps.incidents",
    "apps.metrics",
    "apps.organizations",
    "apps.people",
    "apps.problems",
    "apps.profiles",
    "apps.requests",
    "apps.resources",
    "apps.services",
    "apps.offerings",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.audit.middleware.AuditRequestMiddleware",
    "apps.profiles.middleware.UserLanguageMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

# Auth entry points used by the platform (apps.dashboard provides the views).
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.common.context_processors.user_context",
                "apps.common.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database ---------------------------------------------------------------
# PostgreSQL (docker-compose.yml by default). Override via environment
# variables, which is how CI and other hosts point at their own server.

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": get_str("POSTGRES_DB", "orgplatform"),
        "USER": get_str("POSTGRES_USER", "orgplatform"),
        "PASSWORD": get_str("POSTGRES_PASSWORD", "orgplatform"),
        "HOST": get_str("POSTGRES_HOST", "127.0.0.1"),
        "PORT": get_str("POSTGRES_PORT", "5432"),
    }
}

# --- Security ---------------------------------------------------------------

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Keep the authentication identity separate from domain people info.
# We deliberately keep the built-in ``auth.User`` and link ``people.Person`` to it.
AUTH_USER_MODEL = "auth.User"

# --- Internationalization ---------------------------------------------------
# Both ``en`` (default) and ``pt`` are first-class. The active language for a
# signed-in user is persisted on ``profiles.UserProfile.language`` and applied
# by ``profiles.middleware.UserLanguageMiddleware``.

LANGUAGES = [
    ("en", "English"),
    ("pt", "Português"),
]
LANGUAGE_CODE = "en"
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Static files -----------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded files (request attachments). Never served from a public URL: the
# ``requests:attachment`` view checks the user can see the request first.
MEDIA_ROOT = Path(get_str("MEDIA_ROOT", str(BASE_DIR / "media")))
MEDIA_URL = "/media/"

# Request form attachments: size limit and accepted file types.
REQUEST_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
REQUEST_ATTACHMENT_EXTENSIONS = ("pdf", "jpg", "jpeg", "png", "doc", "docx", "odt")

# --- Models -----------------------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Logging ----------------------------------------------------------------
# Audit/event signals write to the application logger so operational and
# mutation activity are visible in one place.

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": get_str("DJANGO_LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "apps.audit": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
