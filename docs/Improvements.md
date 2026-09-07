# Improvements

Audit of the current codebase for **performance, security and general good
practices**, with concrete, prioritized recommendations. Each item points to
the relevant file and gives the "why" plus the "how".

> This is a proposal, not a mandate. Items are ordered by impact. Small, safe
> wins are marked **[Do it]** — anything with trade-offs is **[Discuss]**
> before you apply it.

Current state: Django 5.2 / PostgreSQL 16, dev server (`runserver`) running as
**root** inside the container.

---

## 1. Security

### 1.1 Production hardening — `config/settings.py`
| Check | Current | Should be | File |
|---|---|---|---|
| `DEBUG` | `"1"` default | `0` in prod | `settings.py` (env-driven, OK) |
| `SECRET_KEY` | insecure dev fallback | required env, no fallback in prod | `settings.py` |
| `ALLOWED_HOSTS` | dev default | explicit prod hosts | `settings.py` |
| Cookie flags | defaults | secure/httponly/samesite in prod | `settings.py` |
| `SECURE_SSL_REDIRECT` / `X_FRAME_OPTIONS` | not set (default `DENY` for XFO) | explicit | `settings.py` |

**Recommendations (prod only; keep dev loose):**
```python
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"
```
`SECRET_KEY` and DB credentials must come from the environment and **fail
closed** in production:
```python
import os

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]  # no default when DEBUG=0
```

### 1.2 Run the container as a non-root user — `Dockerfile`
Currently `docker-entrypoint.sh` runs as **root**. Best practice (defense in
depth against an RCE):
```dockerfile
# create a non-privileged user in the image
RUN useradd --create-home --shell /bin/bash appuser
USER appuser
```
Note the entrypoint and any writes to writable dirs need to be owned by
`appuser`. `[Discuss]` — involves touching Dockerfile + entrypoint + volume
permissions.

### 1.3 Dev server in production path — `docker-compose.yml`
`CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]` is a dev server
(no TLS, one process, stack traces on error). For any non-local deployment,
switch to `gunicorn` + a WSGI worker pool:
```yaml
command: ["gunicorn", "config.wsgi:application", "-b", "0.0.0.0:8000", "-w", "4"]
```

### 1.4 SQL injection — `seed_default_data.py`
`_reset_domain_data` builds `TRUNCATE TABLE {names}` by *whitelisting* table
names from `connection.introspection` and gating on `_RESET_PREFIXES`, and
the delete is a fixed `DELETE ... WHERE is_superuser=false`. This is **safe**
(no user input), but keep it that way — never reuse the pattern with external
strings.

### 1.5 CSRF
`CsrfViewMiddleware` is enabled (the earlier fix restored the correct import).
- Confirm every POST/mutating view passes `{% csrf_token %}` in its template.
- The lifecycle POST-only actions (`apps/common/lifecycle.py`) return 405 on
  GET — good. Ensure no mutating endpoint is reachable via GET with side
  effects.

### 1.6 Error handling
In dev, `runserver` + `DEBUG=1` can leak stack traces / path info. Guard the
production path with `DEBUG=0` and, ideally, a JSON/HTML 500 handler that
doesn't expose internals.

### 1.7 Secrets hygiene
`.env` is committed (contains only dev credentials `orgplatform`/`orgplatform`).
Verify `.env` is in `.gitignore` once the repo is versioned, and switch all
demo/seeded passwords to per-environment injection in real deployments.

---

## 2. Performance

### 2.1 N+1 query audit — dashboard & detail views (`apps/dashboard/views.py`, `apps/*/views.py`)
Several list/detail pages run the authorization scope query **per model** —
that's fine, but watch for N+1 in the panels:
- `ExecutiveDashboard` builds `requests`, `incidents`, `problems`, `changes`,
  `workflows`, `resources` querysets **even when panels aren't rendered** — they
  are all evaluated. Consider `if`-gating or aggregating lazily.
- The `.count()` calls on visible-query sets each run a query with the full
  membership/scope machinery. Fine at demo scale; optimize when datasets grow.
- `stat_card` "People" uses `_visible_people(user)` which does a `distinct()`
  over memberships — ok, but add `select_related("user")` if the template
  shows more than a count.

**Useful tool:** `django-debug-toolbar` (add in dev) plus
`assertNumQueries` in tests for hot endpoints.

### 2.2 Large-permission-role queries
`authorization_scope_q` iterates memberships in Python (small) but relies on
`_permission_codes_for_role`, `_service_access_ids` subqueries — acceptable
now. When membership counts grow, cache scopes per-user (per request) to avoid
re-running the same scope for every model on a dashboard.

### 2.3 Pagination / `.count()` on filtered sets
Fine at this scale. Revisit `ListViews` `page_size=25` (default) — no change
needed.

### 2.4 Static/`STATIC_URL = "static/"`
No `collectstatic` + no CDN. In prod, run `python manage.py collectstatic` and
serve via the web server/CDN, set `STATIC_ROOT`, and use
`ManifestStaticFilesStorage` in prod (long cache-busting hashes).

---

## 3. General good practices

### 3.1 Pin dependency versions — `requirements.txt`
Currently `>=` ranges: `Django>=5.1,<5.3`, `psycopg[binary]>=3.1`, etc.
For reproducible builds:
- Freeze to exact versions (`Django==5.2.x`) in a lock file.
- Add a vulnerability scan (`pip-audit`) to CI.
- Keep `django-extensions` as a **dev-only** dependency (move out of the
  runtime image).

### 3.2 Migrations — unapplied `profiles` migration
`apps/profiles` migration `0001_initial` is unapplied (the `UserProfile`
table is missing and `User.delete()` cascades into it — see the earlier
`seed --reset` note). Fully applying all migrations should be a hard
requirement before any prod deploy. **[Discuss]** — investigate why it's
unapplied and reconcile.

### 3.3 `admin.py` registrations
Many apps have empty `admin.py`. Registering models in Django admin gives a
free back-office + audit, but only if intended. **[Discuss]**.

### 3.4 Tests — signal the seed data reality
`seed_rbac` uses `update_or_create` and deletes/recreates `RolePermission`
rows — remember this makes `seed_rbac` non-idempotent *in terms of PKs* (it
wipes and recreates). Tests that create roles should create their own or call
the command, not assume fixed PKs. Add a tooling test that `python -m pytest`
stays green (it is: **141 passed** at time of writing).

### 3.5 Naming / dead code
- `STATUS.md` vs `README.md` duplication is intentional (tracking vs
  source-of-truth) — keep the banner "update both" in sync.
- This `Improvements.md` should be treated as a living backlog.

### 3.6 CI/CD
Add a GitHub Actions workflow: `ruff`/`flake8` lint + `pytest` + `pip-audit`
+ Docker build. There is **no** linter/formatter config today (`pyproject.toml`
with `[tool.ruff]` or `[tool.black]` is a low-cost win).

### 3.7 Logging
No `LOGGING` config in `settings.py`. Add structured logging:
```python
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
```
and log authz-denials/audit events (there is an `audit` app — emit on
`save()`/actions for the audit trail; currently the audit log is only seeded).

---

## 4. Prioritized quick wins (order of effort → impact)

1. **[Do it]** `requirements.txt` → pin + `pip-audit` in CI.
2. **[Do it]** `LOGGING` config + emit `audit`/`event` rows on model `save()`.
3. **[Do it]** `ruff` + `pyproject.toml`, wire into CI.
4. **[Do it]** `collectstatic` + `ManifestStaticFilesStorage` for prod static.
5. **[Discuss]** Non-root `USER appuser` in Dockerfile.
6. **[Discuss]** `gunicorn` worker pool instead of `runserver` in compose.
7. **[Discuss]** Prod settings block (secure cookies, HSTS, SSL redirect,
   secret key required when `DEBUG=0`).
8. **[Discuss]** Resolve the unapplied `profiles` migration.
9. **[Discuss]** `django-debug-toolbar` (dev) + `assertNumQueries` on hot pages.

---

### Suggested next step
Pick the **[Do it]** items first (low risk, immediate value), then bring the
**[Discuss]** items to the team for a decision — several involve Docker
footprint changes or reversals of dev conveniences.
