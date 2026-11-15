import re
from datetime import UTC, datetime, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.offerings.models import (
    ExecutionNote,
    ExecutionStatus,
    OfferingExecution,
    OfferingStep,
    OfferingTask,
    OfferingTransition,
    PauseReason,
    ServiceOffering,
    StepType,
    TaskActivity,
    TaskStatus,
)

pytestmark = pytest.mark.django_db


def visible_text(html):
    """Page text as a sighted user reads it (no tags or screen-reader-only labels)."""
    html = re.sub(r'<span class="sr-only">.*?</span>', "", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace(" :", ":")


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
    assert 'class="space-y-6 xl:w-[calc((100%-1.5rem)/2)]"' in body
    assert body.index(">Validate</p>") < body.index(">Approve</p>")
    assert "Check documents" in body
    assert "Operator" in body
    assert "2h 0m 0s" in body
    assert "Started:" in body
    assert "Completed:" in body
    assert "No task recorded for this step." in body
    assert "Task history records the people who performed each action." in body
    assert "Current step" in body


def test_execution_final_deadline_requires_slas_on_normal_manual_path(execution_context):
    execution = execution_context["execution"]
    offering = execution_context["offering"]
    validate = offering.steps.get(code="validate")
    approve = offering.steps.get(code="approve")
    validate.sla_minutes = 60
    approve.sla_minutes = 0
    validate.save(update_fields=["sla_minutes"])
    approve.save(update_fields=["sla_minutes"])
    start = OfferingStep.objects.create(
        offering=offering, code="start", name="Start", step_type=StepType.START, order=1
    )
    end = OfferingStep.objects.create(
        offering=offering, code="end", name="End", step_type=StepType.END, order=99
    )
    for source, target in ((start, validate), (validate, approve), (approve, end)):
        OfferingTransition.objects.create(offering=offering, from_step=source, to_step=target)
    client = execution_context["client"]
    url = reverse("offerings:execution_detail", args=[execution.pk])
    response = client.get(url)
    assert response.context["final_deadline"] == execution.started_at + timedelta(hours=1)
    assert "Final deadline" in response.content.decode()
    timing_row = (
        response.content.decode()
        .split('class="flex flex-wrap gap-x-8 gap-y-1 sm:col-span-2"', 1)[1]
        .split("</div>", 1)[0]
    )
    assert "Started" in timing_row and "Final deadline" in timing_row
    assert "Prazo final" in client.get(url, HTTP_ACCEPT_LANGUAGE="pt").content.decode()
    execution.status = ExecutionStatus.WAITING
    execution.save(update_fields=["status"])
    assert client.get(url).context["final_deadline"] == execution.started_at + timedelta(hours=1)
    approve.sla_minutes = None
    approve.save(update_fields=["sla_minutes"])
    response = client.get(url)
    assert response.context["final_deadline"] is None
    assert "Final deadline" not in response.content.decode()
    approve.sla_minutes = 0
    approve.save(update_fields=["sla_minutes"])
    OfferingTransition.objects.filter(from_step=approve).delete()
    assert client.get(url).context["final_deadline"] is None
    execution.started_at = None
    execution.save(update_fields=["started_at"])
    assert client.get(url).context["final_deadline"] is None


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


def test_execution_list_shows_ten_per_page(execution_context):
    offering = execution_context["offering"]
    started = execution_context["execution"].started_at
    for index in range(20):
        OfferingExecution.objects.create(
            offering=offering, started_at=started + timedelta(minutes=index + 1)
        )
    client = execution_context["client"]
    url = reverse("offerings:executions")
    pages = [client.get(url, {"page": number}) for number in (1, 2, 3)]
    assert [len(response.context["object_list"]) for response in pages] == [10, 10, 1]
    assert all(response.context["paginator"].count == 21 for response in pages)
    assert len({execution.pk for page in pages for execution in page.context["object_list"]}) == 21


def test_offerings_list_average_elapsed_time_by_version_and_permissions(
    execution_context, make_user, make_person, make_role, make_membership, org_tree
):
    offering = execution_context["offering"]
    other_version = ServiceOffering.objects.create(
        service=offering.service, name=offering.name, version=2
    )
    now = timezone.now()
    for hours in (2, 6):
        run = OfferingExecution.objects.create(
            offering=offering, status=ExecutionStatus.COMPLETED, completed_at=now
        )
        OfferingExecution.objects.filter(pk=run.pk).update(created_at=now - timedelta(hours=hours))
    OfferingExecution.objects.create(
        offering=offering, status=ExecutionStatus.COMPLETED, completed_at=now - timedelta(days=1)
    )
    OfferingExecution.objects.create(
        offering=other_version,
        status=ExecutionStatus.COMPLETED,
        completed_at=now + timedelta(hours=10),
    )
    client = execution_context["client"]
    url = reverse("offerings:index")
    response = client.get(url, {"sort": "-average_elapsed_duration"})
    assert response.status_code == 200
    rows = {row.pk: row for row in response.context["object_list"]}
    assert rows[offering.pk].average_elapsed_duration == timedelta(hours=4)
    assert rows[other_version.pk].average_elapsed_duration > timedelta(hours=9)
    body = response.content.decode()
    assert "Average elapsed time" in body
    assert "4h 0m 0s" in body
    assert "sort=average_elapsed_duration" in body

    viewer = make_user()
    make_membership(
        make_person(viewer), org_tree["parish_a"], make_role(permissions=["offering.view"])
    )
    client.force_login(viewer)
    response = client.get(url)
    rows = {row.pk: row for row in response.context["object_list"]}
    assert rows[offering.pk].average_elapsed_duration is None


def test_offering_detail_places_other_sections_in_left_column(execution_context):
    body = (
        execution_context["client"]
        .get(reverse("offerings:detail", args=[execution_context["offering"].pk]))
        .content.decode()
    )
    assert '<main class="px-4 pb-16 pt-6 sm:px-6 lg:px-8">' in body
    assert '<div class="space-y-6">' in body
    columns = body.split('id="offering-detail-columns"', 1)[1]
    left, right = columns.split('id="eligible-actors-panel"', 1)
    assert "xl:grid-cols-2" in left
    assert "Steps" in left
    assert "Eligible people" in right
    assert "xl:col-start-1 xl:row-start-2" in right
    assert "Request form" in right
    assert "Recent executions" in right


def test_offering_step_pause_reasons_include_ongoing_and_invalid_history(
    execution_context, monkeypatch, make_user, make_person, make_membership, make_role, org_tree
):
    from django.utils import timezone

    from apps.offerings import pause_statistics

    now = timezone.now()
    monkeypatch.setattr(pause_statistics.timezone, "now", lambda: now)
    offering = execution_context["offering"]
    execution = execution_context["execution"]
    step = offering.steps.get(code="validate")
    client = execution_context["client"]

    def task_with_events(events, status=TaskStatus.DONE, step_override=None, run=None):
        task = OfferingTask.objects.create(
            execution=run or execution,
            step=step_override or step,
            status=status,
            started_at=now - timedelta(hours=5),
            completed_at=now if status == TaskStatus.DONE else None,
        )
        for action, detail, offset in events:
            activity = task.activities.create(action=action, detail=detail)
            TaskActivity.objects.filter(pk=activity.pk).update(
                created_at=now - timedelta(hours=offset)
            )
        return task

    repeated = task_with_events(
        [
            ("paused", PauseReason.VENDOR, 4),
            ("resumed", "", 3),
            ("paused", PauseReason.VENDOR, 2),
            ("resumed", "", 1),
            ("paused", PauseReason.REQUESTER, 1),
            ("resumed", "", 0),
        ]
    )
    ongoing_task = task_with_events([("paused", PauseReason.VENDOR, 2)], status=TaskStatus.PAUSED)
    task_with_events([("paused", PauseReason.APPROVAL, 3)])
    task_with_events([("paused", "unknown", 2), ("resumed", "", 1)])
    other_step = offering.steps.get(code="approve")
    task_with_events([("paused", PauseReason.VENDOR, 2)], step_override=other_step)
    other_version = ServiceOffering.objects.create(
        service=offering.service, name=offering.name, version=2
    )
    other_run = OfferingExecution.objects.create(offering=other_version)
    other_version_step = OfferingStep.objects.create(
        offering=other_version, code=step.code, name=step.name, order=step.order
    )
    task_with_events(
        [("paused", PauseReason.VENDOR, 2)], step_override=other_version_step, run=other_run
    )

    url = reverse("offerings:detail", args=[offering.pk])
    response = client.get(url, {"step": step.pk})
    rows = response.context["pause_context"]["object_list"]
    assert [(str(row.reason), row.count, row.total_duration) for row in rows] == [
        ("Waiting for vendor", 3, timedelta(hours=4)),
        ("Other or unspecified", 1, timedelta(hours=1)),
        ("Waiting for approval", 1, None),
        ("Waiting for requester", 1, timedelta(hours=1)),
    ]
    assert [row.ongoing_count for row in rows] == [1, 0, 0, 0]
    # Pause links open the normal task list ("All tasks" tab) filtered by step
    # and pause reason: one row per task, so the repeated task appears once.
    assert "scope=all" in rows[0].history_url and "pause_reason=VENDOR" in rows[0].history_url
    all_pauses = client.get(rows[0].history_url)
    assert all_pauses.status_code == 200
    assert set(all_pauses.context["object_list"]) == {repeated, ongoing_task}
    drawer = {item["param"]: item["value"] for item in all_pauses.context["table_filters"]}
    assert drawer["pause_reason"] == PauseReason.VENDOR
    assert drawer["step"] == str(step.pk)
    assert reverse("offerings:execution_detail", args=[execution.pk]) in all_pauses.content.decode()
    ongoing_response = client.get(rows[0].ongoing_url)
    assert list(ongoing_response.context["object_list"]) == [ongoing_task]
    assert len(client.get(rows[1].history_url).context["object_list"]) == 1
    body = response.content.decode()
    assert body.index("eligible_actors-wrap") < body.index("pause_reasons-wrap")
    assert "4h 0m 0s" in body
    assert "The average above uses completed tasks only." in body

    partial = client.get(
        url, {"step": step.pk}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="eligible-actors-panel"
    )
    assert "eligible_actors-wrap" in partial.content.decode()
    assert "pause_reasons-wrap" in partial.content.decode()
    paginated = client.get(
        url,
        {"step": step.pk, "page": 2},
        HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="eligible_actors-wrap",
    )
    assert "eligible_actors-wrap" in paginated.content.decode()
    assert "pause_reasons-wrap" not in paginated.content.decode()
    translated = client.get(url, {"step": step.pk}, HTTP_ACCEPT_LANGUAGE="pt").content.decode()
    assert "Causas de pausa" in translated
    assert "Tempo total em pausa" in translated
    assert "Pausas em curso" in translated
    assert "A média acima usa apenas tarefas concluídas." in translated

    viewer = make_user()
    make_membership(
        make_person(viewer), org_tree["parish_a"], make_role(permissions=["offering.view"])
    )
    client.force_login(viewer)
    assert client.get(url, {"step": step.pk}).status_code == 404
    assert client.get(url, {"step": step.pk}, HTTP_HX_REQUEST="true").status_code == 404
    assert not list(client.get(rows[0].history_url).context["object_list"])


def test_step_history_pagination_and_invalid_filters(execution_context):
    from django.utils import timezone

    client = execution_context["client"]
    execution = execution_context["execution"]
    step = execution_context["offering"].steps.get(code="validate")
    person = execution_context["person"]
    now = timezone.now()
    for index in range(12):
        task = OfferingTask.objects.create(
            execution=execution,
            step=step,
            title=f"Historical task {index}",
            assigned_to=person,
            status=TaskStatus.DONE,
            started_at=now - timedelta(hours=2),
            completed_at=now - timedelta(hours=1),
        )
        paused = task.activities.create(action="paused", detail=PauseReason.VENDOR)
        resumed = task.activities.create(action="resumed")
        TaskActivity.objects.filter(pk=paused.pk).update(created_at=now - timedelta(minutes=100))
        TaskActivity.objects.filter(pk=resumed.pk).update(created_at=now - timedelta(minutes=80))
    url = reverse("offerings:tasks")
    task_query = {
        "scope": "all",
        "activity": "all",
        "step": step.pk,
        "assignee": person.pk,
        "status": TaskStatus.DONE,
    }
    page1 = client.get(url, task_query)
    page2 = client.get(url, {**task_query, "page": 2})
    assert [len(page.context["object_list"]) for page in (page1, page2)] == [10, 3]
    assert len({task.pk for page in (page1, page2) for task in page.context["object_list"]}) == 13
    pause_query = {
        "scope": "all",
        "activity": "all",
        "step": step.pk,
        "pause_reason": PauseReason.VENDOR,
    }
    page1 = client.get(url, pause_query)
    page2 = client.get(url, {**pause_query, "page": 2}, HTTP_HX_REQUEST="true")
    assert len(page1.context["object_list"]) == 10
    assert len(page2.context["object_list"]) == 2
    assert f"step={step.pk}" in page1.content.decode()
    assert "pause_reason=VENDOR" in page1.content.decode()
    assert "<html" not in page2.content.decode()
    assert client.get(url, {**task_query, "assignee": "-1"}).status_code == 404
    assert client.get(url, {**task_query, "step": "x"}).status_code == 404
    assert client.get(url, {**pause_query, "pause_reason": "invalid"}).status_code == 404
    assert client.get(url, {**task_query, "activity": "invalid"}).status_code == 404


def test_offering_detail_expands_step_with_sla_and_averages(execution_context):
    offering = execution_context["offering"]
    step = offering.steps.get(code="validate")
    step.sla_minutes = 60
    step.save(update_fields=["sla_minutes"])
    response = execution_context["client"].get(reverse("offerings:detail", args=[offering.pk]))
    row = response.content.decode().split(">Validate</p>", 1)[1].split("</li>", 1)[0]
    summary, details = row.split("</summary>", 1)
    # Averages above the 1h SLA are shown in red with a danger symbol.
    assert "Average active time: 2h 0m 0s" in visible_text(summary)
    assert summary.count('data-sla-level="danger"') == 1
    assert "SLA" not in visible_text(summary)
    assert '>SLA</dt><dd class="font-medium text-zinc-800">1h</dd>' in details
    assert "Average active time 2h 0m 0s" in visible_text(details)
    assert "Average elapsed time 2h 0m 0s" in visible_text(details)
    assert details.count('data-sla-level="danger"') == 2
    assert "font-semibold text-red-600" in details
    assert '>Average paused time</dt><dd class="font-medium text-zinc-800">0s' in details
    assert "Based on 1 reliable completed task in this offering version." in details
    empty_row = response.content.decode().split(">Approve</p>", 1)[1].split("</li>", 1)[0]
    assert '>SLA</dt><dd class="font-medium text-zinc-800">—' in empty_row
    assert "Based on 0 reliable completed tasks" in empty_row


def test_offering_step_details_handle_zero_sla_and_automatic_steps(execution_context):
    offering = execution_context["offering"]
    step = offering.steps.get(code="validate")
    step.sla_minutes = 0
    step.save(update_fields=["sla_minutes"])
    for code, step_type, order in (("start", StepType.START, 1), ("end", StepType.END, 30)):
        OfferingStep.objects.create(
            offering=offering,
            code=code,
            name=code.capitalize(),
            step_type=step_type,
            order=order,
            sla_minutes=120,
        )
    client = execution_context["client"]
    body = client.get(reverse("offerings:detail", args=[offering.pk])).content.decode()
    row = body.split(">Validate</p>", 1)[1].split("</li>", 1)[0]
    assert "Step details" in row
    assert "Show eligible people" in row
    assert '>SLA</dt><dd class="font-medium text-zinc-800">0min</dd>' in row
    for name in ("Start", "End"):
        automatic = body.split(f">{name}</p>", 1)[0].rsplit("<li>", 1)[1]
        automatic += body.split(f">{name}</p>", 1)[1].split("</li>", 1)[0]
        assert "SLA" not in automatic
        assert "<details" not in automatic
        assert "Step details" not in automatic
        assert "Show eligible people" not in automatic
        assert "hx-get" not in automatic
    execution_body = client.get(
        reverse("offerings:execution_detail", args=[execution_context["execution"].pk])
    ).content.decode()
    assert "Step details" not in execution_body
    end = execution_body.split(">End</p>", 1)[1].split("</li>", 1)[0]
    assert "Average elapsed time: —" in end
    assert "Total SLA: —" in end


def test_offering_step_details_preserve_aggregate_permissions(
    execution_context, make_user, make_person, make_role, make_membership, org_tree
):
    viewer = make_user()
    make_membership(
        make_person(viewer), org_tree["parish_a"], make_role(permissions=["offering.view"])
    )
    client = execution_context["client"]
    client.force_login(viewer)
    response = client.get(reverse("offerings:detail", args=[execution_context["offering"].pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Step details" in body
    assert 'class="space-y-6 xl:w-[calc((100%-1.5rem)/2)]"' in body
    assert 'id="eligible-actors-panel"' not in body
    assert "Aggregate timings require access to this service and its offerings." in body
    assert "Average elapsed time" not in body
    assert "Average paused time" not in body
    assert "2h 0m 0s" not in body
    step = execution_context["offering"].steps.get(code="validate")
    assert (
        client.get(
            reverse("offerings:detail", args=[execution_context["offering"].pk]),
            {"step": step.pk},
            HTTP_HX_REQUEST="true",
        ).status_code
        == 404
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


@pytest.mark.parametrize(("sla", "display"), [(1501, "1d 1h 1min"), (0, "0min"), (None, "—")])
def test_execution_step_maximum_time_follows_average(execution_context, sla, display):
    execution = execution_context["execution"]
    step = execution.offering.steps.get(code="validate")
    step.sla_minutes = sla
    step.save(update_fields=["sla_minutes"])
    url = reverse("offerings:execution_detail", args=[execution.pk])
    body = execution_context["client"].get(url).content.decode()
    step_html = body.split(">Validate</p>", 1)[1].split("</li>", 1)[0]
    step_text = visible_text(step_html)
    assert step_text.index("Average active time: 2h 0m 0s") < step_text.index(
        f"Maximum time: {display}"
    )
    pt_body = execution_context["client"].get(url, HTTP_ACCEPT_LANGUAGE="pt").content.decode()
    assert f"Tempo máximo: {display}" in pt_body


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
        execution=execution,
        step=step,
        title="Another visit",
        status=TaskStatus.DONE,
        started_at=start,
        completed_at=start + timedelta(hours=1),
    )
    invalid = OfferingTask.objects.create(
        execution=execution,
        step=step,
        title="Incomplete pause",
        status=TaskStatus.DONE,
        started_at=start,
        completed_at=start + timedelta(hours=20),
    )
    event = TaskActivity.objects.create(task=invalid, action="paused")
    TaskActivity.objects.filter(pk=event.pk).update(created_at=start + timedelta(hours=1))
    OfferingTask.objects.create(
        execution=execution,
        step=step,
        title="No timestamp",
        status=TaskStatus.DONE,
    )
    OfferingTask.objects.create(
        execution=execution,
        step=step,
        title="In progress",
        status=TaskStatus.IN_PROGRESS,
        started_at=start,
        completed_at=start + timedelta(hours=30),
    )
    other_version = ServiceOffering.objects.create(
        service=execution.offering.service, name=execution.offering.name, version=2
    )
    other_step = OfferingStep.objects.create(
        offering=other_version, code=step.code, name=step.name, step_type=step.step_type
    )
    other_execution = OfferingExecution.objects.create(offering=other_version)
    OfferingTask.objects.create(
        execution=other_execution,
        step=other_step,
        title="Other version",
        status=TaskStatus.DONE,
        started_at=start,
        completed_at=start + timedelta(hours=100),
    )

    client = execution_context["client"]
    for url in (
        reverse("offerings:execution_detail", args=[execution.pk]),
        reverse("offerings:detail", args=[execution.offering_id]),
    ):
        body = client.get(url).content.decode()
        assert "Average active time: 2h 0m 0s" in visible_text(body)
        assert "No reliable completed tasks" in body
        assert "excludes recorded pauses" in body
    response = client.get(reverse("offerings:detail", args=[execution.offering_id]))
    work_step = next(s for s in response.context["steps"] if s.pk == step.pk)
    assert work_step.average_duration == timedelta(hours=2)
    assert work_step.average_elapsed_duration == timedelta(hours=3)
    assert work_step.average_pause_duration == timedelta(hours=1)
    assert work_step.average_sample_count == 2
    details = response.content.decode().split(">Validate</p>", 1)[1].split("</li>", 1)[0]
    assert "Average elapsed time 3h 0m 0s" in visible_text(details)
    assert '>Average paused time</dt><dd class="font-medium text-zinc-800">1h 0m 0s' in details
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
    assert "Tempo médio ativo: 0s" in visible_text(body)
    assert "Sem tarefas concluídas com tempos fiáveis" in body
    with translation.override("pt"):
        offering_body = (
            execution_context["client"]
            .get(
                reverse("offerings:detail", args=[execution.offering_id]),
                HTTP_ACCEPT_LANGUAGE="pt",
            )
            .content.decode()
        )
    assert "Detalhes do passo" in offering_body
    assert "Tempo médio decorrido" in offering_body
    assert "Tempo médio em pausa" in offering_body
    assert "1 tarefa concluída com tempos fiáveis" in offering_body


def test_execution_log_combines_existing_updates_and_viewer_notes(execution_context):
    client = execution_context["client"]
    execution = execution_context["execution"]
    task = execution.tasks.get()
    TaskActivity.objects.create(task=task, actor=execution_context["person"], action="completed")
    url = reverse("offerings:execution_detail", args=[execution.pk])
    assert (
        client.post(
            url,
            {"kind": "WORK_NOTE", "body": "  A note <script>alert(1)</script>  "},
        ).status_code
        == 302
    )
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


def test_execution_log_displays_person_name_and_account_fallback(execution_context, make_user):
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


def test_sla_level_filter():
    from apps.common.templatetags.ui import sla_level

    assert sla_level(timedelta(minutes=61), 60) == "danger"
    assert sla_level(timedelta(minutes=60), 60) == "warning"
    assert sla_level(timedelta(minutes=48), 60) == "warning"
    assert sla_level(timedelta(minutes=47), 60) == ""
    assert sla_level(timedelta(days=3), None) == ""
    assert sla_level(None, 60) == ""
    assert sla_level(timedelta(seconds=1), 0) == "danger"
    assert sla_level(timedelta(0), 0) == ""
