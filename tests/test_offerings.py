"""Service Offerings: definition vs execution, versioning, steps/transitions."""

import pytest
from django.db import IntegrityError, transaction

from apps.offerings.models import (
    OfferingExecution,
    OfferingStep,
    OfferingTransition,
    ServiceOffering,
)


@pytest.fixture
def offering_graph(db, org_tree, make_service, make_user, make_person, make_role, make_membership):
    service = make_service(org_tree["parish_a"])
    role = make_role()
    person = make_person()
    author = make_person(name="Author")
    wf = ServiceOffering.objects.create(
        service=service, name="process_sync", status="ACTIVE", created_by=author
    )
    steps = [
        OfferingStep.objects.create(
            offering=wf, code="s1", name="Collect", order=1, step_type="START"
        ),
        OfferingStep.objects.create(
            offering=wf, code="s2", name="Approve", order=2, step_type="APPROVAL"
        ),
        OfferingStep.objects.create(
            offering=wf, code="s3", name="Execute", order=3, step_type="TASK"
        ),
    ]
    OfferingTransition.objects.create(offering=wf, from_step=steps[0], to_step=steps[1], order=1)
    OfferingTransition.objects.create(offering=wf, from_step=steps[1], to_step=steps[2], order=1)
    return {
        "service": service,
        "wf": wf,
        "steps": steps,
        "role": role,
        "person": person,
        "author": author,
    }


@pytest.mark.django_db
def test_steps_ordered_and_transitions_oriented(offering_graph):
    g = offering_graph
    steps = g["steps"]
    wf = g["wf"]
    assert wf.steps.count() == 3
    assert list(wf.steps.order_by("order").values_list("order", flat=True)) == [1, 2, 3]
    assert steps[0].outgoing.count() == 1
    assert steps[2].outgoing.count() == 0
    assert steps[1].incoming.count() == 1
    assert wf.transitions.count() == 2


@pytest.mark.django_db
def test_offering_versioning_unique(offering_graph):
    g = offering_graph
    wf = g["wf"]
    assert wf.version == 1
    ServiceOffering.objects.create(
        service=g["service"], name="process_sync", version=2, status="ACTIVE"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        ServiceOffering.objects.create(service=g["service"], name="process_sync", version=2)
    # current_version resolves the highest non-draft row.
    assert ServiceOffering.current_version(g["service"], "process_sync").version == 2


@pytest.mark.django_db
def test_transition_unique_order(offering_graph):
    g = offering_graph
    t = g["wf"].transitions.first()
    with pytest.raises(IntegrityError), transaction.atomic():
        OfferingTransition.objects.create(
            offering=g["wf"], from_step=t.from_step, to_step=t.to_step, order=t.order
        )


@pytest.mark.django_db
def test_execution_defaults(offering_graph):
    g = offering_graph
    ex = OfferingExecution.objects.create(offering=g["wf"])
    assert ex.status == "PENDING"
    assert ex.current_step is None
    assert ex.offering_id == g["wf"].pk
    assert OfferingExecution.objects.filter(offering=g["wf"]).count() == 1


@pytest.mark.django_db
def test_execution_generic_subject(offering_graph):
    g = offering_graph
    ex = OfferingExecution.objects.create(offering=g["wf"])
    ex.subject = g["person"]
    ex.save()
    assert ex.subject == g["person"]


# ---------------------------------------------------------------------------
# Step responsibility: service_offering (blank = the step's own offering)
# ---------------------------------------------------------------------------


def test_step_defaults_to_its_own_offering(offering_graph):
    step = offering_graph["steps"][2]
    assert step.service_offering is None
    assert step.effective_offering == offering_graph["wf"]
    assert step.effective_service == offering_graph["service"]
    assert not step.is_delegated


def test_step_handed_to_another_services_offering(offering_graph, make_service, org_tree):
    other = make_service(org_tree["parish_a"], "Limpeza Urbana")
    cleaning = ServiceOffering.objects.create(service=other, name="Limpeza", status="ACTIVE")
    step = offering_graph["steps"][2]
    step.service_offering = cleaning
    step.save()
    step.refresh_from_db()
    assert step.service_offering.service == other
    assert step.effective_offering == cleaning
    assert step.effective_service == other
    assert step.is_delegated


def test_pointing_at_own_offering_is_not_a_delegation(offering_graph):
    step = offering_graph["steps"][2]
    step.service_offering = offering_graph["wf"]
    assert not step.is_delegated


def test_responsible_offering_in_use_cannot_be_deleted(offering_graph, make_service, org_tree):
    from django.db.models import ProtectedError

    other = make_service(org_tree["parish_a"], "Other")
    target = ServiceOffering.objects.create(service=other, name="Target", status="ACTIVE")
    step = offering_graph["steps"][2]
    step.service_offering = target
    step.save()
    with pytest.raises(ProtectedError):
        target.delete()
