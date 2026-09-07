"""Dashboard "drill down" links must reproduce what they count.

Regression test: stat cards on the service/organization/executive dashboards
used to link to the plain, unfiltered list view, so a service dashboard
showing "Open Requests: 4" would land on a list showing every request in the
system. Each card's link must carry the same service/status scoping used to
compute its number.
"""

import pytest
from django.urls import reverse

from apps.requests.models import Request, RequestStatus, RequestType

pytestmark = pytest.mark.django_db


def _member(make_person, make_membership, user, org, role):
    return make_membership(make_person(user), org, role)


@pytest.fixture
def request_type(db):
    return RequestType.objects.create(code="generic", name="Generic")


def test_service_dashboard_open_requests_link_is_scoped(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service, request_type
):
    other_service = make_service(org_tree["municipality"], "Other Service")
    service = make_service(org_tree["municipality"], "Target Service")
    user = make_user()
    role = make_role(
        code="svcmgr", permissions=["service.view", "request.view", "request.create"]
    )
    _member(make_person, make_membership, user, org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    Request.objects.create(
        service=service, request_type=request_type, title="Open on target", status=RequestStatus.NEW
    )
    Request.objects.create(
        service=service, request_type=request_type, title="Closed on target", status=RequestStatus.CLOSED
    )
    Request.objects.create(
        service=other_service, request_type=request_type, title="Open on other", status=RequestStatus.NEW
    )

    body = client.get(reverse("dashboard:service_dashboard", args=[service.pk])).content.decode()
    raw_url = f"/requests/?service={service.pk}&status=OPEN"
    assert f"/requests/?service={service.pk}&amp;status=OPEN" in body

    listing = client.get(raw_url).content.decode()
    assert "Open on target" in listing
    assert "Closed on target" not in listing
    assert "Open on other" not in listing


def test_requests_list_service_filter_is_exact_not_substring(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service, request_type
):
    """``?service=<pk>`` must be an exact match — unlike the free-text
    ``service.name`` column filter it replaces for dashboard links."""
    alpha = make_service(org_tree["municipality"], "Alpha")
    alpha_two = make_service(org_tree["municipality"], "Alpha Two")
    user = make_user()
    role = make_role(code="svcmgr2", permissions=["service.view", "request.view"])
    _member(make_person, make_membership, user, org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    Request.objects.create(service=alpha, request_type=request_type, title="Alpha request", status=RequestStatus.NEW)
    Request.objects.create(service=alpha_two, request_type=request_type, title="Alpha Two request", status=RequestStatus.NEW)

    body = client.get(reverse("requests:index"), {"service": alpha.pk}).content.decode()
    assert "Alpha request" in body
    assert "Alpha Two request" not in body


def test_requests_list_status_open_pseudo_filter(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service, request_type
):
    service = make_service(org_tree["municipality"], "Svc")
    user = make_user()
    role = make_role(code="svcmgr3", permissions=["service.view", "request.view"])
    _member(make_person, make_membership, user, org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    Request.objects.create(service=service, request_type=request_type, title="New one", status=RequestStatus.NEW)
    Request.objects.create(
        service=service, request_type=request_type, title="In progress one", status=RequestStatus.IN_PROGRESS
    )
    Request.objects.create(service=service, request_type=request_type, title="Closed one", status=RequestStatus.CLOSED)

    body = client.get(reverse("requests:index"), {"status": "OPEN"}).content.decode()
    assert "New one" in body
    assert "In progress one" in body
    assert "Closed one" not in body


def test_requests_service_column_filter_is_a_dropdown(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service, request_type
):
    """The "Service" column filter in the drawer is a <select> of the user's
    visible services (scoped, exact-match), not a free-text search box."""
    alpha = make_service(org_tree["municipality"], "Alpha")
    beta = make_service(org_tree["municipality"], "Beta")
    user = make_user()
    role = make_role(code="svcmgr4", permissions=["service.view", "request.view"])
    _member(make_person, make_membership, user, org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("requests:index")).content.decode()
    # A <select name="service.name"> populated with the visible services.
    assert '<select name="service.name"' in body
    assert f'<option value="{alpha.pk}"' in body
    assert f'<option value="{beta.pk}"' in body
    assert ">Alpha<" in body
    assert ">Beta<" in body
    # The old free-text input for this column must be gone.
    assert 'name="service.name" placeholder' not in body


def test_requests_service_column_filter_scoped_to_visible_services(
    client, make_user, make_person, make_role, make_membership, grant_service, org_tree, make_service
):
    """A restricted membership only sees its granted services in the dropdown,
    not every service in the system."""
    allowed = make_service(org_tree["municipality"], "Allowed")
    hidden = make_service(org_tree["municipality"], "Hidden")
    user = make_user()
    role = make_role(code="restricted", permissions=["service.view", "request.view"])
    membership = _member(make_person, make_membership, user, org_tree["municipality"], role)
    grant_service(membership, allowed)
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("requests:index")).content.decode()
    assert f'<option value="{allowed.pk}"' in body
    assert f'<option value="{hidden.pk}"' not in body


def test_requests_service_column_filter_by_id_is_exact(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service, request_type
):
    """Submitting the dropdown's own param (``service.name``, the pk) filters
    by exact service id — no name-substring ambiguity."""
    alpha = make_service(org_tree["municipality"], "Alpha")
    alpha_two = make_service(org_tree["municipality"], "Alpha Two")
    user = make_user()
    role = make_role(code="svcmgr5", permissions=["service.view", "request.view"])
    _member(make_person, make_membership, user, org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    Request.objects.create(service=alpha, request_type=request_type, title="Alpha req", status=RequestStatus.NEW)
    Request.objects.create(
        service=alpha_two, request_type=request_type, title="Alpha Two req", status=RequestStatus.NEW
    )

    body = client.get(reverse("requests:index"), {"service.name": alpha.pk}).content.decode()
    assert "Alpha req" in body
    assert "Alpha Two req" not in body
