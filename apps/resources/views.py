"""Resources: catalog, detail and assignments."""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.lifecycle import CatalogCreateView, LifecycleActionView
from apps.common.mixins import PermissionRequiredMixin, ScopedDetailView
from apps.common.tables.config import Column, Filter, FilterKind, TableConfig
from apps.common.tables.views import TableView
from apps.organizations.models import Organization
from apps.resources.models import Resource, ResourceAssignment, ResourceType
from apps.services.models import Service


class ResourceListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = Resource
    template_name = "resources/index.html"
    platform_codes = ("resource.view",)
    table_config = TableConfig(
        key="resources",
        columns=[
            Column("name", _("Resource"), sortable=True, searchable=True),
            Column(
                "resource_type",
                _("Type"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=ResourceType.choices),
                template="partials/pill.html",
            ),
            Column("code", _("Code"), searchable=True),
            Column(
                "organization.name",
                _("Organization"),
                sortable=True,
                searchable=True,
                filter=Filter(FilterKind.TEXT),
            ),
            Column("service.name", _("Service"), sortable=True, searchable=True),
            Column("active", _("Active"), sortable=True, template="partials/bool.html"),
        ],
        default_sort="name",
        caption="Resources",
        empty_headline="No resources in scope",
        empty_body="Resources owned by organizations you can see will appear here.",
        row_url_name="resources:detail",
    )
    breadcrumb_title = _("Resources")

    def get_base_queryset(self):
        return (
            Resource.objects.visible_to(self.request.user, "resource.view")
            .select_related("organization", "service")
            .alive()
        )


class ResourceDetailView(BreadcrumbsMixin, ScopedDetailView):
    model = Resource
    template_name = "resources/detail.html"
    scope_action = "resource.view"

    def get_breadcrumb_title(self) -> str:
        return self.object.name

    def get_queryset(self):
        return super().get_queryset().select_related("organization", "service").alive()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["current_assignments"] = self.object.assignments.filter(
            ended_at__isnull=True
        ).select_related("resource")
        ctx["past_assignments"] = self.object.assignments.filter(
            ended_at__isnull=False
        ).select_related("resource")[:10]
        return ctx


class ResourceAssignmentListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = ResourceAssignment
    template_name = "resources/assignments.html"
    platform_codes = ("resource.view",)
    table_config = TableConfig(
        key="resource_assignments",
        columns=[
            Column(
                "resource.name",
                _("Resource"),
                sortable=True,
                searchable=True,
                filter=Filter(FilterKind.TEXT),
            ),
            Column(
                "resource.resource_type",
                _("Type"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=ResourceType.choices),
                template="partials/pill.html",
            ),
            Column("target", _("Assigned to"), template="resources/_target.html"),
            Column("label", _("Label"), searchable=True),
            Column("started_at", _("Started"), sortable=True, filter=Filter(FilterKind.DATERANGE)),
            Column("ended_at", _("Ended"), sortable=True),
            Column("created_at", _("Created"), sortable=True, template="partials/datetime.html"),
        ],
        default_sort="-created_at",
        caption="Assignments",
        empty_headline="No assignments in scope",
        empty_body="Allocations of resources you can see will appear here.",
    )
    breadcrumb_title = _("Assignments")
    breadcrumb_parent = (_("Resources"), "resources:index")

    def get_base_queryset(self):
        visible_ids = list(
            Resource.objects.visible_to(self.request.user, "resource.view")
            .alive()
            .values_list("id", flat=True)
        )
        return ResourceAssignment.objects.filter(resource_id__in=visible_ids).select_related(
            "resource", "resource__organization"
        )


class ResourceCreateForm(forms.ModelForm):
    """New resource owned by an organization (optionally a specific service)."""

    class Meta:
        model = Resource
        fields = ("resource_type", "name", "code", "description", "organization", "service")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if getattr(user, "is_superuser", False):
            orgs = Organization.objects.all()
            services = Service.objects.all()
        else:
            orgs = Organization.objects.visible_to(user, "organization.view")
            services = Service.objects.visible_to(user, "service.view")
        self.fields["organization"].queryset = orgs
        self.fields["service"].queryset = services
        self.fields["service"].required = False


class ResourceCreateView(CatalogCreateView):
    form_class = ResourceCreateForm
    create_perm_code = "resource.create"
    breadcrumb_parent = (_("Resources"), "resources:index")
    success_url_name = "resources:index"
    success_message = _("Resource created.")
    heading = _("New resource")
    intro = _("Register a resource (vehicle, equipment, material or facility).")
    submit_label = _("Create resource")


class ResourceActivateView(LifecycleActionView):
    model = Resource
    perm_code = "resource.manage"
    scope_action = "resource.view"
    verb = "activate"
    success_message = _("Resource activated.")
    redirect_url_name = "resources:index"


class ResourceDeactivateView(LifecycleActionView):
    model = Resource
    perm_code = "resource.manage"
    scope_action = "resource.view"
    verb = "deactivate"
    success_message = _("Resource deactivated.")
    redirect_url_name = "resources:index"


class ResourceDeleteView(LifecycleActionView):
    model = Resource
    perm_code = "resource.delete"
    scope_action = "resource.view"
    verb = "delete"
    success_message = _("Resource deleted.")
    redirect_url_name = "resources:index"
