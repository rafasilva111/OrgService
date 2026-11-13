# orgplatform — Organizational Service Platform

A Django **monolithic** service-management platform for a (demo) **Portuguese municipality**: `Torres Vedras, Lisboa`. It models the organisation tree, the municipal services it delivers, the people and roles who run them, and the operational day-to-day: service requests, incidents, problems, changes, offering executions, resources, KPIs/metrics and audit events.

The default dataset makes the app immediately explorable — 9 municipal services, 17 demo accounts, ~24 requests, incidents, problems, changes, offering runs and metric time-series, all under a realistic `Portugal → Lisboa → Torres Vedras` hierarchy.

---

## 1. The project

- **Stack**: Python 3.12 / Django 5.2 / PostgreSQL 16 / pytest. Runs locally or via Docker Compose.
- **Shape**: a single `config/` project + 16 app packages under `apps/` — 13 domain apps plus `common` (shared helpers), `dashboard` (views only) and `profiles` (profile/settings). Listed below.
- **Postgres is mandatory** — the org hierarchy uses the `ltree` extension (`apps/common/fields.py`), indexed with a GiST index.
- **Authorization is centralized** — `apps/common/authorization.py` provides `can()` and `.visible_to()` queryset managers; operational data is scoped by RBAC, while every authenticated user can read the people directory globally. People write actions remain permission-gated.
- **Docs in Portuguese**: `docs/architecture.md`, `docs/domain-model.md`, `docs/authorization.md`, `docs/er-diagram.mmd`; outstanding issues in `ISSUES.md`.

### Apps (14 domain apps + `common` + `dashboard`)

| App | Holds | Notes |
|---|---|---|
| `organizations` | Organization (ltree tree), OrganizationMembership, OrganizationUnit | `PORTUGAL → LISBOA → TORRES-VEDRAS → 3 parishes` |
| `services` | Service, ServiceActor, ServiceCapability/Assignment | each Service requires a Service Manager actor |
| `people` | Person, Skill, PersonSkill | Person links 1:1 to a Django `User` |
| `access` | Permission, Role, RolePermission, ServiceAccess | RBAC catalogue seeded by `seed_rbac` |
| `requests` | Request, RequestComment | citizen-facing demand tickets; each asks for one service offering |
| `incidents` | Incident, IncidentUpdate | disruptions & outages (+ Impact/Urgency) |
| `problems` | Problem, ProblemIncident | root-cause management |
| `changes` | Change, ChangeApproval | planned changes + approval chain |
| `offerings` | ServiceOfferingType, ServiceOffering, OfferingStep, OfferingTransition, OfferingExecution, OfferingTask | definition ≠ instance; type classifies offerings |
| `resources` | Resource, Asset, Equipment, Material, ResourceAssignment | vehicles/equipment |
| `metrics` | Metric, MetricValue, KPI/KpiMetric, SLA | dashboard data |
| `events` | Event | immutable, generic-FK |
| `audit` | AuditLog | immutable, generic-FK |
| `dashboard` | views only | executive/org/service dashboards (charts) |
| `profiles` | profile settings | header dropdown |

> `events`/`audit` and generic links (`Problem→Incident`, offering `subject`) use Django **GenericForeignKey** to avoid circular dependencies between apps.

---

## 2. The data (default dataset)

Seeded by `python manage.py seed_default_data [--reset]` (idempotent — stable codes/emails, re-running updates rather than duplicates). The entrypoint also runs it automatically on container start.

### Organization tree

```
Portugal            (COUNTRY)
└── Lisboa          (DISTRICT)
    └── Torres Vedras (MUNICIPALITY)
        ├── Santa Maria, São Pedro e Matacães (PARISH)
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
| Serviços Gerais | Santa Maria, São Pedro e Matacães | OPERATIONAL | `m.silva` |
| Serviços Educativos | Santa Maria, São Pedro e Matacães | OPERATIONAL | `r.costa` |
| Espaço Público, Infraestruturas e Edifícios | Santa Maria, São Pedro e Matacães | OPERATIONAL | `p.lopes` |
| Serviços Sociais e Associativismo | Santa Maria, São Pedro e Matacães | OPERATIONAL | `m.silva` |
| Cemitérios | Santa Maria, São Pedro e Matacães | OPERATIONAL | `p.lopes` |
| Limpeza Urbana e Espaços Verdes | Santa Maria, São Pedro e Matacães | OPERATIONAL | `p.lopes` |

> Extra service actors: `l.rodrigues` (Operations Manager) on Water Supply; `p.nunes` (Operator) on Waste Management, Road Maintenance and Cemitérios; `a.santos` and `c.oliveira` (Operators) on Cemitérios.

### Operational samples

- **Requests** (24) spread across all 9 services — NEW / IN_PROGRESS / WAITING / RESOLVED, various priorities; waste & water requests are linked to their offering.
- **Incidents** (8): `Burst main on Rua do Espírito Santo` (INVESTIGATING), `Substation fault…` (OPEN), `Collection vehicle breakdown` (RESOLVED), etc.
- **Problems** (3): `Recurring water main failures`, `Failing lighting control gear`, `Sewer blockages in the old town` (linked to incidents).
- **Changes** (4): `Replace aging trunk main section` (PROPOSED), `Smart bins pilot rollout` (SCHEDULED), with EXECUTIVE approval rows.
- **Service Offerings** (5): `Waste Collection Request` and `Water Maintenance Request` — each a `start → assign → work → verify → end` definition with transitions, plus 4 executions/tasks (running + completed).
  Cemitérios (Matacães) adds three Portuguese offerings owned by `p.lopes`: `Limpar cemitério` (on request + monthly on day 1), `Realizar um funeral` and `Realizar transladação`. Every offering's steps are chained linearly by `order`. A step can name a responsible offering of another service (`OfferingStep.service_offering`; its service is `service_offering.service`).
- Cemitérios includes six sample executions across its three offerings, in running, waiting and completed states; completed runs include task records and action history for every manual step. Re-seeding fills missing task history without resetting executions.
- The six cemetery demo requests include fictitious form answers and clearly marked sample PDF attachments. Re-seeding leaves existing answers and attachments untouched.
- New requests start an execution and create tasks as steps are reached. Execution details show assignees, timings and recorded action history; notifications appear in the header bell.
- Request and incident details show steps for an accessible linked execution, with task actions subject to the same permissions as execution details. Incidents do not yet start executions automatically.
- Request details also show the execution log and allow authorized viewers to add notes; existing request comments remain visible.
- A task assignee or scoped manager can pause an in-progress task with a reason and resume it; the execution and linked request wait until resumed. Pause and resume events are recorded, and the comments reason requires a new comment. Existing deadlines are not adjusted.
- Requests linked to executions follow their execution state: pending → new, running → in progress, waiting → waiting (or failed when the current task is blocked), completed → resolved, failed → failed and cancelled → cancelled. Such requests cannot be closed independently.
- Demo seeding creates a request for each otherwise-unassociated execution, using its creator as requester when available, and preserves existing links and execution history. `python manage.py rebuild_execution_requests --confirm-delete-all-requests` explicitly deletes all existing requests and their related records before rebuilding these links; it does not delete incidents.
- Offering steps can specify a responsible role from the service's configured actors (or the delegated service's actors). Eligible members with scoped execution permission can claim group tasks; managers may assign or reassign them.
- The offering productivity dashboard compares accessible offerings across versions, with preset periods, execution counts, daily activity and completed manual tasks by recorded assignee. It does not yet calculate pause-adjusted SLA time.
- Offering and execution step panels show the average active duration of completed tasks for that step and offering version, excluding recorded pauses. Aggregate timings require service and offering visibility.
- On the offering detail page, expand a step to see its configured SLA and average active, elapsed and paused times for reliable completed manual tasks of that version.
- The offering detail page lists currently eligible people for the selected step, with ten per page, completed tasks by assignee and average active, elapsed and paused times for reliable completed tasks.
- For a larger cemetery productivity demo, run `python manage.py seed_cemetery_productivity` after the default seed. It adds 100 marked fictional requests and executions across the previous three months; re-running leaves them untouched.
- The seeded superuser has administrative access without seeded operational roles; its person record is inactive by default and is not offered for task or incident assignment or as a service manager. Re-seeding preserves existing person and membership settings.
- The role catalogue follows the header context: a selected service shows its active configured roles; a selected organization shows active membership roles and roles configured on visible services in its subtree. With no organization selected, it shows the global catalogue. Role definitions and their permissions remain shared.
- Managers can edit a service offering from its detail page. Editing a published offering creates a draft version; existing executions continue to use their original version.
- Each offering version has a configurable default request priority, initially **Very low**. New requests inherit it and display it as read-only; existing requests retain their recorded priority.
- New requests use the signed-in user's linked person as requester and creator; an account without a linked person cannot create a request.
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
Roles are permission bundles; every seeded person gets an `OrganizationMembership` (person↔org↔role). Service managers get a **per-service** clone of `SERVICE_MANAGER` (e.g. `SERVICE_MANAGER_CEMETERIES`, every permission scoped to that one service) with a membership at the service's organization, so they only reach the services they manage:

| Role | Focus | Key permissions |
|---|---|---|
| `EXECUTIVE` | Platform leadership | org view/manage/delete, people manage, metrics, **change.approve**, audit, role.* |
| `SERVICE_MANAGER` | Own a service | service.manage, request/incident manage, problem.manage, offering.manage, resource.manage, change.manage |
| `OPERATIONS_MANAGER` | Field operations | request assign/close, incident assign/resolve, offering.execute, resource.manage |
| `OPERATOR` | Field staff | request.view/update, incident.create, offering.execute, resource.view |
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

### Service Offerings (definition vs instance)
`ServiceOffering` = immutable definition (service, name, version, status ACTIVE, `on_request` and/or `on_schedule`) with ordered `OfferingStep`s (START / TASK / APPROVAL / END, `responsible_role`, `required_inputs`, SLA minutes) and `OfferingTransition`s. `OfferingExecution` = a run of a definition: `status` (RUNNING/COMPLETED/…), `current_step`, `completed_at`, plus `OfferingTask`s (assigned to a person, PENDING/IN_PROGRESS/DONE, deadline).

### New request = service + offering
`/requests/new/` asks for a **Service** first — pre-selected from the header's service (or `?service=<pk>`) — and then one of **that service's active offerings** (required). Changing the service reloads the offering list over HTMX (`/requests/new/offerings/?service=<pk>`). There is no separate request type: the type is the offering's `ServiceOfferingType` (e.g. Manutenção, Limpeza, Serviço funerário).

### Building service offerings
Managers with `offering.manage` create offerings at `/service-offerings/new/` (button on the list): offering fields (the offering type can be left "to be defined") plus an editable list of steps (responsible offering picked by district → municipality → parish, then step type and SLA), each with a *next* step and an optional *on error* branch; **Notification** steps hold a message for the requester. An offering starts **on request** (listed in New request) and/or **on a recurring schedule** (daily/weekly/monthly/yearly, with start/end dates). Each offering stores a **total SLA**, mirrored as a resolution SLA record listed under Analytics › SLAs (`/metrics/slas/`) (sum of the step SLAs on the normal path; a step handed to another service counts that offering's total, and its type/role come from it). SLAs are typed as durations such as `1d 4h 30m`. Each offering can also define a **request form** (text, number, date, yes/no, email, phone, choice, multiple choice, attachment, address) whose questions appear in New request when it is chosen; answers are stored on the request and attachments (≤ 10 MB) are kept under `MEDIA_ROOT` and only downloadable by users who can see the request. Offerings start as **drafts**, **Publish** makes them available for new requests (the previous active version is deprecated), and a published offering is changed through **New version**.

On request creation an execution starts on the published offering. Authorized workers start and complete tasks; managers assign them and resolve failures. **Service Offerings › Service tasks** lists active tasks with search and filters scoped to the service responsible for each step, including delegated steps. The execution log combines viewer notes with recorded task updates; delegated users only see entries for their current task. Notification steps generate in-app notifications, and scheduled offerings are not yet run automatically. To create missing current-step tasks for older active executions, run `python manage.py backfill_execution_tasks --dry-run` first, then `python manage.py backfill_execution_tasks`; use `--service-id <id>` to restrict the operation.

### Permissions and protected reporting

Role permissions are restricted to each membership's organization subtree and any service restrictions. `python manage.py preview_permission_policy` reports the role-policy impact before deployment. The access migration updates existing Executive and Analyst grants; back up the database and restore desired grants explicitly if rollback is required. Requesters can see the full execution log for their own requests, including notes written before this policy. The protected overview requires `dashboard.aggregate`: it shows only scoped totals of at least five, suppresses groups overlapping the user's own record access, and provides no record drill-down. Totals can still be inferred from external knowledge or comparisons across snapshots; do not use this as a formal anonymity guarantee.

### Catalog lifecycle (soft-delete)
Organization / Service / Person / Resource / Role support **create / activate / deactivate / soft-delete** via generic lifecycle views (`apps/common/lifecycle.py`): POST-only, gated by `*.create` / `*.manage` / `*.delete`, scoped (404 outside scope). `soft_delete()` fills `deleted_at`; lists hide deleted rows via `.alive()` (seeds keep working because `get_or_create` still sees them).

### Data flow for a request
`Request` (`requester`, `service`, `offering` → its `offering_type`, `status`, `priority`, `assigned_to`) → `OfferingExecution`/`OfferingTask` for new requests → comments, linked incidents → problems/changes for root cause → metrics/KPI rolled up → `Event`/`AuditLog` recorded → dashboards aggregate counts.

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
| `m.silva` | Mariana Silva | SERVICE_MANAGER | Serviços Gerais, Serviços Sociais e Associativismo |
| `r.costa` | Rui Costa | SERVICE_MANAGER | Serviços Educativos |
| `n.barreto` | Nuno Barreto | SERVICE_MANAGER | Sanitation Network |
| `p.lopes` | Paula Lopes | SERVICE_MANAGER | Espaço Público, Infraestruturas e Edifícios; Cemitérios; Limpeza Urbana e Espaços Verdes |
| `l.rodrigues` | Luís Rodrigues | OPERATIONS_MANAGER | Torres Vedras (+Water actor) |
| `m.mendes` | Marta Mendes | REQUEST_MANAGER | Torres Vedras |
| `b.lopes` | Beatriz Lopes | INCIDENT_MANAGER | Torres Vedras |
| `h.ferreira` | Hugo Ferreira | PROBLEM_MANAGER | Torres Vedras |
| `i.cardoso` | Inês Cardoso | ANALYST | Torres Vedras |
| `p.nunes` | Pedro Nunes | OPERATOR | Santa Maria, São Pedro e Matacães (+ Waste/Road actors) |
| `a.santos` | André Santos | OPERATOR | Santa Maria, São Pedro e Matacães (+ Cemitérios actor) |
| `c.oliveira` | Carla Oliveira | OPERATOR | Santa Maria, São Pedro e Matacães (+ Cemitérios actor) |

---

## 5. How to run

```bash
# Docker (db + web):
docker compose up -d --build        # web at http://localhost:8000
# entrypoint runs migrate + seed_rbac + seed_default_data automatically
# (set SEED_DATA=0 to skip the demo dataset; seed_rbac always runs)

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

Main surfaces: `/` (context-aware overview), `/organizations/tree/` (org tree with services & managers), `/services/`, `/requests/`, `/incidents/`, `/service-offerings/`, `/metrics/`, `/admin/`.

The header profile menu selects the working **organization** and then an optional **service/role**. The Overview link follows that selection: service → `/service/<pk>/`, organization → `/organization/<pk>/`, neither → the global executive overview. Clicking the active service clears it; clicking the active organization clears both. Services stay hidden until an organization is selected. List views follow the same scope automatically: a selected service scopes lists to it, and a selected organization scopes them to its subtree when no service is picked. An explicit `?organization=<pk>` (subtree scope) or `?service=<pk>` drill-down filter overrides the header scope for that request; an empty value (`?service=`) means “All”.

### CI/CD & automatic deploy (GitHub Actions)

- **Pull request into `dev`/`main`** → `.github/workflows/ci.yml` runs lint (ruff), `pip-audit` and pytest (Postgres 16).
- **Push/merge to `main` or `dev`** → `.github/workflows/deploy.yml` reruns the CI and, if it passes, SSHes into the server, checks out the pushed commit and runs `docker compose -f docker-compose.prod.yml up -d --build`, then waits for a health check.
- `docker-compose.prod.yml` bakes the code into the image (no bind mounts), keeps Postgres off the host, forces `ENVIRONMENT=prod` (gunicorn + WhiteNoise) and seeds the demo data only when `SEED_DATA=1`.
- Required secrets, in a GitHub Environment named after each branch (`main`, `dev`): `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `DEPLOY_PATH`, optional `DEPLOY_PORT`. The server keeps its own `.env`.
- Server setup and rollback: `.opencode/obsidian/Procedures/Deploying.md`.

---

## 6. Test status & known issues

- Test suite: **334 passed, 0 failed** (`.venv/bin/python -m pytest -q`).
- Lint and formatting are clean (`.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`).
- Model state is in sync with migrations (`.venv/bin/python manage.py makemigrations --check --dry-run`).
- Run pytest **sequentially** — concurrent runs race on the shared `test_orgplatform` database.

Outstanding issues and their resolutions are recorded in [`ISSUES.md`](ISSUES.md). Architecture and
domain documentation lives in `docs/`; the Obsidian vault under `.opencode/obsidian/` holds the
decision record (ADRs) and task notes.
