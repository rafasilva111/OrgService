"""Workflows: definition vs execution, versioning, steps/transitions."""

import pytest
from django.db import IntegrityError, transaction

from apps.workflows.models import (
    Workflow,
    WorkflowExecution,
    WorkflowStep,
    WorkflowTransition,
)


@pytest.fixture
def workflow_graph(db, org_tree, make_service, make_user, make_person, make_role, make_membership):
    service = make_service(org_tree["parish_a"])
    role = make_role()
    person = make_person()
    author = make_person(name="Author")
    wf = Workflow.objects.create(
        service=service, name="process_sync", status="ACTIVE", created_by=author
    )
    steps = [
        WorkflowStep.objects.create(
            workflow=wf, code="s1", name="Collect", order=1, step_type="START"
        ),
        WorkflowStep.objects.create(
            workflow=wf, code="s2", name="Approve", order=2, step_type="APPROVAL"
        ),
        WorkflowStep.objects.create(
            workflow=wf, code="s3", name="Execute", order=3, step_type="TASK"
        ),
    ]
    WorkflowTransition.objects.create(workflow=wf, from_step=steps[0], to_step=steps[1], order=1)
    WorkflowTransition.objects.create(workflow=wf, from_step=steps[1], to_step=steps[2], order=1)
    return {
        "service": service,
        "wf": wf,
        "steps": steps,
        "role": role,
        "person": person,
        "author": author,
    }


@pytest.mark.django_db
def test_steps_ordered_and_transitions_oriented(workflow_graph):
    g = workflow_graph
    steps = g["steps"]
    wf = g["wf"]
    assert wf.steps.count() == 3
    assert list(wf.steps.order_by("order").values_list("order", flat=True)) == [1, 2, 3]
    assert steps[0].outgoing.count() == 1
    assert steps[2].outgoing.count() == 0
    assert steps[1].incoming.count() == 1
    assert wf.transitions.count() == 2


@pytest.mark.django_db
def test_workflow_versioning_unique(workflow_graph):
    g = workflow_graph
    wf = g["wf"]
    assert wf.version == 1
    Workflow.objects.create(service=g["service"], name="process_sync", version=2, status="ACTIVE")
    with pytest.raises(IntegrityError), transaction.atomic():
        Workflow.objects.create(service=g["service"], name="process_sync", version=2)
    # current_version resolves the highest non-draft row.
    assert Workflow.current_version(g["service"], "process_sync").version == 2


@pytest.mark.django_db
def test_transition_unique_order(workflow_graph):
    g = workflow_graph
    t = g["wf"].transitions.first()
    with pytest.raises(IntegrityError), transaction.atomic():
        WorkflowTransition.objects.create(
            workflow=g["wf"], from_step=t.from_step, to_step=t.to_step, order=t.order
        )


@pytest.mark.django_db
def test_execution_defaults(workflow_graph):
    g = workflow_graph
    ex = WorkflowExecution.objects.create(workflow=g["wf"])
    assert ex.status == "PENDING"
    assert ex.current_step is None
    assert ex.workflow_id == g["wf"].pk
    assert WorkflowExecution.objects.filter(workflow=g["wf"]).count() == 1


@pytest.mark.django_db
def test_execution_generic_subject(workflow_graph):
    g = workflow_graph
    ex = WorkflowExecution.objects.create(workflow=g["wf"])
    ex.subject = g["person"]
    ex.save()
    assert ex.subject == g["person"]
