#!/usr/bin/env sh
# Container bootstrap: apply migrations, seed the RBAC catalogue, optionally seed
# the demo dataset, then exec CMD.
set -e

echo "[entrypoint] Running migrations..."
python manage.py migrate --noinput

# The RBAC catalogue (permissions + system roles) is required by the app and the
# command is idempotent, so it always runs — also in production.
echo "[entrypoint] Seeding RBAC catalogue..."
python manage.py seed_rbac

if [ "${SEED_DATA:-1}" != "0" ]; then
  echo "[entrypoint] Seeding default dataset..."
  python manage.py seed_default_data
fi

echo "[entrypoint] Executing: $@"
exec "$@"
