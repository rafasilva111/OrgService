"""New request form: service first (pre-selected from the header), then one of
that service's offerings. The request's type comes from the offering."""

import re

import pytest
from django.urls import reverse

from apps.offerings.models import OfferingStatus, ServiceOffering, ServiceOfferingType
from apps.requests.models import Request, RequestStatus

pytestmark = pytest.mark.django_db

PERMS = ["service.view", "request.view", "request.create", "organization.view"]


@pytest.fixture
def setup(client, make_user, make_person, make_role, make_membership, org_tree, make_service):
    cleaning = ServiceOfferingType.objects.create(code="CLEANING", name="Limpeza")
    funeral = ServiceOfferingType.objects.create(code="FUNERAL", name="Serviço funerário")
    cemetery = make_service(org_tree["parish_a"], "Cemitérios")
    roads = make_service(org_tree["parish_a"], "Roads")
    clean = ServiceOffering.objects.create(
        service=cemetery,
        name="Limpar cemitério",
        status=OfferingStatus.ACTIVE,
        offering_type=cleaning,
    )
    burial = ServiceOffering.objects.create(
        service=cemetery,
        name="Realizar um funeral",
        status=OfferingStatus.ACTIVE,
        offering_type=funeral,
    )
    draft = ServiceOffering.objects.create(
        service=cemetery, name="Draft offering", status=OfferingStatus.DRAFT
    )
    potholes = ServiceOffering.objects.create(
        service=roads, name="Fix potholes", status=OfferingStatus.ACTIVE
    )

    user = make_user()
    person = make_person(user, name="Clerk")
    make_membership(person, org_tree["parish_a"], make_role(permissions=PERMS))
    assert client.login(username=user.username, password="x")
    return {
        "client": client,
        "org": org_tree["parish_a"],
        "cemetery": cemetery,
        "roads": roads,
        "clean": clean,
        "burial": burial,
        "draft": draft,
        "potholes": potholes,
        "person": person,
    }


def _set_header_service(client, org, service):
    session = client.session
    session["org_context"] = {"pk": org.pk}
    session["service_context"] = {"pk": service.pk, "role": "x"}
    session.save()


def _select(html, name):
    match = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', html, re.S)
    assert match, f"no <select name={name}>"
    return match.group(1)


def test_form_has_no_request_type_and_waits_for_a_service(setup):
    body = setup["client"].get(reverse("requests:create")).content.decode()
    assert 'name="request_type"' not in body
    offering_select = _select(body, "offering")
    assert "Select a service first" in offering_select
    assert "Limpar cemitério" not in offering_select


def test_header_service_is_preselected_and_its_offerings_loaded(setup):
    client = setup["client"]
    _set_header_service(client, setup["org"], setup["cemetery"])
    body = client.get(reverse("requests:create")).content.decode()

    service_select = _select(body, "service")
    assert f'<option value="{setup["cemetery"].pk}" selected>' in service_select

    offering_select = _select(body, "offering")
    assert "Limpar cemitério — Limpeza" in offering_select
    assert "Realizar um funeral — Serviço funerário" in offering_select
    assert "Draft offering" not in offering_select  # only ACTIVE offerings
    assert "Fix potholes" not in offering_select  # other service


def test_service_select_reloads_offerings_over_htmx(setup):
    client = setup["client"]
    body = client.get(reverse("requests:create")).content.decode()
    assert f'hx-get="{reverse("requests:offering_options")}"' in body
    assert 'hx-target="#id_offering"' in body

    fragment = client.get(
        reverse("requests:offering_options"), {"service": setup["roads"].pk}
    ).content.decode()
    assert f'<option value="{setup["potholes"].pk}">Fix potholes</option>' in fragment
    assert "Limpar cemitério" not in fragment

    empty = client.get(reverse("requests:offering_options")).content.decode()
    assert "Select a service first" in empty


def test_options_endpoint_hides_services_outside_scope(setup, org_tree, make_service):
    hidden = make_service(org_tree["parish_b"], "Hidden")
    ServiceOffering.objects.create(service=hidden, name="Secret", status=OfferingStatus.ACTIVE)
    fragment = (
        setup["client"]
        .get(reverse("requests:offering_options"), {"service": hidden.pk})
        .content.decode()
    )
    assert "Secret" not in fragment


def test_create_request_with_offering(setup):
    response = setup["client"].post(
        reverse("requests:create"),
        {
            "service": setup["cemetery"].pk,
            "offering": setup["burial"].pk,
            "title": "Funeral na sexta",
            "priority": "MEDIUM",
            "requester": setup["person"].pk,
        },
    )
    req = Request.objects.get(title="Funeral na sexta")
    assert response.status_code == 302
    assert response.url == reverse("requests:detail", args=[req.pk])
    assert req.offering == setup["burial"]
    assert req.status == RequestStatus.NEW

    detail = setup["client"].get(response.url).content.decode()
    assert "Realizar um funeral" in detail
    assert "Serviço funerário" in detail


def test_offering_is_required(setup):
    response = setup["client"].post(
        reverse("requests:create"),
        {"service": setup["cemetery"].pk, "title": "No offering", "priority": "MEDIUM"},
    )
    assert response.status_code == 200
    assert not Request.objects.filter(title="No offering").exists()


def test_offering_must_belong_to_selected_service(setup):
    response = setup["client"].post(
        reverse("requests:create"),
        {
            "service": setup["cemetery"].pk,
            "offering": setup["potholes"].pk,
            "title": "Mismatch",
            "priority": "MEDIUM",
        },
    )
    assert response.status_code == 200
    assert not Request.objects.filter(title="Mismatch").exists()


def _add_steps(offering):
    from apps.offerings.models import OfferingStep, StepType

    OfferingStep.objects.create(
        offering=offering, code="start", name="Início", step_type=StepType.START, order=10
    )
    OfferingStep.objects.create(
        offering=offering,
        code="clean",
        name="Executar limpeza",
        step_type=StepType.TASK,
        order=20,
        sla_minutes=480,
    )


def test_steps_panel_shows_hint_until_an_offering_is_selected(setup):
    body = setup["client"].get(reverse("requests:create")).content.decode()
    assert '<aside id="offering-steps"' in body
    assert "Select a service offering to see its steps." in body
    offering_select = _select(body, "offering")
    assert f'hx-get="{reverse("requests:offering_steps")}"' in body
    assert "Executar limpeza" not in offering_select


def test_selecting_an_offering_loads_its_steps(setup):
    _add_steps(setup["clean"])
    fragment = (
        setup["client"]
        .get(reverse("requests:offering_steps"), {"offering": setup["clean"].pk})
        .content.decode()
    )
    assert fragment.index("Início") < fragment.index("Executar limpeza")
    assert "SLA 8h" in fragment

    hint = setup["client"].get(reverse("requests:offering_steps"), {"offering": ""})
    assert "Select a service offering to see its steps." in hint.content.decode()


def test_steps_endpoint_hides_draft_and_out_of_scope_offerings(setup, org_tree, make_service):
    hidden = make_service(org_tree["parish_b"], "Hidden")
    secret = ServiceOffering.objects.create(
        service=hidden, name="Secret", status=OfferingStatus.ACTIVE
    )
    _add_steps(secret)
    _add_steps(setup["draft"])
    for offering in (secret, setup["draft"]):
        body = (
            setup["client"]
            .get(reverse("requests:offering_steps"), {"offering": offering.pk})
            .content.decode()
        )
        assert "Executar limpeza" not in body


def test_changing_service_clears_steps_out_of_band(setup):
    fragment = (
        setup["client"]
        .get(reverse("requests:offering_options"), {"service": setup["roads"].pk})
        .content.decode()
    )
    assert '<aside id="offering-steps"' in fragment
    assert 'hx-swap-oob="true"' in fragment
    assert "Select a service offering to see its steps." in fragment


def test_redisplayed_form_keeps_selected_offering_steps(setup):
    _add_steps(setup["clean"])
    response = setup["client"].post(
        reverse("requests:create"),
        {"service": setup["cemetery"].pk, "offering": setup["clean"].pk, "priority": "MEDIUM"},
    )
    assert response.status_code == 200  # title missing → re-displayed
    assert "Executar limpeza" in response.content.decode()


def test_steps_panel_shows_responsible_service_and_offering(setup):
    from apps.offerings.models import OfferingStep, StepType

    OfferingStep.objects.create(
        offering=setup["clean"], code="plan", name="Planear", step_type=StepType.TASK, order=10
    )
    OfferingStep.objects.create(
        offering=setup["clean"],
        code="fix",
        name="Reparar acesso",
        step_type=StepType.TASK,
        order=20,
        service_offering=setup["potholes"],
    )
    fragment = (
        setup["client"]
        .get(reverse("requests:offering_steps"), {"offering": setup["clean"].pk})
        .content.decode()
    )
    assert "Cemitérios · Limpar cemitério" in fragment
    assert "Roads · Fix potholes" in fragment
    assert "text-indigo-600" in fragment  # the handed-over step is highlighted
