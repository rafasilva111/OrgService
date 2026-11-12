"""Service Offerings: definitions, details and executions."""

from django.utils.translation import gettext_lazy as _

from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.mixins import (
    PermissionRequiredMixin,
    ScopedDetailView,
    service_filter_choices,
    visible_services,
)
from apps.common.tables.config import Column, Filter, FilterKind, TableConfig
from apps.common.tables.filters import organization_column
from apps.common.tables.views import TableView
from apps.offerings.models import (
    ExecutionStatus,
    OfferingExecution,
    OfferingStatus,
    ServiceOffering,
)

ACTIVE_STATUS_CHOICES = tuple(
    c for c in OfferingStatus.choices if c[0] != OfferingStatus.DEPRECATED
)


class ServiceOfferingListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = ServiceOffering
    template_name = "offerings/index.html"
    platform_codes = ("offering.view",)
    table_config = TableConfig(
        key="offerings",
        columns=[
            Column("name", _("Service Offering"), sortable=True, searchable=True),
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
            Column(
                "status",
                _("Status"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=ACTIVE_STATUS_CHOICES),
                template="partials/pill.html",
            ),
            Column("version", _("Version"), sortable=True),
            Column("updated_at", _("Updated"), sortable=True, filter=Filter(FilterKind.DATERANGE)),
        ],
        default_sort="-version",
        caption="Service Offerings",
        empty_headline="No offerings in scope",
        empty_body="Service Offering definitions for services you can see will appear here.",
        row_url_name="offerings:detail",
    )
    breadcrumb_title = _("Service Offerings")
    service_filter_field = "service"
    organization_filter_fields = ("service__organization",)

    def get_base_queryset(self):
        return ServiceOffering.objects.visible_to(
            self.request.user, "offering.view"
        ).select_related("service")


class ServiceOfferingDetailView(BreadcrumbsMixin, ScopedDetailView):
    model = ServiceOffering
    template_name = "offerings/detail.html"
    context_object_name = "offering"
    scope_action = "offering.view"

    def get_breadcrumb_title(self) -> str:
        return f"{self.object.name} v{self.object.version}"

    def get_queryset(self):
        return super().get_queryset().select_related("service")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["steps"] = self.object.steps.select_related("responsible_role").order_by("order")
        ctx["versions"] = (
            ServiceOffering.objects.visible_to(self.request.user, "offering.view")
            .filter(service=self.object.service, name=self.object.name)
            .order_by("-version")
        )
        ctx["executions"] = self.object.executions.select_related("created_by").order_by(
            "-started_at"
        )[:8]
        return ctx


class OfferingExecutionListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = OfferingExecution
    template_name = "offerings/executions.html"
    platform_codes = ("offering.view",)
    table_config = TableConfig(
        key="offerings_executions",
        columns=[
            Column("offering.name", _("Service Offering"), sortable=True, searchable=True),
            organization_column(
                key="offering.service.organization.name",
                lookups=("offering__service__organization__path",),
            ),
            Column(
                "offering.service.name",
                _("Service"),
                sortable=True,
                searchable=True,
                filter=Filter(
                    FilterKind.CHOICE,
                    param="service",
                    lookup="offering__service_id",
                    choices_callable=service_filter_choices,
                ),
            ),
            Column(
                "status",
                _("Status"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=ExecutionStatus.choices),
                template="offerings/exec_state.html",
            ),
            Column("current_step.name", _("Step"), sortable=True),
            Column("created_by.full_name", _("Started by"), sortable=True, searchable=True),
            Column("started_at", _("Started"), sortable=True, filter=Filter(FilterKind.DATERANGE)),
            Column("started_at", _("Duration"), template="offerings/exec_duration.html"),
        ],
        default_sort="-started_at",
        caption="Executions",
        empty_headline="No executions yet",
        empty_body="Runs of the offerings you can see will appear here.",
    )
    breadcrumb_title = _("Executions")
    breadcrumb_parent = (_("Service Offerings"), "offerings:index")
    service_filter_field = "offering__service"
    organization_filter_fields = ("offering__service__organization",)

    def get_base_queryset(self):
        service_ids = list(visible_services(self.request.user).values_list("id", flat=True))
        return OfferingExecution.objects.filter(
            offering__service_id__in=service_ids
        ).select_related("offering", "offering__service", "created_by", "current_step")
