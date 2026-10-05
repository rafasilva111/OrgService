"""Helpers for reading configuration values from the environment.

Every settings configuration reads its values through these helpers so that
missing variables fail the same way everywhere and so that ``.env`` defaults and
real environment variables are handled consistently.

Two files can supply values. Later sources win:

1. real environment variables — shell, docker-compose, CI;
2. ``.env.{ENVIRONMENT}`` — per-environment overrides, e.g. ``.env.dev``;
3. ``.env`` — shared base values, also read directly by docker-compose.

Neither file is required; a missing one is simply skipped.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

ENVIRONMENTS = ("dev", "prod", "test")
DEFAULT_ENVIRONMENT = "dev"

# Whatever the process was started with outranks both files, so remember it before
# loading anything.
_inherited = dict(os.environ)

# Shared base first. override=False so the real environment stays in front.
load_dotenv(BASE_DIR / ".env", override=False)

# Resolved before the per-environment file is loaded, because that file is named
# after it. An unknown value fails loudly instead of falling back to a default,
# so a typo in deployment cannot quietly boot a development server in production.
ENVIRONMENT = os.environ.get("ENVIRONMENT", "").strip().lower() or DEFAULT_ENVIRONMENT
if ENVIRONMENT not in ENVIRONMENTS:
    raise ImproperlyConfigured(
        f"Unknown ENVIRONMENT={ENVIRONMENT!r}. Expected one of: {', '.join(ENVIRONMENTS)}."
    )

# override=True so the per-environment file beats the shared base, then put the
# inherited variables back so they still beat both.
load_dotenv(BASE_DIR / f".env.{ENVIRONMENT}", override=True)
os.environ.update(_inherited)

# The per-environment file must agree with the configuration that was requested;
# otherwise a stale file would silently supply values from the wrong environment.
_loaded = os.environ.get("ENVIRONMENT", "").strip().lower() or ENVIRONMENT
if _loaded != ENVIRONMENT:
    raise ImproperlyConfigured(
        f"ENVIRONMENT={ENVIRONMENT!r} was requested but .env.{ENVIRONMENT} sets "
        f"ENVIRONMENT={_loaded!r}. Make the two agree."
    )

TRUE_VALUES = frozenset({"1", "true", "t", "yes", "y", "on"})
FALSE_VALUES = frozenset({"0", "false", "f", "no", "n", "off"})


def get_str(name, default=None, *, required=False, secret=False):
    """Return an environment variable as a stripped string.

    With ``required=True`` a missing or blank value raises ``ImproperlyConfigured``
    instead of silently falling back to ``default``. With ``secret=True`` the value
    is never included in the error message.
    """
    raw = os.environ.get(name)
    value = raw.strip() if raw is not None else ""

    if not value:
        if required:
            where = f"{name} (set it in the environment or in .env)"
            if secret:
                where = f"{name} (set it in the environment or in .env, do not commit it)"
            raise ImproperlyConfigured(f"Missing required configuration value: {where}")
        return default

    return value


def get_bool(name, default=False, *, required=False):
    """Return an environment variable as a boolean.

    Accepts ``1/true/t/yes/y/on`` and ``0/false/f/no/n/off`` (case-insensitive).
    Anything else raises ``ImproperlyConfigured`` so typos never become a silent
    truthy value.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        if required:
            raise ImproperlyConfigured(f"Missing required configuration value: {name}")
        return default

    value = raw.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False

    raise ImproperlyConfigured(
        f"Invalid boolean for {name}: {raw!r}. "
        f"Use one of {sorted(TRUE_VALUES)} or {sorted(FALSE_VALUES)}."
    )


def get_list(name, default=(), *, required=False):
    """Return a comma-separated environment variable as a list of strings.

    Blank entries are dropped, so trailing commas and ``a,,b`` are tolerated.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        if required:
            raise ImproperlyConfigured(f"Missing required configuration value: {name}")
        return list(default)

    return [item.strip() for item in raw.split(",") if item.strip()]
