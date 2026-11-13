import pytest
from django.urls import reverse

from apps.incidents.models import Incident
from apps.requests.models import Request

pytestmark = pytest.mark.django_db


def test_aggregate_requires_permission_and_suppresses_small_and_overlapping_groups(
    client, make_user, make_person, make_role, make_membership, make_service, org_tree
):
    user = make_user()
    person = make_person(user)
    role = make_role(permissions=["dashboard.aggregate"])
    make_membership(person, org_tree["parish_a"], role)
    service = make_service(org_tree["parish_a"], "A")
    hidden_service = make_service(org_tree["parish_b"], "B")
    for i in range(5):
        Request.objects.create(service=service, title=f"Request {i}")
        Incident.objects.create(service=hidden_service, title=f"Hidden incident {i}")
    client.force_login(user)
    response = client.get(reverse("dashboard:protected_overview"))
    assert response.status_code == 200
    reports = {str(r["label"]): r["value"] for r in response.context["reports"]}
    assert reports["Requests"] == 5
    assert reports["Incidents"] is None
    assert "Request 0" not in response.content.decode()
    assert "requests/" not in response.content.decode().split("<main", 1)[-1]
    request = Request.objects.filter(service=service).first()
    request.requester = person
    request.save(update_fields=["requester"])
    response = client.get(reverse("dashboard:protected_overview"))
    assert response.context["reports"][0]["value"] is None
    outsider = make_user()
    make_person(outsider)
    client.force_login(outsider)
    assert client.get(reverse("dashboard:protected_overview")).status_code == 403
