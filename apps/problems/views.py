"""Problems: root-cause records behind recurring incidents."""

from django.utils.translation import gettext_lazy as _

from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.mixins import PermissionRequiredMixin, ScopedDetailView, service_filter_choices
from apps.common.tables.config import Column, Filter, FilterKind, TableConfig
from apps.common.tables.views import TableView
from apps.problems.models import Problem, ProblemStatus
from apps.requests.models import Priority


class ProblemListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = Problem
    template_name = "problems/index.html"
    platform_codes = ("problem.view",)
    table_config = TableConfig(
        key="problems",
        columns=[
            Column("title", _("Problem"), sortable=True, searchable=True),
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
                filter=Filter(FilterKind.CHOICE, choices=ProblemStatus.choices),
                template="partials/pill.html",
            ),
            Column(
                "priority",
                _("Priority"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=Priority.choices),
                template="partials/pill.html",
            ),
            Column("assigned_to.full_name", _("Assigned"), sortable=True, searchable=True),
            Column("created_at", _("Created"), sortable=True, template="partials/datetime.html"),
        ],
        default_sort="-created_at",
        caption="Problems",
        empty_headline="No problems in scope",
        empty_body="Problems for services you can see will appear here.",
        row_url_name="problems:detail",
    )
    breadcrumb_title = _("Problems")
    service_filter_field = "service"

    def get_base_queryset(self):
        return Problem.objects.visible_to(self.request.user, "problem.view").select_related(
            "service", "assigned_to"
        )


class ProblemDetailView(BreadcrumbsMixin, ScopedDetailView):
    model = Problem
    template_name = "problems/detail.html"
    scope_action = "problem.view"

    def get_breadcrumb_title(self) -> str:
        return f"#{self.object.pk} {self.object.title}"

    def get_queryset(self):
        return super().get_queryset().select_related("service", "assigned_to")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["incident_links"] = self.object.incident_links.select_related(
            "incident", "incident__service"
        )
        return ctx
