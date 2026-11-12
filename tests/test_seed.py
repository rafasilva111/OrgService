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


def _main_path(offering):
    """Step names from Início to Fim following the SUCCESS transitions."""
    from apps.offerings.builder import steps_queryset
    from apps.offerings.models import StepType

    steps = list(steps_queryset(offering))
    current, names, seen = steps[0], [], set()
    while current is not None and current.pk not in seen:
        seen.add(current.pk)
        names.append(current.name)
        if current.step_type == StepType.END:
            break
        current = current.next_step
    return names


@pytest.mark.parametrize(
    "name, path",
    [
        (
            "Limpar cemitério",
            ["Início", "Planear limpeza", "Executar limpeza", "Verificar limpeza", "Fim"],
        ),
        (
            "Realizar um funeral",
            [
                "Início",
                "Validar documentação",
                "Atribuir sepultura",
                "Preparar sepultura",
                "Realizar inumação",
                "Registar inumação",
                "Fim",
            ],
        ),
        (
            "Realizar transladação",
            [
                "Início",
                "Validar requerimento",
                "Aprovar transladação",
                "Agendar exumação",
                "Realizar exumação",
                "Transportar restos mortais",
                "Registar transladação",
                "Fim",
            ],
        ),
    ],
)
def test_cemeteries_default_offerings(seeded, name, path):
    from apps.offerings.models import OfferingStatus, ServiceOffering, StepType

    offering = ServiceOffering.objects.get(service__name="Cemitérios", name=name)
    assert offering.status == OfferingStatus.ACTIVE
    assert offering.created_by.user.username == "p.lopes"
    steps = list(offering.steps.order_by("order"))
    assert steps[0].step_type == StepType.START
    assert steps[-1].step_type == StepType.END
    assert _main_path(offering) == path


def test_funeral_error_branch_notifies_the_requester(seeded):
    from apps.offerings.models import OfferingStep, StepType

    validate = OfferingStep.objects.get(offering__name="Realizar um funeral", code="validate")
    notify = validate.failure_step
    assert notify is not None
    assert notify.step_type == StepType.NOTIFY
    assert "documentação" in notify.message
    assert notify.next_step.step_type == StepType.END


def test_every_seeded_offering_is_publishable(seeded):
    from apps.offerings.builder import validate_offering_graph
    from apps.offerings.models import ServiceOffering, StepType, TransitionOutcome

    for offering in ServiceOffering.objects.all():
        validate_offering_graph(offering)  # raises if a step is unreachable/stuck
        for step in offering.steps.exclude(step_type=StepType.END):
            assert step.outgoing.filter(outcome=TransitionOutcome.SUCCESS).count() == 1, (
                offering.name,
                step.name,
            )


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


def test_cemetery_cleaning_is_on_request_and_monthly(seeded):
    from datetime import date

    from apps.offerings.models import ServiceOffering

    cleaning = ServiceOffering.objects.get(name="Limpar cemitério")
    assert cleaning.on_request and cleaning.on_schedule
    assert cleaning.recurrence_frequency == "MONTHLY" and cleaning.recurrence_month_day == 1
    assert cleaning.next_occurrences(1, after=date(2026, 11, 12)) == [date(2026, 12, 1)]


def test_funeral_total_sla_counts_the_external_step(seeded):
    from apps.offerings.models import OfferingStep, ServiceOffering

    maintenance = ServiceOffering.objects.get(name="Manutenção de espaço público")
    prepare = OfferingStep.objects.get(offering__name="Realizar um funeral", code="prepare")
    assert prepare.is_external and prepare.responsible_role is None
    assert prepare.sla_minutes == maintenance.sla_minutes == 2880  # 1d triage + 1d verify
    funeral = ServiceOffering.objects.get(name="Realizar um funeral")
    # validate 4h + assign 4h + prepare 2d + burial 0 + register 1d; notify branch excluded
    assert funeral.sla_minutes == 240 + 240 + 2880 + 0 + 1440
    assert all(o.sla_minutes is not None for o in ServiceOffering.objects.all())


def test_every_seeded_offering_has_its_sla_and_service_slas_remain(seeded):
    from apps.metrics.models import SLA
    from apps.offerings.models import ServiceOffering

    for offering in ServiceOffering.objects.all():
        sla = SLA.objects.get(offering=offering)
        assert sla.target_minutes == offering.sla_minutes and sla.active
    # The service-level SLAs on Waste Management are still seeded.
    assert SLA.objects.filter(offering__isnull=True, service__name="Waste Management").count() == 2
