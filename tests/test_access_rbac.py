"""Access/RBAC: roles, permissions, service access, seeding."""

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction

from apps.access.models import Permission, Role, RolePermission, ServiceAccess


@pytest.mark.django_db
def test_permission_code_unique():
    Permission.objects.create(code="request.view", label="A")
    with pytest.raises(IntegrityError), transaction.atomic():
        Permission.objects.create(code="request.view", label="B")


@pytest.mark.django_db
def test_role_permission_grant_is_unique(
    make_role, make_membership, grant_service, make_person, make_service, org_tree, make_user
):
    role = make_role(permissions=["request.view", "request.update"])
    assert role.permissions.count() == 2
    codes = {p.code for p in role.permissions.all()}
    assert codes == {"request.view", "request.update"}

    # Manager .add() is idempotent — re-adding does not duplicate the row.
    first = role.permissions.first()
    role.permissions.add(first, through_defaults={"active": True})
    assert role.permissions.count() == 2

    # Direct creation of the same (role, permission) pair is forbidden.
    with pytest.raises(IntegrityError), transaction.atomic():
        RolePermission.objects.create(role=role, permission=first)


@pytest.mark.django_db
def test_service_access_unique(
    make_person, org_tree, make_role, make_membership, make_service, grant_service
):
    person = make_person()
    role = make_role()
    membership = make_membership(person, org_tree["municipality"], role)
    service = make_service(org_tree["parish_a"])
    grant_service(membership, service)
    with pytest.raises(IntegrityError), transaction.atomic():
        grant_service(membership, service)


@pytest.mark.django_db
def test_seed_command_is_idempotent():
    call_command("seed_rbac")
    before = (Permission.objects.count(), Role.objects.count(), RolePermission.objects.count())
    call_command("seed_rbac")
    after = (Permission.objects.count(), Role.objects.count(), RolePermission.objects.count())
    assert before == after
    assert before[0] > 0
    # Roles from the docs are present.
    assert Role.objects.filter(code="EXECUTIVE").exists()
    assert Role.objects.filter(code="OPERATOR").exists()
    assert Permission.objects.filter(code="request.close").exists()


@pytest.mark.django_db
def test_service_access_lists_services(
    make_person, org_tree, make_role, make_membership, make_service, grant_service
):
    person = make_person()
    membership = make_membership(person, org_tree["parish_a"], make_role())
    service = make_service(org_tree["parish_a"])
    grant_service(membership, service)
    assert list(membership.service_access.all()) == [
        ServiceAccess.objects.get(membership=membership)
    ]
    # Revert:
    assert ServiceAccess.objects.filter(membership=membership).count() == 1
