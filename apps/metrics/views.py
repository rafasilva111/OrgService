"""Metrics & KPIs: definitions, values and targets."""

from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _

from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.mixins import (
    PermissionRequiredMixin,
    ScopedDetailView,
    service_filter_choices,
)
from apps.common.tables.config import Column, Filter, FilterKind, TableConfig
from apps.common.tables.views import TableView
from apps.metrics.models import KPI, SLA, AggregationType, Metric, ScopeType


def _scoped_metrics(user, queryset, *permissions):
    from apps.common.authorization import has_platform_scope
    from apps.services.models import Service

    if getattr(user, "is_superuser", False):
        return queryset
    services = Service.objects.none()
    include_global = False
    for permission in permissions:
        services |= Service.objects.visible_to(user, permission)
        include_global |= has_platform_scope(user, permission)
    scope = Q(service__in=services)
    if include_global:
        scope |= Q(service__isnull=True)
    return queryset.filter(scope)


def _metric_queryset(user):
    return _scoped_metrics(user, Metric.objects.all(), "metrics.view")


def _kpi_queryset(user):
    return _scoped_metrics(user, KPI.objects.all(), "metrics.view")


class MetricListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = Metric
    template_name = "metrics/index.html"
    platform_codes = ("metrics.view",)
    table_config = TableConfig(
        key="metrics",
        columns=[
            Column("code", _("Code"), sortable=True, searchable=True),
            Column("name", _("Name"), sortable=True, searchable=True),
            Column(
                "service.name",
                _("Service"),
                sortable=True,
                searchable=True,
                filter=Filter(FilterKind.TEXT),
            ),
            Column(
                "aggregation",
                _("Aggregation"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=AggregationType.choices),
                template="partials/pill.html",
            ),
            Column("unit", _("Unit"), sortable=True, searchable=True),
        ],
        default_sort="code",
        caption="Metrics",
        empty_headline="No metrics in scope",
        empty_body="Metrics for your services (and platform-wide ones) will appear here.",
    )
    breadcrumb_title = _("Metrics")

    def get_base_queryset(self):
        return _metric_queryset(self.request.user).select_related("service")


class MetricDetailView(BreadcrumbsMixin, ScopedDetailView):
    model = Metric
    template_name = "metrics/detail.html"
    scope_action = "metrics.view"

    def get_breadcrumb_title(self) -> str:
        return self.object.code

    def get_queryset(self):
        return _metric_queryset(self.request.user).select_related("service")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["values"] = self.object.values.order_by("-period_start")[:30]
        ctx["value_count"] = self.object.values.count()
        return ctx


class KpiListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = KPI
    template_name = "metrics/kpis.html"
    platform_codes = ("metrics.view",)
    table_config = TableConfig(
        key="kpis",
        columns=[
            Column("code", _("Code"), sortable=True, searchable=True),
            Column("name", _("KPI"), sortable=True, searchable=True),
            Column(
                "service.name",
                _("Service"),
                sortable=True,
                searchable=True,
                filter=Filter(FilterKind.TEXT),
            ),
            Column("target_value", _("Target"), sortable=True),
            Column("tolerance", _("Tolerance"), sortable=True),
            Column("metrics_count", _("Metrics"), template="metrics/_metrics_count.html"),
        ],
        default_sort="code",
        caption="KPIs",
        empty_headline="No KPIs in scope",
        empty_body="KPIs for your services (and platform-wide ones) will appear here.",
    )
    breadcrumb_title = _("KPIs")

    def get_base_queryset(self):
        return (
            _kpi_queryset(self.request.user)
            .select_related("service")
            .annotate(
                metrics_count=Count(
                    "metrics",
                    filter=Q(metrics__in=_metric_queryset(self.request.user)),
                    distinct=True,
                )
            )
        )


def _sla_queryset(user):
    return _scoped_metrics(user, SLA.objects.all(), "metrics.view", "offering.view")


class SlaListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    """Service-level targets, including the one generated for each offering."""

    model = SLA
    template_name = "metrics/slas.html"
    platform_codes = ("metrics.view", "offering.view")
    table_config = TableConfig(
        key="slas",
        columns=[
            Column("name", _("SLA"), sortable=True, searchable=True),
            Column(
                "service.name",
                _("Service"),
                sortable=True,
                searchable=True,
                filter=Filter(
                    FilterKind.CHOICE,
                    param="service",
                    lookup="service_id",
                    choices_callable=service_filter_choices,
                ),
            ),
            Column("offering", _("Service Offering"), template="metrics/_sla_offering.html"),
            Column(
                "scope",
                _("Scope"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=ScopeType.choices),
                template="partials/pill.html",
            ),
            Column(
                "target_minutes", _("Target"), sortable=True, template="offerings/_sla_cell.html"
            ),
            Column("active", _("Active"), sortable=True, template="partials/bool.html"),
        ],
        default_sort="name",
        caption="SLAs",
        empty_headline="No SLAs in scope",
        empty_body="SLAs of your services and of their service offerings will appear here.",
    )
    breadcrumb_title = _("SLAs")
    service_filter_field = "service"
    organization_filter_fields = ("service__organization",)

    def get_base_queryset(self):
        return _sla_queryset(self.request.user).select_related("service", "offering")
