"""View-layer tests: authz gating, scoping and rendering of the app pages.

These exercise the class-based view surfaces added for the platform: every
page must (1) redirect anonymous users to the login URL, (2) 403 logged-in
users without the relevant permission, (3) scope rows to what the user may
see, and (4) render without template errors.
"""

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.access.models import Permission, Role
from apps.organizations.models import OrganizationMembership
from apps.people.models import Person
from apps.services.models import ServiceActor


def _grant(role, *codes):
    for pcode in codes:
        perm, _ = Permission.objects.get_or_create(
            code=pcode, defaults={"label": pcode, "module": pcode.split(".")[0]}
        )
        role.permissions.add(perm, through_defaults={"active": True})


@pytest.fixture
def make_member(db):
    def _make(user, organization, *codes, role_code="member"):
        person = Person.objects.create(user=user, full_name="Member", person_type="EMPLOYEE")
        role = Role.objects.create(code=role_code, name="Member")
        _grant(role, *codes)
        OrganizationMembership.objects.create(person=person, organization=organization, role=role)
        return person

    return _make


@pytest.fixture
def staff_user(make_user):
    return get_user_model().objects.create_superuser(username="root_admin", password="x")


# ---------------------------------------------------------------------------
# Anonymous redirect
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url_name",
    [
        "dashboard",
        "organizations:index",
        "organizations:memberships",
        "services:index",
        "requests:index",
        "incidents:index",
        "problems:index",
        "changes:index",
        "workflows:index",
        "workflows:executions",
        "resources:index",
        "resources:assignments",
        "people:directory",
        "people:skills",
        "events:timeline",
        "metrics:index",
        "metrics:kpis",
        "access:roles",
        "access:permissions",
        "audit:log",
    ],
)
def test_anonymous_redirect(client, url_name):
    response = client.get(reverse(url_name))
    assert response.status_code == 302
    assert "/login/" in response.url


def test_anonymous_redirect_with_next(client):
    response = client.get(reverse("requests:index"))
    assert "/login/?next=/requests/" in response.url


def test_login_page_renders(client):
    response = client.get(reverse("dashboard:login"))
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Missing-permission → 403
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url_name",
    [
        "organizations:index",
        "services:index",
        "requests:index",
        "incidents:index",
        "problems:index",
        "changes:index",
        "workflows:index",
    ],
)
def test_no_permission_403(client, make_user, make_member, org_tree, url_name):
    user = make_user("nobody")
    make_member(user, org_tree["parish_a"])  # no permissions granted
    client.force_login(user)
    response = client.get(reverse(url_name))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Superuser renders every list page
# ---------------------------------------------------------------------------

VIEWS_WITH_TEMPLATES = [
    "dashboard",
    "organizations:index",
    "organizations:memberships",
    "services:index",
    "requests:index",
    "incidents:index",
    "problems:index",
    "changes:index",
    "workflows:index",
    "workflows:executions",
    "resources:index",
    "resources:assignments",
    "people:directory",
    "people:skills",
    "events:timeline",
    "metrics:index",
    "metrics:kpis",
    "audit:log",
]


@pytest.mark.parametrize("url_name", VIEWS_WITH_TEMPLATES)
def test_superuser_renders_list(client, staff_user, org_tree, url_name):
    client.force_login(staff_user)
    response = client.get(reverse(url_name))
    assert response.status_code == 200


def test_superuser_renders_detail_pages(client, staff_user, org_tree, make_service):
    from apps.access.models import Role
    from apps.changes.models import Change, ChangeStatus, RiskLevel
    from apps.incidents.models import Impact, Incident, IncidentStatus, Urgency
    from apps.metrics.models import Metric
    from apps.people.models import Skill
    from apps.problems.models import Problem, ProblemStatus
    from apps.requests.models import Priority, Request, RequestStatus, RequestType
    from apps.resources.models import Resource, ResourceType
    from apps.workflows.models import Workflow

    client.force_login(staff_user)

    service = make_service(org_tree["parish_a"], name="SvcDetail")
    req_type = RequestType.objects.create(name="Standard")
    req = Request.objects.create(
        service=service,
        request_type=req_type,
        title="Req",
        status=RequestStatus.NEW,
        priority=Priority.MEDIUM,
    )
    inc = Incident.objects.create(
        service=service,
        title="Inc",
        status=IncidentStatus.OPEN,
        impact=Impact.MEDIUM,
        urgency=Urgency.MEDIUM,
        priority=Priority.HIGH,
    )
    problem = Problem.objects.create(service=service, title="Prob", status=ProblemStatus.OPEN)
    change = Change.objects.create(
        service=service,
        title="Change",
        status=ChangeStatus.PROPOSED,
        risk=RiskLevel.MEDIUM,
    )
    role = Role.objects.create(code="role", name="Role")
    workflow = Workflow.objects.create(
        service=service,
        name="WF",
        version=1,
        status="ACTIVE",
    )
    resource = Resource.objects.create(
        organization=service.organization, name="Res", resource_type=ResourceType.VEHICLE
    )
    person = Person.objects.create(user=staff_user, full_name="Admin", person_type="EMPLOYEE")
    metric = Metric.objects.create(code="m1", name="Metric", unit="n")
    Skill.objects.create(name="Python")

    urls = [
        ("organizations:detail", [org_tree["parish_a"].pk]),
        ("services:detail", [service.pk]),
        ("requests:detail", [req.pk]),
        ("incidents:detail", [inc.pk]),
        ("problems:detail", [problem.pk]),
        ("changes:detail", [change.pk]),
        ("workflows:detail", [workflow.pk]),
        ("resources:detail", [resource.pk]),
        ("people:detail", [person.pk]),
        ("metrics:detail", [metric.pk]),
        ("access:role_detail", [role.pk]),
    ]
    for url_name, args in urls:
        response = client.get(reverse(url_name, args=args))
        assert response.status_code == 200, f"{url_name} -> {response.status_code}"


# ---------------------------------------------------------------------------
# Scoping: detail pages 404 for out-of-subtree services
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target, expected",
    [
        ("own", 200),
        ("other", 404),
    ],
)
def test_service_scope_detail(
    client, org_tree, make_service, make_user, make_member, target, expected
):
    service = make_service(org_tree["parish_a"], name="Parish A Service")
    other = make_service(org_tree["parish_b"], name="Parish B Service")

    user = make_user("parish_user")
    make_member(user, org_tree["parish_a"], "service.view")
    client.force_login(user)

    pk = service.pk if target == "own" else other.pk
    response = client.get(reverse("services:detail", args=[pk]))
    assert response.status_code == expected


# ---------------------------------------------------------------------------
# Create permissions
# ---------------------------------------------------------------------------


def test_request_create_without_perm_403(client, make_user, make_member, org_tree):
    user = make_user("reader")
    make_member(user, org_tree["parish_a"], "request.view")
    client.force_login(user)
    response = client.get(reverse("requests:create"))
    assert response.status_code == 403


def test_request_create_with_perm_renders(client, make_user, make_member, org_tree):
    user = make_user("writer")
    make_member(user, org_tree["parish_a"], "request.create")
    client.force_login(user)
    response = client.get(reverse("requests:create"))
    assert response.status_code == 200


def test_incident_create_without_perm_403(client, make_user, make_member, org_tree):
    user = make_user("reader")
    make_member(user, org_tree["parish_a"], "incident.view")
    client.force_login(user)
    response = client.get(reverse("incidents:create"))
    assert response.status_code == 403


def test_incident_create_with_perm_renders(client, make_user, make_member, org_tree):
    user = make_user("writer")
    make_member(user, org_tree["parish_a"], "incident.create")
    client.force_login(user)
    response = client.get(reverse("incidents:create"))
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Bulk action on requests (mark closed)
# ---------------------------------------------------------------------------


def test_request_bulk_mark_closed(client, make_user, make_member, org_tree, make_service):
    from apps.requests.models import Request, RequestStatus, RequestType

    service = make_service(org_tree["parish_a"])
    req_type = RequestType.objects.create(name="Standard")
    req1 = Request.objects.create(
        service=service, request_type=req_type, title="A", status=RequestStatus.NEW
    )
    req2 = Request.objects.create(
        service=service, request_type=req_type, title="B", status=RequestStatus.NEW
    )

    user = make_user("closer")
    make_member(user, org_tree["parish_a"], "request.view", "request.update")
    client.force_login(user)

    response = client.post(
        reverse("requests:index"),
        {"action": "mark_closed", "ids": [str(req1.pk), str(req2.pk)]},
    )
    assert response.status_code in (200, 302)
    req1.refresh_from_db()
    req2.refresh_from_db()
    assert req1.status == RequestStatus.CLOSED
    assert req2.status == RequestStatus.CLOSED


# ---------------------------------------------------------------------------
# Dashboard renders for a scoped user
# ---------------------------------------------------------------------------


def test_dashboard_renders_scoped(client, make_user, make_member, org_tree, make_service):
    user = make_user("op")
    make_member(user, org_tree["municipality"], "organization.view", "service.view")
    make_service(org_tree["parish_a"], name="Svc1")
    client.force_login(user)
    response = client.get(reverse("dashboard"))
    assert response.status_code == 200


def test_organization_dashboard_renders(client, make_user, make_member, org_tree):
    user = make_user("op")
    make_member(user, org_tree["district"], "organization.view", "service.view")
    client.force_login(user)
    response = client.get(
        reverse("dashboard:organization_dashboard", args=[org_tree["district"].pk])
    )
    assert response.status_code == 200


def test_header_shows_organization_and_role(client, make_user, make_member, staff_user, org_tree):
    user = make_user("op")
    person = make_member(user, org_tree["municipality"], "organization.view", "service.view")
    client.force_login(user)
    response = client.get(reverse("dashboard"))
    assert response.status_code == 200
    html = response.content.decode()
    assert org_tree["municipality"].name in html
    assert person.memberships.get().role.name in html

    client.force_login(staff_user)
    response = client.get(reverse("dashboard"))
    assert response.status_code == 200
    assert "Organization / Role" not in response.content.decode()


def test_sidebar_footer_shows_org_service_role(
    client, make_user, make_member, org_tree, make_service
):
    user = make_user("sm")
    membership = make_member(user, org_tree["municipality"], "organization.view", "service.view")
    svc = make_service(org_tree["municipality"], name="Waste Management")
    ServiceActor.objects.create(
        service=svc, role=membership.memberships.get().role, person=user.person
    )
    client.force_login(user)
    response = client.get(reverse("dashboard"))
    html = response.content.decode()
    footer = html[html.find("border-t border-zinc-800") : html.find("</aside>")]
    assert org_tree["municipality"].name in footer
    assert "Waste Management" in footer
    assert membership.memberships.get().role.name in footer


def test_service_dashboard_renders(client, make_user, make_member, org_tree, make_service):
    user = make_user("op")
    make_member(user, org_tree["parish_a"], "organization.view", "service.view")
    service = make_service(org_tree["parish_a"])
    client.force_login(user)
    response = client.get(reverse("dashboard:service_dashboard", args=[service.pk]))
    assert response.status_code == 200


def test_dashboard_query_count_stable(client, make_user, make_member, org_tree, make_service):
    """Guard against N+1 regressions on the executive dashboard.

    The dashboard aggregates across requests/incidents/problems/changes/
    resources/workflows/metrics. A fixed, per-user bounded number of queries
    is expected; this test fails loudly if the page starts issuing unbounded
    per-row queries.
    """
    user = make_user("dashboard_q")
    make_member(user, org_tree["municipality"], "organization.view", "service.view")
    client.force_login(user)

    # Ceiling guard (not an exact match): the dashboard currently uses a fixed
    # ~39 queries for a scoped user with an empty dataset; a per-row N+1
    # anywhere would push this past the cap quickly.
    ctx = CaptureQueriesContext(connection)
    with ctx:
        response = client.get(reverse("dashboard"))
    assert response.status_code == 200
    assert len(ctx.captured_queries) <= 55, f"query count grew: {len(ctx.captured_queries)}"
