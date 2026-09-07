# Domain model

The platform models an organization (e.g. a municipality) that provides operational **services**
to citizens. Around each service orbit the operational entities: requests, incidents, problems,
changes, workflows, resources, people and metrics — all anchored to organizations/services so a
single authorization model can govern every one of them.

## organizations

| Model | Purpose | Key fields / constraints |
|---|---|---|
| `Organization` | Node in District → Municipality → Parish hierarchy | `name`, `code` (slug, path segment), `parent` (self-FK), `path` (ltree, `editable=False`, auto-built in `save()`), `organization_type`. Unique `(parent, code)`; GiST index on `path`. Helpers: `get_ancestors/get_descendants(include_self)`. |
| `OrganizationMembership` | A person belongs to an organization with a role | FK `person` (people), FK `organization`, FK `role` (access). Unique `(person, organization, role)` — one may hold several roles in the same org, each with its own `ServiceAccess`. `start_date`/`end_date` optional. |
| `OrganizationUnit` | Internal unit/department of an org (flexible) | Own ltree `path` scoped per organization, `parent` self-FK. Unique `(organization, parent, code)`. |

## people

| Model | Purpose | Key fields |
|---|---|---|
| `Person` | Actor (employee, contractor, supplier, citizen, partner) | optional OneToOne to `auth.User` (login identity kept separate), `email` (unique when set), `person_type`. |
| `Skill` | Reusable qualification | `code` unique, `name` unique. |
| `PersonSkill` | A person holds a skill | `person`, `skill`, `level` (BASIC…EXPERT), `verified_by`/`verified_at`. Unique `(person, skill)`. |

## access

| Model | Purpose | Key fields |
|---|---|---|
| `Permission` | Atomic action `module.verb`, e.g. `request.view` | `code` unique. Read and write are distinct atoms. |
| `Role` | Reusable role (EXECUTIVE, OPERATOR, …) | `code` unique, M2M `permissions` via `RolePermission`. Not owned by any organization. |
| `RolePermission` | Grant of a permission to a role | `active` flag. Unique `(role, permission)`. |
| `ServiceAccess` | Restricts a membership to a subset of services | Unique `(membership, service)`. See `authorization.md` for semantics. |

## services

| Model | Purpose | Key fields |
|---|---|---|
| `Service` | An operational "micro-organism" of an organization | FK org, `name`, `status` (OPERATIONAL…RETIRED), `active`. Unique `(organization, name)`. |
| `ServiceCapability` | Reusable capability (e.g. Route Planning) | M2M `services` via `ServiceCapabilityAssignment`. |
| `ServiceCapabilityAssignment` | Link capability ↔ service | `required` flag. Unique `(service, capability)`. |
| `ServiceActor` | Who acts for a service | FK `service`, FK `role`, optional FK `person`, `actor_type` (INTERNAL/EXTERNAL). Unique constraints keep a person linked once per (service, role) and a role placeholder once per service. |

## workflows

| Model | Purpose | Key fields |
|---|---|---|
| `Workflow` | Versioned *definition* of work for a service | FK service, `name`, `version` (default 1), `status` (DRAFT/ACTIVE/DEPRECATED), `trigger`, `created_by`. Unique `(service, name, version)`. `current_version()` = newest non-draft. |
| `WorkflowStep` | Activity/decision in a definition | FK workflow, `code`, `step_type` (START/TASK/GATEWAY/APPROVAL/END), `order`, `responsible_role`, `sla_minutes`, `required_inputs`/`outputs` (JSON). Unique `(workflow, code)` and `(workflow, order)`. |
| `WorkflowTransition` | Directed move between steps (branching) | FK workflow, `from_step`, `to_step`, `condition` (JSON). Unique `(workflow, from_step, order)`. |
| `WorkflowExecution` | An *instance* (run) of a definition | FK workflow (PROTECT), `status` (PENDING/…), `current_step`, generic `subject`, timestamps, `created_by`. |
| `WorkflowTask` | Executable work item of an execution | FK execution, FK step, `title`, `assigned_to` (person), `status`, `deadline`, `result` (JSON). |

A request triggers a workflow; the linkage is the generic `subject` on `WorkflowExecution`.

## requests

| Model | Purpose | Key fields |
|---|---|---|
| `Request` | A demand entering the service | FK service, FK `request_type`, `title`, `status` (NEW…CLOSED/CANCELLED), `priority`, `requester`/`created_by`/`assigned_to` (people), optional FK `workflow` to trigger. |
| `RequestType` | Reusable category | `code` unique; optional per-service scoping. |
| `RequestComment` | Communication on a request | FK request, FK `author` (person), `body`. |

## incidents & problems

| Model | Purpose | Key fields |
|---|---|---|
| `Incident` | Something is wrong **now** | FK service, `title`, `priority`, `impact`, `urgency`, `status` (OPEN/INVESTIGATING/RESOLVED/CLOSED), `reported_by`/`assigned_to`, `started_at`/`resolved_at`. |
| `IncidentUpdate` | Progress/history | FK incident, `author`, `message`, `status`. |
| `Problem` | Why does this keep happening? | FK service, `title`, `status` (OPEN…CLOSED), `priority`, `assigned_to`, `root_cause`. |
| `ProblemIncident` | Links incidents to a problem | Unique `(problem, incident)`. |

## changes

| Model | Purpose | Key fields |
|---|---|---|
| `Change` | Controlled, authorized modification | FK `service` or FK `organization` (exactly one target), `title`, `risk` (LOW/MEDIUM/HIGH), `status` (DRAFT…ROLLED_BACK), `requested_by`, planned window. |
| `ChangeApproval` | Mandatory approval | FK change, FK `approver` (person), FK `role` (who authorizes), `decision` (PENDING/APPROVED/REJECTED), `comment`, `decided_at`. Unique `(change, approver)`. |

## resources

| Model | Purpose | Key fields |
|---|---|---|
| `Resource` | Generic resource (base) | `resource_type` (VEHICLE/EQUIPMENT/MATERIAL/FACILITY/SUPPLIER), `name`, `code`, org/service scope, `active`. |
| `Asset` | Persistent identifiable resource | extends Resource; `serial_number`, `purchase_date`, `status` (AVAILABLE…RETIRED). |
| `Equipment` | Specialized asset | extends Asset; `equipment_type`, `capacity`. |
| `Material` | Consumable | extends Resource; `quantity_on_hand`, `unit`, `reorder_level`. |
| `ResourceAssignment` | Allocation of a resource to a target | FK resource, generic `target` (service, person, org, task…), `label`, start/end. |

## events & audit

| Model | Purpose | Key fields |
|---|---|---|
| `Event` | Immutable record of what happened | `event_type`, generic `subject`, `actor` (person), denormalized `organization`/`service`, `metadata` (`editable=False`, immutable by convention). |
| `AuditLog` | Immutable compliance record | `action`, `actor_person`/`actor_user`, generic `object`, denormalized `organization`/`service`, `old_value`/`new_value` (JSON). |

## metrics

| Model | Purpose | Key fields |
|---|---|---|
| `Metric` | Definition of a measurable value | `code` unique, `unit`, `aggregation` (SUM/AVG/COUNT/LAST/MIN/MAX), optional FK service. |
| `MetricValue` | Observation over a window | FK metric, `value`, `period_start`/`period_end`, `dimension` (JSON slices, e.g. `{service: 5}`). |
| `KPI` | Higher-level indicator | `code` unique, M2M `metrics` via `KpiMetric` (weighted), optional FK service, `target_value`/`tolerance`. |
| `KpiMetric` | Weighted contribution of a metric to a KPI | Unique `(kpi, metric)`. |
| `SLA` | Service-level expectation | FK service (optional), `name`, `priority`, `scope` (RESPONSE/RESOLUTION), `target_minutes`. Unique `(service, name, scope)`. |

## Relationship map (summary)

```
Organization ──< Service ──< Request / Incident / Problem / Change / Workflow / Metric / Actor
     │            │
     ├─< Membership >─ Person      Workflow ──< Execution ─< (any subject: Request, Incident, Change)
     └─< Unit       └─< ServiceAccess (restricts membership to services)
```

- Almost every domain table is reachable from a `Service` → `Organization`, which is exactly what the centralized authorization layer exploits (see `authorization.md`).
- Cross-app links (execution subjects, events, audit objects, resource assignments) use `GenericForeignKey` to avoid circular imports.