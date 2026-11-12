# ISSUES.md

Test-run findings for **orgplatform** — the Django service-management platform for
Torres Vedras.

This document records every issue found by running the test suite, the evidence
behind each diagnosis, and how it was resolved.

> **Status: all resolved.** The suite went from **105 passed / 40 failed / 22 errors**
> to **169 passed / 0 failed**, with Ruff clean and `makemigrations --check`
> reporting no drift. See [§3 Final verification](#3-final-verification).

---

## 1. How to reproduce

```bash
.venv/bin/python -m pytest -q
```

Requires PostgreSQL on `127.0.0.1:5432` with the credentials in `.env` — the org
hierarchy depends on the `ltree` extension, and per
`.opencode/obsidian/Decisions/008-Real-Migrations-in-Tests.md` the test database is
built by **real migrations**, not from models.

> **Run pytest one process at a time.** Two concurrent runs collide while creating
> the shared `test_orgplatform` database and every test errors out with
> `duplicate key value violates unique constraint "pg_database_datname_index"`.
> See [Issue 7](#issue-7--tooling-footguns-do-not-change-passfail-results).

## 2. Baseline (before any fix)

| | |
|---|---|
| Collected | **167** |
| Passed | 105 |
| Failed | 40 |
| Errors | 22 |
| **Total problems** | **62** |

Re-running produces a byte-identical failure list, so this baseline is
deterministic.

**The 62 failures are not 62 bugs.** They collapse into **2 root causes**: one is a
test-code defect that accounts for 60 of them, and the other is an application bug
accounting for 2. A third, latent crash that no test originally reached was
uncovered while fixing the second and is recorded as [Issue 3](#issue-3--servicetype-name-collision).

| # | Root cause | Kind | Tests hit |
|---|---|---|---|
| 1 | Test factories omit the required `Service.service_type` | Test-code defect | 60 |
| 2 | `ServiceCreateView.get_form()` reads `cleaned_data` too early | **Application bug** | 2 |
| 3 | `ServiceType` name collision breaks `views.py` | **Latent application bug** | 0 → 1 |
| 4 | Missing `services` migration | Process violation (ADR 008) | — |
| 5 | Lint and format violations | Hygiene | — |
| 6 | Documentation states wrong numbers and fixed bugs | Documentation drift | — |
| 7 | Tooling footguns | Hygiene | — |
| 8 | `Service.dimension` always `None` | **Application bug** | 0 |

## 3. Final verification

| Check | Command | Result |
|---|---|---|
| Tests | `.venv/bin/python -m pytest -q` | **169 passed**, 0 failed |
| Lint | `.venv/bin/ruff check .` | All checks passed |
| Format | `.venv/bin/ruff format --check .` | 199 files already formatted |
| Migrations | `.venv/bin/python manage.py makemigrations --check --dry-run` | No changes detected |

The count rose from 167 to 169 because [Issue 8](#issue-8--servicedimension-always-resolves-to-none)
and the Management fan-out added two regression tests for bugs that previously had
no coverage at all.


---

## Issue 1 — Test factories omit the required `Service.service_type`

**Impact: 60 of 62 failures (97%).** The single cause of almost the entire red suite.

**Status: resolved.**

### Symptom

```
django.db.utils.IntegrityError: null value in column "service_type_id"
of relation "services_service" violates not-null constraint
DETAIL:  Failing row contains (1, ..., 'Waste Management', , OPERATIONAL, 3, null, null, null)
```

### Root cause

`Service.service_type` is a **required** foreign key. The model, the migration and
the view layer all agree on this:

`apps/services/models.py:84-90` — declared without `null=True`:

```python
service_type = models.ForeignKey(
    "ServiceType",
    on_delete=models.PROTECT,
    related_name="services",
    verbose_name=_("service type"),
    help_text=_("Classification of this service (maps to an ITIL dimension)."),
)
```

`apps/services/migrations/0003_service_type_parent_consumption.py` applies the
standard add-nullable → backfill → tighten sequence, and its **final state is NOT
NULL** (lines 84-94 add it nullable, line 140 backfills, lines 141-150 `AlterField`
it back to non-null).

`apps/services/views.py:47` documents the contract in the form docstring:
*"**`service_type` is required.**"*

The test fixtures predate that refactor. `tests/conftest.py:85-93`:

```python
@pytest.fixture
def make_service(db):
    def _make(organization, name="Waste Management"):
        from apps.services.models import Service

        return Service.objects.create(organization=organization, name=name)

    return _make
```

`service_type` is never passed, so Django sends `NULL` and PostgreSQL rejects it.
`Model.objects.create()` does not run `full_clean()`, so the model-level
constraint is not enforced and the failure surfaces as a raw database error
rather than a validation error.

This is why **seeding works while tests do not** — `seed_default_data.py` sets
`service_type` on every service it creates (lines 255, 262, 268-269, 289, 293-294).

### Resolution

Supply a `service_type` from the fixture, defaulting to the existing `processes`
row:

- `tests/conftest.py` — `make_service` now takes an optional `service_type` and
  falls back to `ServiceType.objects.get(code="processes")`.
- `tests/test_requests_authorization.py:77` — direct `Service.objects.create` given
  a type.
- `tests/test_services.py:18` — direct `Service.objects.create` given a type.

Three edits fix ~90 call sites, because `make_service` is the shared factory for
every other test.

Two deliberate choices:

- **`processes`, not `management`.** `ServiceCreateView` auto-creates four
  department children when `service_type.code == ServiceTypeCode.MANAGEMENT`
  (`apps/services/views.py`, in `form_valid()`). A neutral default keeps fixtures
  free of that side effect. The fan-out is now covered by its own dedicated
  regression test rather than being reached by accident through a factory.
- **Reuse the migrated rows, never create new ones.** Migration `0003`'s
  `RunPython(create_service_types)` already inserts the six types (`finance`, `hr`,
  `supplies`, `it`, `processes`, `management`) into the test database, so the
  fixture can just look one up. Verified present in the dev database:

  ```
  finance | ORGANIZATIONS_AND_PEOPLE        it        | INFORMATION_AND_TECHNOLOGY
  hr      | ORGANIZATIONS_AND_PEOPLE        processes | VALUE_STREAMS_AND_PROCESSES
  supplies| PARTNERS_AND_SUPPLIERS          management| ORGANIZATIONS_AND_PEOPLE
  ```

Safe to do this because `grep -rn "service_type\|bulk_create" tests/*.py` returns
**nothing** — no test asserts on `service_type`, so giving it a value cannot
invalidate an existing expectation.

### Tests fixed

All 60 failures listed in [Appendix A](#appendix-a--full-failure-list) except
`test_create_service` and `test_create_service_requires_manager`, which also hit
Issue 2.

---

## Issue 2 — `ServiceCreateView.get_form()` reads `cleaned_data` before validation

**Impact: 2 failures.** This is a genuine **application** bug, not a test defect —
the same crash would occur for a real user opening `GET /services/new/`.

**Status: resolved.**

### Symptom

```
AttributeError: 'ServiceCreateForm' object has no attribute 'cleaned_data'.
Did you mean: 'changed_data'?

  File "apps/services/views.py", line 141, in get_form
    form._scope_service_choices(form.cleaned_data.get("organization"))
```

### Root cause

`apps/services/views.py:139-142`:

```python
def get_form(self):
    form = super().get_form()
    form._scope_service_choices(form.cleaned_data.get("organization"))
    return form
```

Django calls `get_form()` while **constructing** the form — to render an unbound
`GET`, and again on an invalid `POST`. `Form.cleaned_data` does not exist until
`is_valid()` has run *and* passed. Django raises `AttributeError` rather than
returning empty data, so every render of the create form 500s.

The override is also **redundant**: `form_valid()` already scopes the choices
correctly, after validation, with a real organization
(`apps/services/views.py:152-153`):

```python
form._user = self.request.user
form._scope_service_choices(organization)
```

### Resolution

Delete the `get_form()` override. `form_valid()` is the correct place for
choice-scoping and already does it. This removes the crash without changing
behaviour, because the scoping that mattered was never being applied here anyway.

The two affected tests then exposed a second, separate problem: their POST payloads
predate the required `service_type` field.

`tests/test_catalog_lifecycle.py:102-110` (`test_create_service`) and
`:130-137` (`test_create_service_requires_manager`) both POST to
`services:create` without `service_type`. With the crash removed,
`test_create_service` would fail its `assert resp.status_code == 302` because the
form is now genuinely invalid. Both payloads gained the field:

- `test_create_service` posts a real `service_type` → form valid → `302` + redirect,
  and `service.has_manager` / `service.manager().person == manager` hold.
- `test_create_service_requires_manager` posts the type but **still omits**
  `service_manager` → form invalid → `200` re-render and no `Service` created,
  which is exactly what the test asserts.

---

## Issue 3 — `ServiceType` name collision causes a latent crash

**Impact: 0 tests at baseline; 1 test once Issue 2 was fixed. A production crash on
the service-creation path.**

**Status: resolved.**

### The problem (line numbers as originally found)

`apps/services/models.py` defined the name **`ServiceType` twice`:

| Line | Definition | Purpose |
|---|---|---|
| 37 | `class ServiceTypeDimension(models.TextChoices)` | ITIL dimension enum — the one actually used |
| 44 | `class ServiceType(models.TextChoices)` | legacy code enum — **shadowed** |
| 141 | `class ServiceType(models.Model)` | the classification model — **wins the namespace** |

The model at line 141 overwrote the enum at line 44, so
`from apps.services.models import ServiceType` yielded the **model**. Verified:

```
ServiceType -> ServiceType (model)
has MANAGEMENT attr? False
```

`_SERVICE_TYPE_DIMENSION` was built before the redefinition, so it captured the enum.
Only `_SERVICE_TYPE_DIMENSION` and `form_valid()` in `apps/services/views.py`
reached for `ServiceType` as an enum, and the latter was broken.


### The crash

`apps/services/views.py`, inside `form_valid()`:

```python
if service_type.code == ServiceType.MANAGEMENT:
    form._create_departments(self.object)
```

`ServiceType` here is the **model**, which has no `MANAGEMENT` attribute.
Reproduced:

```
AttributeError: type object 'ServiceType' has no attribute 'MANAGEMENT'
```

This fired whenever *any* service was created through the form, because Python
evaluates the right-hand side of the comparison before comparing. It was originally
**masked** by Issue 2: the crash on line 141 happened first, so execution never
reached this line. **Fixing Issue 2 without fixing this turns a masked bug into a
reachable one** — which it duly became: the suite dropped to a single failure on
`test_create_service` the moment Issue 2 was fixed.

Note the comparison was *semantically* broken as well as crashing. The enum's values
were uppercase (`"MANAGEMENT"`) while `ServiceType.code` stores lowercase slugs
(`"management"`, seeded by migration `0003`), so a naive string-literal fix would
have made the department fan-out **silently never run** instead of crashing.

### Resolution

The shadowed enum was renamed to `ServiceTypeCode` and its values corrected to the
lowercase slugs that are actually stored, so a member compares directly against
`ServiceType.code`. `ServiceType` now unambiguously means the model.

```python
class ServiceTypeCode(models.TextChoices):
    FINANCE = "finance", _("Finance")
    HR = "hr", _("Human Resources")
    SUPPLIES = "supplies", _("Supplies")
    IT = "it", _("IT")
    PROCESSES = "processes", _("Processes")
    MANAGEMENT = "management", _("Management")


class ServiceType(models.Model):
    code = models.SlugField(_("code"), unique=True, max_length=64, choices=ServiceTypeCode.choices)
```

`apps/services/views.py` keeps the typed constant rather than a magic string:

```python
if service_type.code == ServiceTypeCode.MANAGEMENT:
```

The enum is now wired up as the field's `choices`, so it is the single source of
truth for valid codes instead of an unused parallel definition.

`_SERVICE_TYPE_DIMENSION` was **deleted** — it existed only to feed the broken
lookup described in [Issue 8](#issue-8--servicedimension-always-resolves-to-none),
and the `ServiceType` model already carries a `dimension` field.

A regression test,
`tests/test_catalog_lifecycle.py::test_create_management_service_auto_creates_departments`,
now creates a Management service through `services:create` and asserts all four
department children appear.

The remaining question — whether classification should be an FK to a model, a
`TextChoices` field, or both — is a domain-model decision rather than a bug fix and
is now recorded as [[ServiceType Refactor Completion]].


---

## Issue 4 — Missing `services` migration

**Impact: none at runtime; violates ADR 008 and silently grows model/migration drift.**

**Status: resolved.**

### Evidence

```bash
$ python manage.py makemigrations --check --dry-run
Migrations for 'services':
  apps/services/migrations/0004_rename_services_service_type_idx_...py
    ~ Rename index services_service_type_idx on service to services_se_service_57f6a4_idx
    - Remove field created_at from servicetype
    - Remove field updated_at from servicetype
    ~ Alter field service_type on service
```

Three real divergences between model and migration state:

1. **`ServiceType.created_at` / `updated_at` still exist in the database but not on
   the model.** Migration `0003` created them (lines 64-65); the model dropped
   them. The dev database confirms the columns are still there:
   `services_servicetype cols: ['id', 'created_at', 'updated_at', 'code', 'name', 'dimension']`.
   This is a live `RemoveField` that has never been applied — any code that later
   writes timestamps to `ServiceType` would silently no-op.
2. The `service_type` index name in migration `0003` was hand-written
   (`services_service_type_idx`); Django's auto-generated name differs.
3. `service_type` gained `verbose_name` and `help_text` in the model.

Note the DB column nullability is **correct** — `service_type_id` is `NOT NULL` in
`information_schema`, matching both the model and the end state of migration 0003.
The schema is not the problem; the missing 0004 is.

### Resolution

Generated so `makemigrations --check` passes, as ADR
`008-Real-Migrations-in-Tests.md` requires ("*A missing migration is a real failure
mode*"). The auto-generated index rename is acceptable — it is cosmetic, and
keeping hand-written names only recreates the drift.

Two migrations were produced:

- `0004_rename_services_service_type_idx_and_more.py` — the three divergences above.
- `0005_alter_servicetype_code.py` — the `choices` added to `ServiceType.code` by
  the [Issue 3](#issue-3--servicetype-name-collision) fix.

```bash
$ python manage.py makemigrations --check --dry-run
No changes detected
```

The migrations were **not** applied to the development database. Both are schema
changes (`RemoveField` drops `ServiceType.created_at`/`updated_at`), so applying
them is an explicit operational step rather than part of a test fix.


---

## Issue 5 — Lint and format violations

**Impact: none on tests. `ruff check` and `ruff format --check` both fail, so CI is red on a clean tree.**

**Status: resolved.**

```bash
$ ruff check .            # 15 errors (11 auto-fixable)
$ ruff format .           # 10 files reformatted, 189 already formatted
```

| Rule | Count | Location |
|---|---|---|
| `F401` unused import | 5 | `apps/services/models.py:21`, `apps/services/migrations/0003_...py:5`, `tests/test_profiles_i18n.py:12,14`, and one fixed earlier |
| `F841` unused local | 3 | `tests/test_service_roles_ui.py:18,34,116` |
| `I001` unsorted imports | 2 | `apps/services/admin.py:1`, `apps/services/migrations/0003_...py:3` |
| `W292` no newline at EOF | 1 | `tests/test_service_roles_ui.py` |
| `UP035` deprecated import | 1 | `apps/common/tables/config.py:8` — `Callable` from `collections.abc` |
| `UP031` percent formatting | 1 | `apps/services/views.py:113` — `_(... % {...})` should use a format specifier |

### Resolution

`ruff check --fix` for the 11 mechanical fixes, then the remaining 4 by hand.

The three `F841`s need care rather than blind autofix: `svc = make_service(...)`
and `hidden = make_service(...)` look unused but the **call itself is the point** —
it creates the database rows the test then asserts on. Removing the line would
delete the fixture setup. Correct fix is to drop only the assignment and keep the
call.

The two `F401`s in `tests/test_profiles_i18n.py` (`gettext`, `Organization`) were
confirmed genuinely unreferenced before removal.

Final state:

```bash
$ ruff check .            # All checks passed!
$ ruff format --check .   # 199 files already formatted
```


---

## Issue 6 — Documentation drift

**Impact: misleading. Anyone trusting these numbers is told the suite is nearly
green when it is 37% red, and is sent to chase bugs that were fixed long ago.**

**Status: resolved.**

| Source | Claim | Reality |
|---|---|---|
| `README.md` §6 | "129 passed, 12 failed" | 105 passed, 62 problems at baseline |
| `README.md` §6 | Dashboard `/` fails: `TypeError: Object of type __proxy__ is not JSON serializable` | **Already fixed** — `apps/dashboard/views.py:217` wraps it: `_bar(labels, series, str(_("Requests")))` |
| `README.md` §6 | Login template: `{% blocktranslate %}` containing `{% url 'admin:login' %}` fails to render | **Already fixed** — `templates/registration/login.html:48` uses separate `{% translate %}` + `<a href="{% url 'admin:login' %}">`. `test_login_page_renders` passes. |
| `README.md` §6 + intro | "operational log in `STATUS.md`" | **No such file exists** |
| Obsidian `Tasks/ServiceType Refactor Completion.md` | "40 failed, 95 passed, 22 errors (157 collected)" | 167 collected; 105 passed / 40 failed / 22 errors |
| Obsidian `Tasks/ServiceType Refactor Completion.md` | CI `audit` job installs `requirements.txt` but runs `pip-audit` | **Already fixed** — `.github/workflows/ci.yml` uses `uv sync --locked` + `uv run pip-audit`. No `requirements*.txt` exists. |
| Obsidian (same note) | `opencode.json` `MEMORY_FILE_PATH` → `/home/kali/projects/OrgService` | **Already correct** — `opencode.json:13` points at `/home/kali/OrgService` |
| Obsidian (same note) | `_AUTH_PERMISSION_PREFIX` is an unused hook | **Wrong** — it is read via `getattr` at `apps/common/authorization.py:39` as a real extension point |
| Obsidian (same note) | `apps/common/__init__.py` claims it is not in `INSTALLED_APPS` | **Wrong** — it *is* registered (`config/settings/base.py:38`) and holds only abstract models |
| Obsidian (same note) | `apps/access/management/` missing `__init__.py` | **Confirmed** — works via PEP 420, but inconsistent with `apps/common/management/` |

The README's §6 was the most damaging item: it attributed *all* failures to two
fixed bugs, so a reader would conclude the `service_type` refactor breakage did not
exist. The Obsidian note had the correct diagnosis but stale counts and three claims
that had since been overtaken by other changes.

### Resolution

- `README.md` §6 rewritten with the verified numbers and the real causes; the two
  fixed bugs removed and the dangling `STATUS.md` reference dropped. §6 now also
  records the sequential-run requirement and points at this file.
- Obsidian `ServiceType Refactor Completion.md` counts corrected, the "what remains"
  list replaced by "what was done", and each outstanding item re-verified against
  source — three were already stale, two were genuinely wrong and fixed, one was
  confirmed and fixed.
- `apps/common/__init__.py` docstring corrected to match `INSTALLED_APPS`.
- `apps/access/management/__init__.py` and `.../commands/__init__.py` added for
  consistency; `manage.py help` confirms `seed_rbac` is still discovered.
- ADR `008` cross-reference kept — that note correctly predicted this failure mode.


---

## Issue 7 — Tooling footguns (do not change pass/fail results)

**Status: resolved.**

Documented because both produce alarming output that looks like new bugs.

### Stale bytecode from a previous project path

`tests/__pycache__/` holds `.pyc` files compiled when the project lived at
`/home/kali/Porject`. That path no longer exists, so tracebacks render as:

```
/home/kali/Porject/tests/conftest.py:90: in _make
    ???
```

The `?????` means Python cannot read the source to show the line. It is purely
cosmetic — the `.pyc` files pass their `mtime`/`size` validation, so the tests
genuinely execute the current source. Verified: all 18 pytest-rewritten `.pyc`
files match their `.py` on disk.

Also present: 4 orphaned `.pyc` with no `.py` source
(`test_debug_tmp`, `test_seedprobe`, `test_zz_debug`, `test_zzz_djdt`). Harmless,
since pytest collects from `.py` files.

Resolution: **done.** Every `__pycache__` directory outside `.venv` was removed and
the stale artefacts are gone. All were gitignored and untracked, so this is safe and
restores readable tracebacks.

Note that running the suite regenerates `__pycache__` from the *current* source path,
so the directories reappear normally — that is expected. The defect was never the
existence of the cache, only bytecode left over from the old `/home/kali/Porject`
path.



### Concurrent runs corrupt the shared test database

Every pytest process targets the same `test_orgplatform` name. Running two at once
produces:

```
IntegrityError: duplicate key value violates unique constraint "pg_database_datname_index"
```

and **all 163–167 tests error**, which looks catastrophic but is purely an
orchestration artifact. This is easy to trigger accidentally when firing off
several `pytest` commands in parallel.

A useful side effect: spurious symptoms from this collision include
`ValueError: nulls_first and nulls_last values must be True or None` and
`NameError: name 'Column' is not defined`. **Neither reproduces when the suite is
run sequentially** — `Column` is imported normally at `apps/services/views.py:11`.
Chasing either would have been wasted effort.

Resolution: documented here. Run pytest serially; the baseline in §2 is from
sequential runs.

---

## Issue 8 — `Service.dimension` always resolves to `None`

**Impact: silent data bug. No test covered it.**

**Status: resolved.**

Found while fixing [Issue 3](#issue-3--servicetype-name-collision), not by a failing
test. `apps/services/models.py` had:

```python
@property
def dimension(self):
    """The ITIL dimension this service type belongs to."""
    return _SERVICE_TYPE_DIMENSION.get(self.service_type_id)
```

`_SERVICE_TYPE_DIMENSION` is keyed by the **enum's string values**, but it was looked
up with `self.service_type_id`, an **integer primary key**. The lookup could never
hit. Verified at runtime:

```
_SERVICE_TYPE_DIMENSION keys: ['FINANCE', 'HR', 'SUPPLIES', 'IT', 'PROCESSES', 'MANAGEMENT']
service_type.pk = 5 (int)  |  model .dimension field = VALUE_STREAMS_AND_PROCESSES
_SERVICE_TYPE_DIMENSION.get(pk) -> None
```

So `Service.dimension` returned `None` for **every** service in the platform — and
would have returned `None` even for a correct string key, because the enum values
were uppercase while `ServiceType.code` stores lowercase slugs.

The map was redundant regardless: the `ServiceType` model already carries its own
`dimension` field. The property now delegates to it.

```python
@property
def dimension(self):
    """The ITIL dimension this service belongs to, via its service type."""
    return self.service_type.dimension
```

`_SERVICE_TYPE_DIMENSION` was deleted. Covered by
`tests/test_catalog_lifecycle.py::test_service_dimension_comes_from_its_service_type`.

---

## Appendix A — full failure list

62 problems, all traced to Issue 1 except the two marked.

**`tests/test_catalog_lifecycle.py`** — 10 problems
- `FAILED` `test_action_get_not_allowed`
- `FAILED` `test_action_out_of_scope_404`
- `FAILED` `test_action_requires_manage_perm`
- `FAILED` `test_create_resource`
- `FAILED` `test_create_service` ← also Issue 2
- `FAILED` `test_create_service_requires_manager` ← also Issue 2
- `FAILED` `test_delete_requires_delete_perm`
- `FAILED` `test_resource_delete_is_soft`
- `FAILED` `test_service_deactivate_and_activate`
- `FAILED` `test_service_delete_is_soft_and_hidden`

**`tests/test_requests_authorization.py`** — 8 problems
- `ERROR` `test_anonymous_and_unauthenticated_users_see_nothing`
- `ERROR` `test_can_on_resource`
- `ERROR` `test_does_not_flow_upward_or_sibling`
- `ERROR` `test_scope_subquery_matches_request_sql`
- `ERROR` `test_service_access_restricts_visibility`
- `ERROR` `test_superuser_sees_everything`
- `ERROR` `test_visible_inherits_downward_to_services`
- `ERROR` `test_visible_to_only_rows_with_permission`

**`tests/test_service_roles_ui.py`** — 7 problems
- `FAILED` `test_header_dropdown_hides_services_outside_membership_scope`
- `FAILED` `test_header_dropdown_lists_visible_services`
- `FAILED` `test_membership_drives_service_selector`
- `FAILED` `test_profile_service_roles_respect_service_access`
- `FAILED` `test_profile_shows_service_roles_for_unrestricted_membership`
- `FAILED` `test_session_service_context_is_validated`
- `FAILED` `test_session_service_context_selects_active_service`

**`tests/test_views.py`** — 7 problems
- `FAILED` `test_dashboard_renders_scoped`
- `FAILED` `test_request_bulk_mark_closed`
- `FAILED` `test_service_dashboard_renders`
- `FAILED` `test_service_scope_detail[own-200]`
- `FAILED` `test_service_scope_detail[other-404]`
- `FAILED` `test_sidebar_footer_shows_org_service_role`
- `FAILED` `test_superuser_renders_detail_pages`

**`tests/test_dashboard_filters.py`** — 6 problems
- `FAILED` `test_requests_list_service_filter_is_exact_not_substring`
- `FAILED` `test_requests_list_status_open_pseudo_filter`
- `FAILED` `test_requests_service_column_filter_by_id_is_exact`
- `FAILED` `test_requests_service_column_filter_is_a_dropdown`
- `FAILED` `test_requests_service_column_filter_scoped_to_visible_services`
- `FAILED` `test_service_dashboard_open_requests_link_is_scoped`

**`tests/test_services.py`** — 5 problems
- `FAILED` `test_capability_assignment`
- `FAILED` `test_same_service_name_allowed_in_different_organizations`
- `FAILED` `test_service_actor_uniqueness`
- `FAILED` `test_service_name_unique_within_organization`
- `FAILED` `test_service_str`

**`tests/test_workflows.py`** — 5 problems
- `ERROR` `test_execution_defaults`
- `ERROR` `test_execution_generic_subject`
- `ERROR` `test_steps_ordered_and_transitions_oriented`
- `ERROR` `test_transition_unique_order`
- `ERROR` `test_workflow_versioning_unique`

**`tests/test_changes.py`** — 3 problems
- `FAILED` `test_approval_unique_per_approver`
- `FAILED` `test_change_and_approvals`
- `FAILED` `test_change_status_flow`

**`tests/test_events_audit_metrics.py`** — 3 problems
- `ERROR` `test_audit_log_generic_entity`
- `ERROR` `test_event_generic_subject`
- `ERROR` `test_metric_value_roundtrip`

**`tests/test_incidents_problems.py`** — 3 problems
- `ERROR` `test_incident_and_updates`
- `ERROR` `test_problem_incident_uniqueness`
- `ERROR` `test_problem_links_incidents`

**`tests/test_resources.py`** — 3 problems
- `ERROR` `test_asset_defaults`
- `ERROR` `test_multi_table_inheritance`
- `ERROR` `test_resource_assignment_generic_target`

**`tests/test_access_rbac.py`** — 2 problems
- `FAILED` `test_service_access_lists_services`
- `FAILED` `test_service_access_unique`

`ERROR` means the failure happened during fixture setup, so the test body never
ran; `FAILED` means the test ran and asserted wrongly.

## Appendix B — files changed

| File | Issue |
|---|---|
| `tests/conftest.py` | 1 |
| `tests/test_requests_authorization.py` | 1 |
| `tests/test_services.py` | 1 |
| `apps/services/views.py` | 2, 3, 5 |
| `apps/services/models.py` | 3, 8 |
| `tests/test_catalog_lifecycle.py` | 2, 3, 8 (+ regression tests) |
| `apps/services/migrations/0004_*.py` (new) | 4 |
| `apps/services/migrations/0005_alter_servicetype_code.py` (new) | 4 |
| `apps/services/admin.py`, `apps/common/tables/config.py` | 5 |
| `tests/test_service_roles_ui.py`, `tests/test_profiles_i18n.py` | 5 |
| `README.md` | 6 |
| `.opencode/obsidian/Tasks/ServiceType Refactor Completion.md` | 6 |
| `apps/common/__init__.py` | 6 (verified stale claim, corrected) |
| `apps/access/management/__init__.py` (new), `apps/access/management/commands/__init__.py` (new) | 6 (consistency) |

Also removed: stale `__pycache__` directories compiled against a previous project
path (`/home/kali/Porject`). All gitignored and regenerated automatically — see
[Issue 7](#issue-7--tooling-footguns-do-not-change-passfail-results).

**Deliberately unchanged:** the unused `ActiveQuerySet`, `AuthorizationManagerMixin`
and `AuthorizationManager` in `apps/common/`. They are genuinely unreferenced but
live in a shared helpers package, so removing public API is a design decision rather
than a test fix.
