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


@pytest.mark.parametrize(
    "name, first, last",
    [
        ("Limpar cemitério", "Planear limpeza", "Verificar limpeza"),
        ("Realizar um funeral", "Validar documentação", "Registar inumação"),
        ("Realizar transladação", "Validar requerimento", "Registar transladação"),
    ],
)
def test_cemeteries_default_offerings(seeded, name, first, last):
    from apps.offerings.models import OfferingStatus, ServiceOffering, StepType

    offering = ServiceOffering.objects.get(service__name="Cemitérios", name=name)
    assert offering.status == OfferingStatus.ACTIVE
    assert offering.created_by.user.username == "p.lopes"

    steps = list(offering.steps.order_by("order"))
    assert steps[0].step_type == StepType.START
    assert steps[-1].step_type == StepType.END
    assert steps[1].name == first
    assert steps[-2].name == last
    # Linear chain: one transition from every step to the next.
    pairs = set(offering.transitions.values_list("from_step_id", "to_step_id"))
    assert pairs == {(a.pk, b.pk) for a, b in zip(steps, steps[1:], strict=False)}


def test_every_seeded_offering_is_fully_connected(seeded):
    from apps.offerings.models import ServiceOffering

    for offering in ServiceOffering.objects.all():
        steps = offering.steps.count()
        assert offering.transitions.count() == steps - 1, offering.name


def test_every_seeded_request_has_a_typed_offering_of_its_service(seeded):
    from apps.offerings.models import ServiceOffering

    assert not ServiceOffering.objects.filter(offering_type__isnull=True).exists()
    for req in Request.objects.select_related("offering"):
        assert req.offering is not None, req.title
        assert req.offering.service_id == req.service_id, req.title


def test_cemetery_steps_handed_to_other_parish_services(seeded):
    from apps.offerings.models import OfferingStep

    clean = OfferingStep.objects.get(offering__name="Limpar cemitério", code="clean")
    assert clean.service_offering.name == "Limpeza de espaço público"
    assert clean.service_offering.service.name == "Limpeza Urbana e Espaços Verdes"

    prepare = OfferingStep.objects.get(offering__name="Realizar um funeral", code="prepare")
    assert prepare.service_offering.name == "Manutenção de espaço público"
    assert prepare.service_offering.service.name == "Espaço Público, Infraestruturas e Edifícios"

    plan = OfferingStep.objects.get(offering__name="Limpar cemitério", code="plan")
    assert plan.service_offering is None
    assert plan.effective_service.name == "Cemitérios"
