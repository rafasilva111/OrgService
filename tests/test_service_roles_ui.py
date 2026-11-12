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
    make_service(org_tree["municipality"], "Street Lighting")

    user = make_user()
    role = make_role(code="analyst", permissions=["service.view", "organization.view"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("profiles:profile")).content.decode()
    assert "Service roles" in body
    assert "Street Lighting" in body


def test_profile_service_roles_respect_service_access(
    client,
    make_user,
    make_person,
    make_role,
    make_membership,
    grant_service,
    org_tree,
    make_service,
):
    allowed = make_service(org_tree["municipality"], "Waste Collection")
    make_service(org_tree["municipality"], "Street Lighting")

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

    user = make_user()
    role = make_role(code="viewer", permissions=["organization.view", "service.view"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    client.post(reverse("dashboard:set_org_context"), {"organization": org_tree["municipality"].pk})
    body = client.get(
        reverse("dashboard:organization_dashboard", args=[org_tree["municipality"].pk])
    ).content.decode()
    dashboard_url = reverse("dashboard:service_dashboard", args=[svc.pk])
    assert "Services" in body
    assert dashboard_url in body
    assert "Waste Management" in body


def test_header_dropdown_hides_services_outside_membership_scope(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    inside = make_service(org_tree["parish_a"], "Citizen Window")
    outside = make_service(org_tree["municipality"], "Payroll")
    user = make_user()
    role = make_role(code="viewer2", permissions=["service.view"])
    make_membership(make_person(user), org_tree["parish_a"], role)
    assert client.login(username=user.username, password="x")

    client.post(reverse("dashboard:set_org_context"), {"organization": org_tree["parish_a"].pk})
    body = client.get(reverse("dashboard:service_dashboard", args=[inside.pk])).content.decode()
    assert reverse("dashboard:service_dashboard", args=[outside.pk]) not in body
    assert "Payroll" not in body


def test_organization_dashboard_keeps_header_role_name(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    """Selecting an organization must not blank the header service/role dropdown.

    The organization dashboard exposes its own service list under the same
    ``services`` name; renaming it to ``org_services`` keeps the context
    processor's service+role options (with ``role_name``) intact.
    """
    svc = make_service(org_tree["municipality"], "Waste Management")

    user = make_user()
    role = make_role(code="manager", permissions=["organization.view", "service.view"])
    role.name = "Service Manager"
    role.save(update_fields=["name"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    client.post(reverse("dashboard:set_service_context"), {"service": svc.pk, "role": "manager"})
    client.post(reverse("dashboard:set_org_context"), {"organization": org_tree["municipality"].pk})

    body = client.get(
        reverse("dashboard:organization_dashboard", args=[org_tree["municipality"].pk])
    ).content.decode()
    assert "Waste Management" in body
    assert "Service Manager" in body


def test_session_service_context_selects_active_service(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    svc = make_service(org_tree["municipality"], "Street Lighting")
    user = make_user()
    role = make_role(code="analyst", permissions=["service.view"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    client.post(reverse("dashboard:set_org_context"), {"organization": org_tree["municipality"].pk})
    url = reverse("dashboard:set_service_context")
    resp = client.post(url, {"service": svc.pk, "role": "analyst"})
    assert resp.status_code == 302
    assert resp.url == "/"
    assert client.session["service_context"]["pk"] == svc.pk

    resp = client.get(reverse("dashboard"))
    assert resp.status_code == 302
    assert resp.url == reverse("dashboard:service_dashboard", args=[svc.pk])


def test_session_service_context_is_validated(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    svc = make_service(org_tree["municipality"], "Street Lighting")
    user = make_user()
    role = make_role(code="analyst", permissions=["service.view"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    client.post(reverse("dashboard:set_org_context"), {"organization": org_tree["municipality"].pk})

    # Invalid role for that service => context rejected (no crash).
    client.post(reverse("dashboard:set_service_context"), {"service": svc.pk, "role": "nope"})
    assert client.session.get("service_context") is None


def test_membership_drives_service_selector(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    svc = make_service(org_tree["municipality"], "Street Lighting")

    user = make_user()
    role = make_role(code="executive", permissions=["service.view"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("dashboard")).content.decode()
    assert "Service / Role" not in body and "Serviço / Função" not in body

    client.post(reverse("dashboard:set_org_context"), {"organization": org_tree["municipality"].pk})
    body = client.get(reverse("dashboard:service_dashboard", args=[svc.pk])).content.decode()
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

    body = client.get(reverse("dashboard"), follow=True).content.decode()
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


def test_no_default_organization_until_explicit_pick(
    client, make_user, make_person, make_role, make_membership, org_tree
):
    """Without an explicit choice the overview is global (no org highlighted)."""
    user = make_user()
    role = make_role(code="orgview1", permissions=["organization.view"])
    person = make_person(user)
    make_membership(person, org_tree["municipality"], role)
    make_membership(person, org_tree["parish_a"], role)
    assert client.login(username=user.username, password="x")

    response = client.get(reverse("dashboard"))
    assert response.status_code == 200
    assert response.context["active_organization"] is None


def test_explicit_org_selection_routes_overview(
    client, make_user, make_person, make_role, make_membership, org_tree
):
    user = make_user()
    role = make_role(code="orgview2", permissions=["organization.view"])
    person = make_person(user)
    make_membership(person, org_tree["municipality"], role)
    make_membership(person, org_tree["parish_a"], role)
    assert client.login(username=user.username, password="x")

    client.post(reverse("dashboard:set_org_context"), {"organization": org_tree["parish_a"].pk})
    assert client.session["org_context"]["pk"] == org_tree["parish_a"].pk

    response = client.get(reverse("dashboard"))
    assert response.status_code == 302
    assert response.url == reverse(
        "dashboard:organization_dashboard", args=[org_tree["parish_a"].pk]
    )


def test_stale_org_context_is_cleared_without_default(
    client, make_user, make_person, make_role, make_membership, org_tree
):
    """A stale session pick is cleared; there is no implicit default org."""
    user = make_user()
    role = make_role(code="orgview3", permissions=["organization.view"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    session = client.session
    session["org_context"] = {"pk": 999999}
    session.save()

    response = client.get(reverse("dashboard"))
    assert response.status_code == 200
    assert response.context["active_organization"] is None
    assert client.session.get("org_context") is None


def test_no_memberships_means_no_active_organization(client, make_user, make_person):
    user = make_user()
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])
    make_person(user)
    assert client.login(username=user.username, password="x")

    response = client.get(reverse("dashboard"))
    assert response.status_code == 200
    assert response.context["active_organization"] is None


def test_service_selection_requires_an_active_organization(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    svc = make_service(org_tree["municipality"], "Scoped Service")
    user = make_user()
    role = make_role(code="needorg", permissions=["service.view"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    resp = client.post(
        reverse("dashboard:set_service_context"), {"service": svc.pk, "role": "needorg"}
    )
    assert resp.status_code == 302
    assert client.session.get("service_context") is None


def test_reselecting_active_service_clears_it(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    svc = make_service(org_tree["municipality"], "Toggle Service")
    user = make_user()
    role = make_role(code="toggle", permissions=["service.view"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    client.post(reverse("dashboard:set_org_context"), {"organization": org_tree["municipality"].pk})
    url = reverse("dashboard:set_service_context")
    client.post(url, {"service": svc.pk, "role": "toggle"})
    assert client.session["service_context"]["pk"] == svc.pk

    resp = client.post(url, {"service": svc.pk, "role": "toggle"})
    assert resp.status_code == 302
    assert resp.url == "/"
    assert client.session.get("service_context") is None


def test_selecting_organization_clears_service_and_reselecting_org_clears_both(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    svc = make_service(org_tree["municipality"], "Reset Service")
    user = make_user()
    role = make_role(code="reset", permissions=["service.view"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    client.post(reverse("dashboard:set_org_context"), {"organization": org_tree["municipality"].pk})
    client.post(reverse("dashboard:set_service_context"), {"service": svc.pk, "role": "reset"})
    assert client.session["service_context"]["pk"] == svc.pk

    # Re-selecting the same organization toggles it off and clears the service.
    resp = client.post(
        reverse("dashboard:set_org_context"), {"organization": org_tree["municipality"].pk}
    )
    assert resp.status_code == 302
    assert resp.url == "/"
    assert client.session.get("org_context") is None
    assert client.session.get("service_context") is None


def test_sidebar_scope_card_shows_global_without_selection(
    client, make_user, make_person, make_role, make_membership, org_tree
):
    user = make_user()
    role = make_role(code="globalscope", permissions=["organization.view"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("dashboard")).content.decode()
    assert 'href="/" aria-current="true"' in body
    assert "All organizations" in body or "Todas as organizações" in body


def test_sidebar_scope_card_shows_selected_organization(
    client, make_user, make_person, make_role, make_membership, org_tree
):
    user = make_user()
    role = make_role(code="orgscope", permissions=["organization.view"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    client.post(reverse("dashboard:set_org_context"), {"organization": org_tree["municipality"].pk})
    response = client.get(
        reverse("dashboard:organization_dashboard", args=[org_tree["municipality"].pk])
    )
    body = response.content.decode()
    assert (
        f'href="{reverse("dashboard:organization_dashboard", args=[org_tree["municipality"].pk])}"'
        ' aria-current="true"' in body
    )
    assert response.context["active_organization"]["parent_path"] == "District"
    assert "District" in body


def test_sidebar_scope_card_prefers_selected_service(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    svc = make_service(org_tree["municipality"], "Scoped Service")
    user = make_user()
    role = make_role(code="svcscope", permissions=["organization.view", "service.view"])
    make_membership(make_person(user), org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    client.post(reverse("dashboard:set_org_context"), {"organization": org_tree["municipality"].pk})
    client.post(reverse("dashboard:set_service_context"), {"service": svc.pk, "role": "svcscope"})
    body = client.get(reverse("dashboard:service_dashboard", args=[svc.pk])).content.decode()
    assert f'href="/service/{svc.pk}/" aria-current="true"' in body
    assert (
        f'href="{reverse("dashboard:organization_dashboard", args=[org_tree["municipality"].pk])}"'
        ' aria-current="true"' not in body
    )
