"""Service roles on the profile page and the header dropdown service selector.

The platform scopes roles to services: a membership either grants access to the
whole organizational subtree (no ``ServiceAccess`` rows) or only to the listed
services. These tests assert that scope is rendered both on the profile page
and in the header dropdown.
"""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_profile_shows_service_roles_for_unrestricted_membership(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    svc = make_service(org_tree["municipality"], "Street Lighting")
    from apps.organizations.models import OrganizationMembership

    user = make_user()
    role = make_role(code="analyst", permissions=["service.view", "organization.view"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("profiles:profile")).content.decode()
    assert "Service roles" in body
    assert "Street Lighting" in body


def test_profile_service_roles_respect_service_access(
    client, make_user, make_person, make_role, make_membership, grant_service, org_tree, make_service
):
    allowed = make_service(org_tree["municipality"], "Waste Collection")
    hidden = make_service(org_tree["municipality"], "Street Lighting")
    from apps.organizations.models import OrganizationMembership

    user = make_user()
    role = make_role(code="operator", permissions=["service.view"])
    membership = make_membership(make_person(user), org_tree["municipality"], role)
    grant_service(membership, allowed)
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("profiles:profile")).content.decode()
    assert "Waste Collection" in body
    assert "Street Lighting" not in body


def test_header_dropdown_lists_visible_services(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    svc = make_service(org_tree["municipality"], "Waste Management")
    from apps.organizations.models import OrganizationMembership

    user = make_user()
    role = make_role(code="viewer", permissions=["service.view"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("dashboard")).content.decode()
    dashboard_url = reverse("dashboard:service_dashboard", args=[svc.pk])
    assert "Services" in body
    assert dashboard_url in body
    assert "Waste Management" in body


def test_header_dropdown_hides_services_outside_membership_scope(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    window = make_service(org_tree["municipality"], "Citizen Window")
    user = make_user()
    role = make_role(code="viewer2", permissions=["service.view"])
    make_membership(make_person(user), org_tree["parish_a"], role)
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("dashboard")).content.decode()
    assert reverse("dashboard:service_dashboard", args=[window.pk]) not in body
    assert "Citizen Window" not in body


def test_session_service_context_selects_active_service(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    svc = make_service(org_tree["municipality"], "Street Lighting")
    user = make_user()
    role = make_role(code="analyst", permissions=["service.view"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    url = reverse("dashboard:set_service_context")
    resp = client.post(url, {"service": svc.pk, "role": "analyst"})
    assert resp.status_code == 302
    assert resp.url == "/"

    body = client.get(reverse("dashboard")).content.decode()
    assert "Atual" in body or "Current" in body
    assert "Serviço / Função" in body or "Service / Role" in body


def test_session_service_context_is_validated(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    svc = make_service(org_tree["municipality"], "Street Lighting")
    user = make_user()
    role = make_role(code="analyst", permissions=["service.view"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    # Invalid role for that service => context rejected (no crash).
    client.post(reverse("dashboard:set_service_context"), {"service": svc.pk, "role": "nope"})
    body = client.get(reverse("dashboard")).content.decode()
    assert "Street Lighting" in body
    assert "Atual" not in body and "Current" not in body


def test_membership_drives_service_selector(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    svc = make_service(org_tree["municipality"], "Street Lighting")
    user = make_user()
    role = make_role(code="executive", permissions=["service.view"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("dashboard")).content.decode()
    assert "Service / Role" in body or "Serviço / Função" in body
    assert "Street Lighting" in body


def test_organization_tab_lists_one_row_per_organization(
    client, make_user, make_person, make_role, make_membership, org_tree
):
    """A person holding several roles at the same org sees ONE selectable row
    for that organization (not one per role)."""
    user = make_user()
    exec_role = make_role(code="exec2", permissions=["organization.view"])
    ops_role = make_role(code="ops2", permissions=["organization.view"])
    person = make_person(user)
    make_membership(person, org_tree["municipality"], exec_role)
    make_membership(person, org_tree["municipality"], ops_role)
    assert client.login(username=user.username, password="x")

    url = reverse("dashboard:set_org_context")
    resp = client.post(url, {"organization": org_tree["municipality"].pk})
    assert resp.status_code == 302

    body = client.get(reverse("dashboard")).content.decode()
    assert body.count(f'value="{org_tree["municipality"].pk}"') == 1
    assert "District / Municipality" in body


def test_set_organization_context_rejects_unrelated_org(
    client, make_user, make_person, make_role, make_membership, org_tree
):
    user = make_user()
    role = make_role(code="member1", permissions=["organization.view"])
    make_membership(make_person(user), org_tree["parish_a"], role)
    assert client.login(username=user.username, password="x")

    resp = client.post(
        reverse("dashboard:set_org_context"), {"organization": org_tree["parish_b"].pk}
    )
    assert resp.status_code == 302
    assert client.session.get("org_context") is None