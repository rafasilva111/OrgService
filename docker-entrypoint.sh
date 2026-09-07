#!/usr/bin/env sh
# Container bootstrap: apply migrations, seed the demo dataset, then exec CMD.
set -e

echo "[entrypoint] Running migrations..."
python manage.py migrate --noinput

if [ "${SEED_DATA:-1}" != "0" ]; then
  echo "[entrypoint] Seeding RBAC catalogue..."
  python manage.py seed_rbac
  echo "[entrypoint] Seeding default dataset..."
  python manage.py seed_default_data
fi

echo "[entrypoint] Executing: $@"
exec "$@"
