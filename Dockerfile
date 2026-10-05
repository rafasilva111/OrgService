FROM python:3.12-slim

# uv is installed from PyPI so the build does not depend on ghcr.io access.
ARG UV_VERSION=0.12.5
RUN pip install --no-cache-dir "uv==${UV_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Runtime dependencies only, resolved from uv.lock. The Django project itself is
# not installed into the image (--no-install-project); it runs from /app.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY . .

# Run as a non-privileged user (defense in depth).
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "-b", "0.0.0.0:8000", "-w", "4", "--timeout", "120"]
