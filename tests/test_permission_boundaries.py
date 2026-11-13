import pytest
from django.contrib.auth.models import Permission as DjangoPermission
from django.urls import reverse

from apps.access.models import GrantScope, GrantScopeType
from apps.changes.models import Change
from apps.incidents.views import IncidentCreateForm
from apps.metrics.models import KPI, SLA, Metric
from apps.metrics.views import _kpi_queryset, _metric_queryset, _sla_queryset
from apps.organizations.models import Organization
from apps.resources.models import Resource

pytestmark = pytest.mark.django_db


def test_incident_assignment_choices_follow_target_service(
    org_tree, make_user, make_person, make_role, make_membership, make_service
):
    service_a = make_service(org_tree["parish_a"], "A")
    make_service(org_tree["parish_b"], "B")
    manager = make_user()
    person = make_person(manager)
    role = make_role(permissions=["service.view", "incident.create", "incident.assign"])
    make_membership(person, org_tree["parish_a"], role)
    candidate = make_person(make_user())
    make_membership(candidate, org_tree["parish_a"], make_role(permissions=["incident.view"]))
    outsider = make_person(make_user())
    make_membership(outsider, org_tree["parish_b"], make_role(permissions=["incident.view"]))
    form = IncidentCreateForm(
        data={"service": service_a.pk, "assigned_to": outsider.pk}, user=manager
    )
    assert candidate in form.fields["assigned_to"].queryset
    assert outsider not in form.fields["assigned_to"].queryset
    assert not form.is_valid()
    assert "assigned_to" in form.errors


def test_membership_admin_requires_superuser(client, make_user):
    user = make_user()
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    permission = DjangoPermission.objects.get(codename="add_organizationmembership")
    user.user_permissions.add(permission)
    client.force_login(user)
    assert client.get(reverse("admin:organizations_organizationmembership_add")).status_code == 403


def test_service_scopes_do_not_reveal_sibling_or_org_only_resources(
    org_tree, make_user, make_person, make_role, make_membership, make_service
):
    user = make_user()
    person = make_person(user)
    service_a = make_service(org_tree["parish_a"], "A")
    service_b = make_service(org_tree["parish_a"], "B")
    role = make_role(permissions=["resource.view", "change.view"])
    make_membership(person, org_tree["parish_a"], role)
    for grant in role.rolepermissions.all():
        GrantScope.objects.create(
            role_permission=grant, scope_type=GrantScopeType.SPECIFIC_SERVICE, service=service_a
        )
    a = Resource.objects.create(name="Resource A", service=service_a, resource_type="MATERIAL")
    b = Resource.objects.create(name="Resource B", service=service_b, resource_type="MATERIAL")
    shared = Resource.objects.create(
        name="Shared", organization=org_tree["parish_a"], resource_type="MATERIAL"
    )
    change = Change.objects.create(title="Organization change", organization=org_tree["parish_a"])
    assert a in Resource.objects.visible_to(user, "resource.view")
    assert b not in Resource.objects.visible_to(user, "resource.view")
    assert shared not in Resource.objects.visible_to(user, "resource.view")
    assert change not in Change.objects.visible_to(user, "change.view")


def test_platform_metrics_require_unrestricted_root_scope(
    org_tree, make_user, make_person, make_role, make_membership, make_service, grant_service
):
    user = make_user()
    person = make_person(user)
    role = make_role(permissions=["metrics.view", "offering.view"])
    membership = make_membership(person, org_tree["district"], role)
    service = make_service(org_tree["district"], "Root service")
    metric = Metric.objects.create(code="global", name="Global")
    kpi = KPI.objects.create(code="global", name="Global")
    sla = SLA.objects.create(name="Global", target_minutes=30)
    assert metric in _metric_queryset(user)
    assert kpi in _kpi_queryset(user)
    assert sla in _sla_queryset(user)

    access = grant_service(membership, service)
    assert not _metric_queryset(user).filter(pk=metric.pk).exists()
    assert not _kpi_queryset(user).filter(pk=kpi.pk).exists()
    assert not _sla_queryset(user).filter(pk=sla.pk).exists()
    access.delete()

    for grant in role.rolepermissions.all():
        GrantScope.objects.create(
            role_permission=grant, scope_type=GrantScopeType.SPECIFIC_SERVICE, service=service
        )
    assert not _metric_queryset(user).filter(pk=metric.pk).exists()
    assert not _sla_queryset(user).filter(pk=sla.pk).exists()
    role.rolepermissions.all().delete()
    role = make_role(permissions=["metrics.view"])
    membership.role = role
    membership.save(update_fields=["role"])
    Organization.objects.create(name="Another root", code="other-root")
    assert not _metric_queryset(user).filter(pk=metric.pk).exists()


def test_kpi_counts_only_visible_metrics(
    client, org_tree, make_user, make_person, make_role, make_membership, make_service
):
    user = make_user()
    make_membership(
        make_person(user), org_tree["parish_a"], make_role(permissions=["metrics.view"])
    )
    service = make_service(org_tree["parish_a"], "A")
    hidden_service = make_service(org_tree["parish_b"], "B")
    kpi = KPI.objects.create(code="local", name="Local", service=service)
    visible = Metric.objects.create(code="visible", name="Visible", service=service)
    hidden = Metric.objects.create(code="hidden", name="Hidden", service=hidden_service)
    kpi.metrics.add(visible, hidden)
    client.force_login(user)
    response = client.get(reverse("metrics:kpis"))
    assert response.status_code == 200
    assert response.context["object_list"][0].metrics_count == 1
