"""Settings entry point. Resolves which configuration to load.

``DJANGO_SETTINGS_MODULE`` stays ``config.settings`` everywhere (``manage.py``,
``config.wsgi``, ``config.asgi``, gunicorn), and this module picks the concrete
configuration at import time:

1. Importing :mod:`config.settings.env` loads ``.env`` and then
   ``.env.{ENVIRONMENT}``, and resolves and validates ``ENVIRONMENT``
   (``dev``, ``prod`` or ``test``; default ``dev``).
2. Import the matching submodule and re-export its settings as this module's
   namespace, so Django sees one flat settings object.

To select a configuration:

    python manage.py runserver            # dev is the default
    ENVIRONMENT=prod gunicorn config.wsgi:application
    ENVIRONMENT=test python manage.py migrate

An unknown value fails loudly rather than falling back to a default, so a typo in
deployment cannot quietly boot a development server in production.
"""

from importlib import import_module

from .env import ENVIRONMENT

# Re-export the selected configuration into this module's namespace. Dunder
# attributes are skipped so ``config.settings.__name__`` stays
# ``config.settings`` rather than becoming ``config.settings.dev``.
globals().update(
    {
        name: value
        for name, value in vars(import_module(f"{__name__}.{ENVIRONMENT}")).items()
        if not name.startswith("__")
    }
)
