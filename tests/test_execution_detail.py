from datetime import UTC, datetime, timedelta

import pytest
from django.urls import reverse

from apps.offerings.models import (
    ExecutionNote,
    ExecutionStatus,
    OfferingExecution,
    OfferingStep,
    OfferingTask,
    ServiceOffering,
    StepType,
    TaskActivity,
    TaskStatus,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def execution_context(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    user = make_user()
    person = make_person(user, name="Operator")
    role = make_role(permissions=["service.view", "offering.view"])
    make_membership(person, org_tree["parish_a"], role)
    service = make_service(org_tree["parish_a"], "Cemetery")
    offering = ServiceOffering.objects.create(service=service, name="Funeral")
    first = OfferingStep.objects.create(
        offering=offering, code="validate", name="Validate", step_type=StepType.TASK, order=10
    )
    second = OfferingStep.objects.create(
        offering=offering, code="approve", name="Approve", step_type=StepType.APPROVAL, order=20
    )
    started = datetime(2026, 10, 1, 10, tzinfo=UTC)
    execution = OfferingExecution.objects.create(
        offering=offering,
        status=ExecutionStatus.RUNNING,
        current_step=second,
        created_by=person,
        started_at=started,
    )
    OfferingTask.objects.create(
        execution=execution,
        step=first,
        title="Check documents",
        assigned_to=person,
        status=TaskStatus.DONE,
        started_at=started,
        completed_at=started + timedelta(hours=2),
    )
    client.force_login(user)
    return {"client": client, "person": person, "execution": execution, "offering": offering}


def test_execution_detail_shows_steps_tasks_and_real_timings(execution_context):
    execution = execution_context["execution"]
    response = execution_context["client"].get(
        reverse("offerings:execution_detail", args=[execution.pk])
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert body.index(">Validate</p>") < body.index(">Approve</p>")
    assert "Check documents" in body
    assert "Operator" in body
    assert "2h 0m 0s" in body
    assert "Started:" in body
    assert "Completed:" in body
    assert "No task recorded for this step." in body
    assert "Task history records the people who performed each action." in body
    assert "Current step" in body


def test_execution_list_and_offering_detail_link_to_execution(execution_context):
    client = execution_context["client"]
    execution = execution_context["execution"]
    url = reverse("offerings:execution_detail", args=[execution.pk])
    assert url in client.get(reverse("offerings:executions")).content.decode()
    assert (
        url
        in client.get(
            reverse("offerings:detail", args=[execution_context["offering"].pk])
        ).content.decode()
    )


def test_offering_detail_shows_step_average_without_sla(execution_context):
    offering = execution_context["offering"]
    step = offering.steps.get(code="validate")
    step.sla_minutes = 60
    step.save(update_fields=["sla_minutes"])
    response = execution_context["client"].get(reverse("offerings:detail", args=[offering.pk]))
    row = response.content.decode().split(">Validate</p>", 1)[1].split("</li>", 1)[0]
    assert "Average active time: 2h 0m 0s" in row
    assert "SLA" not in row


def test_execution_detail_rejects_out_of_scope_and_anonymous(
    execution_context, client, make_service, org_tree
):
    service = make_service(org_tree["parish_b"], "Other")
    offering = ServiceOffering.objects.create(service=service, name="Private")
    hidden = OfferingExecution.objects.create(offering=offering)
    url = reverse("offerings:execution_detail", args=[hidden.pk])
    assert execution_context["client"].get(url).status_code == 404
    client.logout()
    assert client.get(url).status_code == 302


def test_execution_detail_without_tasks_or_timestamps(execution_context):
    execution = execution_context["execution"]
    execution.tasks.all().delete()
    execution.started_at = None
    execution.created_by = None
    execution.save(update_fields=["started_at", "created_by"])
    response = execution_context["client"].get(
        reverse("offerings:execution_detail", args=[execution.pk])
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert body.count("No task recorded for this step.") == 2
    assert "Started by:" not in body
    assert "Started:" not in body
    assert "Completed:" not in body
    assert "Duration:" not in body
    assert "Deadline:" not in body


def test_execution_detail_shows_multiple_tasks_on_one_step(execution_context):
    execution = execution_context["execution"]
    task = execution.tasks.get()
    OfferingTask.objects.create(execution=execution, step=task.step, title="Second check")
    body = (
        execution_context["client"]
        .get(reverse("offerings:execution_detail", args=[execution.pk]))
        .content.decode()
    )
    assert "Check documents" in body and "Second check" in body


def test_step_average_excludes_pauses_and_unreliable_visits(execution_context, monkeypatch):
    from unittest.mock import Mock

    from apps.offerings import step_duration

    warning = Mock()
    monkeypatch.setattr(step_duration.logger, "warning", warning)
    execution = execution_context["execution"]
    task = execution.tasks.get()
    start = task.started_at
    step = task.step
    task.completed_at = start + timedelta(hours=5)
    task.save(update_fields=["completed_at"])
    for action, offset in (("paused", 1), ("resumed", 2), ("paused", 3), ("resumed", 4)):
        event = TaskActivity.objects.create(task=task, action=action)
        TaskActivity.objects.filter(pk=event.pk).update(created_at=start + timedelta(hours=offset))
    OfferingTask.objects.create(
        execution=execution, step=step, title="Another visit", status=TaskStatus.DONE,
        started_at=start, completed_at=start + timedelta(hours=1),
    )
    invalid = OfferingTask.objects.create(
        execution=execution, step=step, title="Incomplete pause", status=TaskStatus.DONE,
        started_at=start, completed_at=start + timedelta(hours=20),
    )
    event = TaskActivity.objects.create(task=invalid, action="paused")
    TaskActivity.objects.filter(pk=event.pk).update(created_at=start + timedelta(hours=1))
    OfferingTask.objects.create(
        execution=execution, step=step, title="No timestamp", status=TaskStatus.DONE,
    )
    OfferingTask.objects.create(
        execution=execution, step=step, title="In progress", status=TaskStatus.IN_PROGRESS,
        started_at=start, completed_at=start + timedelta(hours=30),
    )
    other_version = ServiceOffering.objects.create(
        service=execution.offering.service, name=execution.offering.name, version=2
    )
    other_step = OfferingStep.objects.create(
        offering=other_version, code=step.code, name=step.name, step_type=step.step_type
    )
    other_execution = OfferingExecution.objects.create(offering=other_version)
    OfferingTask.objects.create(
        execution=other_execution, step=other_step, title="Other version", status=TaskStatus.DONE,
        started_at=start, completed_at=start + timedelta(hours=100),
    )

    client = execution_context["client"]
    for url in (
        reverse("offerings:execution_detail", args=[execution.pk]),
        reverse("offerings:detail", args=[execution.offering_id]),
    ):
        body = client.get(url).content.decode()
        assert "Average active time: 2h 0m 0s" in body
        assert "No reliable completed tasks" in body
        assert "excludes recorded pauses" in body
    assert any(call.args[1] == invalid.pk for call in warning.call_args_list)


@pytest.mark.parametrize(
    ("events", "end_hours", "expected"),
    [
        ([], 2, timedelta(hours=2)),
        ([("paused", 0), ("resumed", 2)], 2, timedelta()),
        ([("resumed", 1)], 2, None),
        ([("paused", 1), ("paused", 2)], 3, None),
        ([("paused", 1)], 2, None),
        ([("paused", -1), ("resumed", 1)], 2, None),
        ([("paused", 1), ("resumed", 3)], 2, None),
        ([], -1, None),
    ],
)
def test_active_duration_rejects_inconsistent_pause_history(events, end_hours, expected):
    from types import SimpleNamespace

    from apps.offerings.step_duration import active_duration

    start = datetime(2026, 10, 1, tzinfo=UTC)
    task = SimpleNamespace(
        started_at=start,
        completed_at=start + timedelta(hours=end_hours),
        pause_events=[
            SimpleNamespace(action=action, created_at=start + timedelta(hours=offset))
            for action, offset in events
        ],
    )
    assert active_duration(task) == expected


def test_zero_average_is_visible_in_portuguese(execution_context):
    from django.utils import translation

    execution = execution_context["execution"]
    task = execution.tasks.get()
    task.completed_at = task.started_at
    task.save(update_fields=["completed_at"])
    with translation.override("pt"):
        response = execution_context["client"].get(
            reverse("offerings:execution_detail", args=[execution.pk]),
            HTTP_ACCEPT_LANGUAGE="pt",
        )
    body = response.content.decode()
    assert "Tempo médio ativo: 0s" in body
    assert "Sem tarefas concluídas com tempos fiáveis" in body


def test_execution_log_combines_existing_updates_and_viewer_notes(execution_context):
    client = execution_context["client"]
    execution = execution_context["execution"]
    task = execution.tasks.get()
    TaskActivity.objects.create(task=task, actor=execution_context["person"], action="completed")
    url = reverse("offerings:execution_detail", args=[execution.pk])
    assert client.post(
        url,
        {"kind": "WORK_NOTE", "body": "  A note <script>alert(1)</script>  "},
    ).status_code == 302
    note = execution.notes.get()
    assert note.body == "A note <script>alert(1)</script>"
    assert note.author == execution_context["person"].user
    assert note.task is None
    body = client.get(url).content.decode()
    log = body.split('id="execution-log"', 1)[1]
    assert log.index("Message type") < log.index("A note")
    assert log.index("A note") < log.index("Completed")
    assert "Work Notes" in log
    assert 'bg-violet-50 text-violet-700 ring-violet-600/20">Work Notes</span>' in log
    assert 'text-zinc-600 ring-1 ring-inset ring-zinc-500/20">System</span>' in log
    assert 'bg-emerald-50 text-emerald-700 ring-emerald-600/20">Completed</span>' in log
    assert "Operator" in log
    assert "&lt;script&gt;" in log
    assert "<script>alert(1)</script>" not in log


def test_execution_log_displays_person_name_and_account_fallback(
    execution_context, make_user
):
    client = execution_context["client"]
    execution = execution_context["execution"]
    person = execution_context["person"]
    person.user.first_name = "Account"
    person.user.save(update_fields=["first_name"])
    no_person = make_user()
    no_person.first_name = "Standalone"
    no_person.save(update_fields=["first_name"])
    ExecutionNote.objects.create(execution=execution, author=person.user, body="Linked")
    ExecutionNote.objects.create(execution=execution, author=no_person, body="Unlinked")
    ExecutionNote.objects.create(execution=execution, author=None, body="Deleted author")
    response = client.get(reverse("offerings:execution_detail", args=[execution.pk]))
    entries = {entry.body: entry.author_display for entry in response.context["log_entries"]}
    assert entries == {"Linked": "Operator", "Unlinked": "Standalone", "Deleted author": "—"}
    no_person.first_name = ""
    no_person.save(update_fields=["first_name"])
    response = client.get(reverse("offerings:execution_detail", args=[execution.pk]))
    entries = {entry.body: entry.author_display for entry in response.context["log_entries"]}
    assert entries["Unlinked"] == no_person.username


@pytest.mark.parametrize("text", ["", "  ", "x" * 4001])
def test_execution_log_rejects_invalid_notes(execution_context, text):
    client = execution_context["client"]
    execution = execution_context["execution"]
    response = client.post(
        reverse("offerings:execution_detail", args=[execution.pk]),
        {"kind": "WORK_NOTE", "body": text},
    )
    assert response.status_code == 400
    assert response.context["note_form"].errors["body"]
    assert not execution.notes.exists()


def test_execution_log_rejects_outsiders_and_allows_viewer_without_person(
    execution_context, client, make_user
):
    execution = execution_context["execution"]
    url = reverse("offerings:execution_detail", args=[execution.pk])
    client.force_login(make_user())
    assert client.post(url, {"kind": "COMMENT", "body": "unauthorized"}).status_code == 404
    assert not ExecutionNote.objects.exists()
    superuser = make_user()
    superuser.is_superuser = True
    superuser.save(update_fields=["is_superuser"])
    client.force_login(superuser)
    assert client.post(url, {"kind": "COMMENT", "body": "Viewer note"}).status_code == 302
    assert execution.notes.get().author == superuser


@pytest.mark.parametrize("local_permission", ["service.view", "offering.view"])
def test_execution_owner_requires_both_permissions_in_service_scope(
    execution_context,
    client,
    make_user,
    make_person,
    make_role,
    make_membership,
    org_tree,
    local_permission,
):
    user = make_user()
    person = make_person(user)
    remote_permission = "offering.view" if local_permission == "service.view" else "service.view"
    make_membership(person, org_tree["parish_a"], make_role(permissions=[local_permission]))
    make_membership(person, org_tree["parish_b"], make_role(permissions=[remote_permission]))
    client.force_login(user)
    execution = execution_context["execution"]
    url = reverse("offerings:execution_detail", args=[execution.pk])
    assert client.get(url).status_code == 404
    assert url not in client.get(reverse("offerings:executions")).content.decode()
    if local_permission == "offering.view":
        offering_url = reverse("offerings:detail", args=[execution.offering_id])
        offering_response = client.get(offering_url)
        assert offering_response.status_code == 200
        assert "Recent executions" not in offering_response.content.decode()
