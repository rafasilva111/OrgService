"""Events: immutable timeline of platform activity."""

from django.utils.translation import gettext_lazy as _

from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.mixins import PermissionRequiredMixin
from apps.common.tables.config import Column, Filter, FilterKind, TableConfig
from apps.common.tables.views import TableView
from apps.events.models import Event


class EventTimelineView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = Event
    template_name = "events/timeline.html"
    platform_codes = ("event.view",)
    table_config = TableConfig(
        key="events_timeline",
        columns=[
            Column(
                "event_type",
                _("Event"),
                sortable=True,
                searchable=True,
                filter=Filter(FilterKind.TEXT),
            ),
            Column("subject", _("Subject"), template="events/_subject.html"),
            Column("actor.full_name", _("Actor"), sortable=True, searchable=True),
            Column("service.name", _("Service"), sortable=True, searchable=True),
            Column("organization.name", _("Organization"), sortable=True),
            Column(
                "created_at",
                _("Occurred"),
                sortable=True,
                filter=Filter(FilterKind.DATERANGE),
                template="partials/datetime.html",
            ),
        ],
        default_sort="-created_at",
        caption="Timeline",
        empty_headline="No events in scope",
        empty_body="Events from the scopes you can see will appear here.",
    )
    breadcrumb_title = _("Timeline")

    def get_base_queryset(self):
        return Event.objects.visible_to(self.request.user, "event.view").select_related(
            "actor", "service", "organization"
        )
