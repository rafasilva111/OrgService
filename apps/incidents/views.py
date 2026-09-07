"""Incidents: table, detail and create."""

from django import forms
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView

from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.mixins import (
    CreatePermissionMixin,
    PermissionRequiredMixin,
    ScopedDetailView,
    service_filter_choices,
    visible_services,
)
from apps.common.tables.config import Column, Filter, FilterKind, TableConfig
from apps.common.tables.views import TableView
from apps.incidents.models import Impact, Incident, IncidentStatus
from apps.people.models import Person
from apps.requests.models import Priority


class IncidentCreateForm(forms.ModelForm):
    class Meta:
        model = Incident
        fields = ("service", "title", "description", "priority", "impact", "urgency", "assigned_to")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = visible_services(user)
        self.fields["assigned_to"].queryset = Person.objects.filter(active=True).order_by(
            "full_name"
        )


class IncidentListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = Incident
    template_name = "incidents/index.html"
    platform_codes = ("incident.view",)
    table_config = TableConfig(
        key="incidents",
        columns=[
            Column("title", _("Incident"), sortable=True, searchable=True),
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
                filter=Filter(FilterKind.CHOICE, choices=IncidentStatus.choices),
                template="partials/pill.html",
            ),
            Column(
                "impact",
                _("Impact"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=Impact.choices),
                template="partials/pill.html",
            ),
            Column(
                "priority",
                _("Priority"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=Priority.choices),
                template="partials/pill.html",
            ),
            Column("started_at", _("Started"), sortable=True, filter=Filter(FilterKind.DATERANGE)),
            Column("created_at", _("Created"), sortable=True, template="partials/datetime.html"),
        ],
        default_sort="-created_at",
        caption="Incidents",
        empty_headline="No incidents in scope",
        empty_body="Incidents for services you can see will appear here.",
        row_url_name="incidents:detail",
    )
    breadcrumb_title = _("Incidents")
    service_filter_field = "service"

    def get_base_queryset(self):
        return Incident.objects.visible_to(self.request.user, "incident.view").select_related(
            "service"
        )


class IncidentCreateView(BreadcrumbsMixin, CreatePermissionMixin, FormView):
    template_name = "incidents/form.html"
    form_class = IncidentCreateForm
    create_perm_code = "incident.create"
    breadcrumb_title = _("New incident")
    breadcrumb_parent = (_("Incidents"), "incidents:index")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        incident = form.save(commit=False)
        incident.reported_by = Person.objects.filter(user=self.request.user).first()
        incident.status = IncidentStatus.OPEN
        incident.save()
        from django.contrib import messages

        messages.success(self.request, "Incident registered.")
        self.object = incident
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("incidents:detail", args=[self.object.pk])


class IncidentDetailView(BreadcrumbsMixin, ScopedDetailView):
    model = Incident
    template_name = "incidents/detail.html"
    scope_action = "incident.view"

    def get_breadcrumb_title(self) -> str:
        return f"#{self.object.pk} {self.object.title}"

    def get_queryset(self):
        return super().get_queryset().select_related("service", "assigned_to", "reported_by")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["updates"] = self.object.updates.select_related("author").order_by("created_at")
        return ctx
