"""Events, audit log and metrics."""

import pytest
from django.contrib.contenttypes.models import ContentType

from apps.audit.models import AuditLog
from apps.events.models import Event
from apps.metrics.models import KPI, SLA, KpiMetric, Metric, MetricValue
from apps.requests.models import Request


@pytest.fixture
def event_graph(db, org_tree, make_service, make_user, make_person, make_role, make_membership):
    service = make_service(org_tree["parish_a"])
    user = make_user("actor")
    person = make_person(user=user)
    role = make_role()
    membership = make_membership(person, org_tree["parish_a"], role)
    return {"service": service, "person": person, "membership": membership}


@pytest.mark.django_db
def test_event_generic_subject(event_graph):
    service_ct = ContentType.objects.get_for_model(event_graph["service"])
    evt = Event.objects.create(
        organization=event_graph["service"].organization,
        service=event_graph["service"],
        event_type="SERVICE_CREATED",
        subject_type=service_ct,
        subject_id=event_graph["service"].pk,
        actor=event_graph["person"],
    )
    assert evt.subject == event_graph["service"]
    assert evt.occurred_at == evt.created_at
    assert evt.metadata == {}


@pytest.mark.django_db
def test_audit_log_generic_entity(event_graph):
    service = event_graph["service"]
    log = AuditLog.objects.create(
        action="service.create",
        actor_person=event_graph["person"],
        actor_user=event_graph["person"].user,
        organization=service.organization,
        service=service,
        object_type=ContentType.objects.get_for_model(service),
        object_id=service.pk,
        old_value=None,
        new_value={"status": "OPERATIONAL"},
    )
    assert log.object == service
    assert log.action == "service.create"
    assert log.actor_user == event_graph["person"].user


@pytest.mark.django_db
def test_metric_value_roundtrip(event_graph):
    metric = Metric.objects.create(code="water.quality", name="Water Quality", unit="ppm")
    MetricValue.objects.create(
        metric=metric,
        value=12.5,
        period_start="2024-01-01T00:00:00Z",
        period_end="2024-01-02T00:00:00Z",
        dimension={"service": event_graph["service"].pk},
    )
    MetricValue.objects.create(
        metric=metric,
        value=14.2,
        period_start="2024-01-02T00:00:00Z",
        period_end="2024-01-03T00:00:00Z",
    )
    assert metric.values.count() == 2
    m1 = metric.values.first().value
    assert m1 is not None


@pytest.mark.django_db
def test_kpi_aggregates_metrics():
    sla = SLA.objects.create(name="Repair SLA", scope="RESPONSE", target_minutes=3600)
    metric = Metric.objects.create(code="m.repair_time", name="Repair Time", unit="minutes")
    kpi = KPI.objects.create(code="kpi.repair", name="Repair", target_value=60)
    kpi_metric = KpiMetric.objects.create(kpi=kpi, metric=metric, weight=1.5)
    assert sla.name == "Repair SLA"
    assert kpi.kpi_metrics.get() == kpi_metric
    assert kpi.metrics.get(pk=metric.pk) == metric
    assert str(metric) == "m.repair_time"


@pytest.mark.django_db
def test_metric_required_fields():
    metric = Metric.objects.create(code="m2", name="M2")
    assert metric.service is None
    assert Request._meta.get_field("service").remote_field.get_accessor_name() == "requests"
