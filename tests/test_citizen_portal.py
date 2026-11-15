"""Citizen portal (/citizen/): sign-up, public catalogue, own requests only,
comments visible, Work Notes hidden (ADR 018/019)."""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.offerings.models import (
    ExecutionNote,
    FormFieldType,
    OfferingExecution,
    OfferingFormField,
    OfferingStatus,
    OfferingStep,
    OfferingTransition,
    ServiceOffering,
    StepType,
)
from apps.people.models import Person, PersonType
from apps.requests.models import Request, RequestAttachment, RequestStatus
from apps.services.models import ServiceStatus

pytestmark = pytest.mark.django_db


def _flow(offering):
    start = OfferingStep.objects.create(
        offering=offering, code="start", name="Start", step_type=StepType.START, order=10
    )
    work = OfferingStep.objects.create(
        offering=offering, code="work", name="Assess the site", step_type=StepType.TASK, order=20
    )
    end = OfferingStep.objects.create(
        offering=offering, code="end", name="End", step_type=StepType.END, order=30
    )
    OfferingTransition.objects.create(offering=offering, from_step=start, to_step=work)
    OfferingTransition.objects.create(offering=offering, from_step=work, to_step=end)


@pytest.fixture
def catalogue(org_tree, make_service):
    lighting = make_service(org_tree["parish_a"], "Public Lighting")
    water = make_service(org_tree["parish_b"], "Water Supply")
    water.status = ServiceStatus.DEGRADED
    water.save(update_fields=["status"])
    retired = make_service(org_tree["parish_a"], "Old service")
    retired.status = ServiceStatus.RETIRED
    retired.save(update_fields=["status"])

    lamp = ServiceOffering.objects.create(
        service=lighting, name="Report a broken street light", status=OfferingStatus.ACTIVE
    )
    _flow(lamp)
    OfferingFormField.objects.create(
        offering=lamp,
        key="photo",
        label="Photo of the lamp",
        field_type=FormFieldType.FILE,
        order=10,
    )
    leak = ServiceOffering.objects.create(
        service=water, name="Report a leak", status=OfferingStatus.ACTIVE
    )
    _flow(leak)
    draft = ServiceOffering.objects.create(
        service=lighting, name="Draft lighting offering", status=OfferingStatus.DRAFT
    )
    internal = ServiceOffering.objects.create(
        service=lighting,
        name="Monthly lamp inspection",
        status=OfferingStatus.ACTIVE,
        on_request=False,
        on_schedule=True,
        recurrence_start="2026-01-01",
    )
    old = ServiceOffering.objects.create(
        service=retired, name="Retired offering", status=OfferingStatus.ACTIVE
    )
    return {
        "lighting": lighting,
        "water": water,
        "lamp": lamp,
        "leak": leak,
        "draft": draft,
        "internal": internal,
        "old": old,
    }


@pytest.fixture
def citizen(client, make_user, make_person):
    user = make_user("citizen")
    person = make_person(user, name="Maria Cidadã", person_type=PersonType.CITIZEN)
    assert client.login(username=user.username, password="x")
    return person


def _submit(client, offering, **extra):
    data = {"offering": offering.pk, "title": "Lamp out", "description": "Since Monday", **extra}
    return client.post(f"{reverse('citizen:request_create')}?offering={offering.pk}", data)


def test_pages_require_login(client, catalogue):
    response = client.get(reverse("citizen:home"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("citizen:login"))


def test_signup_creates_citizen_person_and_logs_in(client):
    response = client.post(
        reverse("citizen:signup"),
        {
            "full_name": "João Munícipe",
            "email": "Joao@Example.com",
            "phone": "+351 912 345 678",
            "home_organization": "",
            "password1": "a-Strong-pass-42",
            "password2": "a-Strong-pass-42",
        },
    )
    assert response.status_code == 302
    person = Person.objects.get(email="joao@example.com")
    assert person.person_type == PersonType.CITIZEN
    assert person.user.username == "joao@example.com"
    assert client.get(reverse("citizen:home")).status_code == 200

    client.logout()
    assert (
        client.post(
            reverse("citizen:login"),
            {"username": "JOAO@example.com", "password": "a-Strong-pass-42"},
        ).status_code
        == 302
    )


def test_signup_rejects_duplicate_email(client, make_person):
    Person.objects.create(full_name="Existing", email="taken@example.com")
    response = client.post(
        reverse("citizen:signup"),
        {
            "full_name": "Other",
            "email": "taken@example.com",
            "password1": "a-Strong-pass-42",
            "password2": "a-Strong-pass-42",
        },
    )
    assert response.status_code == 200
    assert "already exists" in response.content.decode()


def test_catalogue_lists_only_public_offerings(client, citizen, catalogue):
    body = client.get(reverse("citizen:services")).content.decode()
    assert "Report a broken street light" in body
    assert "Report a leak" in body
    assert "Draft lighting offering" not in body
    assert "Monthly lamp inspection" not in body
    assert "Retired offering" not in body

    filtered = client.get(reverse("citizen:services"), {"q": "leak"}).content.decode()
    assert "Report a leak" in filtered
    assert "street light" not in filtered

    for offering in ("draft", "internal", "old"):
        url = reverse("citizen:offering_detail", args=[catalogue[offering].pk])
        assert client.get(url).status_code == 404


def test_home_shows_service_notices(client, citizen, catalogue):
    body = client.get(reverse("citizen:home")).content.decode()
    assert "Water Supply" in body
    assert "Degraded" in body


def test_offering_page_lists_public_steps_and_documents(client, citizen, catalogue):
    body = client.get(
        reverse("citizen:offering_detail", args=[catalogue["lamp"].pk])
    ).content.decode()
    assert "Assess the site" in body
    assert "Photo of the lamp" in body
    assert f"?offering={catalogue['lamp'].pk}" in body


def test_submit_request_with_attachment(client, citizen, catalogue, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    photo = SimpleUploadedFile("lamp.jpg", b"\xff\xd8\xff", content_type="image/jpeg")
    response = _submit(client, catalogue["lamp"], q_photo=photo)
    req = Request.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("citizen:request_detail", args=[req.pk])
    assert req.requester == citizen and req.created_by == citizen
    assert req.service == catalogue["lighting"]
    assert req.priority == catalogue["lamp"].default_priority
    assert OfferingExecution.objects.filter(
        subject_type=ContentType.objects.get_for_model(Request), subject_id=req.pk
    ).exists()
    attachment = RequestAttachment.objects.get(request=req)
    assert req.form_data["photo"]["attachment"] == attachment.pk

    download = client.get(reverse("citizen:attachment", args=[req.pk, attachment.pk]))
    assert download.status_code == 200


def test_cannot_submit_to_non_public_offering(client, citizen, catalogue):
    response = _submit(client, catalogue["draft"])
    assert response.status_code == 404
    assert not Request.objects.exists()


def test_citizen_sees_only_own_requests(client, citizen, catalogue, make_person):
    other = make_person(name="Someone else", person_type=PersonType.CITIZEN)
    theirs = Request.objects.create(
        service=catalogue["lighting"],
        offering=catalogue["lamp"],
        title="Not yours",
        requester=other,
    )
    mine = Request.objects.create(
        service=catalogue["lighting"],
        offering=catalogue["lamp"],
        title="Mine",
        requester=citizen,
    )
    body = client.get(reverse("citizen:requests"), {"state": "all"}).content.decode()
    assert "Mine" in body
    assert "Not yours" not in body
    assert client.get(reverse("citizen:request_detail", args=[mine.pk])).status_code == 200
    assert client.get(reverse("citizen:request_detail", args=[theirs.pk])).status_code == 404
    attachment = RequestAttachment.objects.create(
        request=theirs, field_key="photo", original_name="x.jpg", file="requests/x.jpg"
    )
    url = reverse("citizen:attachment", args=[theirs.pk, attachment.pk])
    assert client.get(url).status_code == 404


def test_comments_visible_work_notes_hidden(client, citizen, catalogue, make_user):
    _submit(client, catalogue["lamp"], q_photo="")
    req = Request.objects.get()
    execution = OfferingExecution.objects.get(subject_id=req.pk)
    staff = make_user("staff")
    ExecutionNote.objects.create(
        execution=execution, author=staff, body="Internal: crew busy", kind="WORK_NOTE"
    )
    ExecutionNote.objects.create(
        execution=execution, author=staff, body="We will visit on Friday", kind="COMMENT"
    )
    url = reverse("citizen:request_detail", args=[req.pk])
    body = client.get(url).content.decode()
    assert "We will visit on Friday" in body
    assert "Internal: crew busy" not in body

    response = client.post(url, {"body": "Thank you!"})
    assert response.status_code == 302
    note = execution.notes.get(body="Thank you!")
    assert note.kind == ExecutionNote.Kind.COMMENT


def test_closed_request_rejects_messages(client, citizen, catalogue):
    req = Request.objects.create(
        service=catalogue["lighting"],
        offering=catalogue["lamp"],
        title="Done",
        requester=citizen,
        status=RequestStatus.CLOSED,
    )
    url = reverse("citizen:request_detail", args=[req.pk])
    client.post(url, {"body": "Hello?"})
    assert not req.comments.exists()


def test_profile_update(client, citizen):
    response = client.post(
        reverse("citizen:profile"),
        {"full_name": "Maria Silva", "email": "maria@example.com", "phone": "", "language": "pt"},
    )
    assert response.status_code == 302
    citizen.refresh_from_db()
    assert citizen.full_name == "Maria Silva"
    assert citizen.email == "maria@example.com"
    assert citizen.user.profile.language == "pt"


def test_signup_and_profile_store_parish(client, org_tree):
    parish = org_tree["parish_a"]
    client.post(
        reverse("citizen:signup"),
        {
            "full_name": "Rosa Munícipe",
            "email": "rosa@example.com",
            "home_organization": parish.pk,
            "password1": "a-Strong-pass-42",
            "password2": "a-Strong-pass-42",
        },
    )
    person = Person.objects.get(email="rosa@example.com")
    assert person.home_organization == parish
    assert "Parish A" in client.get(reverse("citizen:home")).content.decode()

    # Only parishes can be chosen.
    response = client.post(
        reverse("citizen:profile"),
        {
            "full_name": "Rosa",
            "email": "rosa@example.com",
            "home_organization": org_tree["municipality"].pk,
        },
    )
    assert response.status_code == 200
    client.post(
        reverse("citizen:profile"),
        {
            "full_name": "Rosa",
            "email": "rosa@example.com",
            "home_organization": org_tree["parish_b"].pk,
        },
    )
    person.refresh_from_db()
    assert person.home_organization == org_tree["parish_b"]
    assert not person.memberships.exists()
