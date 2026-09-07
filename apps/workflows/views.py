"""Workflows: definitions, details and executions."""

from django.utils.translation import gettext_lazy as _

from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.mixins import (
    PermissionRequiredMixin,
    ScopedDetailView,
    service_filter_choices,
    visible_services,
)
from apps.common.tables.config import Column, Filter, FilterKind, TableConfig
from apps.common.tables.views import TableView
from apps.workflows.models import ExecutionStatus, Workflow, WorkflowExecution, WorkflowStatus

ACTIVE_STATUS_CHOICES = tuple(
    c for c in WorkflowStatus.choices if c[0] != WorkflowStatus.DEPRECATED
)


class WorkflowListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = Workflow
    template_name = "workflows/index.html"
    platform_codes = ("workflow.view",)
    table_config = TableConfig(
        key="workflows",
        columns=[
            Column("name", _("Workflow"), sortable=True, searchable=True),
            Column(
                "service.name",
                _("Service"),
                sortable=True,
                searchable=True,
                filter=Filter(
                    FilterKind.CHOICE,
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
        caption="Workflows",
        empty_headline="No workflows in scope",
        empty_body="Workflow definitions for services you can see will appear here.",
        row_url_name="workflows:detail",
    )
    breadcrumb_title = _("Workflows")
    service_filter_field = "service"

    def get_base_queryset(self):
        return Workflow.objects.visible_to(self.request.user, "workflow.view").select_related(
            "service"
        )


class WorkflowDetailView(BreadcrumbsMixin, ScopedDetailView):
    model = Workflow
    template_name = "workflows/detail.html"
    scope_action = "workflow.view"

    def get_breadcrumb_title(self) -> str:
        return f"{self.object.name} v{self.object.version}"

    def get_queryset(self):
        return super().get_queryset().select_related("service")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["steps"] = self.object.steps.select_related("responsible_role").order_by("order")
        ctx["versions"] = (
            Workflow.objects.visible_to(self.request.user, "workflow.view")
            .filter(service=self.object.service, name=self.object.name)
            .order_by("-version")
        )
        ctx["executions"] = self.object.executions.select_related("created_by").order_by(
            "-started_at"
        )[:8]
        return ctx


class WorkflowExecutionListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = WorkflowExecution
    template_name = "workflows/executions.html"
    platform_codes = ("workflow.view",)
    table_config = TableConfig(
        key="workflows_executions",
        columns=[
            Column("workflow.name", _("Workflow"), sortable=True, searchable=True),
            Column(
                "workflow.service.name",
                _("Service"),
                sortable=True,
                searchable=True,
                filter=Filter(
                    FilterKind.CHOICE,
                    lookup="workflow__service_id",
                    choices_callable=service_filter_choices,
                ),
            ),
            Column(
                "status",
                _("Status"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=ExecutionStatus.choices),
                template="workflows/exec_state.html",
            ),
            Column("current_step.name", _("Step"), sortable=True),
            Column("created_by.full_name", _("Started by"), sortable=True, searchable=True),
            Column("started_at", _("Started"), sortable=True, filter=Filter(FilterKind.DATERANGE)),
            Column("started_at", _("Duration"), template="workflows/exec_duration.html"),
        ],
        default_sort="-started_at",
        caption="Executions",
        empty_headline="No executions yet",
        empty_body="Runs of the workflows you can see will appear here.",
    )
    breadcrumb_title = _("Executions")
    breadcrumb_parent = (_("Workflows"), "workflows:index")
    service_filter_field = "workflow__service"

    def get_base_queryset(self):
        service_ids = list(visible_services(self.request.user).values_list("id", flat=True))
        return WorkflowExecution.objects.filter(
            workflow__service_id__in=service_ids
        ).select_related("workflow", "workflow__service", "created_by", "current_step")
