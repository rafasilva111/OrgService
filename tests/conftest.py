"""Shared fixtures: builders for the domain graph (org tree, people, RBAC)."""

import uuid

import pytest
from django.contrib.auth import get_user_model

from apps.access.models import Permission, Role
from apps.organizations.models import Organization, OrganizationMembership
from apps.people.models import Person


def _uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:6]}"


@pytest.fixture
def org_tree(db):
    """District → Municipality → {Parish A, Parish B}."""
    district = Organization.objects.create(
        name="District", code="dist", organization_type="DISTRICT"
    )
    municipality = Organization.objects.create(
        name="Municipality",
        code="muni",
        organization_type="MUNICIPALITY",
        parent=district,
    )
    parish_a = Organization.objects.create(
        name="Parish A", code="pa", organization_type="PARISH", parent=municipality
    )
    parish_b = Organization.objects.create(
        name="Parish B", code="pb", organization_type="PARISH", parent=municipality
    )
    return {
        "district": district,
        "municipality": municipality,
        "parish_a": parish_a,
        "parish_b": parish_b,
    }


@pytest.fixture
def make_user(db):
    def _make(username="op"):
        return get_user_model().objects.create_user(username=_uid(username), password="x")

    return _make


@pytest.fixture
def make_person(db):
    def _make(user=None, name="Person", person_type="EMPLOYEE"):
        return Person.objects.create(user=user, full_name=name, person_type=person_type)

    return _make


@pytest.fixture
def make_role(db):
    """Create a role and grant it the given permission codes."""

    def _make(code=None, permissions=()):
        role = Role.objects.create(code=code or _uid("role"), name="Role")
        for pcode in permissions:
            perm, _ = Permission.objects.get_or_create(
                code=pcode, defaults={"label": pcode, "module": pcode.split(".")[0]}
            )
            role.permissions.add(perm, through_defaults={"active": True})
        return role

    return _make


@pytest.fixture
def make_membership(db):
    def _make(person, organization, role):
        return OrganizationMembership.objects.create(
            person=person, organization=organization, role=role
        )

    return _make


@pytest.fixture
def make_service(db):
    def _make(organization, name="Waste Management"):
        from apps.services.models import Service

        return Service.objects.create(organization=organization, name=name)

    return _make


@pytest.fixture
def grant_service(db):
    def _make(membership, service):
        from apps.access.models import ServiceAccess

        return ServiceAccess.objects.create(membership=membership, service=service)

    return _make
