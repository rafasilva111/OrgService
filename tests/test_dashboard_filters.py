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
    # A <select name="service.name"> populated with the visible services.
    assert '<select name="service.name"' in body
    assert f'<option value="{alpha.pk}"' in body
    assert f'<option value="{beta.pk}"' in body
    assert ">Alpha<" in body
    assert ">Beta<" in body
    # The old free-text input for this column must be gone.
    assert 'name="service.name" placeholder' not in body


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
    """Submitting the dropdown's own param (``service.name``, the pk) filters
    by exact service id — no name-substring ambiguity."""
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

    body = client.get(reverse("requests:index"), {"service.name": alpha.pk}).content.decode()
    assert "Alpha req" in body
    assert "Alpha Two req" not in body


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
