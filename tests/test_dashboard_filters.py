"""Dashboard "drill down" links must reproduce what they count.

Regression test: stat cards on the service/organization/executive dashboards
used to link to the plain, unfiltered list view, so a service dashboard
showing "Open Requests: 4" would land on a list showing every request in the
system. Each card's link must carry the same service/status scoping used to
compute its number.
"""

import re

import pytest
from django.urls import reverse

from apps.organizations.models import Organization
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
    role = make_role(code="svcmgr", permissions=["service.view", "request.view", "request.create"])
    _member(make_person, make_membership, user, org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    Request.objects.create(
        service=service, request_type=request_type, title="Open on target", status=RequestStatus.NEW
    )
    Request.objects.create(
        service=service,
        request_type=request_type,
        title="Closed on target",
        status=RequestStatus.CLOSED,
    )
    Request.objects.create(
        service=other_service,
        request_type=request_type,
        title="Open on other",
        status=RequestStatus.NEW,
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

    Request.objects.create(
        service=alpha, request_type=request_type, title="Alpha request", status=RequestStatus.NEW
    )
    Request.objects.create(
        service=alpha_two,
        request_type=request_type,
        title="Alpha Two request",
        status=RequestStatus.NEW,
    )

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

    Request.objects.create(
        service=service, request_type=request_type, title="New one", status=RequestStatus.NEW
    )
    Request.objects.create(
        service=service,
        request_type=request_type,
        title="In progress one",
        status=RequestStatus.IN_PROGRESS,
    )
    Request.objects.create(
        service=service, request_type=request_type, title="Closed one", status=RequestStatus.CLOSED
    )

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
    # A <select name="service"> populated with the visible services.
    assert '<select name="service"' in body
    assert f'<option value="{alpha.pk}"' in body
    assert f'<option value="{beta.pk}"' in body
    assert ">Alpha<" in body
    assert ">Beta<" in body
    # The old free-text input for this column must be gone.
    assert 'name="service" placeholder' not in body


def test_requests_service_column_filter_scoped_to_visible_services(
    client,
    make_user,
    make_person,
    make_role,
    make_membership,
    grant_service,
    org_tree,
    make_service,
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
    """The drawer's Service dropdown uses the same exact ``?service=<pk>`` param
    as dashboard links, so a dashboard selection is reflected back in the form."""
    alpha = make_service(org_tree["municipality"], "Alpha")
    alpha_two = make_service(org_tree["municipality"], "Alpha Two")
    user = make_user()
    role = make_role(code="svcmgr5", permissions=["service.view", "request.view"])
    _member(make_person, make_membership, user, org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    Request.objects.create(
        service=alpha, request_type=request_type, title="Alpha req", status=RequestStatus.NEW
    )
    Request.objects.create(
        service=alpha_two,
        request_type=request_type,
        title="Alpha Two req",
        status=RequestStatus.NEW,
    )

    body = client.get(reverse("requests:index"), {"service": alpha.pk}).content.decode()
    assert "Alpha req" in body
    assert "Alpha Two req" not in body
    assert '<select name="service"' in body
    assert f'<option value="{alpha.pk}" selected>' in body


def _org_viewer(make_user, make_person, make_role, make_membership, org):
    """A user whose membership sees ``org`` and its whole subtree."""
    user = make_user()
    role = make_role(code=f"orgview{org.pk}", permissions=["organization.view"])
    _member(make_person, make_membership, user, org, role)
    return user


def _row_id_order(body: str) -> list[int]:
    """Ids of the organizations rendered as table rows, in render order."""
    return [int(pk) for pk in re.findall(r'href="/organizations/(\d+)/"', body)]


def _row_ids(body: str) -> set[int]:
    """Ids of the organizations actually rendered as table rows.

    Asserting on names is ambiguous once the table carries a dropdown of every
    organization the user may see — rows link to ``organizations:detail``, so
    match on those hrefs instead.
    """
    return set(_row_id_order(body))


def test_organizations_parent_filter_is_a_dropdown(
    client, make_user, make_person, make_role, make_membership, org_tree
):
    """The "Parent" filter in the drawer is a <select> of the organizations the
    user may see, not a free-text search box."""
    user = _org_viewer(make_user, make_person, make_role, make_membership, org_tree["district"])
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("organizations:index")).content.decode()
    assert '<select name="parent"' in body
    for org in org_tree.values():
        assert f'<option value="{org.pk}"' in body
    assert ">Municipality<" in body
    # The old free-text input for this column must be gone.
    assert 'name="parent" placeholder' not in body


def test_organizations_parent_filter_matches_whole_subtree(
    client, make_user, make_person, make_role, make_membership, org_tree
):
    """Selecting a parent returns that organization plus every descendant."""
    user = _org_viewer(make_user, make_person, make_role, make_membership, org_tree["district"])
    assert client.login(username=user.username, password="x")

    body = client.get(
        reverse("organizations:index"), {"parent": org_tree["district"].pk}
    ).content.decode()
    assert _row_ids(body) == {org.pk for org in org_tree.values()}

    body = client.get(
        reverse("organizations:index"), {"parent": org_tree["municipality"].pk}
    ).content.decode()
    assert _row_ids(body) == {
        org_tree["municipality"].pk,
        org_tree["parish_a"].pk,
        org_tree["parish_b"].pk,
    }


def test_organizations_parent_filter_is_exact_not_substring(
    client, make_user, make_person, make_role, make_membership, org_tree
):
    """The dropdown's own param is a pk, so it must not cross-match on name."""
    user = _org_viewer(make_user, make_person, make_role, make_membership, org_tree["district"])
    assert client.login(username=user.username, password="x")

    body = client.get(
        reverse("organizations:index"), {"parent": org_tree["parish_a"].pk}
    ).content.decode()
    assert _row_ids(body) == {org_tree["parish_a"].pk}


def test_organizations_parent_filter_scoped_to_visible_orgs(
    client, make_user, make_person, make_role, make_membership, org_tree
):
    """A membership on a parish only sees its own subtree as filter options,
    not every organization in the system."""
    user = _org_viewer(make_user, make_person, make_role, make_membership, org_tree["parish_a"])
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("organizations:index")).content.decode()
    assert f'<option value="{org_tree["parish_a"].pk}"' in body
    assert f'<option value="{org_tree["municipality"].pk}"' not in body
    assert f'<option value="{org_tree["district"].pk}"' not in body


def test_organizations_parent_filter_ignores_unresolvable_pk(
    client, make_user, make_person, make_role, make_membership, org_tree
):
    """An unknown, out-of-scope or non-numeric pk is ignored — never a 500."""
    user = _org_viewer(make_user, make_person, make_role, make_membership, org_tree["parish_a"])
    assert client.login(username=user.username, password="x")

    for value in ("999999", "abc", ""):
        response = client.get(reverse("organizations:index"), {"parent": value})
        assert response.status_code == 200
        assert _row_ids(response.content.decode()) == {org_tree["parish_a"].pk}


def test_organizations_type_filter_still_applies_alongside_parent(
    client, make_user, make_person, make_role, make_membership, org_tree
):
    """The pre-existing type filter is unaffected by the new Parent column."""
    user = _org_viewer(make_user, make_person, make_role, make_membership, org_tree["district"])
    assert client.login(username=user.username, password="x")

    body = client.get(
        reverse("organizations:index"),
        {"parent": org_tree["municipality"].pk, "organization_type": "PARISH"},
    ).content.decode()
    assert _row_ids(body) == {org_tree["parish_a"].pk, org_tree["parish_b"].pk}


def _org_path_tree():
    """A root/child pair whose *names* deliberately disagree with path order.

    Name order would be Alpha, Zulu; path order (``root`` < ``root.kid``) is
    Zulu, Alpha — so any assertion on row order proves which one was used.
    """
    root = Organization.objects.create(name="Zulu", code="root", organization_type="COUNTRY")
    child = Organization.objects.create(
        name="Alpha", code="kid", organization_type="DISTRICT", parent=root
    )
    return root, child


def test_organizations_default_sort_is_path_not_name(
    client, make_user, make_person, make_role, make_membership
):
    """Rows are ordered by ``path`` (caminho), so a parent always precedes its
    children instead of sorting alphabetically by name."""
    root, child = _org_path_tree()
    user = make_user()
    role = make_role(code="pathsort", permissions=["organization.view"])
    _member(make_person, make_membership, user, root, role)
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("organizations:index")).content.decode()
    assert _row_id_order(body) == [root.pk, child.pk]


def test_organizations_path_sort_is_toggleable(
    client, make_user, make_person, make_role, make_membership
):
    """Clicking the Path header reverses to ``-path``, deepest first."""
    root, child = _org_path_tree()
    user = make_user()
    role = make_role(code="pathdesc", permissions=["organization.view"])
    _member(make_person, make_membership, user, root, role)
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("organizations:index"), {"sort": "-path"}).content.decode()
    assert _row_id_order(body) == [child.pk, root.pk]


def test_organizations_path_sort_puts_blank_paths_first(client, make_user):
    """An unset path sorts to the top, not the bottom.

    PostgreSQL defaults to NULLS LAST when ascending, so this needs explicit
    NULLS FIRST — declared via ``Column(nulls_first=True)``. A superuser is used
    because a NULL path cannot fall inside any membership's subtree, so it would
    be invisible to a scoped user rather than merely sorted differently.
    """
    root, child = _org_path_tree()
    Organization.objects.filter(pk=child.pk).update(path=None)

    user = make_user()
    user.is_superuser = True
    user.save()
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("organizations:index")).content.decode()
    assert _row_id_order(body) == [child.pk, root.pk]


def test_organizations_descending_sort_on_plain_column(
    client, make_user, make_person, make_role, make_membership
):
    """Reversing a column that did *not* opt into ``nulls_first`` still works.

    Regression: the sort expression has to pass ``None`` rather than ``False`` for
    null placement, because Django rejects ``nulls_first=False``. Getting that
    wrong breaks ``?sort=-name`` on every table in the project.
    """
    root, child = _org_path_tree()
    user = make_user()
    role = make_role(code="namdesc", permissions=["organization.view"])
    _member(make_person, make_membership, user, root, role)
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("organizations:index"), {"sort": "name"}).content.decode()
    assert _row_id_order(body) == [child.pk, root.pk]

    body = client.get(reverse("organizations:index"), {"sort": "-name"}).content.decode()
    assert _row_id_order(body) == [root.pk, child.pk]


def test_incidents_active_status_pseudo_filter(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    """``?status=ACTIVE`` means "not resolved/closed" for incidents.

    Incident ``OPEN`` is a real status, so the dashboard's "Active Incidents"
    counter cannot use it; the pseudo-filter keeps the number and the list in
    agreement.
    """
    from apps.incidents.models import Incident, IncidentStatus

    service = make_service(org_tree["municipality"], "Incidents Svc")
    for title, status in (
        ("Inc Open", IncidentStatus.OPEN),
        ("Inc Investigating", IncidentStatus.INVESTIGATING),
        ("Inc Resolved", IncidentStatus.RESOLVED),
        ("Inc Closed", IncidentStatus.CLOSED),
    ):
        Incident.objects.create(service=service, title=title, status=status)

    user = make_user()
    role = make_role(code="incactive", permissions=["incident.view"])
    _member(make_person, make_membership, user, org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("incidents:index"), {"status": "ACTIVE"}).content.decode()
    assert "Inc Open" in body
    assert "Inc Investigating" in body
    assert "Inc Resolved" not in body
    assert "Inc Closed" not in body


def test_problems_active_status_pseudo_filter(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    from apps.problems.models import Problem, ProblemStatus

    service = make_service(org_tree["municipality"], "Problems Svc")
    for title, status in (
        ("Prob Open", ProblemStatus.OPEN),
        ("Prob Diagnosed", ProblemStatus.DIAGNOSED),
        ("Prob In Progress", ProblemStatus.IN_PROGRESS),
        ("Prob Resolved", ProblemStatus.RESOLVED),
        ("Prob Closed", ProblemStatus.CLOSED),
    ):
        Problem.objects.create(service=service, title=title, status=status)

    user = make_user()
    role = make_role(code="probactive", permissions=["problem.view"])
    _member(make_person, make_membership, user, org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("problems:index"), {"status": "ACTIVE"}).content.decode()
    assert "Prob Open" in body
    assert "Prob Diagnosed" in body
    assert "Prob In Progress" in body
    assert "Prob Resolved" not in body
    assert "Prob Closed" not in body


def test_changes_active_status_pseudo_filter(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    from apps.changes.models import Change, ChangeStatus

    service = make_service(org_tree["municipality"], "Changes Svc")
    for title, status in (
        ("Chg Draft", ChangeStatus.DRAFT),
        ("Chg Approved", ChangeStatus.APPROVED),
        ("Chg Implemented", ChangeStatus.IMPLEMENTED),
        ("Chg Rejected", ChangeStatus.REJECTED),
        ("Chg Rolled Back", ChangeStatus.ROLLED_BACK),
    ):
        Change.objects.create(service=service, title=title, status=status)

    user = make_user()
    role = make_role(code="chgactive", permissions=["change.view"])
    _member(make_person, make_membership, user, org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("changes:index"), {"status": "ACTIVE"}).content.decode()
    assert "Chg Draft" in body
    assert "Chg Approved" in body
    assert "Chg Implemented" not in body
    assert "Chg Rejected" not in body
    assert "Chg Rolled Back" not in body


def test_service_dashboard_active_incident_link_is_scoped(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    from apps.incidents.models import Incident, IncidentStatus

    service = make_service(org_tree["municipality"], "Target")
    other = make_service(org_tree["municipality"], "Other")
    Incident.objects.create(
        service=service, title="Target inc", status=IncidentStatus.INVESTIGATING
    )
    Incident.objects.create(service=other, title="Other inc", status=IncidentStatus.OPEN)

    user = make_user()
    role = make_role(code="svcinc", permissions=["service.view", "incident.view"])
    _member(make_person, make_membership, user, org_tree["municipality"], role)
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("dashboard:service_dashboard", args=[service.pk])).content.decode()
    assert f"/incidents/?service={service.pk}&amp;status=ACTIVE" in body

    listing = client.get(
        reverse("incidents:index"), {"service": service.pk, "status": "ACTIVE"}
    ).content.decode()
    assert "Target inc" in listing
    assert "Other inc" not in listing


def test_organization_dashboard_links_use_org_subtree_filter(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    municipality = org_tree["municipality"]
    make_service(org_tree["parish_a"], "Parish Service")
    make_service(org_tree["parish_b"], "Other Parish Service")

    user = make_user()
    role = make_role(code="orgdash", permissions=["organization.view", "service.view"])
    _member(make_person, make_membership, user, municipality, role)
    assert client.login(username=user.username, password="x")

    body = client.get(
        reverse("dashboard:organization_dashboard", args=[municipality.pk])
    ).content.decode()
    assert f"/services/?organization={municipality.pk}" in body
    assert f"/requests/?organization={municipality.pk}&amp;status=OPEN" in body
    assert f"/incidents/?organization={municipality.pk}&amp;status=ACTIVE" in body
    assert f"/problems/?organization={municipality.pk}" in body
    assert f"/changes/?organization={municipality.pk}" in body
    assert f"/service-offerings/executions/?organization={municipality.pk}" in body
    assert f"/organizations/?parent={municipality.pk}" in body
    assert f"/people/?organization={municipality.pk}" in body

    # The services list applies the same subtree scope used by the counter.
    listing = client.get(
        reverse("services:index"), {"organization": municipality.pk}
    ).content.decode()
    assert "Parish Service" in listing
    assert "Other Parish Service" in listing


# ---------------------------------------------------------------------------
# Organization + Service drawer filters on the work lists
# ---------------------------------------------------------------------------


def _filter_member(make_user, make_person, make_role, make_membership, org, permissions, code):
    user = make_user()
    role = make_role(code=code, permissions=permissions)
    _member(make_person, make_membership, user, org, role)
    return user


def test_requests_organization_filter_is_subtree_dropdown(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service, request_type
):
    service_a = make_service(org_tree["parish_a"], "Waste A")
    service_b = make_service(org_tree["parish_b"], "Waste B")
    Request.objects.create(
        service=service_a, request_type=request_type, title="Req A", status=RequestStatus.NEW
    )
    Request.objects.create(
        service=service_b, request_type=request_type, title="Req B", status=RequestStatus.NEW
    )

    municipality = org_tree["municipality"]
    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        municipality,
        ["request.view", "service.view", "organization.view"],
        "reqorg",
    )
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("requests:index")).content.decode()
    assert '<select name="organization"' in body
    assert '<select name="service"' in body
    assert body.index('name="organization"') < body.index('name="service"')
    for org in (municipality, org_tree["parish_a"], org_tree["parish_b"]):
        assert f'<option value="{org.pk}"' in body

    body = client.get(reverse("requests:index"), {"organization": municipality.pk}).content.decode()
    assert "Req A" in body
    assert "Req B" in body
    assert f'<option value="{municipality.pk}" selected>' in body

    body = client.get(
        reverse("requests:index"), {"organization": org_tree["parish_a"].pk}
    ).content.decode()
    assert "Req A" in body
    assert "Req B" not in body


def test_requests_organization_filter_ignores_unresolvable_pk(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service, request_type
):
    service = make_service(org_tree["parish_a"], "Waste A")
    Request.objects.create(
        service=service, request_type=request_type, title="Req A", status=RequestStatus.NEW
    )
    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["parish_a"],
        ["request.view", "service.view", "organization.view"],
        "reqorgbad",
    )
    assert client.login(username=user.username, password="x")

    for value in ("999999", "abc", ""):
        response = client.get(reverse("requests:index"), {"organization": value})
        assert response.status_code == 200
        assert "Req A" in response.content.decode()


def test_requests_organization_and_service_filters_combine(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service, request_type
):
    service_a = make_service(org_tree["parish_a"], "Waste A")
    service_b = make_service(org_tree["parish_b"], "Waste B")
    Request.objects.create(
        service=service_a, request_type=request_type, title="Req A", status=RequestStatus.NEW
    )
    Request.objects.create(
        service=service_b, request_type=request_type, title="Req B", status=RequestStatus.NEW
    )
    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["municipality"],
        ["request.view", "service.view", "organization.view"],
        "reqorgsvc",
    )
    assert client.login(username=user.username, password="x")

    body = client.get(
        reverse("requests:index"),
        {"organization": org_tree["municipality"].pk, "service": service_a.pk},
    ).content.decode()
    assert "Req A" in body
    assert "Req B" not in body


def test_incidents_organization_filter_matches_subtree(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    from apps.incidents.models import Incident

    service_a = make_service(org_tree["parish_a"], "Inc Svc A")
    service_b = make_service(org_tree["parish_b"], "Inc Svc B")
    Incident.objects.create(service=service_a, title="Inc A")
    Incident.objects.create(service=service_b, title="Inc B")
    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["municipality"],
        ["incident.view", "organization.view"],
        "incorg",
    )
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("incidents:index")).content.decode()
    assert '<select name="organization"' in body
    assert '<select name="service"' in body
    assert body.index('name="organization"') < body.index('name="service"')

    body = client.get(
        reverse("incidents:index"), {"organization": org_tree["municipality"].pk}
    ).content.decode()
    assert "Inc A" in body
    assert "Inc B" in body

    body = client.get(
        reverse("incidents:index"), {"organization": org_tree["parish_a"].pk}
    ).content.decode()
    assert "Inc A" in body
    assert "Inc B" not in body


def test_problems_organization_filter_matches_subtree(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    from apps.problems.models import Problem

    service_a = make_service(org_tree["parish_a"], "Prob Svc A")
    service_b = make_service(org_tree["parish_b"], "Prob Svc B")
    Problem.objects.create(service=service_a, title="Prob A")
    Problem.objects.create(service=service_b, title="Prob B")
    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["municipality"],
        ["problem.view", "organization.view"],
        "proborg",
    )
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("problems:index")).content.decode()
    assert '<select name="organization"' in body
    assert '<select name="service"' in body
    assert body.index('name="organization"') < body.index('name="service"')

    body = client.get(
        reverse("problems:index"), {"organization": org_tree["municipality"].pk}
    ).content.decode()
    assert "Prob A" in body
    assert "Prob B" in body

    body = client.get(
        reverse("problems:index"), {"organization": org_tree["parish_a"].pk}
    ).content.decode()
    assert "Prob A" in body
    assert "Prob B" not in body


def test_changes_organization_filter_covers_service_and_direct_org(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    from apps.changes.models import Change

    service = make_service(org_tree["parish_a"], "Change Svc")
    Change.objects.create(service=service, title="Change via service")
    Change.objects.create(organization=org_tree["parish_b"], title="Change direct org")

    user = make_user()
    user.is_superuser = True
    user.save()
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("changes:index")).content.decode()
    assert '<select name="organization"' in body
    assert '<select name="service"' in body
    assert body.index('name="organization"') < body.index('name="service"')

    body = client.get(
        reverse("changes:index"), {"organization": org_tree["municipality"].pk}
    ).content.decode()
    assert "Change via service" in body
    assert "Change direct org" in body

    body = client.get(
        reverse("changes:index"), {"organization": org_tree["parish_a"].pk}
    ).content.decode()
    assert "Change via service" in body
    assert "Change direct org" not in body

    body = client.get(
        reverse("changes:index"), {"organization": org_tree["parish_b"].pk}
    ).content.decode()
    assert "Change via service" not in body
    assert "Change direct org" in body


def test_executions_organization_filter_matches_subtree(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    from apps.offerings.models import OfferingExecution, ServiceOffering

    service_a = make_service(org_tree["parish_a"], "Exec Svc A")
    service_b = make_service(org_tree["parish_b"], "Exec Svc B")
    offering_a = ServiceOffering.objects.create(
        service=service_a, name="WF A", version=1, status="ACTIVE"
    )
    offering_b = ServiceOffering.objects.create(
        service=service_b, name="WF B", version=1, status="ACTIVE"
    )
    OfferingExecution.objects.create(offering=offering_a)
    OfferingExecution.objects.create(offering=offering_b)

    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["municipality"],
        ["offering.view", "service.view", "organization.view"],
        "execorg",
    )
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("offerings:executions")).content.decode()
    assert '<select name="organization"' in body
    assert '<select name="service"' in body
    assert body.index('name="organization"') < body.index('name="service"')

    body = client.get(
        reverse("offerings:executions"), {"organization": org_tree["municipality"].pk}
    ).content.decode()
    assert "WF A" in body
    assert "WF B" in body

    body = client.get(
        reverse("offerings:executions"), {"organization": org_tree["parish_a"].pk}
    ).content.decode()
    assert "WF A" in body
    assert "WF B" not in body


# ---------------------------------------------------------------------------
# Header scope (session) flows into list views
# ---------------------------------------------------------------------------


def _set_header_scope(client, org, service=None, role="ctxrole"):
    session = client.session
    session["org_context"] = {"pk": org.pk}
    if service is None:
        session.pop("service_context", None)
    else:
        session["service_context"] = {"pk": service.pk, "role": role}
    session.save()


def test_requests_list_follows_header_service_scope(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service, request_type
):
    """Selecting a service in the header scopes Requests without a URL param."""
    target = make_service(org_tree["municipality"], "Cemiterios")
    other = make_service(org_tree["municipality"], "Waste")
    Request.objects.create(
        service=target, request_type=request_type, title="Cemiterios req", status=RequestStatus.NEW
    )
    Request.objects.create(
        service=other, request_type=request_type, title="Waste req", status=RequestStatus.NEW
    )
    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["municipality"],
        ["request.view", "service.view", "organization.view"],
        "ctxreq",
    )
    assert client.login(username=user.username, password="x")
    _set_header_scope(client, org_tree["municipality"], target)

    body = client.get(reverse("requests:index")).content.decode()
    assert "Cemiterios req" in body
    assert "Waste req" not in body
    # The drawer reflects the effective scope instead of showing "All".
    assert f'<option value="{target.pk}" selected>' in body


def test_explicit_service_param_overrides_header_scope(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service, request_type
):
    scoped = make_service(org_tree["municipality"], "Scoped")
    explicit = make_service(org_tree["municipality"], "Explicit")
    Request.objects.create(
        service=scoped, request_type=request_type, title="Scoped req", status=RequestStatus.NEW
    )
    Request.objects.create(
        service=explicit, request_type=request_type, title="Explicit req", status=RequestStatus.NEW
    )
    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["municipality"],
        ["request.view", "service.view", "organization.view"],
        "ctxoverride",
    )
    assert client.login(username=user.username, password="x")
    _set_header_scope(client, org_tree["municipality"], scoped)

    body = client.get(reverse("requests:index"), {"service": explicit.pk}).content.decode()
    assert "Explicit req" in body
    assert "Scoped req" not in body


def test_empty_service_param_clears_header_scope(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service, request_type
):
    scoped = make_service(org_tree["municipality"], "Scoped")
    other = make_service(org_tree["municipality"], "Other")
    Request.objects.create(
        service=scoped, request_type=request_type, title="Scoped req", status=RequestStatus.NEW
    )
    Request.objects.create(
        service=other, request_type=request_type, title="Other req", status=RequestStatus.NEW
    )
    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["municipality"],
        ["request.view", "service.view", "organization.view"],
        "ctxclear",
    )
    assert client.login(username=user.username, password="x")
    _set_header_scope(client, org_tree["municipality"], scoped)

    body = client.get(reverse("requests:index"), {"service": ""}).content.decode()
    assert "Scoped req" in body
    assert "Other req" in body
    assert f'<option value="{scoped.pk}" selected>' not in body


def test_header_service_scope_is_ignored_when_not_visible(
    client,
    make_user,
    make_person,
    make_role,
    make_membership,
    grant_service,
    org_tree,
    make_service,
    request_type,
):
    """A stale/forged session pk cannot filter (or leak) outside the user's scope."""
    allowed = make_service(org_tree["municipality"], "Allowed")
    hidden = make_service(org_tree["municipality"], "Hidden")
    Request.objects.create(
        service=allowed, request_type=request_type, title="Allowed req", status=RequestStatus.NEW
    )
    Request.objects.create(
        service=hidden, request_type=request_type, title="Hidden req", status=RequestStatus.NEW
    )
    user = make_user()
    role = make_role(code="ctxrestricted", permissions=["request.view", "service.view"])
    membership = _member(make_person, make_membership, user, org_tree["municipality"], role)
    grant_service(membership, allowed)
    assert client.login(username=user.username, password="x")
    _set_header_scope(client, org_tree["municipality"], hidden)

    body = client.get(reverse("requests:index")).content.decode()
    assert "Allowed req" in body
    assert "Hidden req" not in body


def test_requests_list_follows_header_organization_scope(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service, request_type
):
    service_a = make_service(org_tree["parish_a"], "Waste A")
    service_b = make_service(org_tree["parish_b"], "Waste B")
    Request.objects.create(
        service=service_a, request_type=request_type, title="Req A", status=RequestStatus.NEW
    )
    Request.objects.create(
        service=service_b, request_type=request_type, title="Req B", status=RequestStatus.NEW
    )
    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["municipality"],
        ["request.view", "service.view", "organization.view"],
        "ctxorg",
    )
    assert client.login(username=user.username, password="x")
    _set_header_scope(client, org_tree["parish_a"])

    body = client.get(reverse("requests:index")).content.decode()
    assert "Req A" in body
    assert "Req B" not in body
    assert f'<option value="{org_tree["parish_a"].pk}" selected>' in body

    # Explicit organization URL param wins over the header pick.
    body = client.get(
        reverse("requests:index"), {"organization": org_tree["municipality"].pk}
    ).content.decode()
    assert "Req A" in body
    assert "Req B" in body


def test_executions_list_follows_header_service_scope(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    from apps.offerings.models import OfferingExecution, ServiceOffering

    target = make_service(org_tree["municipality"], "Exec Target")
    other = make_service(org_tree["municipality"], "Exec Other")
    wf_target = ServiceOffering.objects.create(
        service=target, name="WF Target", version=1, status="ACTIVE"
    )
    wf_other = ServiceOffering.objects.create(
        service=other, name="WF Other", version=1, status="ACTIVE"
    )
    OfferingExecution.objects.create(offering=wf_target)
    OfferingExecution.objects.create(offering=wf_other)

    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["municipality"],
        ["offering.view", "service.view", "organization.view"],
        "ctxexec",
    )
    assert client.login(username=user.username, password="x")
    _set_header_scope(client, org_tree["municipality"], target)

    body = client.get(reverse("offerings:executions")).content.decode()
    assert "WF Target" in body
    assert "WF Other" not in body
    assert f'<option value="{target.pk}" selected>' in body


def test_offerings_list_follows_header_service_scope(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    from apps.offerings.models import ServiceOffering

    target = make_service(org_tree["municipality"], "Flow Svc Target")
    other = make_service(org_tree["municipality"], "Flow Svc Other")
    ServiceOffering.objects.create(
        service=target, name="Target Service Offering", version=1, status="ACTIVE"
    )
    ServiceOffering.objects.create(
        service=other, name="Other Service Offering", version=1, status="ACTIVE"
    )

    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["municipality"],
        ["offering.view", "service.view", "organization.view"],
        "ctxflow",
    )
    assert client.login(username=user.username, password="x")
    _set_header_scope(client, org_tree["municipality"], target)

    body = client.get(reverse("offerings:index")).content.decode()
    assert '<select name="service"' in body
    assert "Target Service Offering" in body
    assert "Other Service Offering" not in body
    assert f'<option value="{target.pk}" selected>' in body


def test_changes_list_follows_header_service_scope(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    from apps.changes.models import Change, ChangeStatus

    target = make_service(org_tree["municipality"], "Cemiterios Svc")
    other = make_service(org_tree["municipality"], "Waste Svc")
    Change.objects.create(service=target, title="Cemiterios change", status=ChangeStatus.DRAFT)
    Change.objects.create(service=other, title="Waste change", status=ChangeStatus.DRAFT)

    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["municipality"],
        ["change.view", "service.view", "organization.view"],
        "ctxchange",
    )
    assert client.login(username=user.username, password="x")
    _set_header_scope(client, org_tree["municipality"], target)

    body = client.get(reverse("changes:index")).content.decode()
    assert "Cemiterios change" in body
    assert "Waste change" not in body
    assert f'<option value="{target.pk}" selected>' in body


def test_resources_list_follows_header_service_scope(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    from apps.resources.models import Resource, ResourceType

    target = make_service(org_tree["municipality"], "Res Svc Target")
    other = make_service(org_tree["municipality"], "Res Svc Other")
    Resource.objects.create(
        service=target,
        organization=org_tree["municipality"],
        name="Target Resource",
        resource_type=ResourceType.VEHICLE,
    )
    Resource.objects.create(
        service=other,
        organization=org_tree["municipality"],
        name="Other Resource",
        resource_type=ResourceType.VEHICLE,
    )

    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["municipality"],
        ["resource.view", "service.view", "organization.view"],
        "ctxresource",
    )
    assert client.login(username=user.username, password="x")
    _set_header_scope(client, org_tree["municipality"], target)

    body = client.get(reverse("resources:index")).content.decode()
    assert "Target Resource" in body
    assert "Other Resource" not in body


def test_resources_header_service_prefills_its_organization(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    """A header-selected service shows its own organization in the drawer."""
    from apps.resources.models import Resource, ResourceType

    target = make_service(org_tree["parish_a"], "Prefill Svc")
    Resource.objects.create(
        service=target,
        organization=org_tree["parish_a"],
        name="Prefill Resource",
        resource_type=ResourceType.VEHICLE,
    )
    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["municipality"],
        ["resource.view", "service.view", "organization.view"],
        "resprefill",
    )
    assert client.login(username=user.username, password="x")
    _set_header_scope(client, org_tree["municipality"], target)

    body = client.get(reverse("resources:index")).content.decode()
    assert "Prefill Resource" in body
    assert f'<option value="{target.pk}" selected>' in body
    assert f'<option value="{org_tree["parish_a"].pk}" selected>' in body
    assert f'<option value="{org_tree["municipality"].pk}" selected>' not in body

    # Submitting the drawer as-is (both params explicit) yields the same rows.
    body = client.get(
        reverse("resources:index"),
        {"service": target.pk, "organization": org_tree["parish_a"].pk},
    ).content.decode()
    assert "Prefill Resource" in body


@pytest.mark.parametrize(
    "url_name, view_perm",
    [
        ("requests:index", "request.view"),
        ("incidents:index", "incident.view"),
        ("problems:index", "problem.view"),
        ("changes:index", "change.view"),
        ("resources:index", "resource.view"),
    ],
)
def test_lists_header_service_prefills_its_organization(
    client,
    make_user,
    make_person,
    make_role,
    make_membership,
    org_tree,
    make_service,
    url_name,
    view_perm,
):
    target = make_service(org_tree["parish_a"], "Prefill Any Svc")
    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["municipality"],
        [view_perm, "service.view", "organization.view"],
        f"prefill_{view_perm.split('.')[0]}",
    )
    assert client.login(username=user.username, password="x")
    _set_header_scope(client, org_tree["municipality"], target)

    body = client.get(reverse(url_name)).content.decode()
    assert f'<option value="{target.pk}" selected>' in body
    assert f'<option value="{org_tree["parish_a"].pk}" selected>' in body


def test_people_header_service_scopes_to_its_organization(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    """People have no service FK: a header service scopes them to its org subtree."""
    target = make_service(org_tree["parish_a"], "People Svc A")
    member_role = make_role(code="peoplemember")
    make_membership(make_person(name="Alice Parish A"), org_tree["parish_a"], member_role)
    make_membership(make_person(name="Bruno Parish B"), org_tree["parish_b"], member_role)

    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["municipality"],
        ["people.view", "service.view", "organization.view"],
        "peoplectx",
    )
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("people:directory")).content.decode()
    assert '<select name="organization"' in body
    assert "Alice Parish A" in body
    assert "Bruno Parish B" in body

    _set_header_scope(client, org_tree["municipality"], target)
    body = client.get(reverse("people:directory")).content.decode()
    assert "Alice Parish A" in body
    assert "Bruno Parish B" not in body
    assert f'<option value="{org_tree["parish_a"].pk}" selected>' in body

    # An explicit empty organization opts out of the header scope ("All").
    body = client.get(reverse("people:directory"), {"organization": ""}).content.decode()
    assert "Alice Parish A" in body
    assert "Bruno Parish B" in body

    # The drawer dropdown filters the subtree directly.
    body = client.get(
        reverse("people:directory"), {"organization": org_tree["parish_b"].pk}
    ).content.decode()
    assert "Alice Parish A" not in body
    assert "Bruno Parish B" in body


def test_resources_organization_filter_matches_direct_or_service_org(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    from apps.resources.models import Resource, ResourceType

    service = make_service(org_tree["parish_a"], "Resource Svc A")
    Resource.objects.create(
        service=service,
        organization=org_tree["parish_b"],
        name="Resource via service",
        resource_type=ResourceType.VEHICLE,
    )
    Resource.objects.create(
        organization=org_tree["parish_b"],
        name="Resource direct org",
        resource_type=ResourceType.VEHICLE,
    )

    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["municipality"],
        ["resource.view", "service.view", "organization.view"],
        "resorg",
    )
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("resources:index")).content.decode()
    assert '<select name="organization"' in body
    assert '<select name="service"' in body
    assert body.index('name="organization"') < body.index('name="service"')

    body = client.get(
        reverse("resources:index"), {"organization": org_tree["municipality"].pk}
    ).content.decode()
    assert "Resource via service" in body
    assert "Resource direct org" in body

    body = client.get(
        reverse("resources:index"), {"organization": org_tree["parish_a"].pk}
    ).content.decode()
    assert "Resource via service" in body
    assert "Resource direct org" not in body

    body = client.get(
        reverse("resources:index"), {"organization": org_tree["parish_b"].pk}
    ).content.decode()
    assert "Resource via service" in body
    assert "Resource direct org" in body


def test_resources_service_filter_is_dropdown(
    client, make_user, make_person, make_role, make_membership, org_tree, make_service
):
    from apps.resources.models import Resource, ResourceType

    target = make_service(org_tree["municipality"], "Res Dropdown Target")
    other = make_service(org_tree["municipality"], "Res Dropdown Other")
    Resource.objects.create(
        service=target,
        organization=org_tree["municipality"],
        name="Dropdown Target Resource",
        resource_type=ResourceType.VEHICLE,
    )
    Resource.objects.create(
        service=other,
        organization=org_tree["municipality"],
        name="Dropdown Other Resource",
        resource_type=ResourceType.VEHICLE,
    )

    user = _filter_member(
        make_user,
        make_person,
        make_role,
        make_membership,
        org_tree["municipality"],
        ["resource.view", "service.view", "organization.view"],
        "ressvc",
    )
    assert client.login(username=user.username, password="x")

    body = client.get(reverse("resources:index")).content.decode()
    assert '<select name="service"' in body
    assert f'<option value="{target.pk}"' in body
    assert f'<option value="{other.pk}"' in body

    body = client.get(reverse("resources:index"), {"service": target.pk}).content.decode()
    assert "Dropdown Target Resource" in body
    assert "Dropdown Other Resource" not in body
    assert f'<option value="{target.pk}" selected>' in body
