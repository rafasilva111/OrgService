"""Changes: planned, coordinated service modifications."""

from django.utils.translation import gettext_lazy as _

from apps.changes.models import Change, ChangeStatus, RiskLevel
from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.mixins import PermissionRequiredMixin, ScopedDetailView
from apps.common.tables.config import Column, Filter, FilterKind, TableConfig
from apps.common.tables.views import TableView


class ChangeListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = Change
    template_name = "changes/index.html"
    platform_codes = ("change.view",)
    table_config = TableConfig(
        key="changes",
        columns=[
            Column("title", _("Change"), sortable=True, searchable=True),
            Column(
                "service.name",
                _("Service"),
                sortable=True,
                searchable=True,
                filter=Filter(FilterKind.TEXT),
            ),
            Column("organization.name", _("Organization"), sortable=True, searchable=True),
            Column(
                "status",
                _("Status"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=ChangeStatus.choices),
                template="partials/pill.html",
            ),
            Column(
                "risk",
                _("Risk"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=RiskLevel.choices),
                template="partials/pill.html",
            ),
            Column(
                "planned_start",
                _("Planned start"),
                sortable=True,
                filter=Filter(FilterKind.DATERANGE),
            ),
            Column("created_at", _("Created"), sortable=True, template="partials/datetime.html"),
        ],
        default_sort="-created_at",
        caption="Changes",
        empty_headline="No changes in scope",
        empty_body="Changes for services you can see will appear here.",
        row_url_name="changes:detail",
    )
    breadcrumb_title = _("Changes")

    def get_base_queryset(self):
        return Change.objects.visible_to(self.request.user, "change.view").select_related(
            "service", "organization"
        )


class ChangeDetailView(BreadcrumbsMixin, ScopedDetailView):
    model = Change
    template_name = "changes/detail.html"
    scope_action = "change.view"

    def get_breadcrumb_title(self) -> str:
        return f"#{self.object.pk} {self.object.title}"

    def get_queryset(self):
        return super().get_queryset().select_related("service", "organization")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["approvals"] = self.object.approvals.select_related("approver", "role")
        return ctx
