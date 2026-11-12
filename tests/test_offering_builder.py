"""Service offering builder: plan parsing/validation, draft → publish lifecycle,
new versions, branches (on error) and permissions."""

import json

import pytest
from django.urls import reverse

from apps.offerings.builder import parse_definition, save_definition, validate_graph
from apps.offerings.models import (
    OfferingStatus,
    OfferingTransition,
    ServiceOffering,
    ServiceOfferingType,
    StepType,
    TransitionOutcome,
)

pytestmark = pytest.mark.django_db

MANAGER_PERMS = [
    "service.view",
    "offering.view",
    "offering.manage",
    "request.create",
    "organization.view",
]


def _parse(definition, roles=(), offerings=()):
    return parse_definition(json.dumps(definition), roles=roles, offerings=offerings)


# ----------------------------------------------------------------- parsing
def test_next_defaults_to_the_following_step_and_last_to_end():
    result = _parse([{"key": "a", "name": "A"}, {"key": "b", "name": "B"}])
    assert result.ok
    assert [(s.key, s.next, s.on_error) for s in result.steps] == [
        ("a", "b", ""),
        ("b", "end", ""),
    ]


def test_parse_reports_problems_per_step():
    result = _parse(
        [
            {"key": "a", "name": ""},
            {"key": "b", "name": "B", "next": "missing"},
            {"key": "c", "name": "C", "on_error": "c"},
            {"key": "d", "name": "D", "step_type": "NOTIFY"},
            {"key": "e", "name": "E", "sla": "-5"},
            {"key": "f", "name": "F", "service_offering": 999},
        ]
    )
    assert not result.ok
    assert set(result.step_errors) == {0, 1, 2, 3, 4, 5}
    assert result.steps[1].next == "c"  # invalid ref dropped → default following
    assert result.steps[2].on_error == ""


def test_parse_rejects_garbage():
    result = parse_definition("not json", roles=[], offerings=[])
    assert not result.ok and result.errors


def test_validate_graph_rules():
    ok = _parse(
        [
            {"key": "work", "name": "Work"},
            {"key": "check", "name": "Check", "next": "end", "on_error": "work"},  # rework loop
        ]
    ).steps
    assert validate_graph(ok) == []

    unreachable = _parse(
        [{"key": "a", "name": "A", "next": "end"}, {"key": "b", "name": "Orphan"}]
    ).steps
    assert any("Orphan" in e for e in validate_graph(unreachable))

    stuck = _parse(
        [{"key": "a", "name": "A", "next": "b"}, {"key": "b", "name": "B", "next": "a"}]
    ).steps
    assert any("never lead to the end" in e for e in validate_graph(stuck))

    assert validate_graph([])


# --------------------------------------------------------------- fixtures
@pytest.fixture
def ctx(client, make_user, make_person, make_role, make_membership, org_tree, make_service):
    otype = ServiceOfferingType.objects.create(code="FUNERAL", name="Serviço funerário")
    cemetery = make_service(org_tree["parish_a"], "Cemitérios")
    cleaning_service = make_service(org_tree["parish_a"], "Limpeza Urbana")
    cleaning = ServiceOffering.objects.create(
        service=cleaning_service,
        name="Limpeza de espaço público",
        status=OfferingStatus.ACTIVE,
        sla_minutes=480,  # 8h total
        offering_type=ServiceOfferingType.objects.create(code="CLEANING", name="Limpeza"),
    )
    outside = make_service(org_tree["parish_b"], "Outside")
    role = make_role(permissions=MANAGER_PERMS)
    user = make_user()
    make_membership(make_person(user, name="Manager"), org_tree["parish_a"], role)
    assert client.login(username=user.username, password="x")
    return {
        "client": client,
        "type": otype,
        "cemetery": cemetery,
        "cleaning": cleaning,
        "outside": outside,
        "role": role,
    }


def _plan(ctx):
    return [
        {"key": "validate", "name": "Validar documentação", "on_error": "notify"},
        {
            "key": "burial",
            "name": "Realizar inumação",
            "role": ctx["role"].pk,
            "sla": 60,
            "service_offering": ctx["cleaning"].pk,
            "next": "end",
        },
        {
            "key": "notify",
            "name": "Notificar requerente",
            "step_type": "NOTIFY",
            "message": "Falta documentação.",
            "next": "end",
        },
    ]


def _post(ctx, url, plan, action="save", **fields):
    data = {
        "service": ctx["cemetery"].pk,
        "name": "Realizar um funeral",
        "offering_type": ctx["type"].pk,
        "on_request": "on",
        "description": "",
        "definition": json.dumps(plan),
        "action": action,
    }
    data.update(fields)
    return ctx["client"].post(url, data)


# ------------------------------------------------------------------ views
def test_list_shows_new_button_to_managers(ctx):
    body = ctx["client"].get(reverse("offerings:index")).content.decode()
    assert reverse("offerings:create") in body


def test_create_draft_builds_steps_and_branches(ctx):
    response = _post(ctx, reverse("offerings:create"), _plan(ctx))
    offering = ServiceOffering.objects.get(name="Realizar um funeral")
    assert response.status_code == 302
    assert response.url == reverse("offerings:edit", args=[offering.pk])
    assert offering.status == OfferingStatus.DRAFT and offering.version == 1
    assert offering.offering_type == ctx["type"]

    steps = list(offering.steps.order_by("order"))
    assert [s.step_type for s in steps] == [
        StepType.START,
        StepType.TASK,
        StepType.TASK,
        StepType.NOTIFY,
        StepType.END,
    ]
    start, validate, burial, notify, end = steps
    # Handed to another service: SLA/type/role come from the responsible offering.
    assert burial.service_offering == ctx["cleaning"]
    assert burial.sla_minutes == 480 and burial.responsible_role is None
    assert burial.step_type == StepType.TASK
    # Total = normal path (validate + burial); the error branch (notify) is excluded.
    assert offering.sla_minutes == 480
    assert notify.message == "Falta documentação."
    edges = {
        (t.from_step.code, t.to_step.code, t.outcome)
        for t in OfferingTransition.objects.filter(offering=offering)
    }
    assert edges == {
        ("start", "validate", TransitionOutcome.SUCCESS),
        ("validate", "burial", TransitionOutcome.SUCCESS),
        ("validate", "notify", TransitionOutcome.FAILURE),
        ("burial", "end", TransitionOutcome.SUCCESS),
        ("notify", "end", TransitionOutcome.SUCCESS),
    }


def test_editor_reopens_draft_with_its_plan(ctx):
    _post(ctx, reverse("offerings:create"), _plan(ctx))
    offering = ServiceOffering.objects.get(name="Realizar um funeral")
    body = ctx["client"].get(reverse("offerings:edit", args=[offering.pk])).content.decode()
    definition = json.loads(
        body.split('id="editor-definition" type="application/json">')[1].split("</script>")[0]
    )
    assert [d["key"] for d in definition] == ["validate", "burial", "notify"]
    assert definition[0]["on_error"] == "notify"
    assert definition[0]["next"] == ""  # follows the list order
    assert definition[1]["next"] == "end"


def test_invalid_plan_is_redisplayed_with_step_errors(ctx):
    response = _post(ctx, reverse("offerings:create"), [{"key": "x", "name": ""}])
    assert response.status_code == 200
    assert not ServiceOffering.objects.filter(name="Realizar um funeral").exists()
    assert "Give the step a name." in response.content.decode()


def test_publish_from_editor_and_published_is_read_only(ctx):
    response = _post(ctx, reverse("offerings:create"), _plan(ctx), action="publish")
    offering = ServiceOffering.objects.get(name="Realizar um funeral")
    assert response.url == reverse("offerings:detail", args=[offering.pk])
    assert offering.status == OfferingStatus.ACTIVE
    assert ctx["client"].get(reverse("offerings:edit", args=[offering.pk])).status_code == 404


def test_publish_refuses_a_broken_plan(ctx):
    plan = [{"key": "a", "name": "A", "next": "end"}, {"key": "b", "name": "Orphan"}]
    response = _post(ctx, reverse("offerings:create"), plan, action="publish")
    offering = ServiceOffering.objects.get(name="Realizar um funeral")
    assert offering.status == OfferingStatus.DRAFT  # saved, not published
    assert response.url == reverse("offerings:edit", args=[offering.pk])


def test_new_version_copies_plan_and_publishing_deprecates_previous(ctx):
    _post(ctx, reverse("offerings:create"), _plan(ctx), action="publish")
    v1 = ServiceOffering.objects.get(name="Realizar um funeral")
    response = ctx["client"].post(reverse("offerings:new_version", args=[v1.pk]))
    v2 = ServiceOffering.objects.get(name="Realizar um funeral", version=2)
    assert response.url == reverse("offerings:edit", args=[v2.pk])
    assert v2.status == OfferingStatus.DRAFT
    assert v2.steps.count() == v1.steps.count()
    assert v2.transitions.filter(outcome=TransitionOutcome.FAILURE).count() == 1

    ctx["client"].post(reverse("offerings:publish", args=[v2.pk]))
    v1.refresh_from_db()
    v2.refresh_from_db()
    assert (v1.status, v2.status) == (OfferingStatus.DEPRECATED, OfferingStatus.ACTIVE)


def test_duplicate_name_points_to_new_version(ctx):
    _post(ctx, reverse("offerings:create"), _plan(ctx), action="publish")
    response = _post(ctx, reverse("offerings:create"), _plan(ctx))
    assert response.status_code == 200
    assert "new version" in response.content.decode()
    assert ServiceOffering.objects.filter(name="Realizar um funeral").count() == 1


def test_delete_draft(ctx):
    _post(ctx, reverse("offerings:create"), _plan(ctx))
    offering = ServiceOffering.objects.get(name="Realizar um funeral")
    ctx["client"].post(reverse("offerings:delete", args=[offering.pk]))
    assert not ServiceOffering.objects.filter(pk=offering.pk).exists()


def test_preview_renders_the_steps_card(ctx):
    response = ctx["client"].post(
        reverse("offerings:preview"),
        {"service": ctx["cemetery"].pk, "name": "X", "definition": json.dumps(_plan(ctx))},
    )
    body = response.content.decode()
    assert "Validar documentação" in body
    assert "On error" in body and "Notificar requerente" in body
    assert "Limpeza Urbana · Limpeza de espaço público" in body
    assert "Falta documentação." in body


def test_cannot_build_for_services_outside_scope(ctx):
    response = _post(ctx, reverse("offerings:create"), _plan(ctx), service=ctx["outside"].pk)
    assert response.status_code == 200  # invalid choice
    assert not ServiceOffering.objects.filter(name="Realizar um funeral").exists()


def test_users_without_offering_manage_are_refused(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    service = make_service(org_tree["parish_a"], "S")
    draft = ServiceOffering.objects.create(service=service, name="D")
    user = make_user()
    make_membership(
        make_person(user), org_tree["parish_a"], make_role(permissions=["offering.view"])
    )
    client.login(username=user.username, password="x")
    assert client.get(reverse("offerings:create")).status_code == 403
    assert client.get(reverse("offerings:edit", args=[draft.pk])).status_code == 404
    assert client.post(reverse("offerings:publish", args=[draft.pk])).status_code in (403, 404)
    body = client.get(reverse("offerings:index")).content.decode()
    assert reverse("offerings:create") not in body


def test_drafts_are_not_offered_on_new_requests(ctx):
    _post(ctx, reverse("offerings:create"), _plan(ctx))
    fragment = (
        ctx["client"]
        .get(reverse("requests:offering_options"), {"service": ctx["cemetery"].pk})
        .content.decode()
    )
    assert "Realizar um funeral" not in fragment


def test_save_definition_refuses_published_offerings(ctx):
    from django.core.exceptions import ValidationError

    published = ServiceOffering.objects.create(
        service=ctx["cemetery"], name="P", status=OfferingStatus.ACTIVE
    )
    with pytest.raises(ValidationError):
        save_definition(published, [])


# ------------------------------------------------- location cascade options
def test_location_options_build_district_municipality_parish_chain(ctx, org_tree):
    from apps.offerings.forms import location_options
    from apps.organizations.models import OrganizationType

    org_tree["district"].organization_type = OrganizationType.DISTRICT
    org_tree["district"].save()
    municipal = ServiceOffering.objects.create(
        service=ServiceOffering.objects.get(pk=ctx["cleaning"].pk).service,
        name="Parish level",
        status=OfferingStatus.ACTIVE,
    )
    organizations, offerings, _chains = location_options(
        ServiceOffering.objects.filter(pk__in=[ctx["cleaning"].pk, municipal.pk]).select_related(
            "service__organization"
        )
    )
    by_id = {o["id"]: o for o in organizations}
    district, muni, parish = org_tree["district"], org_tree["municipality"], org_tree["parish_a"]
    assert by_id[district.pk]["type"] == "DISTRICT" and by_id[district.pk]["ancestors"] == []
    assert by_id[muni.pk]["ancestors"] == [district.pk]
    assert by_id[parish.pk]["ancestors"] == [district.pk, muni.pk]
    assert org_tree["parish_b"].pk not in by_id  # no offerings there → pruned
    rows = {row[0]: row for row in offerings}
    assert rows[ctx["cleaning"].pk][2] == [district.pk, muni.pk, parish.pk]
    assert rows[ctx["cleaning"].pk][1] == "Limpeza Urbana · Limpeza de espaço público"


def test_editor_receives_location_options(ctx):
    body = ctx["client"].get(reverse("offerings:create")).content.decode()
    options = json.loads(
        body.split('id="editor-options" type="application/json">')[1].split("</script>")[0]
    )
    assert {o["type"] for o in options["organizations"]} >= {"MUNICIPALITY", "PARISH"}
    assert any(row[0] == ctx["cleaning"].pk and len(row[2]) >= 2 for row in options["offerings"])
    assert 'x-model="loc(step).district"' in body


def test_editor_can_prefill_cascade_from_the_offering_service(ctx, org_tree):
    body = ctx["client"].get(reverse("offerings:create")).content.decode()
    options = json.loads(
        body.split('id="editor-options" type="application/json">')[1].split("</script>")[0]
    )
    chain = options["serviceChains"][str(ctx["cemetery"].pk)]
    assert chain == [org_tree["district"].pk, org_tree["municipality"].pk, org_tree["parish_a"].pk]
    assert str(ctx["outside"].pk) not in options["serviceChains"]  # not manageable
    assert "followService()" in body


# ------------------------------------------- how it starts: request / schedule
def _schedule(**fields):
    from datetime import date

    from apps.offerings.models import RecurrenceFrequency

    defaults = {
        "on_request": True,
        "on_schedule": True,
        "recurrence_frequency": RecurrenceFrequency.MONTHLY,
        "recurrence_interval": 1,
        "recurrence_month_day": 1,
        "recurrence_start": date(2026, 12, 1),
    }
    defaults.update(fields)
    return ServiceOffering(name="X", **defaults)


def test_recurrence_summary_and_next_runs():
    from datetime import date

    from django.utils import translation

    monthly = _schedule()
    with translation.override("en"):
        assert monthly.availability_summary().startswith("On request · Every month on day 1")
    assert monthly.next_occurrences(3, after=date(2026, 11, 12)) == [
        date(2026, 12, 1),
        date(2027, 1, 1),
        date(2027, 2, 1),
    ]
    with translation.override("pt"):
        assert "Todos os meses no dia 1" in monthly.recurrence_summary()

    weekly = _schedule(
        on_request=False,
        recurrence_frequency="WEEKLY",
        recurrence_interval=2,
        recurrence_weekdays=[0, 3],  # Monday, Thursday
        recurrence_month_day=None,
        recurrence_start=date(2026, 11, 2),  # a Monday
    )
    assert weekly.next_occurrences(4, after=date(2026, 11, 1)) == [
        date(2026, 11, 2),
        date(2026, 11, 5),
        date(2026, 11, 16),
        date(2026, 11, 19),
    ]
    with translation.override("en"):
        assert weekly.availability_summary().startswith("Every 2 weeks on Monday, Thursday")

    ends = _schedule(recurrence_end=date(2027, 1, 15))
    assert ends.next_occurrences(5, after=date(2026, 11, 1)) == [
        date(2026, 12, 1),
        date(2027, 1, 1),
    ]

    month_end = _schedule(recurrence_month_day=31, recurrence_start=date(2027, 1, 1))
    assert month_end.next_occurrences(2, after=date(2027, 1, 1)) == [
        date(2027, 1, 31),
        date(2027, 2, 28),
    ]


def test_offering_needs_at_least_one_way_to_start(ctx):
    response = _post(ctx, reverse("offerings:create"), _plan(ctx), on_request="")
    assert response.status_code == 200
    assert "Choose at least one" in response.content.decode()
    assert not ServiceOffering.objects.filter(name="Realizar um funeral").exists()


def test_recurring_offering_needs_a_complete_schedule(ctx):
    response = _post(
        ctx,
        reverse("offerings:create"),
        _plan(ctx),
        on_schedule="on",
        recurrence_frequency="WEEKLY",
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert "Required for recurring offerings." in body  # start date
    assert "Pick at least one day of the week." in body


def test_create_recurring_and_on_request_offering(ctx):
    response = _post(
        ctx,
        reverse("offerings:create"),
        _plan(ctx),
        action="publish",
        on_schedule="on",
        recurrence_frequency="MONTHLY",
        recurrence_interval="1",
        recurrence_month_day="1",
        recurrence_start="2026-12-01",
        recurrence_weekdays=["0"],  # ignored for monthly
    )
    offering = ServiceOffering.objects.get(name="Realizar um funeral")
    assert response.status_code == 302
    assert offering.on_request and offering.on_schedule
    assert offering.recurrence_month_day == 1 and offering.recurrence_weekdays == []
    detail = ctx["client"].get(reverse("offerings:detail", args=[offering.pk])).content.decode()
    assert "Every month on day 1" in detail
    assert "Next runs" in detail

    v2 = offering.new_version()
    assert (v2.on_request, v2.on_schedule, v2.recurrence_month_day) == (True, True, 1)


def test_request_only_fields_are_cleared(ctx):
    _post(
        ctx,
        reverse("offerings:create"),
        _plan(ctx),
        recurrence_frequency="DAILY",
        recurrence_start="2026-12-01",
    )
    offering = ServiceOffering.objects.get(name="Realizar um funeral")
    assert not offering.on_schedule
    assert offering.recurrence_frequency == "" and offering.recurrence_start is None


def test_db_refuses_offering_without_any_way_to_start(ctx):
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        ServiceOffering.objects.create(
            service=ctx["cemetery"], name="Nothing", on_request=False, on_schedule=False
        )


def test_preview_shows_how_it_starts(ctx):
    body = (
        ctx["client"]
        .post(
            reverse("offerings:preview"),
            {
                "service": ctx["cemetery"].pk,
                "name": "X",
                "definition": json.dumps(_plan(ctx)),
                "on_schedule": "on",
                "recurrence_frequency": "DAILY",
                "recurrence_interval": "3",
                "recurrence_start": "2026-12-01",
            },
        )
        .content.decode()
    )
    assert "Every 3 days" in body
    assert "On request" not in body
    assert "Validar documentação" in body


def test_schedule_only_offerings_are_not_offered_on_new_requests(ctx):
    from datetime import date

    ServiceOffering.objects.create(
        service=ctx["cemetery"],
        name="Só agendada",
        status=OfferingStatus.ACTIVE,
        on_request=False,
        on_schedule=True,
        recurrence_frequency="DAILY",
        recurrence_start=date(2026, 12, 1),
    )
    fragment = (
        ctx["client"]
        .get(reverse("requests:offering_options"), {"service": ctx["cemetery"].pk})
        .content.decode()
    )
    assert "Só agendada" not in fragment


def test_interval_reads_as_a_sentence_with_units(ctx):
    from django.utils import translation

    body = ctx["client"].get(reverse("offerings:create")).content.decode()
    units = json.loads(
        body.split('id="editor-units" type="application/json">')[1].split("</script>")[0]
    )
    assert units["MONTHLY"] == ["month", "months"]
    assert set(units) == {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}
    schedule = json.loads(
        body.split('id="editor-schedule" type="application/json">')[1].split("</script>")[0]
    )
    assert schedule["interval"] == 1
    assert 'x-model.number="interval"' in body and 'x-text="unit()"' in body

    with translation.override("pt"):
        ctx["client"].cookies["django_language"] = "pt"
        pt = ctx["client"].get(reverse("offerings:create")).content.decode()
    pt_units = json.loads(
        pt.split('id="editor-units" type="application/json">')[1].split("</script>")[0]
    )
    assert pt_units["MONTHLY"] == ["mês", "meses"]


def test_preview_updates_the_summary_under_the_schedule(ctx):
    body = (
        ctx["client"]
        .post(
            reverse("offerings:preview"),
            {
                "service": ctx["cemetery"].pk,
                "name": "X",
                "definition": json.dumps(_plan(ctx)),
                "on_request": "on",
                "on_schedule": "on",
                "recurrence_frequency": "MONTHLY",
                "recurrence_interval": "3",
                "recurrence_month_day": "1",
                "recurrence_start": "2026-12-01",
            },
        )
        .content.decode()
    )
    assert '<p id="schedule-summary" hx-swap-oob="true"' in body
    assert "→ Every 3 months on day 1" in body


def test_type_can_be_left_to_be_defined(ctx):
    body = ctx["client"].get(reverse("offerings:create")).content.decode()
    assert '<option value="" selected>To be defined</option>' in body

    response = _post(ctx, reverse("offerings:create"), _plan(ctx), offering_type="")
    offering = ServiceOffering.objects.get(name="Realizar um funeral")
    assert response.status_code == 302
    assert offering.offering_type is None
    detail = ctx["client"].get(reverse("offerings:detail", args=[offering.pk])).content.decode()
    assert "To be defined" in detail


def test_sla_accepts_typed_durations(ctx):
    plan = _plan(ctx)
    plan[0]["sla"] = "1d 4h"
    plan[2]["sla"] = "1d 16h 13m"
    _post(ctx, reverse("offerings:create"), plan)
    offering = ServiceOffering.objects.get(name="Realizar um funeral")
    slas = dict(offering.steps.values_list("code", "sla_minutes"))
    assert slas["validate"] == 1680 and slas["notify"] == 2413

    body = ctx["client"].get(reverse("offerings:edit", args=[offering.pk])).content.decode()
    definition = json.loads(
        body.split('id="editor-definition" type="application/json">')[1].split("</script>")[0]
    )
    assert definition[0]["sla"].replace("min", "m") == "1d 4h"
    assert definition[2]["sla"].replace("min", "m") == "1d 16h 13m"

    bad = _plan(ctx)
    bad[0]["sla"] = "amanhã"
    response = _post(ctx, reverse("offerings:create"), bad, name="Outra")
    assert response.status_code == 200
    assert "SLA must be a duration" in response.content.decode()


# ------------------------------------------ external steps and the total SLA
def test_external_step_takes_type_sla_and_no_role_from_the_offering(ctx):
    plan = [
        {
            "key": "clean",
            "name": "Limpar",
            "step_type": "APPROVAL",
            "role": ctx["role"].pk,
            "sla": "1h",
            "service_offering": ctx["cleaning"].pk,
        }
    ]
    other = _parse(
        plan,
        roles=[ctx["role"]],
        offerings=[ctx["cleaning"]],
    )
    own = parse_definition(
        json.dumps(plan),
        roles=[ctx["role"]],
        offerings=[ctx["cleaning"]],
        own_service_id=ctx["cemetery"].pk,
    )
    step = own.steps[0]
    assert (step.step_type, step.role, step.sla_minutes) == (StepType.TASK, None, 480)
    assert other.steps[0].sla_minutes == 60  # no own service known → typed values kept

    same_service = parse_definition(
        json.dumps(plan),
        roles=[ctx["role"]],
        offerings=[ctx["cleaning"]],
        own_service_id=ctx["cleaning"].service_id,
    )
    assert same_service.steps[0].sla_minutes == 60  # same service → manual SLA/role
    assert same_service.steps[0].role == ctx["role"]


def test_total_sla_follows_the_normal_path_only(ctx):
    plan = [
        {"key": "a", "name": "A", "sla": "2h", "on_error": "warn"},
        {"key": "b", "name": "B", "sla": "1d", "next": "end"},
        {
            "key": "warn",
            "name": "Avisar",
            "step_type": "NOTIFY",
            "message": "x",
            "sla": "3d",
            "next": "end",
        },
    ]
    _post(ctx, reverse("offerings:create"), plan, action="publish")
    offering = ServiceOffering.objects.get(name="Realizar um funeral")
    assert offering.sla_minutes == 26 * 60  # 2h + 1d; the 3d error branch is excluded
    detail = ctx["client"].get(reverse("offerings:detail", args=[offering.pk])).content.decode()
    assert "Total SLA" in detail and "1d 2h" in detail
    listing = ctx["client"].get(reverse("offerings:index")).content.decode()
    assert "1d 2h" in listing


def test_card_shows_the_responsible_offering_type_for_external_steps(ctx):
    _post(ctx, reverse("offerings:create"), _plan(ctx))
    offering = ServiceOffering.objects.get(name="Realizar um funeral")
    detail = ctx["client"].get(reverse("offerings:detail", args=[offering.pk])).content.decode()
    row = detail.split("Realizar inumação")[1].split("</li>")[0]
    assert "Limpeza" in row  # responsible offering's type instead of "Task"


def test_preview_total_sla_and_editor_rows_carry_service_sla_and_type(ctx):
    body = (
        ctx["client"]
        .post(
            reverse("offerings:preview"),
            {
                "service": ctx["cemetery"].pk,
                "name": "X",
                "on_request": "on",
                "definition": json.dumps(_plan(ctx)),
            },
        )
        .content.decode()
    )
    assert "Total SLA" in body and "8h" in body

    editor = ctx["client"].get(reverse("offerings:create")).content.decode()
    options = json.loads(
        editor.split('id="editor-options" type="application/json">')[1].split("</script>")[0]
    )
    row = next(r for r in options["offerings"] if r[0] == ctx["cleaning"].pk)
    assert row[3] == ctx["cleaning"].service_id and row[4] == "8h" and row[5] == "Limpeza"


# ------------------------------------------------ one SLA record per offering
def _offering_sla(offering):
    from apps.metrics.models import SLA

    return SLA.objects.get(offering=offering)


def test_saving_a_plan_creates_the_offering_sla(ctx):
    from apps.metrics.models import ScopeType

    plan = [{"key": "a", "name": "A", "sla": "1d 2h"}]
    _post(ctx, reverse("offerings:create"), plan)
    offering = ServiceOffering.objects.get(name="Realizar um funeral")
    sla = _offering_sla(offering)
    assert (sla.target_minutes, sla.scope, sla.service_id) == (
        26 * 60,
        ScopeType.RESOLUTION,
        ctx["cemetery"].pk,
    )
    assert sla.name == "Realizar um funeral v1"
    assert not sla.active  # draft

    plan[0]["sla"] = "3h"
    _post(ctx, reverse("offerings:edit", args=[offering.pk]), plan)
    assert _offering_sla(offering).target_minutes == 180  # updated, not duplicated


def test_publish_activates_sla_and_new_version_gets_its_own(ctx):
    _post(ctx, reverse("offerings:create"), [{"key": "a", "name": "A", "sla": "2h"}], "publish")
    v1 = ServiceOffering.objects.get(name="Realizar um funeral")
    assert _offering_sla(v1).active

    v2 = v1.new_version()
    assert _offering_sla(v2).target_minutes == 120 and not _offering_sla(v2).active
    v2.publish()
    assert _offering_sla(v2).active
    assert not _offering_sla(v1).active  # deprecated version's SLA is switched off


def test_deleting_a_draft_removes_its_sla(ctx):
    from apps.metrics.models import SLA

    _post(ctx, reverse("offerings:create"), [{"key": "a", "name": "A", "sla": "2h"}])
    offering = ServiceOffering.objects.get(name="Realizar um funeral")
    ctx["client"].post(reverse("offerings:delete", args=[offering.pk]))
    assert not SLA.objects.filter(name="Realizar um funeral v1").exists()


def test_sla_list_shows_offering_slas_in_scope(ctx, make_role, make_person, make_membership):
    from apps.metrics.models import SLA

    _post(ctx, reverse("offerings:create"), [{"key": "a", "name": "A", "sla": "2h"}], "publish")
    SLA.objects.create(service=ctx["outside"], name="Hidden SLA", target_minutes=60)
    body = ctx["client"].get(reverse("metrics:slas")).content.decode()
    assert "Realizar um funeral v1" in body
    funeral = ServiceOffering.objects.get(name="Realizar um funeral")
    assert reverse("offerings:detail", args=[funeral.pk]) in body
    assert "2h" in body
    assert "Hidden SLA" not in body
