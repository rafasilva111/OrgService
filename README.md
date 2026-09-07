# orgplatform — Organizational Service Platform

A Django **monolithic** service-management platform for a (demo) **Portuguese municipality**: `Torres Vedras, Lisboa`. It models the organisation tree, the municipal services it delivers, the people and roles who run them, and the operational day-to-day: service requests, incidents, problems, changes, workflow executions, resources, KPIs/metrics and audit events.

The default dataset makes the app immediately explorable — 9 municipal services, 15 demo accounts, ~24 requests, incidents, problems, changes, workflow runs and metric time-series, all under a realistic `Portugal → Lisboa → Torres Vedras` hierarchy.

---

## 1. The project

- **Stack**: Python 3.12 / Django 5.2 / PostgreSQL 16 / pytest. Runs locally or via Docker Compose.
- **Shape**: a single `config/` project + 13 domain apps under `apps/` (listed below).
- **Postgres is mandatory** — the org hierarchy uses the `ltree` extension (`apps/common/fields.py`), indexed with a GiST index.
- **Authorization is centralized** — `apps/common/authorization.py` provides `can()` and `.visible_to()` queryset managers; every list/detail/create view is gated by RBAC permissions scoped to the user's organization.
- **Docs in Portuguese**: `docs/architecture.md`, `docs/domain-model.md`, `docs/authorization.md`, `docs/er-diagram.mmd`; operational log in `STATUS.md`.

### Apps (13)

| App | Holds | Notes |
|---|---|---|
| `organizations` | Organization (ltree tree), OrganizationMembership, OrganizationUnit | `PORTUGAL → LISBOA → TORRES-VEDRAS → 4 parishes` |
| `services` | Service, ServiceActor, ServiceCapability/Assignment | each Service requires a Service Manager actor |
| `people` | Person, Skill, PersonSkill | Person links 1:1 to a Django `User` |
| `access` | Permission, Role, RolePermission, ServiceAccess | RBAC catalogue seeded by `seed_rbac` |
| `requests` | Request, RequestType, RequestComment | citizen-facing demand tickets |
| `incidents` | Incident, IncidentUpdate | disruptions & outages (+ Impact/Urgency) |
| `problems` | Problem, ProblemIncident | root-cause management |
| `changes` | Change, ChangeApproval | planned changes + approval chain |
| `workflows` | Workflow, WorkflowStep, WorkflowTransition, WorkflowExecution, WorkflowTask | definition ≠ instance |
| `resources` | Resource, Asset, Equipment, Material, ResourceAssignment | vehicles/equipment |
| `metrics` | Metric, MetricValue, KPI/KpiMetric, SLA | dashboard data |
| `events` | Event | immutable, generic-FK |
| `audit` | AuditLog | immutable, generic-FK |
| `dashboard` | views only | executive/org/service dashboards (charts) |
| `profiles` | profile settings | header dropdown |

> `events`/`audit` and generic links (`Problem→Incident`, workflow `subject`) use Django **GenericForeignKey** to avoid circular dependencies between apps.

---

## 2. The data (default dataset)

Seeded by `python manage.py seed_default_data [--reset]` (idempotent — stable codes/emails, re-running updates rather than duplicates). The entrypoint also runs it automatically on container start.

### Organization tree

```
Portugal            (COUNTRY)
└── Lisboa          (DISTRICT)
    └── Torres Vedras (MUNICIPALITY)
        ├── São Pedro e Santiago (PARISH)
        ├── Matacães             (PARISH)
        ├── A dos Cunhados e Maceira (PARISH)
        └── Ramalhal             (PARISH)
```

### Services (9) — each bound to an org and a Service Manager

| Service | Bound to | Status | Manager account |
|---|---|---|---|
| Municipal Administration | Torres Vedras | OPERATIONAL | `r.martins` |
| Water Supply | Torres Vedras | **DEGRADED** | `j.fernandes` |
| Sanitation Network | Torres Vedras | OPERATIONAL | `n.barreto` |
| Waste Management | Torres Vedras | OPERATIONAL | `a.duarte` |
| Road Maintenance | Torres Vedras | OPERATIONAL | `t.pereira` |
| Public Lighting | Torres Vedras | OPERATIONAL | `s.almeida` |
| Public Health | São Pedro e Santiago | OPERATIONAL | `m.silva` |
| Urban Cleaning | São Pedro e Santiago | OPERATIONAL | `p.lopes` |
| Green Spaces & Parks | Matacães | OPERATIONAL | `r.costa` |

> Extra service actors: `l.rodrigues` (Operations Manager) on Water Supply; `p.nunes` (Operator) on Waste Management and Road Maintenance.

### Operational samples

- **Requests** (24) spread across all 9 services — NEW / IN_PROGRESS / WAITING / RESOLVED, various priorities; waste & water requests are linked to their workflow.
- **Incidents** (8): `Burst main on Rua do Espírito Santo` (INVESTIGATING), `Substation fault…` (OPEN), `Collection vehicle breakdown` (RESOLVED), etc.
- **Problems** (3): `Recurring water main failures`, `Failing lighting control gear`, `Sewer blockages in the old town` (linked to incidents).
- **Changes** (4): `Replace aging trunk main section` (PROPOSED), `Smart bins pilot rollout` (SCHEDULED), with EXECUTIVE approval rows.
- **Workflows** (2): `Waste Collection Request` and `Water Maintenance Request` — each a `start → assign → work → verify → end` definition with transitions, plus 4 executions/tasks (running + completed).
- **Resources**: skip truck `TV-103`, bulk water tanker, road compactor — each assigned to field operator `p.nunes`.
- **Metrics**: `requests_created`/`requests_resolved` with 6 months of monthly values (a "waste service health" story), plus KPI `waste_service_health` and two SLAs (240 min / 1440 min response and resolution targets on Waste Management).
- **Events/audit**: a `request.created` Event and a `seed.default_dataset` AuditLog.

`--reset` truncates the domain tables (`organizations_*`, `services_*`, … — RBAC and `auth_user` are preserved except stale demo users) and re-seeds cleanly.

---

## 3. The logic (as implemented & understood)

### Central authorization (`apps/common`)
- `can(user, perm, obj)` decides a single permission; `ModelManager.visible_to(user)` filters to DB level (never in Python) following the org tree: **access flows down** (inherited by descendants, never up/sideways).
- Views use `PermissionRequiredMixin` + `platform_codes`; detail/create views scope to the user's organization (`ScopedDetailView`).
- `ServiceAccess` rows restrict an org membership to specific services; **no rows = all services** under that org subtree.

### RBAC roles (`seed_rbac`, 41 permissions + 9 roles)
Roles are permission bundles; every seeded person gets an `OrganizationMembership` (person↔org↔role):

| Role | Focus | Key permissions |
|---|---|---|
| `EXECUTIVE` | Platform leadership | org view/manage/delete, people manage, metrics, **change.approve**, audit, role.* |
| `SERVICE_MANAGER` | Own a service | service.manage, request/incident manage, problem.manage, workflow.manage, resource.manage, change.manage |
| `OPERATIONS_MANAGER` | Field operations | request assign/close, incident assign/resolve, workflow.execute, resource.manage |
| `OPERATOR` | Field staff | request.view/update, incident.create, workflow.execute, resource.view |
| `REQUEST_MANAGER` | Demand workflow | request create/update/assign/close, workflow.execute |
| `INCIDENT_MANAGER` | Incident response | incident create/assign/resolve, problem.view |
| `PROBLEM_MANAGER` | Root-cause | problem.manage, change.approve/manage, metrics.view |
| `ANALYST` | Reporting | view requests/incidents/problems, metrics.view/export |
| `SUPPLIES_MANAGER` | Materials | resource.manage, change.view/manage |

### Service Manager requirement (workflow-level rule)
A Service is created with a **Service Manager** (`ServiceCreateForm`/`ServiceCreateView`):
1. The form is a `ModelChoiceField` over active people.
2. On save the service is created, the `SERVICE_MANAGER` role is `get_or_create`d, and a `ServiceActor(actor_type=INTERNAL, active=True)` is linked to the chosen person.
3. `Service.clean()` raises `ValidationError` if an updated service would be left without an active manager; the seed stderr-fails if any service has none.

### Workflows (definition vs instance)
`Workflow` = immutable definition (service, name, version, status ACTIVE, trigger REQUEST) with ordered `WorkflowStep`s (START / TASK / APPROVAL / END, `responsible_role`, `required_inputs`, SLA minutes) and `WorkflowTransition`s. `WorkflowExecution` = a run of a definition: `status` (RUNNING/COMPLETED/…), `current_step`, `completed_at`, plus `WorkflowTask`s (assigned to a person, PENDING/IN_PROGRESS/DONE, deadline).

### Catalog lifecycle (soft-delete)
Organization / Service / Person / Resource / Role support **create / activate / deactivate / soft-delete** via generic lifecycle views (`apps/common/lifecycle.py`): POST-only, gated by `*.create` / `*.manage` / `*.delete`, scoped (404 outside scope). `soft_delete()` fills `deleted_at`; lists hide deleted rows via `.alive()` (seeds keep working because `get_or_create` still sees them).

### Data flow for a request
`Request` (`requester`, `service`, `request_type`, `status`, `priority`, `assigned_to`, `workflow`) → optional `WorkflowExecution`/`WorkflowTask` → comments, linked incidents → problems/changes for root cause → metrics/KPI rolled up → `Event`/`AuditLog` recorded → dashboards aggregate counts.

---

## 4. Accounts

All demo users have password **`<username>123`** (e.g. `a.duarte` / `a.duarte123`).

| Account | Display name | Role | Membership / service |
|---|---|---|---|
| `admin` | System Administrator | superuser / staff | everything (`admin123` by default, env overridable) |
| `r.martins` | Rita Martins | EXECUTIVE | Torres Vedras |
| `j.fernandes` | José Fernandes | SERVICE_MANAGER | Water Supply |
| `a.duarte` | Ana Duarte | SERVICE_MANAGER | Waste Management |
| `t.pereira` | Tiago Pereira | SERVICE_MANAGER | Road Maintenance |
| `s.almeida` | Sofia Almeida | SERVICE_MANAGER | Public Lighting |
| `m.silva` | Mariana Silva | SERVICE_MANAGER | Public Health |
| `r.costa` | Rui Costa | SERVICE_MANAGER | Green Spaces & Parks |
| `n.barreto` | Nuno Barreto | SERVICE_MANAGER | Sanitation Network |
| `p.lopes` | Paula Lopes | SERVICE_MANAGER | Urban Cleaning |
| `l.rodrigues` | Luís Rodrigues | OPERATIONS_MANAGER | Torres Vedras (+Water actor) |
| `m.mendes` | Marta Mendes | REQUEST_MANAGER | Torres Vedras |
| `b.lopes` | Beatriz Lopes | INCIDENT_MANAGER | Torres Vedras |
| `h.ferreira` | Hugo Ferreira | PROBLEM_MANAGER | Torres Vedras |
| `i.cardoso` | Inês Cardoso | ANALYST | Torres Vedras |
| `p.nunes` | Pedro Nunes | OPERATOR | São Pedro e Santiago (+ Waste/Road actors) |

---

## 5. How to run

```bash
# Docker (db + web):
docker compose up -d --build        # web at http://localhost:8000
# entrypoint runs migrate + seed_rbac + seed_default_data automatically
# (set SEED_DATA=0 to skip seeding)

# Fresh reset with demo data:
docker compose exec web python manage.py seed_default_data --reset

# Local, no Docker:
docker compose up -d db
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_default_data
python manage.py runserver        # or: pytest
```

Main surfaces: `/` (Executive dashboard), `/organizations/tree/` (org tree with services & managers), `/services/`, `/requests/`, `/incidents/`, `/workflows/`, `/metrics/`, `/admin/`.

---

## 6. Test status & known issues

- Test suite: **129 passed, 12 failed** (pre-existing, unrelated to the seed work).
- Known failures:
  - Dashboard `/` — `TypeError: Object of type __proxy__ is not JSON serializable` (lazy `_("Requests")` chart-series name passed to `json.dumps` in `apps/dashboard/views.py:_bar`).
  - Login template — `{% blocktranslate %}` containing `{% url 'admin:login' %}` fails to render.
  - A handful of `test_views.py` render tests depending on the two above.

**Do not edit** `STATUS.md` without also checking whether `README.md` needs an update (and vice-versa) — these two documents are the source of truth for how the platform behaves.