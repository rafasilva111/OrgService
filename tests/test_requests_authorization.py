"""Requests + the centralized authorization layer (can / visible_to)."""

import pytest

from apps.access.models import ServiceAccess
from apps.common.authorization import authorization_scope_q, can, visible
from apps.organizations.models import Organization
from apps.requests.models import Request, RequestType


@pytest.fixture
def request_graph(
    db, org_tree, make_user, make_person, make_role, make_membership, make_service, grant_service
):
    from apps.requests.models import Request

    operator = make_user("op")
    person = make_person(user=operator)
    a = org_tree["parish_a"]
    b = org_tree["parish_b"]

    rbac = make_role(permissions=["request.view", "request.update"])
    membership = make_membership(person, org_tree["municipality"], rbac)

    service_a = make_service(a, name="Waste")
    service_b = make_service(b, name="Parks")
    rt = RequestType.objects.create(code="req", name="Request")

    req_a = Request.objects.create(service=service_a, request_type=rt, title="rA")
    req_b = Request.objects.create(service=service_b, request_type=rt, title="rB")

    return {
        "operator": operator,
        "person": person,
        "membership": membership,
        "service_a": service_a,
        "service_b": service_b,
        "req_a": req_a,
        "req_b": req_b,
    }


@pytest.mark.django_db
def test_visible_inherits_downward_to_services(request_graph, org_tree):
    g = request_graph
    op = g["operator"]
    # Membership is at municipality -> both parish services' requests are visible.
    assert {r.title for r in Request.objects.visible_to(op)} == {"rA", "rB"}
    assert {r.title for r in visible(Request.objects.all(), op)} == {"rA", "rB"}
    assert Request.objects.visible_to(op).count() == 2


@pytest.mark.django_db
def test_can_on_resource(request_graph):
    g = request_graph
    op = g["operator"]
    assert can(op, "request.view", g["req_a"]) is True
    assert can(op, "request.view", g["req_b"]) is True
    # Read permission does not grant mutation.
    assert can(op, "request.update", g["req_a"]) is True
    assert can(op, "request.close", g["req_a"]) is False


@pytest.mark.django_db
def test_does_not_flow_upward_or_sibling(request_graph, org_tree):
    g = request_graph
    op = g["operator"]
    outside = Organization.objects.create(
        name="Parish C",
        code="pc",
        organization_type="PARISH",
        parent=org_tree["district"],
    )
    service_c = None
    from apps.services.models import Service

    service_c = Service.objects.create(organization=outside, name="C")
    rt = RequestType.objects.first()
    req_c = Request.objects.create(service=service_c, request_type=rt, title="rC")

    # Sibling under the same district, but NOT a descendant of the membership org.
    assert {r.title for r in Request.objects.visible_to(op)} == {"rA", "rB"}
    assert can(op, "request.view", req_c) is False


@pytest.mark.django_db
def test_service_access_restricts_visibility(request_graph):
    g = request_graph
    op = g["operator"]
    ServiceAccess.objects.create(membership=g["membership"], service=g["service_a"])
    # Only requests of service A remain visible.
    assert Request.objects.visible_to(op).count() == 1
    assert {r.title for r in Request.objects.visible_to(op)} == {"rA"}
    assert can(op, "request.view", g["req_a"]) is True
    assert can(op, "request.view", g["req_b"]) is False


@pytest.mark.django_db
def test_superuser_sees_everything(request_graph, make_user):
    superuser = make_user("super")
    superuser.is_superuser = True
    superuser.save()
    assert Request.objects.visible_to(superuser).count() == Request.objects.count()


@pytest.mark.django_db
def test_anonymous_and_unauthenticated_users_see_nothing(request_graph):
    assert Request.objects.visible_to(None).count() == 0
    assert list(Request.objects.visible_to(None)) == []


@pytest.mark.django_db
def test_visible_to_only_rows_with_permission(
    request_graph, org_tree, make_user, make_person, make_role, make_membership
):
    # Another user with a role that grants *no* relevant request permission.
    other_user = make_user("other")
    other_person = make_person(user=other_user)
    make_membership(other_person, org_tree["municipality"], make_role(permissions=["service.view"]))
    assert Request.objects.visible_to(other_user).count() == 0


@pytest.mark.django_db
def test_scope_subquery_matches_request_sql(request_graph, org_tree):
    g = request_graph
    scope = authorization_scope_q(Request, g["operator"], "request.view")
    assert scope is not None
    # The scope references the org subtree of the membership
    # plus zero service restrictions.
    muni = org_tree["municipality"]
    sub = muni.get_descendants(include_self=True)
    assert {o.code for o in sub} == {"muni", "pa", "pb"}
    assert Request.objects.filter(scope).count() == 2
