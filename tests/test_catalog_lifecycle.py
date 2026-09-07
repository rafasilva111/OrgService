"""Catalog lifecycle: create, activate/deactivate and soft-delete.

Covers the five catalog entities (organization, service, person, resource,
role): each create page persists the row and is gated by its ``*.create``
permission; each lifecycle action is POST-only, gated by ``*.manage`` /
``*.delete``, scoped to the caller's visibility, and ``delete`` is a soft
delete (row stays in the database but disappears from the UI).
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.access.models import Permission, Role
from apps.organizations.models import Organization, OrganizationMembership
from apps.people.models import Person
from apps.resources.models import Resource
from apps.services.models import Service

User = get_user_model()


def _grant(role, *codes):
    for pcode in codes:
        perm, _ = Permission.objects.get_or_create(
            code=pcode, defaults={"label": pcode, "module": pcode.split(".")[0]}
        )
        role.permissions.add(perm, through_defaults={"active": True})


@pytest.fixture
def make_member(db):
    def _make(user, organization, *codes, role_code="member"):
        person = Person.objects.create(user=user, full_name="Member", person_type="EMPLOYEE")
        role = Role.objects.create(code=role_code, name="Member")
        _grant(role, *codes)
        OrganizationMembership.objects.create(person=person, organization=organization, role=role)
        return person

    return _make


@pytest.fixture
def staff_user(db):
    return User.objects.create_superuser(username="root_lifecycle", password="x")


def login(client, username="root_lifecycle"):
    return client.login(username=username, password="x")


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        reverse("organizations:create"),
        reverse("services:create"),
        reverse("people:create"),
        reverse("resources:create"),
        reverse("access:role_create"),
    ],
)
def test_create_anonymous_redirected(client, url):
    resp = client.get(url)
    assert resp.status_code == 302
    assert "/login/" in resp.headers["Location"]


def test_create_organization(client, org_tree, staff_user):
    assert login(client)
    muni = org_tree["municipality"]
    resp = client.post(
        reverse("organizations:create"),
        {
            "name": "New Parish",
            "code": "npar",
            "organization_type": "PARISH",
            "parent": muni.pk,
        },
    )
    assert resp.status_code == 302
    org = Organization.objects.get(code="npar")
    assert org.name == "New Parish"
    assert org.parent_id == muni.pk
    assert org.path == f"{muni.path}.npar"


def test_create_service(client, org_tree, make_member):
    user = User.objects.create_user(username="sm_lc", password="x")
    manager = make_member(
        user,
        org_tree["municipality"],
        "service.create",
        "service.view",
        "organization.view",
    )
    assert client.login(username="sm_lc", password="x")
    resp = client.post(
        reverse("services:create"),
        {
            "organization": org_tree["municipality"].pk,
            "name": "Street Lighting",
            "status": "OPERATIONAL",
            "service_manager": manager.pk,
        },
    )
    assert resp.status_code == 302
    service = Service.objects.get(
        organization_id=org_tree["municipality"].pk, name="Street Lighting"
    )
    assert service.has_manager
    assert service.manager().person == manager


def test_create_service_requires_manager(client, org_tree, make_member):
    """The create form must reject a service without a Service Manager."""
    user = User.objects.create_user(username="no_mgr", password="x")
    make_member(
        user,
        org_tree["municipality"],
        "service.create",
        "service.view",
        "organization.view",
    )
    assert client.login(username="no_mgr", password="x")
    resp = client.post(
        reverse("services:create"),
        {
            "organization": org_tree["municipality"].pk,
            "name": "Street Lighting",
            "status": "OPERATIONAL",
        },
    )
    assert resp.status_code == 200
    assert not Service.objects.filter(
        organization_id=org_tree["municipality"].pk, name="Street Lighting"
    ).exists()


def test_create_service_requires_perm(client, org_tree, make_member):
    user = User.objects.create_user(username="no_create", password="x")
    make_member(user, org_tree["municipality"], "service.view")
    assert client.login(username="no_create", password="x")
    resp = client.post(reverse("services:create"), {})
    assert resp.status_code == 403


def test_create_person(client, staff_user):
    assert login(client)
    resp = client.post(
        reverse("people:create"),
        {"full_name": "Ada Lovelace", "email": "ada@example.com", "person_type": "EMPLOYEE"},
    )
    assert resp.status_code == 302
    assert Person.objects.filter(email="ada@example.com").exists()


def test_create_resource(client, org_tree, make_service, staff_user):
    assert login(client)
    svc = make_service(org_tree["parish_a"], "Parks")
    resp = client.post(
        reverse("resources:create"),
        {
            "resource_type": "VEHICLE",
            "name": "Maintenance Truck",
            "code": "trk-1",
            "organization": org_tree["parish_a"].pk,
            "service": svc.pk,
        },
    )
    assert resp.status_code == 302
    assert Resource.objects.filter(code="trk-1").exists()


def test_create_role_uppercases_code(client, staff_user):
    assert login(client)
    resp = client.post(
        reverse("access:role_create"),
        {"code": "dispatcher", "name": "Dispatcher", "description": ""},
    )
    assert resp.status_code == 302
    assert Role.objects.filter(code="DISPATCHER").exists()


# ---------------------------------------------------------------------------
# Activate / deactivate
# ---------------------------------------------------------------------------


def test_service_deactivate_and_activate(client, org_tree, make_service, make_member):
    svc = make_service(org_tree["municipality"], "Street Lighting")
    user = User.objects.create_user(username="svc_lc", password="x")
    make_member(user, org_tree["municipality"], "service.manage", "service.view")
    assert client.login(username="svc_lc", password="x")

    resp = client.post(reverse("services:deactivate", args=[svc.pk]))
    assert resp.status_code == 302
    svc.refresh_from_db()
    assert svc.active is False

    resp = client.post(reverse("services:activate", args=[svc.pk]))
    assert resp.status_code == 302
    svc.refresh_from_db()
    assert svc.active is True


def test_role_deactivate_and_activate(client, staff_user):
    assert login(client)
    role = Role.objects.create(code="CUSTOM", name="Custom")

    resp = client.post(reverse("access:role_deactivate", args=[role.pk]))
    assert resp.status_code == 302
    role.refresh_from_db()
    assert role.active is False

    resp = client.post(reverse("access:role_activate", args=[role.pk]))
    assert resp.status_code == 302
    role.refresh_from_db()
    assert role.active is True


def test_action_requires_manage_perm(client, org_tree, make_service, make_member):
    svc = make_service(org_tree["municipality"], "Street Lighting")
    user = User.objects.create_user(username="no_mgmt", password="x")
    make_member(user, org_tree["municipality"], "service.view")
    assert client.login(username="no_mgmt", password="x")
    resp = client.post(reverse("services:deactivate", args=[svc.pk]))
    assert resp.status_code == 403


def test_action_get_not_allowed(client, org_tree, make_service, make_member):
    svc = make_service(org_tree["municipality"], "Street Lighting")
    user = User.objects.create_user(username="get_lc", password="x")
    make_member(user, org_tree["municipality"], "service.manage", "service.view")
    assert client.login(username="get_lc", password="x")
    resp = client.get(reverse("services:deactivate", args=[svc.pk]))
    assert resp.status_code == 405


def test_action_out_of_scope_404(client, org_tree, make_service, make_member):
    svc_b = make_service(org_tree["parish_b"], "Library")
    user = User.objects.create_user(username="scoped_lc", password="x")
    make_member(user, org_tree["parish_a"], "service.manage", "service.delete", "service.view")
    assert client.login(username="scoped_lc", password="x")
    resp = client.post(reverse("services:delete", args=[svc_b.pk]))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Soft delete
# ---------------------------------------------------------------------------


def test_service_delete_is_soft_and_hidden(client, org_tree, make_service, make_member):
    svc = make_service(org_tree["municipality"], "Street Lighting")
    user = User.objects.create_user(username="del_lc", password="x")
    make_member(user, org_tree["municipality"], "service.delete", "service.view")
    assert client.login(username="del_lc", password="x")

    resp = client.post(reverse("services:delete", args=[svc.pk]))
    assert resp.status_code == 302

    svc.refresh_from_db()
    assert svc.deleted_at is not None
    assert Service.objects.filter(pk=svc.pk).exists()

    page = client.get(reverse("services:index")).content.decode()
    assert "Street Lighting" not in page


def test_organization_delete_is_soft_and_hidden(client, org_tree, make_member):
    user = User.objects.create_user(username="org_del_lc", password="x")
    make_member(user, org_tree["municipality"], "organization.delete", "organization.view")
    assert client.login(username="org_del_lc", password="x")

    resp = client.post(reverse("organizations:delete", args=[org_tree["parish_a"].pk]))
    assert resp.status_code == 302

    parish = Organization.objects.get(pk=org_tree["parish_a"].pk)
    assert parish.deleted_at is not None

    page = client.get(reverse("organizations:index")).content.decode()
    assert "Parish A" not in page


def test_person_delete_is_soft_and_hidden(client, org_tree, make_member):
    user = User.objects.create_user(username="people_del", password="x")
    make_member(user, org_tree["municipality"], "people.delete", "people.view")
    assert client.login(username="people_del", password="x")

    person = Person.objects.create(full_name="Seeded Staff", person_type="EMPLOYEE")
    OrganizationMembership.objects.create(
        person=person,
        organization=org_tree["municipality"],
        role=Role.objects.create(code="staff_lc", name="Staff"),
    )

    resp = client.post(reverse("people:delete", args=[person.pk]))
    assert resp.status_code == 302

    person.refresh_from_db()
    assert person.deleted_at is not None
    assert Person.objects.filter(pk=person.pk).exists()

    page = client.get(reverse("people:directory")).content.decode()
    assert "Seeded Staff" not in page


def test_resource_delete_is_soft(client, org_tree, make_service, staff_user):
    assert login(client)
    svc = make_service(org_tree["municipality"], "Street Lighting")
    resource = Resource.objects.create(
        resource_type="VEHICLE", name="Truck", organization=org_tree["municipality"], service=svc
    )
    resp = client.post(reverse("resources:delete", args=[resource.pk]))
    assert resp.status_code == 302

    resource.refresh_from_db()
    assert resource.deleted_at is not None
    assert Resource.objects.filter(pk=resource.pk).exists()


def test_role_delete_is_soft(client, staff_user):
    assert login(client)
    role = Role.objects.create(code="CUSTOM", name="Custom")
    resp = client.post(reverse("access:role_delete", args=[role.pk]))
    assert resp.status_code == 302

    role.refresh_from_db()
    assert role.deleted_at is not None
    assert Role.objects.filter(pk=role.pk).exists()
    assert Role.objects.alive().filter(pk=role.pk).count() == 0


def test_delete_requires_delete_perm(client, org_tree, make_service, make_member):
    svc = make_service(org_tree["municipality"], "Street Lighting")
    user = User.objects.create_user(username="mgmt_not_del", password="x")
    make_member(user, org_tree["municipality"], "service.manage", "service.view")
    assert client.login(username="mgmt_not_del", password="x")
    resp = client.post(reverse("services:delete", args=[svc.pk]))
    assert resp.status_code == 403
    svc.refresh_from_db()
    assert svc.deleted_at is None
