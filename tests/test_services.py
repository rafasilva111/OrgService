"""Services domain: service uniqueness, capabilities, actors."""

import pytest
from django.db import IntegrityError, transaction

from apps.services.models import (
    Service,
    ServiceActor,
    ServiceCapability,
    ServiceCapabilityAssignment,
)


@pytest.mark.django_db
def test_service_name_unique_within_organization(org_tree, make_service):
    make_service(org_tree["parish_a"], name="Waste")
    with pytest.raises(IntegrityError), transaction.atomic():
        Service.objects.create(organization=org_tree["parish_a"], name="Waste")


@pytest.mark.django_db
def test_same_service_name_allowed_in_different_organizations(org_tree, make_service):
    make_service(org_tree["parish_a"], name="Waste")
    make_service(org_tree["parish_b"], name="Waste")
    assert Service.objects.filter(name="Waste").count() == 2


@pytest.mark.django_db
def test_service_actor_uniqueness(org_tree, make_service, make_person, make_role):
    service = make_service(org_tree["parish_a"])
    role = make_role()
    person = make_person()
    ServiceActor.objects.create(service=service, role=role, person=person)
    with pytest.raises(IntegrityError), transaction.atomic():
        ServiceActor.objects.create(service=service, role=role, person=person)


@pytest.mark.django_db
def test_capability_assignment(org_tree, make_service):
    service = make_service(org_tree["parish_a"])
    cap = ServiceCapability.objects.create(code="water_supply", name="Water Supply")
    assignment = ServiceCapabilityAssignment.objects.create(service=service, capability=cap)
    assert assignment.required is True
    assert list(service.capability_links.all()) == [assignment]
    assert list(service.capabilities.all()) == [cap]


@pytest.mark.django_db
def test_service_str(org_tree, make_service):
    service = make_service(org_tree["parish_a"])
    assert str(service) == "dist.muni.pa / Waste Management"
