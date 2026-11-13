from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from importlib import import_module
from io import StringIO
from threading import Barrier

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections
from django.urls import reverse
from django.utils import timezone

from apps.access.models import GrantScope, GrantScopeType, Permission, Role, RolePermission
from apps.offerings.execution import eligible_people, start_request_execution, task_action
from apps.offerings.models import (
    ExecutionNote,
    ExecutionStatus,
    Notification,
    OfferingExecution,
    OfferingStatus,
    OfferingStep,
    OfferingTask,
    OfferingTransition,
    PauseReason,
    ServiceOffering,
    StepType,
    TaskStatus,
    TransitionOutcome,
)
from apps.offerings.views import actionable_tasks
from apps.people.models import PersonType
from apps.requests.models import Request, RequestComment, RequestStatus
from apps.services.models import ServiceActor

pytestmark = pytest.mark.django_db


@pytest.fixture
def flow(make_user, make_person, make_role, make_membership, org_tree, make_service):
    user = make_user()
    actor = make_person(user, name="Worker")
    role = make_role(
        permissions=["service.view", "offering.view", "offering.execute", "offering.manage"]
    )
    make_membership(actor, org_tree["parish_a"], role)
    service = make_service(org_tree["parish_a"], "Cemetery")
    offering = ServiceOffering.objects.create(
        service=service, name="Funeral", status=OfferingStatus.ACTIVE
    )
    start = OfferingStep.objects.create(
        offering=offering, code="start", name="Start", step_type=StepType.START, order=10
    )
    work = OfferingStep.objects.create(
        offering=offering,
        code="work",
        name="Work",
        step_type=StepType.TASK,
        order=20,
        sla_minutes=60,
    )
    notify = OfferingStep.objects.create(
        offering=offering,
        code="notify",
        name="Notify",
        step_type=StepType.NOTIFY,
        order=30,
        message="Your request is ready.",
    )
    end = OfferingStep.objects.create(
        offering=offering, code="end", name="End", step_type=StepType.END, order=40
    )
    for source, target in [(start, work), (work, notify), (notify, end)]:
        OfferingTransition.objects.create(offering=offering, from_step=source, to_step=target)
    request = Request.objects.create(
        service=service, offering=offering, title="Example", requester=actor
    )
    return locals()


def test_request_status_follows_failure_retry_and_terminal_states(flow):
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    task_action(task.pk, flow["user"], "start")
    task_action(task.pk, flow["user"], "fail")
    flow["request"].refresh_from_db()
    assert flow["request"].status == RequestStatus.FAILED
    task_action(task.pk, flow["user"], "retry")
    flow["request"].refresh_from_db()
    assert flow["request"].status == RequestStatus.IN_PROGRESS
    for execution_status, request_status in (
        (ExecutionStatus.PENDING, RequestStatus.NEW),
        (ExecutionStatus.WAITING, RequestStatus.WAITING),
        (ExecutionStatus.FAILED, RequestStatus.FAILED),
        (ExecutionStatus.CANCELLED, RequestStatus.CANCELLED),
        (ExecutionStatus.COMPLETED, RequestStatus.RESOLVED),
    ):
        execution.status = execution_status
        execution.save(update_fields=["status"])
        flow["request"].refresh_from_db()
        assert flow["request"].status == request_status


def test_offering_productivity_periods_versions_statuses_and_assignees(flow, client, make_person,
                                                                       make_user):
    from django.utils.timezone import localtime

    now = timezone.now()
    offering = flow["offering"]
    v2 = ServiceOffering.objects.create(
        service=flow["service"], name=offering.name, version=2, status=OfferingStatus.ACTIVE
    )
    old = OfferingExecution.objects.create(
        offering=offering, started_at=now - timedelta(days=20),
        completed_at=now - timedelta(days=1), status=ExecutionStatus.COMPLETED,
    )
    running = OfferingExecution.objects.create(
        offering=v2, started_at=now - timedelta(days=1), status=ExecutionStatus.RUNNING
    )
    waiting = OfferingExecution.objects.create(
        offering=offering, started_at=now - timedelta(days=2),
        status=ExecutionStatus.WAITING, current_step=flow["work"],
    )
    blocked = OfferingExecution.objects.create(
        offering=offering, started_at=now - timedelta(days=2),
        status=ExecutionStatus.WAITING, current_step=flow["work"],
    )
    OfferingTask.objects.create(execution=blocked, step=flow["work"], status=TaskStatus.BLOCKED)
    OfferingExecution.objects.create(
        offering=offering, started_at=now - timedelta(days=20), status=ExecutionStatus.RUNNING
    )
    worker = make_person(make_user(), name="Assignee")
    OfferingTask.objects.create(
        execution=old, step=flow["work"], status=TaskStatus.DONE,
        assigned_to=worker, completed_at=now - timedelta(hours=2),
    )
    old.tasks.get().activities.create(actor=flow["actor"], action="completed")
    OfferingTask.objects.create(
        execution=running, step=flow["work"], status=TaskStatus.DONE,
        completed_at=now - timedelta(hours=3),
    )
    OfferingTask.objects.create(
        execution=waiting, step=flow["notify"], status=TaskStatus.DONE,
        assigned_to=worker, completed_at=now - timedelta(hours=1),
    )
    client.force_login(flow["user"])
    url = reverse("offerings:productivity")
    response = client.get(url, {"offering": offering.pk})
    assert response.status_code == 200
    assert response.context["period"] == "7"
    row = response.context["rows"][0]
    assert row["offering"] == v2
    assert {k: row["counts"][k] for k in ("started", "completed", "running", "waiting", "failed")} == {
        "started": 3, "completed": 1, "running": 1, "waiting": 1, "failed": 1,
    }
    people = response.context["people"]
    assert {(p["assigned_to__full_name"], p["total"]) for p in people} == {
        ("Assignee", 1), (None, 1),
    }
    assert sum(day["started"] for day in response.context["timeline"]) == 3
    assert sum(day["completed"] for day in response.context["timeline"]) == 1
    assert all(day["day"] <= localtime(now).date() for day in response.context["timeline"])
    assert client.get(url, {"period": "30"}).context["rows"][0]["counts"]["started"] == 5
    assert client.get(url, {"period": "bad"}).status_code == 400
    assert client.get(url, {"offering": "invalid"}).status_code == 404
    assert client.get(url, {"offering": "999999999999999999999999"}).status_code == 404


def test_offering_productivity_period_boundaries_and_future_records(flow, client, monkeypatch):
    from datetime import UTC, datetime

    now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("apps.offerings.views.timezone.now", lambda: now)
    for started in (
        datetime(2026, 11, 6, 23, 59, tzinfo=UTC),
        datetime(2026, 11, 7, 0, 0, tzinfo=UTC),
        datetime(2026, 11, 13, 12, 1, tzinfo=UTC),
    ):
        OfferingExecution.objects.create(
            offering=flow["offering"], started_at=started, status=ExecutionStatus.RUNNING
        )
    client.force_login(flow["user"])
    url = reverse("offerings:productivity")
    with timezone.override("UTC"):
        assert client.get(url, {"period": "7"}).context["rows"][0]["counts"]["started"] == 1
        assert client.get(url, {"period": "30"}).context["rows"][0]["counts"]["started"] == 2
        assert client.get(url, {"period": "90"}).context["rows"][0]["counts"]["started"] == 2
        assert client.get(url, {"period": "year"}).context["rows"][0]["counts"]["started"] == 2


def test_offering_productivity_enforces_scoped_access(flow, client, make_service, make_user,
                                                     make_person, make_role, make_membership):
    hidden_service = make_service(flow["org_tree"]["parish_b"], "Hidden")
    hidden = ServiceOffering.objects.create(service=hidden_service, name="Secret")
    OfferingExecution.objects.create(offering=hidden, status=ExecutionStatus.RUNNING,
                                     started_at=timezone.now())
    client.force_login(flow["user"])
    url = reverse("offerings:productivity")
    response = client.get(url)
    assert response.status_code == 200
    assert "Secret" not in response.content.decode()
    assert client.get(url, {"offering": hidden.pk}).status_code == 404
    viewer = make_user()
    role = make_role(permissions=["request.view"])
    make_membership(make_person(viewer), flow["org_tree"]["parish_a"], role)
    client.force_login(viewer)
    assert client.get(url).status_code == 403
    client.logout()
    assert client.get(url).status_code == 302


def test_pause_and_resume_preserve_task_history_and_request_state(flow, client):
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    task_action(task.pk, flow["user"], "assign", assignee_id=flow["actor"].pk)
    task_action(task.pk, flow["user"], "start")
    task.refresh_from_db()
    started, deadline = task.started_at, task.deadline
    client.force_login(flow["user"])
    url = reverse("requests:detail", args=[flow["request"].pk])
    action_url = reverse("offerings:task_action", args=[task.pk])
    body = client.get(url).content.decode()
    assert 'value="pause"' in body and "Waiting for vendor" in body
    assert client.post(action_url, {
        "action": "pause", "reason": PauseReason.COMMENT,
        "comment": "Waiting for the document.", "next": url,
    }).url == url
    task.refresh_from_db()
    execution.refresh_from_db()
    flow["request"].refresh_from_db()
    assert task.status == TaskStatus.PAUSED
    assert execution.status == ExecutionStatus.WAITING
    assert flow["request"].status == RequestStatus.WAITING
    assert task.started_at == started and task.deadline == deadline
    assert task.completed_at is None
    assert task.activities.get(action="paused").detail == PauseReason.COMMENT
    note = execution.notes.get()
    assert note.task == task and note.body == "Waiting for the document."
    assert note.kind == ExecutionNote.Kind.COMMENT and note.author == flow["user"]
    assert actionable_tasks(flow["user"]).filter(pk=task.pk).exists()
    body = client.get(url).content.decode()
    assert "Mentioned in the comments" in body
    assert "Waiting for the document." in body
    header = body.split('<dl class="grid gap-x-8', 1)[1].split("</dl>", 1)[0]
    assert ">Paused</span>" in header and "Assigned to: Worker" in header
    assert 'value="resume"' in body and 'value="complete"' not in body
    with pytest.raises(ValidationError):
        task_action(task.pk, flow["user"], "complete")
    call_command("backfill_execution_tasks", verbosity=0)
    assert execution.tasks.count() == 1
    assert client.post(action_url, {"action": "resume", "next": url}).url == url
    task.refresh_from_db()
    execution.refresh_from_db()
    flow["request"].refresh_from_db()
    assert task.status == TaskStatus.IN_PROGRESS
    assert task.started_at == started and task.deadline == deadline
    assert execution.status == ExecutionStatus.RUNNING
    assert flow["request"].status == RequestStatus.IN_PROGRESS
    task_action(task.pk, flow["user"], "pause", reason=PauseReason.VENDOR)
    task_action(task.pk, flow["user"], "resume")
    assert list(task.activities.filter(action__in=["paused", "resumed"]).values_list(
        "action", flat=True
    )) == ["paused", "resumed", "paused", "resumed"]
    task_action(task.pk, flow["user"], "complete")
    execution.refresh_from_db()
    assert execution.status == ExecutionStatus.COMPLETED


@pytest.mark.parametrize("reason,comment", [
    ("", "text"),
    ("invalid", "text"),
    (PauseReason.COMMENT, "  "),
    (PauseReason.VENDOR, "x" * 4001),
])
def test_pause_rejects_invalid_input_without_side_effects(flow, reason, comment):
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    task_action(task.pk, flow["user"], "start")
    with pytest.raises(ValidationError):
        task_action(task.pk, flow["user"], "pause", reason=reason, comment=comment)
    task.refresh_from_db()
    execution.refresh_from_db()
    assert task.status == TaskStatus.IN_PROGRESS
    assert execution.status == ExecutionStatus.RUNNING
    assert not task.activities.filter(action="paused").exists()
    assert not execution.notes.exists()


def test_pause_permissions_for_assignee_and_manager(flow, make_user, make_person, make_role,
                                                   make_membership, client):
    executor_role = make_role(permissions=["service.view", "offering.view", "offering.execute"])
    worker = make_person(make_user(), name="Assignee")
    other = make_person(make_user(), name="Other worker")
    for person in (worker, other):
        make_membership(person, flow["org_tree"]["parish_a"], executor_role)
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    task_action(task.pk, flow["user"], "assign", assignee_id=worker.pk)
    task_action(task.pk, worker.user, "start")
    with pytest.raises(PermissionDenied):
        task_action(task.pk, other.user, "pause", reason=PauseReason.APPROVAL)
    client.force_login(other.user)
    assert "Pause task" not in client.get(
        reverse("offerings:execution_detail", args=[execution.pk])
    ).content.decode()
    task_action(task.pk, worker.user, "pause", reason=PauseReason.APPROVAL)
    with pytest.raises(PermissionDenied):
        task_action(task.pk, other.user, "resume")
    task_action(task.pk, worker.user, "resume")
    task_action(task.pk, flow["user"], "pause", reason=PauseReason.REQUESTER)
    task_action(task.pk, flow["user"], "resume")


def test_pause_visible_to_delegated_assignee_and_manager_without_execute(
    flow, make_user, make_person, make_role, make_membership, client
):
    worker = make_person(make_user(), name="Worker only")
    manager = make_person(make_user(), name="Manager only")
    for person, permission in ((worker, "offering.execute"), (manager, "offering.manage")):
        role = make_role(permissions=["service.view", permission])
        make_membership(person, flow["org_tree"]["parish_a"], role)
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    task_action(task.pk, flow["user"], "assign", assignee_id=worker.pk)
    task_action(task.pk, worker.user, "start")
    task_action(task.pk, manager.user, "pause", reason=PauseReason.APPROVAL)
    client.force_login(worker.user)
    response = client.get(reverse("offerings:execution_detail", args=[execution.pk]))
    assert response.status_code == 200
    assert b'value="resume"' in response.content
    task_action(task.pk, worker.user, "resume")
    task_action(task.pk, worker.user, "pause", reason=PauseReason.VENDOR)
    task_action(task.pk, manager.user, "resume")


def test_cannot_start_a_second_execution_for_request(flow):
    start_request_execution(flow["request"], flow["actor"])
    with pytest.raises(ValidationError, match="already has an execution"):
        start_request_execution(flow["request"], flow["actor"])


def test_rebuild_requests_preserves_executions_tasks_and_other_subjects(flow):
    from apps.incidents.models import Incident
    from apps.offerings.request_link import attach_missing_requests

    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    RequestComment.objects.create(request=flow["request"], body="Old demo")
    incident = Incident.objects.create(service=flow["service"], title="Keep incident")
    other = OfferingExecution.objects.create(offering=flow["offering"], subject=incident)
    unattached = OfferingExecution.objects.create(
        offering=flow["offering"], created_by=flow["actor"], status=ExecutionStatus.COMPLETED
    )
    with pytest.raises(CommandError):
        call_command("rebuild_execution_requests")
    call_command("rebuild_execution_requests", confirm_delete_all_requests=True, verbosity=0)
    execution.refresh_from_db()
    other.refresh_from_db()
    unattached.refresh_from_db()
    assert not Request.objects.filter(pk=flow["request"].pk).exists()
    assert not RequestComment.objects.exists()
    assert execution.tasks.filter(pk=task.pk).exists()
    assert execution.subject.offering == flow["offering"]
    assert execution.subject.requester == flow["actor"]
    assert execution.subject.status == RequestStatus.IN_PROGRESS
    assert unattached.subject.status == RequestStatus.RESOLVED
    assert other.subject == incident
    assert Incident.objects.filter(pk=incident.pk).exists()
    before = Request.objects.count()
    attach_missing_requests()
    assert Request.objects.count() == before


def test_request_and_execution_detail_links_respect_permissions(flow, client):
    execution = start_request_execution(flow["request"], flow["actor"])
    client.force_login(flow["user"])
    request_url = reverse("requests:detail", args=[flow["request"].pk])
    execution_url = reverse("offerings:execution_detail", args=[execution.pk])
    assert execution_url in client.get(request_url).content.decode()
    assert request_url in client.get(execution_url).content.decode()


def test_only_requester_in_request_header_links_to_person(flow, client):
    from apps.people.models import get_system_person

    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    task_action(task.pk, flow["user"], "assign", assignee_id=flow["actor"].pk)
    ExecutionNote.objects.create(
        execution=execution, task=task, author=flow["user"], body="A linked comment"
    )
    RequestComment.objects.create(
        request=flow["request"], author=flow["actor"], body="A historical comment"
    )
    client.force_login(flow["user"])
    person_url = reverse("people:detail", args=[flow["actor"].pk])
    system_url = reverse("people:detail", args=[get_system_person().pk])
    assert client.get(person_url).status_code == 200
    body = client.get(reverse("requests:detail", args=[flow["request"].pk])).content.decode()
    header, rest = body.split("</dl>", 1)
    assert (
        f'<a href="{person_url}" class="font-medium text-indigo-600 '
        'hover:text-indigo-800">Worker</a>'
    ) in header
    assert f'href="{person_url}"' not in rest
    assert f'href="{system_url}"' not in body
    body = client.get(reverse("offerings:execution_detail", args=[execution.pk])).content.decode()
    assert f'href="{person_url}"' not in body
    assert f'href="{system_url}"' not in body
    flow["actor"].deleted_at = timezone.now()
    flow["actor"].save(update_fields=["deleted_at"])
    flow["user"].is_superuser = True
    flow["user"].save(update_fields=["is_superuser"])
    response = client.get(reverse("requests:detail", args=[flow["request"].pk]))
    assert response.status_code == 200
    assert f'href="{person_url}"' not in response.content.decode()
    assert "Worker" in response.content.decode()


def test_request_detail_shows_steps_and_allows_current_task_action(flow, client):
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    client.force_login(flow["user"])
    url = reverse("requests:detail", args=[flow["request"].pk])
    body = client.get(url).content.decode()
    assert ">Work</p>" in body
    assert "Current step" in body
    header = body.split('<dl class="grid gap-x-8', 1)[1].split("</dl>", 1)[0]
    assert "Current step</dt>" in header
    assert ">Work " in header
    assert ">Pending</span>" in header
    assert "Assigned to: Unassigned" in header
    assert f'name="next" value="{url}"' in body
    assert 'value="start"' in body
    response = client.post(
        reverse("offerings:task_action", args=[task.pk]),
        {"action": "start", "next": url},
    )
    assert response.status_code == 302 and response.url == url
    task.refresh_from_db()
    assert task.status == TaskStatus.IN_PROGRESS
    body = client.get(url).content.decode()
    assert 'value="complete"' in body
    header = body.split('<dl class="grid gap-x-8', 1)[1].split("</dl>", 1)[0]
    assert ">In Progress</span>" in header
    client.post(
        reverse("offerings:task_action", args=[task.pk]),
        {"action": "complete", "next": url},
    )
    flow["request"].refresh_from_db()
    assert flow["request"].status == RequestStatus.RESOLVED
    body = client.get(url).content.decode()
    assert ">End</p>" in body
    header = body.split('<dl class="grid gap-x-8', 1)[1].split("</dl>", 1)[0]
    assert ">End</dd>" in header


def test_request_current_step_summary_uses_latest_active_visit(flow, client):
    execution = start_request_execution(flow["request"], flow["actor"])
    first = execution.tasks.get()
    OfferingTask.objects.create(
        execution=execution,
        step=flow["work"],
        title="Latest visit",
        status=TaskStatus.ASSIGNED,
        assigned_to=flow["actor"],
    )
    client.force_login(flow["user"])
    response = client.get(reverse("requests:detail", args=[flow["request"].pk]))
    header = response.content.decode().split('<dl class="grid gap-x-8', 1)[1].split("</dl>", 1)[0]
    assert first.status == TaskStatus.PENDING
    assert ">Assigned</span>" in header and "Assigned to: Worker" in header


def test_request_execution_log_displays_activities_and_posts_notes(flow, client):
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    client.force_login(flow["user"])
    url = reverse("requests:detail", args=[flow["request"].pk])
    body = client.get(url).content.decode()
    assert 'id="execution-log"' in body
    assert "Task history records" in body
    assert "Created" in body.split('id="execution-log"', 1)[1]
    assert ">Timeline</h2>" not in body
    response = client.post(url, {"kind": "COMMENT", "body": "  A request note <script>  "})
    assert response.status_code == 302 and response.url == f"{url}#execution-log"
    note = execution.notes.get()
    assert note.body == "A request note <script>"
    assert note.kind == ExecutionNote.Kind.COMMENT
    assert note.task == task and note.author == flow["user"]
    body = client.get(url).content.decode()
    assert "A request note &lt;script&gt;" in body
    assert "A request note &lt;script&gt;" in client.get(
        reverse("offerings:execution_detail", args=[execution.pk])
    ).content.decode()


def test_request_execution_log_keeps_existing_request_comments(flow, client):
    start_request_execution(flow["request"], flow["actor"])
    RequestComment.objects.create(request=flow["request"], body="Existing request comment")
    client.force_login(flow["user"])
    body = client.get(reverse("requests:detail", args=[flow["request"].pk])).content.decode()
    assert 'id="execution-log"' in body
    assert "Existing request comment" in body
    log = body.split('id="execution-log"', 1)[1]
    assert "Comments" in log
    assert ">Timeline</h2>" not in body


def test_execution_log_shows_all_entry_types_newest_first(flow, client):
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    activity = task.activities.get(action="created")
    note = ExecutionNote.objects.create(
        execution=execution, task=task, author=flow["user"], body="Recent note"
    )
    comment = RequestComment.objects.create(
        request=flow["request"], author=flow["actor"], body="Old request comment"
    )
    now = timezone.now()
    type(activity).objects.filter(pk=activity.pk).update(created_at=now - timedelta(minutes=2))
    ExecutionNote.objects.filter(pk=note.pk).update(created_at=now - timedelta(minutes=1))
    RequestComment.objects.filter(pk=comment.pk).update(created_at=now - timedelta(minutes=3))

    client.force_login(flow["user"])
    for url in (
        reverse("requests:detail", args=[flow["request"].pk]),
        reverse("offerings:execution_detail", args=[execution.pk]),
    ):
        response = client.get(url)
        assert response.status_code == 200
        entries = response.context["log_entries"]
        assert [(type(entry), entry.pk) for entry in entries] == [
            (ExecutionNote, note.pk),
            (type(activity), activity.pk),
            (RequestComment, comment.pk),
        ]
        log = response.content.decode().split('id="execution-log"', 1)[1]
        assert log.index("Recent note") < log.index("Created") < log.index("Old request comment")
        assert log.count('bg-blue-50 text-blue-700 ring-blue-600/20">Comments</span>') == 2
        assert 'text-zinc-600 ring-1 ring-inset ring-zinc-500/20">System</span>' in log
        assert 'bg-zinc-100 text-zinc-600 ring-zinc-500/20">Created</span>' in log


@pytest.mark.parametrize("body", ["", "  ", "x" * 4001])
def test_request_execution_log_rejects_invalid_notes(flow, client, body):
    execution = start_request_execution(flow["request"], flow["actor"])
    client.force_login(flow["user"])
    response = client.post(
        reverse("requests:detail", args=[flow["request"].pk]),
        {"kind": "COMMENT", "body": body},
    )
    assert response.status_code == 400
    assert response.context["note_form"].errors["body"]
    assert not execution.notes.exists()


def test_request_without_visible_execution_cannot_post_note(flow, client, make_user, make_person):
    execution = start_request_execution(flow["request"], flow["actor"])
    viewer = make_user()
    person = make_person(viewer)
    flow["request"].requester = person
    flow["request"].save(update_fields=["requester"])
    person.active = False
    person.save(update_fields=["active"])
    client.force_login(viewer)
    assert client.post(
        reverse("requests:detail", args=[flow["request"].pk]),
        {"kind": "COMMENT", "body": "No"},
    ).status_code == 404
    assert not execution.notes.exists()


def test_request_view_permission_alone_does_not_expose_execution_log(
    flow, client, make_user, make_person, make_role, make_membership
):
    execution = start_request_execution(flow["request"], flow["actor"])
    viewer = make_user()
    role = make_role(permissions=["request.view"])
    make_membership(make_person(viewer), flow["org_tree"]["parish_a"], role)
    client.force_login(viewer)
    url = reverse("requests:detail", args=[flow["request"].pk])
    response = client.get(url)
    assert response.status_code == 200
    body = response.content.decode()
    assert 'id="execution-log"' not in body
    header = body.split('<dl class="grid gap-x-8', 1)[1].split("</dl>", 1)[0]
    assert "Current step</dt>" in header and ">—</dd>" in header
    assert ">Work</dd>" not in header
    assert client.post(url, {"kind": "COMMENT", "body": "No access"}).status_code == 404
    assert not execution.notes.exists()


def test_incident_detail_shows_existing_execution_steps_and_actions(flow, client):
    from apps.incidents.models import Incident

    perm, _ = Permission.objects.get_or_create(
        code="incident.view", defaults={"label": "View incident", "module": "incident"}
    )
    flow["role"].permissions.add(perm, through_defaults={"active": True})
    incident = Incident.objects.create(service=flow["service"], title="Incident")
    execution = OfferingExecution.objects.create(
        offering=flow["offering"], subject=incident, current_step=flow["work"],
        status=ExecutionStatus.RUNNING,
    )
    task = OfferingTask.objects.create(execution=execution, step=flow["work"], title="Repair")
    client.force_login(flow["user"])
    url = reverse("incidents:detail", args=[incident.pk])
    body = client.get(url).content.decode()
    assert "Repair" in body
    assert "Average active time: No reliable completed tasks" in body
    request_execution = start_request_execution(flow["request"], flow["actor"])
    request_body = client.get(reverse("requests:detail", args=[flow["request"].pk])).content.decode()
    assert request_execution and "Average active time: No reliable completed tasks" in request_body
    assert 'value="start"' in body
    response = client.post(
        reverse("offerings:task_action", args=[task.pk]), {"action": "start", "next": url}
    )
    assert response.url == url
    task.refresh_from_db()
    assert task.status == TaskStatus.IN_PROGRESS
    other = Incident.objects.create(service=flow["service"], title="No execution")
    assert "Task history records the people" not in client.get(
        reverse("incidents:detail", args=[other.pk])
    ).content.decode()


def test_requester_sees_steps_but_cannot_operate_tasks(
    flow, client, make_user, make_person
):
    requester = make_person(make_user(), name="Requester")
    flow["request"].requester = requester
    flow["request"].save(update_fields=["requester"])
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    client.force_login(requester.user)
    url = reverse("requests:detail", args=[flow["request"].pk])
    body = client.get(url).content.decode()
    assert ">Work</p>" in body
    assert "Average active time" not in body
    assert 'value="start"' not in body
    assert client.post(reverse("offerings:task_action", args=[task.pk]), {"action": "start"}).status_code == 403


def test_task_action_rejects_external_redirect(flow, client):
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    client.force_login(flow["user"])
    response = client.post(
        reverse("offerings:task_action", args=[task.pk]),
        {"action": "start", "next": "https://example.com/"},
    )
    assert response.url == reverse("offerings:execution_detail", args=[execution.pk])


def test_incident_viewer_without_execution_access_does_not_see_steps(
    flow, client, make_user, make_person, make_role, make_membership
):
    from apps.incidents.models import Incident

    incident = Incident.objects.create(service=flow["service"], title="Incident")
    execution = OfferingExecution.objects.create(
        offering=flow["offering"], subject=incident, current_step=flow["work"],
        status=ExecutionStatus.RUNNING,
    )
    OfferingTask.objects.create(execution=execution, step=flow["work"], title="Private work")
    viewer = make_user()
    viewer_role = make_role(permissions=["incident.view"])
    make_membership(make_person(viewer), flow["org_tree"]["parish_a"], viewer_role)
    client.force_login(viewer)
    response = client.get(reverse("incidents:detail", args=[incident.pk]))
    assert response.status_code == 200
    assert "Private work" not in response.content.decode()
    assert client.get(reverse("offerings:execution_detail", args=[execution.pk])).status_code == 404


def test_request_detail_hides_completion_when_task_assigned_to_another_person(
    flow, client, make_user, make_person
):
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    task.assigned_to = make_person(make_user())
    task.status = TaskStatus.IN_PROGRESS
    task.save(update_fields=["assigned_to", "status"])
    client.force_login(flow["user"])
    body = client.get(reverse("requests:detail", args=[flow["request"].pk])).content.decode()
    assert ">Work</p>" in body
    assert 'value="complete"' not in body
    assert client.post(
        reverse("offerings:task_action", args=[task.pk]), {"action": "complete"}
    ).status_code == 302
    task.refresh_from_db()
    assert task.status == TaskStatus.IN_PROGRESS


def test_linked_requests_cannot_be_closed_independently(flow, client):
    permission, _ = Permission.objects.get_or_create(
        code="request.close", defaults={"label": "Close", "module": "request"}
    )
    flow["role"].permissions.add(permission, through_defaults={"active": True})
    start_request_execution(flow["request"], flow["actor"])
    client.force_login(flow["user"])
    response = client.post(
        reverse("requests:index"),
        {"action": "mark_closed", "ids": [flow["request"].pk]},
        follow=True,
    )
    assert response.status_code == 200
    assert "cannot be closed independently" in response.content.decode()
    flow["request"].refresh_from_db()
    assert flow["request"].status == RequestStatus.IN_PROGRESS


def test_group_claim_controls_and_lifecycle(
    flow, client, make_user, make_person, make_role, make_membership
):
    flow["work"].responsible_role = flow["role"]
    flow["work"].save(update_fields=["responsible_role"])
    outsider = make_user()
    outsider_person = make_person(outsider, name="Other group")
    outsider_role = make_role(permissions=["service.view", "offering.view", "offering.execute"])
    make_membership(outsider_person, flow["org_tree"]["parish_a"], outsider_role)
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    client.force_login(outsider)
    assert not actionable_tasks(outsider).filter(pk=task.pk).exists()
    assert actionable_tasks(flow["user"]).filter(pk=task.pk).exists()
    with pytest.raises(PermissionDenied):
        task_action(task.pk, outsider, "claim")
    with pytest.raises(ValidationError, match="cannot be started"):
        task_action(task.pk, flow["user"], "start")

    client.force_login(flow["user"])
    url = reverse("offerings:execution_detail", args=[execution.pk])
    body = client.get(url).content.decode()
    assert ">Claim task</button>" in body
    controls = body.split('<div class="flex flex-wrap items-center gap-2">')[-1].split("</div>", 1)[0]
    assert 'name="assignee" required' in controls
    assert 'value="assign"' in controls and 'value="claim"' in controls
    assert controls.count("<form ") == 2
    assert ">Start</button>" not in body
    task_action(task.pk, flow["user"], "claim")
    task.refresh_from_db()
    assert task.assigned_to == flow["actor"]
    assert task.status == TaskStatus.ASSIGNED
    assert task.activities.filter(action="claimed", actor=flow["actor"]).count() == 1
    with pytest.raises(ValidationError, match="already been claimed"):
        task_action(task.pk, flow["user"], "claim")
    body = client.get(url).content.decode()
    assert ">Claim task</button>" not in body
    assert ">Start</button>" in body
    task_action(task.pk, flow["user"], "start")
    task_action(task.pk, flow["user"], "complete")
    execution.refresh_from_db()
    assert execution.status == ExecutionStatus.COMPLETED


def test_service_actor_can_claim_with_scoped_execution_permission(
    flow, make_user, make_person, make_role, make_membership, make_service
):
    worker = make_person(make_user(), name="Service manager")
    group = make_role()
    scoped_role = make_role(permissions=["service.view", "offering.execute"])
    make_membership(worker, flow["org_tree"]["parish_a"], scoped_role)
    ServiceActor.objects.create(service=flow["service"], role=group, person=worker)
    flow["work"].responsible_role = group
    flow["work"].save(update_fields=["responsible_role"])
    task = start_request_execution(flow["request"], flow["actor"]).tasks.get()
    assert actionable_tasks(worker.user).filter(pk=task.pk).exists()
    task_action(task.pk, worker.user, "claim")
    assert not actionable_tasks(flow["user"]).filter(pk=task.pk, assigned_to=flow["actor"]).exists()
    other_service = make_service(flow["org_tree"]["parish_a"], "Other service")
    flow["offering"].service = other_service
    flow["offering"].save(update_fields=["service"])
    task.refresh_from_db()
    assert worker not in eligible_people(task.step)


def test_service_manager_execution_grant_stays_scoped(make_service, org_tree):
    from django.apps import apps

    call_command("seed_rbac", verbosity=0)
    base = Role.objects.get(code="SERVICE_MANAGER")
    assert base.rolepermissions.filter(permission__code="offering.execute", active=True).exists()
    service = make_service(org_tree["parish_a"])
    clone = Role.objects.create(code="SERVICE_MANAGER_TEST", name="Scoped", is_system=True)
    unscoped = Role.objects.create(
        code="SERVICE_MANAGER_NO_SCOPE", name="Unscoped", is_system=True
    )
    view_grant = RolePermission.objects.create(
        role=clone, permission=Permission.objects.get(code="service.view")
    )
    GrantScope.objects.create(
        role_permission=view_grant, scope_type=GrantScopeType.SPECIFIC_SERVICE, service=service
    )
    migration = import_module("apps.access.migrations.0006_service_manager_task_execution")
    migration.grant_service_manager_task_execution(apps, None)
    grant = clone.rolepermissions.get(permission__code="offering.execute")
    assert list(grant.grant_scopes.values_list("scope_type", "service_id")) == [
        (GrantScopeType.SPECIFIC_SERVICE, service.pk)
    ]
    assert not unscoped.rolepermissions.filter(permission__code="offering.execute").exists()


def test_execution_advances_and_notifies_with_action_history(flow):
    execution = start_request_execution(flow["request"], flow["actor"])
    flow["request"].refresh_from_db()
    assert flow["request"].status == RequestStatus.IN_PROGRESS
    task = execution.tasks.get()
    assert task.step == flow["work"] and task.status == TaskStatus.PENDING
    assert abs((task.deadline - timezone.now() - timedelta(hours=1)).total_seconds()) < 5
    assert flow["actor"] in eligible_people(flow["work"])
    task_action(task.pk, flow["user"], "assign", assignee_id=flow["actor"].pk)
    task_action(task.pk, flow["user"], "start")
    task_action(task.pk, flow["user"], "complete")
    execution.refresh_from_db()
    assert execution.status == ExecutionStatus.COMPLETED
    flow["request"].refresh_from_db()
    assert flow["request"].status == RequestStatus.RESOLVED
    assert execution.current_step == flow["end"]
    assert execution.completed_at is not None
    activities = list(task.activities.select_related("actor").order_by("created_at", "pk"))
    assert [activity.action for activity in activities] == [
        "created",
        "assigned",
        "started",
        "completed",
    ]
    assert activities[0].actor.full_name == "Sistema"
    assert activities[0].actor.person_type == PersonType.SYSTEM
    assert activities[0].actor.user is None
    assert all(activity.actor == flow["actor"] for activity in activities[1:])
    assert Notification.objects.get(task__execution=execution).recipient == flow["actor"]
    assert execution.tasks.filter(step=flow["notify"], status=TaskStatus.DONE).exists()
    with pytest.raises(ValidationError):
        task_action(task.pk, flow["user"], "complete")


def test_assignment_controls_and_duplicate_assignment(
    flow, client, make_user, make_person, make_membership
):
    other = make_person(make_user(), name="Another worker")
    make_membership(other, flow["org_tree"]["parish_a"], flow["role"])
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    client.force_login(flow["user"])
    url = reverse("offerings:execution_detail", args=[execution.pk])

    before = client.get(url).content.decode()
    assert ">Assign</button>" in before
    assert ">Reassign</summary>" not in before

    task_action(task.pk, flow["user"], "assign", assignee_id=flow["actor"].pk)
    with pytest.raises(ValidationError, match="already assigned"):
        task_action(task.pk, flow["user"], "assign", assignee_id=flow["actor"].pk)
    assert task.activities.filter(action="assigned").count() == 1

    assigned = client.get(url).content.decode()
    assert ">Assign</button>" not in assigned
    assert ">Reassign</summary>" in assigned
    assert ">Start</button>" in assigned
    assert f'<option value="{flow["actor"].pk}">' not in assigned
    assert f'<option value="{other.pk}">Another worker</option>' in assigned

    response = client.post(
        reverse("offerings:task_action", args=[task.pk]),
        {"action": "assign", "assignee": flow["actor"].pk},
        follow=True,
    )
    assert "This task is already assigned to that person." in response.content.decode()
    assert task.activities.filter(action="assigned").count() == 1

    task_action(task.pk, flow["user"], "assign", assignee_id=other.pk)
    task.refresh_from_db()
    assert task.assigned_to == other
    assert task.activities.filter(action="assigned").count() == 2
    assert ">Start</button>" not in client.get(url).content.decode()
    client.force_login(other.user)
    assert ">Start</button>" in client.get(url).content.decode()
    task_action(task.pk, other.user, "start")
    with pytest.raises(ValidationError, match="already assigned"):
        task_action(task.pk, flow["user"], "assign", assignee_id=other.pk)
    assert task.activities.filter(action="assigned").count() == 2


def test_assignment_control_hidden_when_no_other_eligible_person(flow, client):
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    task_action(task.pk, flow["user"], "assign", assignee_id=flow["actor"].pk)
    client.force_login(flow["user"])
    body = client.get(reverse("offerings:execution_detail", args=[execution.pk])).content.decode()
    assert ">Assign</button>" not in body
    assert ">Reassign</summary>" not in body
    assert ">Start</button>" in body


def test_failure_retry_and_error_branch_preserve_visits(flow):
    OfferingTransition.objects.create(
        offering=flow["offering"],
        from_step=flow["work"],
        to_step=flow["notify"],
        outcome=TransitionOutcome.FAILURE,
        order=20,
    )
    execution = start_request_execution(flow["request"], flow["actor"])
    first = execution.tasks.get()
    task_action(first.pk, flow["user"], "start")
    task_action(first.pk, flow["user"], "fail")
    first.refresh_from_db()
    failed_at = first.completed_at
    execution.refresh_from_db()
    assert execution.status == ExecutionStatus.WAITING
    task_action(first.pk, flow["user"], "retry")
    first.refresh_from_db()
    assert first.completed_at == failed_at
    second = execution.tasks.exclude(pk=first.pk).get()
    assert second.step == first.step
    task_action(second.pk, flow["user"], "start")
    task_action(second.pk, flow["user"], "fail")
    task_action(second.pk, flow["user"], "branch")
    execution.refresh_from_db()
    assert execution.status == ExecutionStatus.COMPLETED
    assert Notification.objects.filter(task__execution=execution).count() == 1


def test_notification_stored_for_person_without_account(flow, make_person):
    recipient = make_person(name="No login")
    flow["request"].requester = recipient
    flow["request"].save(update_fields=["requester"])
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    task_action(task.pk, flow["user"], "start")
    task_action(task.pk, flow["user"], "complete")
    assert Notification.objects.get(task__execution=execution).recipient == recipient


def test_requester_can_see_own_execution_and_notes_but_not_operate_tasks(
    flow, client, make_user, make_person
):
    user = make_user()
    person = make_person(user)
    flow["request"].requester = person
    flow["request"].save(update_fields=["requester"])
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    ExecutionNote.objects.create(
        execution=execution,
        task=task,
        author=flow["user"],
        body="Internal note",
        kind=ExecutionNote.Kind.WORK_NOTE,
    )
    client.force_login(user)
    request_url = reverse("requests:detail", args=[flow["request"].pk])
    execution_url = reverse("offerings:execution_detail", args=[execution.pk])
    assert client.get(request_url).status_code == 200
    response = client.get(execution_url)
    assert response.status_code == 200
    assert "Internal note" not in response.content.decode()
    assert [value for value, _ in response.context["note_form"].fields["kind"].choices] == [
        ExecutionNote.Kind.COMMENT
    ]
    assert client.post(
        execution_url, {"kind": "WORK_NOTE", "body": "Requester private note"}
    ).status_code == 400
    assert not ExecutionNote.objects.filter(body="Requester private note").exists()
    assert client.post(
        execution_url, {"kind": "COMMENT", "body": "Requester comment"}
    ).status_code == 302
    requester_body = client.get(execution_url).content.decode()
    assert "Requester comment" in requester_body
    assert "Internal note" not in requester_body
    assert ExecutionNote.objects.filter(execution=execution, author=user).exists()
    assert (
        client.post(
            reverse("offerings:task_action", args=[task.pk]), {"action": "start"}
        ).status_code
        == 403
    )
    task.refresh_from_db()
    assert task.status == TaskStatus.PENDING


def test_missing_recipient_blocks_delivery_without_losing_task(flow):
    flow["request"].requester = None
    flow["request"].save(update_fields=["requester"])
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    task_action(task.pk, flow["user"], "start")
    with pytest.raises(ValidationError):
        task_action(task.pk, flow["user"], "complete")
    task.refresh_from_db()
    assert task.status == TaskStatus.IN_PROGRESS
    assert not Notification.objects.exists()


def test_notification_read_is_person_scoped(flow, client, make_user, make_person):
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    task_action(task.pk, flow["user"], "start")
    task_action(task.pk, flow["user"], "complete")
    notification = Notification.objects.get()
    other = make_user()
    make_person(other)
    client.force_login(other)
    assert (
        client.post(reverse("offerings:notification_read", args=[notification.pk])).status_code
        == 404
    )
    assert (
        "Your request is ready."
        not in client.get(reverse("offerings:notifications")).content.decode()
    )
    client.force_login(flow["user"])
    assert (
        "Your request is ready." in client.get(reverse("offerings:notifications")).content.decode()
    )
    assert (
        client.post(reverse("offerings:notification_read", args=[notification.pk])).status_code
        == 302
    )
    notification.refresh_from_db()
    assert notification.read_at is not None


def test_permissions_and_duplicate_actions(flow, make_user, make_person):
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    outsider = make_user()
    make_person(outsider)
    with pytest.raises(PermissionDenied):
        task_action(task.pk, outsider, "start")
    task_action(task.pk, flow["user"], "start")
    with pytest.raises(ValidationError):
        task_action(task.pk, flow["user"], "start")
    assert task.activities.filter(action="started").count() == 1


def test_backfill_current_step_only_and_is_idempotent(flow):
    execution = OfferingExecution.objects.create(
        offering=flow["offering"], current_step=flow["work"], status=ExecutionStatus.RUNNING
    )
    output = StringIO()
    call_command("backfill_execution_tasks", stdout=output)
    call_command("backfill_execution_tasks", stdout=output)
    task = execution.tasks.get()
    assert task.step == flow["work"] and task.started_at is None and task.deadline is None
    assert task.activities.count() == 0


def test_new_request_automatically_starts_execution(flow, client):
    client.force_login(flow["user"])
    permission, _ = Permission.objects.get_or_create(
        code="request.create", defaults={"label": "Create request"}
    )
    flow["role"].permissions.add(permission, through_defaults={"active": True})
    response = client.post(
        reverse("requests:create"),
        {"service": flow["service"].pk, "offering": flow["offering"].pk, "title": "New request"},
    )
    assert response.status_code == 302
    req = Request.objects.get(title="New request")
    execution = OfferingExecution.objects.get(subject_id=req.pk, offering=flow["offering"])
    assert execution.tasks.get().step == flow["work"]


def test_gateway_choice_and_loop_create_new_visits(flow):
    work = flow["work"]
    work.step_type = StepType.GATEWAY
    work.save(update_fields=["step_type"])
    OfferingTransition.objects.create(
        offering=flow["offering"],
        from_step=work,
        to_step=work,
        outcome=TransitionOutcome.FAILURE,
        order=20,
    )
    execution = start_request_execution(flow["request"], flow["actor"])
    first = execution.tasks.get()
    task_action(first.pk, flow["user"], "start")
    task_action(first.pk, flow["user"], "complete", outcome=TransitionOutcome.FAILURE)
    second = execution.tasks.exclude(pk=first.pk).get()
    assert second.step == first.step
    assert second.status == TaskStatus.PENDING
    task_action(second.pk, flow["user"], "start")
    task_action(second.pk, flow["user"], "complete", outcome=TransitionOutcome.SUCCESS)
    execution.refresh_from_db()
    assert execution.status == ExecutionStatus.COMPLETED


def test_invalid_graph_rolls_back_request(flow, client):
    Permission.objects.get_or_create(code="request.create", defaults={"label": "Create request"})
    flow["role"].permissions.add(
        Permission.objects.get(code="request.create"), through_defaults={"active": True}
    )
    flow["start"].delete()
    client.force_login(flow["user"])
    response = client.post(
        reverse("requests:create"),
        {
            "service": flow["service"].pk,
            "offering": flow["offering"].pk,
            "title": "Invalid graph",
        },
    )
    assert response.status_code == 200
    assert "exactly one start step" in response.content.decode()
    assert not Request.objects.filter(title="Invalid graph").exists()


def test_automatic_cycle_rolls_back_completion(flow):
    OfferingTransition.objects.filter(from_step=flow["notify"]).update(to_step=flow["notify"])
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    task_action(task.pk, flow["user"], "start")
    with pytest.raises(ValidationError, match="automatic step cycle"):
        task_action(task.pk, flow["user"], "complete")
    task.refresh_from_db()
    assert task.status == TaskStatus.IN_PROGRESS
    assert execution.tasks.count() == 1
    assert not Notification.objects.exists()


def test_delegated_worker_sees_only_current_task(
    flow, client, make_service, make_user, make_person, make_role, make_membership, org_tree
):
    external_service = make_service(org_tree["parish_b"], "External")
    external_offering = ServiceOffering.objects.create(
        service=external_service, name="External offering"
    )
    flow["work"].service_offering = external_offering
    flow["work"].responsible_role = None
    flow["work"].save(update_fields=["service_offering", "responsible_role"])
    execution = start_request_execution(flow["request"], flow["actor"])
    worker = make_user()
    worker_person = make_person(worker, name="External worker")
    role = make_role(permissions=["offering.execute", "organization.view"])
    make_membership(worker_person, org_tree["parish_b"], role)
    flow["work"].responsible_role = role
    flow["work"].save(update_fields=["responsible_role"])
    client.force_login(worker)
    task_url = reverse("offerings:tasks")
    task = execution.tasks.get()
    client.force_login(flow["user"])
    detail_url = reverse("offerings:execution_detail", args=[execution.pk])
    assert client.post(
        detail_url, {"kind": "COMMENT", "body": "Owner message"}
    ).status_code == 302
    assert ExecutionNote.objects.get(body="Owner message").task == task
    old_task = OfferingTask.objects.create(
        execution=execution, step=flow["notify"], title="Private step", status=TaskStatus.DONE
    )
    ExecutionNote.objects.create(
        execution=execution, task=old_task, author=flow["user"], body="Private old note"
    )
    ExecutionNote.objects.create(
        execution=execution, author=flow["user"], body="Private general note"
    )
    client.force_login(worker)
    response = client.get(task_url, {"service": external_service.pk})
    assert list(response.context["object_list"]) == [task]
    assert "External" in response.content.decode()
    assert "Funeral" not in response.content.decode()
    assert not list(client.get(task_url, {"service": flow["service"].pk}).context["object_list"])
    assert list(
        client.get(task_url, {"organization": org_tree["parish_b"].pk}).context["object_list"]
    ) == [task]
    body = client.get(reverse("offerings:execution_detail", args=[execution.pk])).content.decode()
    assert "Work" in body
    assert "Your request is ready." not in body
    assert ">Notify</p>" not in body
    assert "Funeral" not in body
    assert "Example" not in body
    assert "Owner message" in body
    assert "Private old note" not in body
    assert "Private general note" not in body
    assert "Private step" not in body
    assert "Average active time" not in body
    request_viewer = make_role(permissions=["request.view"])
    make_membership(worker_person, org_tree["parish_a"], request_viewer)
    request_body = client.get(reverse("requests:detail", args=[flow["request"].pk])).content.decode()
    assert ">Work</p>" in request_body
    assert "Average active time" not in request_body
    assert "Private step" not in request_body
    assert ">Notify</p>" not in request_body
    assert ">Claim task</button>" in request_body
    log = request_body.split('id="execution-log"', 1)[1]
    assert "Owner message" in log
    assert "Private old note" not in log and "Private general note" not in log
    request_url = reverse("requests:detail", args=[flow["request"].pk])
    assert client.post(
        request_url, {"kind": "WORK_NOTE", "body": "Request team message"}
    ).url == (
        f"{request_url}#execution-log"
    )
    assert ExecutionNote.objects.get(body="Request team message").task == task
    assert client.post(
        detail_url, {"kind": "WORK_NOTE", "body": "Related team message"}
    ).status_code == 302
    assert ExecutionNote.objects.get(body="Related team message").task == task
    assert "Related team message" in client.get(detail_url).content.decode()
    client.force_login(flow["user"])
    assert "Related team message" not in client.get(detail_url).content.decode()
    client.force_login(worker)
    assert ">Claim task</button>" in client.get(detail_url).content.decode()
    task_action(task.pk, worker, "claim")
    task_action(task.pk, worker, "start")


def test_task_table_navigation_search_filters_sort_and_pagination(
    flow, client, make_service, org_tree
):
    permission, _ = Permission.objects.get_or_create(
        code="organization.view", defaults={"label": "View organization"}
    )
    flow["role"].permissions.add(permission, through_defaults={"active": True})
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    task.title = "Alpha work"
    task.assigned_to = flow["actor"]
    task.save(update_fields=["title", "assigned_to"])
    for i in range(27):
        extra_execution = OfferingExecution.objects.create(
            offering=flow["offering"], current_step=flow["work"], status=ExecutionStatus.RUNNING
        )
        OfferingTask.objects.create(
            execution=extra_execution, step=flow["work"], title=f"Work {i:02d}"
        )
    client.force_login(flow["user"])
    url = reverse("offerings:tasks")
    response = client.get(url)
    assert response.status_code == 200
    assert response.context["paginator"].count == 28
    assert len(response.context["object_list"]) == 25
    assert "Page 1 of 2" in response.content.decode()
    group = next(
        group for group in response.context["nav_groups"] if group["label"] == "Service Offerings"
    )
    urls = [item["url"] for item in group["items"]]
    assert urls.index(url) == urls.index(reverse("offerings:executions")) + 1
    assert 'id="offering_tasks-wrap"' in response.content.decode()
    assert (
        reverse("offerings:execution_detail", args=[execution.pk])
        in client.get(url, {"q": "Alpha"}).content.decode()
    )
    assert list(client.get(url, {"q": "Worker"}).context["object_list"]) == [task]
    assert client.get(url, {"status": "ASSIGNED"}).context["paginator"].count == 0
    assert client.get(url, {"status": "PENDING"}).context["paginator"].count == 28
    assert client.get(url, {"service": flow["service"].pk}).context["paginator"].count == 28
    assert (
        client.get(url, {"organization": org_tree["parish_a"].pk}).context["paginator"].count == 28
    )
    assert client.get(url, {"sort": "title", "page": 1}).context["object_list"][0] == task
    page_two = client.get(url, {"page": 2}, HTTP_HX_REQUEST="true")
    assert len(page_two.context["object_list"]) == 3
    assert "Page 2 of 2" in page_two.content.decode()
    assert client.get(url, {"created_at__from": "2099-01-01"}).context["paginator"].count == 0
    partial = client.get(url, {"q": "Alpha"}, HTTP_HX_REQUEST="true")
    assert partial.context["partial"]
    assert "<html" not in partial.content.decode()
    other_service = make_service(org_tree["parish_a"], "Other")
    assert client.get(url, {"service": other_service.pk}).context["paginator"].count == 0
    client.logout()
    assert client.get(url).status_code == 302


def test_task_table_can_show_all_ongoing_tasks_in_authorized_services(
    flow, client, make_user, make_person, make_role, make_membership,
    make_service, org_tree
):
    execution = start_request_execution(flow["request"], flow["actor"])
    task = execution.tasks.get()
    task.assigned_to = flow["actor"]
    task.save(update_fields=["assigned_to"])
    delegated_service = make_service(org_tree["parish_a"], "Delegated service")
    task.step.service_offering = ServiceOffering.objects.create(
        service=delegated_service, name="Delegated offering"
    )
    task.step.save(update_fields=["service_offering"])

    viewer = make_user()
    viewer_person = make_person(viewer, name="Service viewer")
    viewer_role = make_role(permissions=["service.view", "offering.execute"])
    make_membership(viewer_person, flow["service"].organization, viewer_role)
    client.force_login(viewer)

    url = reverse("offerings:tasks")
    session = client.session
    session["service_context"] = {
        "pk": flow["service"].pk,
        "role": viewer_role.code,
    }
    session.save()
    mine = client.get(url)
    assert mine.status_code == 200
    assert mine.context["paginator"].count == 0
    assert "All ongoing tasks in my services" in mine.content.decode()

    service_tasks = client.get(url, {"scope": "service"})
    assert service_tasks.status_code == 200
    assert list(service_tasks.context["object_list"]) == [task]
    assert service_tasks.context["task_scope"] == "service"
    assert "?scope=service" in service_tasks.content.decode()
    assert service_tasks.context["object_list"][0].responsible_service_id == delegated_service.pk


def test_backfill_does_not_infer_completion(flow):
    execution = OfferingExecution.objects.create(
        offering=flow["offering"], current_step=flow["work"], status=ExecutionStatus.WAITING
    )
    call_command("backfill_execution_tasks", stdout=StringIO())
    task = execution.tasks.get()
    assert task.status == TaskStatus.PENDING
    assert task.started_at is None and task.completed_at is None
    task_action(task.pk, flow["user"], "resume")
    execution.refresh_from_db()
    assert execution.status == ExecutionStatus.RUNNING


@pytest.mark.django_db(transaction=True)
def test_concurrent_claim_and_start_create_only_one_activity(
    flow, make_user, make_person, make_membership
):
    flow["work"].responsible_role = flow["role"]
    flow["work"].save(update_fields=["responsible_role"])
    other = make_person(make_user(), name="Other worker")
    make_membership(other, flow["org_tree"]["parish_a"], flow["role"])
    task = start_request_execution(flow["request"], flow["actor"]).tasks.get()
    barrier = Barrier(2)

    def attempt(user):
        close_old_connections()
        barrier.wait(timeout=10)
        try:
            task_action(task.pk, user, "claim")
            return "claimed"
        except ValidationError:
            return "rejected"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [flow["user"], other.user]))
    assert sorted(results) == ["claimed", "rejected"]
    assert task.activities.filter(action="claimed").count() == 1

    task.refresh_from_db()
    barrier = Barrier(2)

    def start(_):
        close_old_connections()
        barrier.wait(timeout=10)
        try:
            task_action(task.pk, task.assigned_to.user, "start")
            return "started"
        except ValidationError:
            return "rejected"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, range(2)))
    assert sorted(results) == ["rejected", "started"]
    assert task.activities.filter(action="started").count() == 1
