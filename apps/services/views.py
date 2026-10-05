"""Services: catalog list and per-service detail."""

from django import forms
from django.db import transaction
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
    ServiceConsumption,
    ServiceStatus,
    ServiceType,
)
from apps.workflows.models import Workflow, WorkflowExecution

CLOSED_STATUSES = (RequestStatus.RESOLVED, RequestStatus.CLOSED, RequestStatus.CANCELLED)

_DEPARTMENT_CHILDREN = ["it", "supplies", "finance", "hr"]

_DEPARTMENT_TYPE_CODES = {tc: None for tc in _DEPARTMENT_CHILDREN}


def _get_department_type_map():
    return {tc: ServiceType.objects.get(code=tc) for tc in _DEPARTMENT_CHILDREN}


class ServiceCreateForm(forms.ModelForm):
    """New service owned by an organization the user may see.

    Every service requires an active Service Manager, so the form carries a
    ``service_manager`` field that is resolved into a ``ServiceActor`` with the
    SERVICE_MANAGER role on save.

    ``service_type`` is required. ``parent`` is optional and scoped to the
    user's organization and ancestor orgs. ``consumed_services`` is an M2M
    list of services this service depends on (Value Streams and Processes).
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
    consumed_services = forms.ModelMultipleChoiceField(
        queryset=Service.objects.none(),
        label=_("Consumed services"),
        help_text=_(
            "Other services this service depends on (Value Streams and Processes)."
        ),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Service
        fields = ("organization", "name", "description", "status", "service_type", "parent")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._user = user
        self._set_organization_queryset()
        self.fields["parent"].queryset = Service.objects.none()
        self.fields["consumed_services"].queryset = Service.objects.none()

    def _set_organization_queryset(self):
        if getattr(self._user, "is_superuser", False):
            orgs = Organization.objects.all()
        else:
            orgs = Organization.objects.visible_to(self._user, "organization.view")
        self.fields["organization"].queryset = orgs

    def _scope_service_choices(self, organization):
        """Scope parent and consumed_services to user's org + ancestor orgs."""
        if organization is None:
            return
        visible = organization.get_ancestors(include_self=True)
        if not getattr(self._user, "is_superuser", False):
            visible = visible.filter(
                id__in=Organization.objects.visible_to(self._user, "organization.view")
            )
        self.fields["parent"].queryset = Service.objects.filter(
            organization__in=visible
        )
        self.fields["consumed_services"].queryset = Service.objects.filter(
            organization__in=visible
        )

    def _create_departments(self, service):
        """Auto-create the 4 department children for Management-type services."""
        type_map = _get_department_type_map()
        for code in _DEPARTMENT_CHILDREN:
            Service.objects.create(
                organization=service.organization,
                name=f"{service.name} — {code.capitalize()}",
                description=_(
                    "Department of %(parent)s." % {"parent": service.name}
                ),
                service_type=type_map[code],
                parent=service,
                status=service.status,
            )


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

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_form(self):
        form = super().get_form()
        form._scope_service_choices(form.cleaned_data.get("organization"))
        return form

    def form_valid(self, form):
        from django.contrib import messages
        from django.http import HttpResponseRedirect

        organization = form.cleaned_data["organization"]
        service_type = form.cleaned_data["service_type"]
        consumed = form.cleaned_data.get("consumed_services", [])

        form._user = self.request.user
        form._scope_service_choices(organization)

        with transaction.atomic():
            self.object = form.save(commit=False)
            self.object.organization = organization
            self.object.service_type = service_type
            self.object.save()
            # M2M consumed_services via ServiceConsumption
            for svc in consumed:
                ServiceConsumption.objects.get_or_create(
                    service=self.object, consumed=svc
                )
            # Auto-create department children for Management type
            if service_type.code == ServiceType.MANAGEMENT:
                form._create_departments(self.object)
            # Create Service Manager actor
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


class ServiceListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = Service
    template_name = "services/index.html"
    platform_codes = ("service.view",)
    table_config = TableConfig(
        key="services",
        columns=[
            Column("name", _("Service"), sortable=True, searchable=True),
            Column(
                "service_type.name",
                _("Type"),
                sortable=True,
                filter=Filter(FilterKind.TEXT),
            ),
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
            .select_related("organization", "service_type")
            .alive()
        )


class ServiceDetailView(BreadcrumbsMixin, ScopedDetailView):
    model = Service
    template_name = "services/detail.html"
    scope_action = "service.view"

    def get_breadcrumb_title(self) -> str:
        return self.object.name

    def get_queryset(self):
        return super().get_queryset().select_related("organization", "service_type").alive()

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
        ctx["children"] = service.children.alive().select_related("service_type")
        ctx["consumed"] = service.consumed_through.select_related("consumed").order_by("-created_at")
        return ctx


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
