"""Per-offering request forms: builder definition, the generated Django form,
Novo Pedido answers and attachments."""

import json
from datetime import date
from html.parser import HTMLParser

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils.formats import date_format

from apps.offerings.models import (
    FormFieldType,
    OfferingFormField,
    OfferingStatus,
    ServiceOffering,
)
from apps.offerings.request_form import (
    FormFieldSpec,
    RequestAnswersForm,
    parse_form_definition,
)
from apps.requests.models import Request, RequestAttachment

pytestmark = pytest.mark.django_db

T = FormFieldType


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


# --------------------------------------------------------------- definition
def test_parse_form_definition_rules():
    specs, errors = parse_form_definition(
        json.dumps(
            [
                {"label": "Data do óbito", "field_type": "DATE", "required": True},
                {"label": ""},
                {"label": "Tipo", "field_type": "CHOICE", "options": "Jazigo"},
                {"label": "Zonas", "field_type": "MULTICHOICE", "options": " A \nB\n\nA"},
                {"label": "Data do óbito", "field_type": "BOGUS"},
            ]
        )
    )
    assert set(errors) == {1, 2, 4}
    assert specs[0].key == "data-do-obito" and specs[0].required
    assert specs[3].options == ["A", "B"]  # trimmed, blanks and duplicates removed
    assert specs[4].key == "data-do-obito-2"  # unique keys
    assert parse_form_definition("nope")[1] == {-1: ["The request form could not be read."]}


def _answers(specs, data=None, files=None):
    return RequestAnswersForm(specs, data=data, files=files)


def test_generated_form_validates_each_type():
    specs = [
        FormFieldSpec("nome", "Nome", T.TEXT, required=True),
        FormFieldSpec("email", "Email", T.EMAIL),
        FormFieldSpec("tel", "Telefone", T.PHONE),
        FormFieldSpec("tipo", "Tipo", T.CHOICE, options=["Jazigo", "Ossário"]),
        FormFieldSpec("velorio", "Velório", T.BOOLEAN, required=True),
        FormFieldSpec("morada", "Morada", T.ADDRESS, required=True),
        FormFieldSpec("doc", "Certidão", T.FILE),
    ]
    form = _answers(
        specs,
        {
            "q_email": "not-an-email",
            "q_tel": "12ab",
            "q_tipo": "Campa rasa",
            "q_morada__postal_code": "2560",
        },
        {"q_doc": SimpleUploadedFile("virus.exe", b"x")},
    )
    assert not form.is_valid()
    assert set(form.errors) == {
        "q_nome",
        "q_email",
        "q_tel",
        "q_tipo",
        "q_velorio",
        "q_morada__street",
        "q_morada__postal_code",
        "q_morada__locality",
        "q_doc",
    }


def test_attachment_size_limit(settings):
    settings.REQUEST_ATTACHMENT_MAX_BYTES = 10
    form = _answers(
        [FormFieldSpec("doc", "Doc", T.FILE, required=True)],
        {},
        {"q_doc": SimpleUploadedFile("big.pdf", b"x" * 11)},
    )
    assert not form.is_valid() and "too large" in str(form.errors["q_doc"])


def test_answers_are_json_safe():
    specs = [
        FormFieldSpec("data", "Data", T.DATE),
        FormFieldSpec("n", "Número", T.NUMBER),
        FormFieldSpec("zonas", "Zonas", T.MULTICHOICE, options=["A", "B", "C"]),
        FormFieldSpec("velorio", "Velório", T.BOOLEAN),
        FormFieldSpec("morada", "Morada", T.ADDRESS),
        FormFieldSpec("vazio", "Vazio", T.TEXT),
        FormFieldSpec("doc", "Doc", T.FILE),
    ]
    form = _answers(
        specs,
        {
            "q_data": "2026-11-20",
            "q_n": "3",
            "q_zonas": ["A", "C"],
            "q_velorio": "no",
            "q_morada__street": "Rua Direita 1",
            "q_morada__postal_code": "2560-100",
            "q_morada__locality": "Torres Vedras",
        },
        {"q_doc": SimpleUploadedFile("certidao.pdf", b"%PDF")},
    )
    assert form.is_valid(), form.errors
    data, files = form.answers()
    assert data == {
        "data": "2026-11-20",
        "n": "3",
        "zonas": ["A", "C"],
        "velorio": False,
        "morada": {
            "street": "Rua Direita 1",
            "postal_code": "2560-100",
            "locality": "Torres Vedras",
        },
    }
    assert [k for k, _f in files] == ["doc"]
    json.dumps(data)  # storable as-is


# --------------------------------------------------------------- fixtures
@pytest.fixture
def world(client, make_user, make_person, make_role, make_membership, org_tree, make_service):
    cemetery = make_service(org_tree["parish_a"], "Cemitérios")
    funeral = ServiceOffering.objects.create(
        service=cemetery, name="Realizar um funeral", status=OfferingStatus.ACTIVE
    )
    from apps.offerings.models import OfferingStep, OfferingTransition, StepType

    start = OfferingStep.objects.create(
        offering=funeral, code="start", name="Start", step_type=StepType.START, order=10
    )
    work = OfferingStep.objects.create(
        offering=funeral, code="work", name="Work", step_type=StepType.TASK, order=20
    )
    end = OfferingStep.objects.create(
        offering=funeral, code="end", name="End", step_type=StepType.END, order=30
    )
    OfferingTransition.objects.create(offering=funeral, from_step=start, to_step=work)
    OfferingTransition.objects.create(offering=funeral, from_step=work, to_step=end)
    for order, (key, label, ftype, required, options) in enumerate(
        [
            ("nome", "Nome do falecido", T.TEXT, True, []),
            ("data", "Data do óbito", T.DATE, True, []),
            ("tipo", "Tipo de sepultura", T.CHOICE, True, ["Jazigo", "Ossário"]),
            ("certidao", "Certidão de óbito", T.FILE, True, []),
        ],
        start=1,
    ):
        OfferingFormField.objects.create(
            offering=funeral,
            key=key,
            label=label,
            field_type=ftype,
            required=required,
            options=options,
            order=order * 10,
        )
    role = make_role(
        permissions=[
            "service.view",
            "request.view",
            "request.create",
            "offering.view",
            "offering.manage",
            "organization.view",
        ]
    )
    user = make_user()
    person = make_person(user, name="Clerk")
    make_membership(person, org_tree["parish_a"], role)
    assert client.login(username=user.username, password="x")
    return {"client": client, "cemetery": cemetery, "funeral": funeral, "person": person}


def _post_request(world, **extra):
    data = {
        "service": world["cemetery"].pk,
        "offering": world["funeral"].pk,
        "title": "Funeral do Sr. Silva",
        "priority": "MEDIUM",
        "requester": world["person"].pk,
        "q_nome": "João Silva",
        "q_data": "2026-11-10",
        "q_tipo": "Jazigo",
        "q_certidao": SimpleUploadedFile(
            "certidao.pdf", b"%PDF-1.4", content_type="application/pdf"
        ),
    }
    data.update(extra)
    return world["client"].post(reverse("requests:create"), data)


# ------------------------------------------------------------- Novo Pedido
def _request_fieldset_names(body):
    class FieldsetParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.depth = 0
            self.names = set()

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "fieldset":
                if self.depth:
                    self.depth += 1
                elif attrs.get("class") == "mt-4 space-y-3 rounded-lg border border-zinc-200 p-4":
                    self.depth = 1
            elif self.depth and attrs.get("name"):
                self.names.add(attrs["name"])

        def handle_endtag(self, tag):
            if tag == "fieldset" and self.depth:
                self.depth -= 1

    parser = FieldsetParser()
    parser.feed(body)
    return parser.names


def test_choosing_an_offering_swaps_its_questions_into_the_form(world):
    body = (
        world["client"]
        .get(reverse("requests:offering_steps"), {"offering": world["funeral"].pk})
        .content.decode()
    )
    assert '<div id="offering-form-fields" hx-swap-oob="true"' in body
    assert 'name="q_nome"' in body and 'name="q_certidao"' in body
    assert 'name="title"' not in body and 'name="description"' not in body
    assert 'type="date"' in body

    cleared = (
        world["client"]
        .get(reverse("requests:offering_options"), {"service": world["cemetery"].pk})
        .content.decode()
    )
    assert '<div id="offering-form-fields" hx-swap-oob="true"' in cleared
    assert 'name="q_nome"' not in cleared
    assert "<fieldset" not in cleared

    empty_offering = ServiceOffering.objects.create(
        service=world["cemetery"], name="No questions", status=OfferingStatus.ACTIVE
    )
    empty = (
        world["client"]
        .get(reverse("requests:offering_steps"), {"offering": empty_offering.pk})
        .content.decode()
    )
    assert '<div id="offering-form-fields" hx-swap-oob="true"' in empty
    assert '<option value="VERY_LOW" selected>Very low</option>' in empty
    assert "<fieldset" not in empty


def test_new_request_form_is_multipart(world):
    body = world["client"].get(reverse("requests:create")).content.decode()
    assert 'enctype="multipart/form-data"' in body
    assert 'x-data="{ offeringSelected: false }"' in body
    assert "$event.target.name === 'service'" in body
    assert "$event.target.name === 'offering'" in body
    assert '<fieldset x-show="offeringSelected" x-cloak' in body
    assert '<div id="offering-form-fields">' in body
    assert '<legend class="px-1 text-sm font-semibold text-zinc-800">Request form</legend>' in body
    assert {"title", "description"} <= _request_fieldset_names(body)
    assert body.index('name="title"') < body.index('name="description"')
    assert body.index('name="description"') < body.index('id="offering-form-fields"')


def test_request_form_visible_for_offering_without_questions_after_error(world):
    offering = ServiceOffering.objects.create(
        service=world["cemetery"], name="No questions", status=OfferingStatus.ACTIVE
    )
    response = world["client"].post(
        reverse("requests:create"),
        {
            "service": world["cemetery"].pk,
            "offering": offering.pk,
            "title": "",
            "description": "Preserved description",
            "priority": "MEDIUM",
            "requester": world["person"].pk,
        },
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert 'x-data="{ offeringSelected: true }"' in body
    assert "Preserved description" in body
    assert {"title", "description"} <= _request_fieldset_names(body)


def test_create_request_with_answers_and_attachment(world, media):
    response = _post_request(world)
    req = Request.objects.get(title="Funeral do Sr. Silva")
    assert response.status_code == 302
    attachment = RequestAttachment.objects.get(request=req)
    assert attachment.field_key == "certidao" and attachment.original_name == "certidao.pdf"
    assert req.form_data["nome"] == "João Silva"
    assert req.form_data["data"] == "2026-11-10"
    assert req.form_data["certidao"] == {"attachment": attachment.pk, "name": "certidao.pdf"}
    assert str(media) in attachment.file.path

    detail = world["client"].get(reverse("requests:detail", args=[req.pk])).content.decode()
    assert "Nome do falecido" in detail and "João Silva" in detail
    assert date_format(date(2026, 11, 10), "SHORT_DATE_FORMAT") in detail
    assert reverse("requests:attachment", args=[attachment.pk]) in detail

    download = world["client"].get(reverse("requests:attachment", args=[attachment.pk]))
    assert download.status_code == 200
    assert b"".join(download.streaming_content) == b"%PDF-1.4"


def test_missing_required_answers_block_the_request(world):
    response = _post_request(world, q_nome="", q_certidao="")
    assert response.status_code == 200
    assert not Request.objects.filter(title="Funeral do Sr. Silva").exists()
    body = response.content.decode()
    assert 'name="q_nome"' in body  # questions re-displayed with errors
    assert 'x-data="{ offeringSelected: true }"' in body
    assert '<legend class="px-1 text-sm font-semibold text-zinc-800">Request form</legend>' in body
    assert {"title", "description", "q_nome", "q_certidao"} <= _request_fieldset_names(body)
    assert body.index('name="description"') < body.index('name="q_nome"')
    assert "This field is required." in body
    assert "&lt;p" not in body  # error markup is not escaped


def test_attachment_download_is_scoped(
    world, client, make_user, make_person, make_role, make_membership, org_tree
):
    _post_request(world)
    attachment = RequestAttachment.objects.get()
    outsider = make_user()
    make_membership(
        make_person(outsider), org_tree["parish_b"], make_role(permissions=["request.view"])
    )
    client.logout()
    client.login(username=outsider.username, password="x")
    assert client.get(reverse("requests:attachment", args=[attachment.pk])).status_code == 404


# ------------------------------------------------------------ the builder
def test_builder_saves_request_form_and_new_version_copies_it(world):
    definition = [
        {"label": "Nome do falecido", "field_type": "TEXT", "required": True},
        {"label": "Tipo", "field_type": "CHOICE", "options": "Jazigo\nOssário"},
    ]
    response = world["client"].post(
        reverse("offerings:create"),
        {
            "service": world["cemetery"].pk,
            "name": "Exumação",
            "offering_type": "",
            "on_request": "on",
            "description": "",
            "default_priority": "VERY_LOW",
            "definition": json.dumps([{"key": "a", "name": "Exumar"}]),
            "form_definition": json.dumps(definition),
            "action": "publish",
        },
    )
    offering = ServiceOffering.objects.get(name="Exumação")
    assert response.status_code == 302
    fields = list(offering.form_fields.values_list("key", "field_type", "required", "options"))
    assert fields == [
        ("nome-do-falecido", "TEXT", True, []),
        ("tipo", "CHOICE", False, ["Jazigo", "Ossário"]),
    ]
    detail = world["client"].get(reverse("offerings:detail", args=[offering.pk])).content.decode()
    assert "Request form" in detail and 'name="q_nome-do-falecido"' in detail

    v2 = offering.new_version()
    assert v2.form_fields.count() == 2


def test_builder_rejects_an_invalid_form_definition(world):
    response = world["client"].post(
        reverse("offerings:create"),
        {
            "service": world["cemetery"].pk,
            "name": "Exumação",
            "on_request": "on",
            "default_priority": "VERY_LOW",
            "definition": json.dumps([{"key": "a", "name": "Exumar"}]),
            "form_definition": json.dumps([{"label": "Tipo", "field_type": "CHOICE"}]),
        },
    )
    assert response.status_code == 200
    assert not ServiceOffering.objects.filter(name="Exumação").exists()
    assert "at least two options" in response.content.decode()


def test_builder_preview_renders_the_request_form(world):
    body = (
        world["client"]
        .post(
            reverse("offerings:preview"),
            {
                "service": world["cemetery"].pk,
                "name": "X",
                "on_request": "on",
                "definition": "[]",
                "form_definition": json.dumps(
                    [{"label": "Morada do cemitério", "field_type": "ADDRESS", "required": True}]
                ),
            },
        )
        .content.decode()
    )
    assert "Morada do cemitério" in body
    assert 'name="q_morada-do-cemiterio__postal_code"' in body
    assert "disabled" in body  # preview only


def test_seed_request_forms(db):
    from django.core.management import call_command

    call_command("seed_rbac", verbosity=0)
    call_command("seed_default_data", verbosity=0)
    funeral = ServiceOffering.objects.get(name="Realizar um funeral")
    types = list(funeral.form_fields.values_list("field_type", flat=True))
    assert types == ["TEXT", "DATE", "CHOICE", "FILE", "BOOLEAN", "PHONE", "TEXTAREA"]
    assert (
        ServiceOffering.objects.get(name="Road Repair Request")
        .form_fields.filter(field_type="ADDRESS")
        .exists()
    )
