"""Requests: table (search/filters/bulk close), detail and create."""

from django import forms
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, View

from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.mixins import (
    CreatePermissionMixin,
    PermissionRequiredMixin,
    ScopedDetailView,
    selected_service_pk,
    service_filter_choices,
    user_has_permission_code,
    visible_services,
)
from apps.common.tables.config import BulkAction, Column, Filter, FilterKind, TableConfig
from apps.common.tables.filters import organization_column
from apps.common.tables.views import TableView
from apps.offerings.builder import annotate_flow, steps_queryset
from apps.offerings.models import OfferingStatus, ServiceOffering
from apps.people.models import Person
from apps.requests.models import Priority, Request, RequestStatus

CLOSED_STATUSES = (RequestStatus.RESOLVED, RequestStatus.CLOSED, RequestStatus.CANCELLED)


def offerings_for_service(user, service_pk):
    """Active offerings of one service the user can see (empty if not visible)."""
    if not service_pk:
        return ServiceOffering.objects.none()
    return (
        ServiceOffering.objects.filter(
            service__in=visible_services(user),
            service_id=service_pk,
            status=OfferingStatus.ACTIVE,
            on_request=True,
        )
        .select_related("offering_type")
        .order_by("name", "-version")
    )


def offering_steps(offering):
    return annotate_flow(steps_queryset(offering))


def offering_label(offering):
    if offering.offering_type_id:
        return f"{offering.name} — {offering.offering_type.name}"
    return offering.name


class RequestCreateForm(forms.ModelForm):
    """Service first, then one of that service's offerings.

    The offering list depends on the chosen service: it is rendered for the
    bound/initial service and refreshed over HTMX when the service changes.
    Restricting the queryset to that service also makes validation reject an
    offering that belongs to another service.
    """

    class Meta:
        model = Request
        fields = ("service", "offering", "title", "description", "priority", "requester")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = (
            visible_services(user).select_related("organization").order_by("name")
        )
        self.fields["service"].label_from_instance = lambda s: f"{s.name} — {s.organization.name}"
        self.fields["service"].widget.attrs.update(
            {
                "hx-get": reverse("requests:offering_options"),
                "hx-target": "#id_offering",
                "hx-trigger": "change",
                "hx-swap": "innerHTML",
            }
        )
        self.fields["requester"].queryset = Person.objects.filter(active=True).order_by("full_name")

        if self.is_bound:
            service_pk = self.data.get(self.add_prefix("service"))
        else:
            service_pk = self.initial.get("service")
        try:
            service_pk = int(service_pk) if service_pk else None
        except (TypeError, ValueError):
            service_pk = None
        offering = self.fields["offering"]
        offering.queryset = offerings_for_service(user, service_pk)
        offering.label_from_instance = offering_label
        offering.widget.attrs.update(
            {
                "hx-get": reverse("requests:offering_steps"),
                "hx-target": "#offering-steps",
                "hx-trigger": "change",
                "hx-swap": "innerHTML",
            }
        )
        offering.empty_label = (
            _("Select a service offering") if service_pk else _("Select a service first")
        )


class RequestListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = Request
    template_name = "requests/index.html"
    platform_codes = ("request.view",)
    table_config = TableConfig(
        key="requests",
        columns=[
            Column("title", _("Request"), sortable=True, searchable=True),
            organization_column(),
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
            Column("offering.name", _("Service Offering"), sortable=True, searchable=True),
            Column("offering.offering_type.name", _("Type"), sortable=True),
            Column(
                "status",
                _("Status"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=RequestStatus.choices),
                template="partials/pill.html",
            ),
            Column(
                "priority",
                _("Priority"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=Priority.choices),
                template="partials/pill.html",
            ),
            Column(
                "created_at",
                _("Created"),
                sortable=True,
                filter=Filter(FilterKind.DATERANGE),
                template="partials/datetime.html",
            ),
        ],
        default_sort="-created_at",
        caption="Requests",
        empty_headline="No requests in scope",
        empty_body="Requests for services you can see will appear here.",
        row_url_name="requests:detail",
        bulk_actions=[
            BulkAction(
                "mark_closed",
                _("Mark closed"),
                icon="check",
                confirm_text=_("Mark selected requests as closed?"),
            ),
        ],
    )
    breadcrumb_title = _("Requests")
    service_filter_field = "service"
    organization_filter_fields = ("service__organization",)

    def get_base_queryset(self):
        return Request.objects.visible_to(self.request.user, "request.view").select_related(
            "service", "offering", "offering__offering_type"
        )

    def apply_column_filters(self, qs):
        # "OPEN" is a dashboard pseudo-status meaning "not yet closed" — there
        # is no single ``RequestStatus`` value for it (unlike incidents /
        # problems), so intercept it before the generic per-column filter
        # (which would otherwise try — and fail — an exact ``status="OPEN"``
        # match) and translate it into the same exclusion used for counts.
        if self.request.GET.get("status") == "OPEN":
            stripped = self.request.GET.copy()
            stripped.pop("status", None)
            original_get = self.request.GET
            self.request.GET = stripped
            try:
                qs, active = super().apply_column_filters(qs)
            finally:
                self.request.GET = original_get
            qs = qs.exclude(status__in=CLOSED_STATUSES)
            active["status"] = "OPEN"
            return qs, active
        return super().apply_column_filters(qs)

    def perform_bulk_action(self, action: str, queryset):
        if action != "mark_closed":
            raise PermissionDenied
        if not user_has_permission_code(self.request.user, "request.update"):
            raise PermissionDenied
        updated = queryset.exclude(status=RequestStatus.CLOSED).update(status=RequestStatus.CLOSED)
        if updated:
            from django.contrib import messages

            messages.success(self.request, f"Closed {updated} request(s).")
        return self.redirect_to_list()

    def redirect_to_list(self):
        from django.shortcuts import redirect

        return redirect(self.request.path or reverse("requests:index"))


class RequestCreateView(BreadcrumbsMixin, CreatePermissionMixin, FormView):
    template_name = "requests/form.html"
    form_class = RequestCreateForm
    create_perm_code = "request.create"
    breadcrumb_title = _("New request")
    breadcrumb_parent = (_("Requests"), "requests:index")

    def get_initial(self):
        """Pre-select the header's service (``?service=<pk>`` wins)."""
        initial = super().get_initial()
        service_pk = self.request.GET.get("service") or selected_service_pk(
            self.request.user, self.request.session
        )
        if service_pk:
            initial["service"] = service_pk
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        """Render the selected offering's steps (initial value or re-display)."""
        ctx = super().get_context_data(**kwargs)
        form = ctx["form"]
        ctx["offering_steps"] = None
        pk = form["offering"].value()
        if pk:
            try:
                selected = form.fields["offering"].queryset.filter(pk=int(pk)).first()
            except (TypeError, ValueError):
                selected = None
            if selected is not None:
                ctx["offering_steps"] = offering_steps(selected)
        return ctx

    def form_valid(self, form):
        request_obj = form.save(commit=False)
        created_by = Person.objects.filter(user=self.request.user).first()
        request_obj.created_by = created_by
        request_obj.status = RequestStatus.NEW
        request_obj.save()
        self.object = request_obj
        from django.contrib import messages

        messages.success(self.request, _("Request created."))
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("requests:detail", args=[self.object.pk])


class RequestOfferingOptionsView(CreatePermissionMixin, View):
    """HTMX fragment: ``<option>``s for the offerings of ``?service=<pk>``."""

    create_perm_code = "request.create"

    def get(self, request, *args, **kwargs):
        service_pk = request.GET.get("service", "").strip()
        try:
            service_pk = int(service_pk) if service_pk else None
        except ValueError:
            service_pk = None
        offerings = offerings_for_service(request.user, service_pk)
        return render(
            request,
            "requests/_offering_options.html",
            {
                "offerings": [(o.pk, offering_label(o)) for o in offerings],
                "has_service": service_pk is not None,
            },
        )


class RequestOfferingStepsView(CreatePermissionMixin, View):
    """HTMX fragment for the form's right-hand panel: the steps of
    ``?offering=<pk>`` (an active, visible offering), else a hint."""

    create_perm_code = "request.create"

    def get(self, request, *args, **kwargs):
        try:
            pk = int(request.GET.get("offering", ""))
        except ValueError:
            pk = None
        selected = (
            ServiceOffering.objects.filter(
                pk=pk,
                service__in=visible_services(request.user),
                status=OfferingStatus.ACTIVE,
                on_request=True,
            ).first()
            if pk is not None
            else None
        )
        steps = offering_steps(selected) if selected is not None else None
        return render(request, "requests/_steps_panel.html", {"steps": steps})


class RequestDetailView(BreadcrumbsMixin, ScopedDetailView):
    model = Request
    template_name = "requests/detail.html"
    scope_action = "request.view"
    context_object_name = "req"

    def get_breadcrumb_title(self) -> str:
        return f"#{self.object.pk} {self.object.title}"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related(
                "service", "offering", "offering__offering_type", "requester", "assigned_to"
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["comments"] = self.object.comments.select_related("author").order_by("created_at")
        return ctx
