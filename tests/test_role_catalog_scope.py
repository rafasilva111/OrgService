"""Role catalogue follows the selected service or organization."""

import pytest
from django.urls import reverse

from apps.services.models import ServiceActor

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalogue(client, make_user, make_person, make_role, make_membership, make_service, org_tree):
    user = make_user()
    person = make_person(user)
    viewer = make_role(code="viewer", permissions=["organization.view", "service.view"])
    make_membership(person, org_tree["municipality"], viewer)
    first = make_service(org_tree["parish_a"], "Cemetery")
    second = make_service(org_tree["parish_b"], "Cleaning")
    outside = make_service(org_tree["district"], "Outside")
    cemetery_role = make_role(code="cemetery_role")
    cleaning_role = make_role(code="cleaning_role")
    outside_role = make_role(code="outside_role")
    membership_role = make_role(code="organization_role")
    inactive_role = make_role(code="inactive_role")
    inactive_role.active = False
    inactive_role.save(update_fields=["active"])
    ServiceActor.objects.create(service=first, role=cemetery_role)
    ServiceActor.objects.create(service=first, role=cemetery_role, person=person)
    ServiceActor.objects.create(service=second, role=cleaning_role)
    ServiceActor.objects.create(service=outside, role=outside_role)
    ServiceActor.objects.create(service=first, role=inactive_role)
    ServiceActor.objects.create(service=first, role=outside_role, active=False)
    make_membership(make_person(), org_tree["parish_a"], membership_role)
    client.force_login(user)
    return locals()


def role_codes(response):
    return {role.code for role in response.context["object_list"]}


def select_org(client, org):
    client.post(reverse("dashboard:set_org_context"), {"organization": org.pk})


def select_service(client, service, role):
    client.post(reverse("dashboard:set_service_context"), {"service": service.pk, "role": role.code})


def test_global_catalogue_includes_unassociated_and_inactive_roles(client, catalogue, make_role):
    unassociated = make_role(code="unassociated")
    response = client.get(reverse("access:roles"))
    assert response.context["role_scope"] == "global"
    assert {unassociated.code, "outside_role", "inactive_role"} <= role_codes(response)


def test_organization_catalogue_includes_visible_subtree_roles(client, catalogue):
    select_org(client, catalogue["org_tree"]["municipality"])
    response = client.get(reverse("access:roles"))
    assert response.context["role_scope"] == "organization"
    assert response.context["role_scope_name"] == "Municipality"
    assert role_codes(response) == {"viewer", "cemetery_role", "cleaning_role", "organization_role"}


def test_service_catalogue_tracks_selection_and_active_actors(client, catalogue):
    select_org(client, catalogue["org_tree"]["municipality"])
    select_service(client, catalogue["first"], catalogue["viewer"])
    response = client.get(reverse("access:roles"))
    assert response.context["role_scope"] == "service"
    assert response.context["role_scope_name"] == "Cemetery"
    assert list(response.context["object_list"].values_list("code", flat=True)) == ["cemetery_role"]

    select_service(client, catalogue["second"], catalogue["viewer"])
    response = client.get(reverse("access:roles"), {"service": ""})
    assert response.context["role_scope_name"] == "Cleaning"
    assert role_codes(response) == {"cleaning_role"}

    select_service(client, catalogue["second"], catalogue["viewer"])
    response = client.get(reverse("access:roles"))
    assert response.context["role_scope"] == "organization"


def test_service_catalogue_uses_distinct_roles_and_permission_counts(client, catalogue):
    role = catalogue["cemetery_role"]
    role.permissions.add(*catalogue["viewer"].permissions.all(), through_defaults={"active": True})
    select_org(client, catalogue["org_tree"]["municipality"])
    select_service(client, catalogue["first"], catalogue["viewer"])
    response = client.get(reverse("access:roles"), {"partial": "table"})
    assert response.context["object_list"].count() == 1
    assert response.context["object_list"][0].permission_count == 2


def test_service_with_no_active_actors_has_empty_catalogue(client, catalogue):
    select_org(client, catalogue["org_tree"]["municipality"])
    select_service(client, catalogue["second"], catalogue["viewer"])
    ServiceActor.objects.filter(service=catalogue["second"]).update(active=False)
    response = client.get(reverse("access:roles"))
    assert response.context["role_scope"] == "service"
    assert not role_codes(response)


def test_invalid_service_context_falls_back_to_organization(client, catalogue):
    select_org(client, catalogue["org_tree"]["municipality"])
    session = client.session
    session["service_context"] = {"pk": catalogue["outside"].pk, "role": "viewer"}
    session.save()
    response = client.get(reverse("access:roles"))
    assert response.context["role_scope"] == "organization"
    assert "outside_role" not in role_codes(response)


def test_organization_catalogue_respects_service_access(client, catalogue, grant_service):
    restricted_user = catalogue["user"]
    membership = restricted_user.person.memberships.get(role=catalogue["viewer"])
    grant_service(membership, catalogue["first"])
    select_org(client, catalogue["org_tree"]["municipality"])
    response = client.get(reverse("access:roles"))
    assert "cemetery_role" in role_codes(response)
    assert "cleaning_role" not in role_codes(response)
