import pytest
from django.template import Context, Template
from django.urls import reverse
from django.utils.translation import override

from apps.common.tables.config import Column
from apps.common.templatetags.ui import field_display
from apps.incidents.models import Incident, IncidentStatus
from apps.requests.models import Priority, Request, RequestStatus

pytestmark = pytest.mark.django_db


def test_request_list_renders_translated_choice_labels_and_keeps_raw_filters(
    client, make_user, make_person, make_role, make_membership, make_service, org_tree
):
    user = make_user()
    person = make_person(user)
    role = make_role(permissions=["request.view", "service.view"])
    make_membership(person, org_tree["parish_a"], role)
    service = make_service(org_tree["parish_a"])
    Request.objects.create(
        service=service, title="Example", status=RequestStatus.IN_PROGRESS, priority=Priority.VERY_LOW
    )
    client.force_login(user)
    client.cookies["django_language"] = "pt"
    with override("pt"):
        response = client.get(
            reverse("requests:index"),
            {"status": "IN_PROGRESS", "priority": "VERY_LOW", "partial": "table"},
        )
    html = response.content.decode()
    assert response.status_code == 200
    assert ">Em curso</span>" in html
    assert ">Muito baixa</span>" in html
    assert "bg-blue-50 text-blue-700" in html
    assert 'value="IN_PROGRESS"' in html
    assert 'value="VERY_LOW"' in html
    assert ">In_Progress</span>" not in html
    assert ">Very_Low</span>" not in html


def test_shared_pill_renders_other_models_choices_and_fallback(make_service, org_tree):
    service = make_service(org_tree["parish_a"])
    incident = Incident(service=service, title="Sample", status=IncidentStatus.INVESTIGATING)
    template = Template(
        '{% include "partials/pill.html" with record=record value=value column=column %}'
    )
    with override("pt"):
        html = template.render(
            Context({"record": incident, "value": incident.status, "column": Column("status", "")})
        )
        assert ">A investigar</span>" in html
        assert field_display({"nested": incident}, "nested.status") == incident.get_status_display()
    fallback = template.render(
        Context({"record": {"label": "some_value"}, "value": "some_value", "column": Column("label", "")})
    )
    assert ">Some_Value</span>" in fallback
    assert field_display({"nested": None}, "nested.status") == ""
