"""Metrics & KPIs: definitions, values and targets."""

from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _

from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.mixins import PermissionRequiredMixin, ScopedDetailView, visible_services
from apps.common.tables.config import Column, Filter, FilterKind, TableConfig
from apps.common.tables.views import TableView
from apps.metrics.models import KPI, AggregationType, Metric


def _metric_queryset(user):
    qs = Metric.objects.all()
    if getattr(user, "is_superuser", False):
        return qs
    service_ids = list(visible_services(user).values_list("id", flat=True))
    return qs.filter(Q(service__isnull=True) | Q(service_id__in=service_ids))


def _kpi_queryset(user):
    qs = KPI.objects.all()
    if getattr(user, "is_superuser", False):
        return qs
    service_ids = list(visible_services(user).values_list("id", flat=True))
    return qs.filter(Q(service__isnull=True) | Q(service_id__in=service_ids))


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
            .annotate(metrics_count=Count("metrics", distinct=True))
        )
