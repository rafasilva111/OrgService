# Architecture

Modular monolith (Django 5.2) for an **organizational services platform** — municipalities/parishes providing operational services (waste, water, parks…), with requests, incidents, problems, changes, offerings, resources and metrics.

## Stack

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| Web framework | Django 5.2 (LTS) |
| Database | PostgreSQL 16 (Docker `postgres:16-alpine`) |
| ORM extras | `django.contrib.postgres` (ltree, GiST, `JSONField`) |
| Testing | pytest + pytest-django |
| Developer | django-extensions |

## Layout

```
/home/kali/OrgService
├── config/            # startproject: settings, urls, wsgi, asgi (+ $DJANGO_SETTINGS_MODULE)
├── apps/
│   ├── common/        # NOT a real domain app — shared base classes & helpers
│   ├── people/        # Person, Skill, PersonSkill
│   ├── organizations/ # Organization (ltree tree), Membership, Unit
│   ├── access/        # Permission, Role, RolePermission, ServiceAccess + seed_rbac
│   ├── services/      # Service, ServiceType, ServiceCapability(+Assignment), ServiceActor
│   ├── offerings/     # ServiceOffering (definitions), OfferingExecution (instances), Tasks
│   ├── requests/      # Request, RequestComment
│   ├── incidents/     # Incident, IncidentUpdate
│   ├── problems/      # Problem, ProblemIncident
│   ├── changes/       # Change, ChangeApproval
│   ├── resources/     # Resource, Asset, Equipment, Material, ResourceAssignment
│   ├── events/        # Event (immutable; generic subject)
│   ├── audit/         # AuditLog (generic object)
│   ├── metrics/       # Metric, MetricValue, KPI(+via), SLA
│   ├── dashboard/     # views/urls only — no models.py
│   └── profiles/      # UserProfile (OneToOne auth.User, holds `language`)
├── tests/             # pytest suite (fixtures + per-domain modules)
├── templates/         # 23 template subdirectories (server-rendered)
├── locale/pt/LC_MESSAGES/  # compiled django.po/.mo (EN default + PT)
├── docker-compose.yml # PostgreSQL 16 + web
├── requirements.txt
└── pytest.ini
```

Why **modular monolith** instead of microservices:

* a single model of the domain has strong relationships (service ↔ requests/incidents/problems/offerings/metrics), which map cleanly onto foreign keys and shared transactions;
* centralized authorization (`can()` / `visible_to()`) is enforced in one place;
* deployment is a single Django app/process; the app boundaries keep teams focused.

## Key patterns

### Base models (`apps/common/models.py`)

* `TimeStampedModel` — adds `created_at`/`updated_at`; its manager is built from `AuthQuerySet` so **every model in every app** inherits `.visible_to(user, action)`.
* `ActiveModel` — adds `active` (soft-delete/flag), default `True`.

### Hierarchy via PostgreSQL `ltree`

`Organization.path` stores a materialized path (e.g. `dist.muni.pa`). A custom field (`apps/common/fields.py`) adds Django lookups:

* `path__descendants` → `path <@ 'dist.muni'`
* `path__ancestors`  → `path @> 'dist.muni'`
* `path__nlevel`     → `nlevel(path)`

Indexed with **GiST** (`gist_ltree_ops`) — PostgreSQL 16 no longer ships a GIN opclass for `ltree`.

> Django 5.2 does **not** include an `LTreeField`, hence the custom field. The `ltree` extension is created by `apps/organizations/migrations/0001_initial.py` (`CreateExtension`), which is why test DBs are built with real migrations (no `--nomigrations`).

### Versioned offering definitions

`ServiceOffering` rows are *versions* of `(service, name)`; a new revision = a new row with a higher `version` (never mutate a published version). `ServiceOffering.current_version(service, name)` returns the newest non-draft row. `OfferingExecution` is an *instance* and references any domain subject (request/incident/change/person) through a `GenericForeignKey` to avoid app-to-app imports.

### Generic FKs to decouple apps

`OfferingExecution.subject`, `Event.subject`, `AuditLog.object`, `ResourceAssignment.target` use `GenericForeignKey`. Denormalized `organization`/`service` columns on `Event`/`AuditLog` keep authorization queries cheap and DB-level.

### Immutable events

`Event.metadata` is `editable=False`; events are write-once records feeding audit trails, notifications, offering triggers and metrics.

### Multi-table resources

`Asset`/`Equipment`/`Material` inherit `Resource` (multi-table inheritance) so every subtype *is* a `Resource` — generic assignments and visibility keep working, while each subtype adds its own fields.

### Metrics

`MetricValue` stores observations over `(period_start, period_end)`; `Metric` and `KPI` may be scoped to a `Service` (null = platform-wide). Dashboards are **not** entities — they are queries/views over these tables.

## Unit tests

`tests/` covers: ltree paths/ancestors/descendants, authorization (downward inheritance, no uphill/sibling flow, `ServiceAccess` restriction, superuser, anonymous), RBAC seeding idempotency, uniqueness constraints, multi-table resources, generic-FK wiring, metrics round-trips.

Run:

```bash
docker compose up -d   # start PostgreSQL
source .venv/bin/activate
python manage.py migrate
python manage.py seed_rbac
pytest                  # -q for a quiet run
```