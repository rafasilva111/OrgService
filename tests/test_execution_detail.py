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
    assert response.content.decode().count("No task recorded for this step.") == 2


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


def test_execution_log_combines_existing_updates_and_viewer_notes(execution_context):
    client = execution_context["client"]
    execution = execution_context["execution"]
    task = execution.tasks.get()
    TaskActivity.objects.create(task=task, actor=execution_context["person"], action="completed")
    url = reverse("offerings:execution_detail", args=[execution.pk])
    assert client.post(url, {"body": "  A note <script>alert(1)</script>  "}).status_code == 302
    note = execution.notes.get()
    assert note.body == "A note <script>alert(1)</script>"
    assert note.author == execution_context["person"].user
    assert note.task is None
    body = client.get(url).content.decode()
    log = body.split('id="execution-log"', 1)[1]
    assert log.index("Add a note") < log.index("A note")
    assert log.index("Completed") < log.index("A note")
    assert "Operator" in log
    assert "&lt;script&gt;" in log
    assert "<script>alert(1)</script>" not in log


@pytest.mark.parametrize("text", ["", "  ", "x" * 4001])
def test_execution_log_rejects_invalid_notes(execution_context, text):
    client = execution_context["client"]
    execution = execution_context["execution"]
    response = client.post(
        reverse("offerings:execution_detail", args=[execution.pk]), {"body": text}
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
    assert client.post(url, {"body": "unauthorized"}).status_code == 404
    assert not ExecutionNote.objects.exists()
    superuser = make_user()
    superuser.is_superuser = True
    superuser.save(update_fields=["is_superuser"])
    client.force_login(superuser)
    assert client.post(url, {"body": "Viewer note"}).status_code == 302
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
