"""Audit: immutable compliance log."""

from django.utils.translation import gettext_lazy as _

from apps.audit.models import AuditLog
from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.mixins import PermissionRequiredMixin
from apps.common.tables.config import Column, Filter, FilterKind, TableConfig
from apps.common.tables.views import TableView


class AuditLogView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = AuditLog
    template_name = "audit/log.html"
    platform_codes = ("audit.view",)
    table_config = TableConfig(
        key="audit_log",
        columns=[
            Column(
                "created_at",
                _("When"),
                sortable=True,
                filter=Filter(FilterKind.DATERANGE),
                template="partials/datetime.html",
            ),
            Column(
                "action",
                _("Action"),
                sortable=True,
                searchable=True,
                filter=Filter(FilterKind.TEXT),
            ),
            Column(
                "actor_user.username",
                _("Actor"),
                sortable=True,
                searchable=True,
                template="audit/_actor.html",
            ),
            Column("object", _("Object"), template="audit/_object.html"),
            Column("service.name", _("Service"), sortable=True, searchable=True),
            Column("organization.name", _("Organization"), sortable=True),
        ],
        default_sort="-created_at",
        caption="Audit log",
        empty_headline="No audit entries in scope",
        empty_body="Audit entries for organizations you can see will appear here.",
    )
    breadcrumb_title = _("Audit log")

    def get_base_queryset(self):
        return AuditLog.objects.visible_to(self.request.user, "audit.view").select_related(
            "actor_user", "actor_person", "service", "organization"
        )
