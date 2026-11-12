"""The demo seed must leave every Service Manager able to work on their services.

Regression: service managers used to be seeded as ServiceActors only, with no
OrganizationMembership, so they had no permissions at all.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from apps.common.mixins import user_has_any_permission, visible_services
from apps.organizations.models import OrganizationMembership
from apps.requests.models import Request

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded(db):
    call_command("seed_rbac", verbosity=0)
    call_command("seed_default_data", verbosity=0)


def _user(username):
    return get_user_model().objects.get(username=username)


def _visible_service_names(username):
    return set(visible_services(_user(username)).values_list("name", flat=True))


def test_seed_is_idempotent(seeded):
    before = OrganizationMembership.objects.count()
    call_command("seed_default_data", verbosity=0)
    assert OrganizationMembership.objects.count() == before


def test_every_service_manager_has_a_scoped_membership(seeded):
    from apps.services.models import ServiceActor

    actors = ServiceActor.objects.filter(role__code="SERVICE_MANAGER", active=True)
    assert actors.exists()
    for actor in actors.select_related("service", "person"):
        assert OrganizationMembership.objects.filter(
            person=actor.person,
            organization=actor.service.organization,
            role__code__startswith="SERVICE_MANAGER_",
            active=True,
        ).exists(), f"{actor.person} has no membership for {actor.service.name}"


def test_p_lopes_manages_matacaes_cemeteries(seeded):
    membership = OrganizationMembership.objects.get(
        person__user__username="p.lopes", role__code="SERVICE_MANAGER_CEMETERIES"
    )
    assert membership.organization.name == "Santa Maria, São Pedro e Matacães"
    assert "Cemitérios" in _visible_service_names("p.lopes")


def test_service_manager_reach_is_limited_to_managed_services(seeded):
    user = _user("a.duarte")
    assert user_has_any_permission(user, ("request.view",))
    assert user_has_any_permission(user, ("offering.view",))

    names = _visible_service_names("a.duarte")
    assert "Waste Management" in names
    assert "Water Supply" not in names

    visible = Request.objects.visible_to(user, "request.view")
    assert visible.exists()
    assert not visible.exclude(service__name__in=names).exists()
