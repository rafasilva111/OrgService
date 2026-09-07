"""Services: catalog list and per-service detail."""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.access.models import Role
from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.lifecycle import CatalogCreateView, LifecycleActionView
from apps.common.mixins import PermissionRequiredMixin, ScopedDetailView
from apps.common.tables.config import Column, Filter, FilterKind, TableConfig
from apps.common.tables.views import TableView
from apps.incidents.models import Incident, IncidentStatus
from apps.metrics.models import KPI, Metric
from apps.organizations.models import Organization
from apps.people.models import Person
from apps.requests.models import Request, RequestStatus
from apps.services.models import (
    SERVICE_MANAGER_ROLE,
    ActorType,
    Service,
    ServiceActor,
    ServiceStatus,
)
from apps.workflows.models import Workflow, WorkflowExecution

CLOSED_STATUSES = (RequestStatus.RESOLVED, RequestStatus.CLOSED, RequestStatus.CANCELLED)


class ServiceListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = Service
    template_name = "services/index.html"
    platform_codes = ("service.view",)
    table_config = TableConfig(
        key="services",
        columns=[
            Column("name", _("Service"), sortable=True, searchable=True),
            Column(
                "organization.name",
                _("Organization"),
                sortable=True,
                searchable=True,
                filter=Filter(FilterKind.TEXT),
            ),
            Column(
                "status",
                _("Status"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=ServiceStatus.choices),
                template="partials/pill.html",
            ),
        ],
        default_sort="name",
        caption="Services",
        empty_headline="No services in scope",
        empty_body="Services you are allowed to see will appear here.",
        row_url_name="services:detail",
    )
    breadcrumb_title = _("Services")

    def get_base_queryset(self):
        return (
            Service.objects.visible_to(self.request.user, "service.view")
            .select_related("organization")
            .alive()
        )


class ServiceDetailView(BreadcrumbsMixin, ScopedDetailView):
    model = Service
    template_name = "services/detail.html"
    scope_action = "service.view"

    def get_breadcrumb_title(self) -> str:
        return self.object.name

    def get_queryset(self):
        return super().get_queryset().select_related("organization").alive()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        service = self.object
        user = self.request.user

        ctx["requests"] = Request.objects.visible_to(user, "request.view").filter(service=service)
        ctx["incidents"] = Incident.objects.visible_to(user, "incident.view").filter(
            service=service
        )
        ctx["workflows"] = Workflow.objects.visible_to(user, "workflow.view").filter(
            service=service
        )
        ctx["executions"] = WorkflowExecution.objects.filter(workflow__service=service)
        ctx["metrics"] = Metric.objects.filter(service=service)
        ctx["kpis"] = KPI.objects.filter(service=service).prefetch_related("metrics")
        ctx["capabilities"] = service.capability_links.select_related("capability").order_by(
            "capability__name"
        )
        ctx["actors"] = service.actors.select_related("role", "person").order_by("role__name")
        ctx["open_request_count"] = ctx["requests"].exclude(status__in=CLOSED_STATUSES).count()
        ctx["active_incident_count"] = (
            ctx["incidents"]
            .exclude(status__in=(IncidentStatus.RESOLVED, IncidentStatus.CLOSED))
            .count()
        )
        ctx["active_workflow_count"] = ctx["workflows"].filter(status="ACTIVE").count()
        return ctx


class ServiceCreateForm(forms.ModelForm):
    """New service owned by an organization the user may see.

    Every service requires an active Service Manager, so the form carries a
    ``service_manager`` field that is resolved into a ``ServiceActor`` with the
    SERVICE_MANAGER role on save.
    """

    service_manager = forms.ModelChoiceField(
        queryset=Person.objects.filter(active=True).order_by("full_name"),
        label=_("Service manager"),
        help_text=_(
            "The person accountable for this service. A Service Manager actor is "
            "created automatically."
        ),
        required=True,
    )

    class Meta:
        model = Service
        fields = ("organization", "name", "description", "status")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if getattr(user, "is_superuser", False):
            orgs = Organization.objects.all()
        else:
            orgs = Organization.objects.visible_to(user, "organization.view")
        self.fields["organization"].queryset = orgs


class ServiceCreateView(CatalogCreateView):
    form_class = ServiceCreateForm
    create_perm_code = "service.create"
    breadcrumb_parent = (_("Services"), "services:index")
    success_url_name = "services:index"
    success_message = _("Service created.")
    heading = _("New service")
    intro = _(
        "Define a service your organization provides. Pick a Service Manager — "
        "every service requires one."
    )
    submit_label = _("Create service")

    def form_valid(self, form):
        from django.contrib import messages
        from django.http import HttpResponseRedirect

        self.object = form.save()
        manager_role, _ = Role.objects.get_or_create(
            code=SERVICE_MANAGER_ROLE,
            defaults={"name": "Service Manager"},
        )
        ServiceActor.objects.get_or_create(
            service=self.object,
            role=manager_role,
            person=form.cleaned_data["service_manager"],
            defaults={"actor_type": ActorType.INTERNAL, "active": True},
        )
        if self.success_message:
            messages.success(self.request, self.success_message)
        return HttpResponseRedirect(self.get_success_url())


class ServiceActivateView(LifecycleActionView):
    model = Service
    perm_code = "service.manage"
    scope_action = "service.view"
    verb = "activate"
    success_message = _("Service activated.")
    redirect_url_name = "services:index"


class ServiceDeactivateView(LifecycleActionView):
    model = Service
    perm_code = "service.manage"
    scope_action = "service.view"
    verb = "deactivate"
    success_message = _("Service deactivated.")
    redirect_url_name = "services:index"


class ServiceDeleteView(LifecycleActionView):
    model = Service
    perm_code = "service.delete"
    scope_action = "service.view"
    verb = "delete"
    success_message = _("Service deleted.")
    redirect_url_name = "services:index"
