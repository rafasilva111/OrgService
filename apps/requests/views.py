"""Requests: table (search/filters/bulk close), detail and create."""

from django import forms
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView

from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.mixins import (
    CreatePermissionMixin,
    PermissionRequiredMixin,
    ScopedDetailView,
    service_filter_choices,
    user_has_permission_code,
    visible_services,
)
from apps.common.tables.config import BulkAction, Column, Filter, FilterKind, TableConfig
from apps.common.tables.views import TableView
from apps.people.models import Person
from apps.requests.models import Priority, Request, RequestStatus, RequestType
from apps.workflows.models import Workflow

CLOSED_STATUSES = (RequestStatus.RESOLVED, RequestStatus.CLOSED, RequestStatus.CANCELLED)


class RequestCreateForm(forms.ModelForm):
    class Meta:
        model = Request
        fields = (
            "service",
            "request_type",
            "title",
            "description",
            "priority",
            "requester",
            "workflow",
        )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        services = visible_services(user)
        self.fields["service"].queryset = services
        self.fields["request_type"].queryset = RequestType.objects.order_by("name")
        self.fields["requester"].queryset = Person.objects.filter(active=True).order_by("full_name")
        self.fields["workflow"].queryset = Workflow.objects.filter(
            service__in=services, status="ACTIVE"
        ).order_by("-version")


class RequestListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = Request
    template_name = "requests/index.html"
    platform_codes = ("request.view",)
    table_config = TableConfig(
        key="requests",
        columns=[
            Column("title", _("Request"), sortable=True, searchable=True),
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
            Column("request_type.name", _("Type"), sortable=True, searchable=True),
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

    def get_base_queryset(self):
        return Request.objects.visible_to(self.request.user, "request.view").select_related(
            "service", "request_type"
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

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        request_obj = form.save(commit=False)
        created_by = Person.objects.filter(user=self.request.user).first()
        request_obj.created_by = created_by
        request_obj.status = RequestStatus.NEW
        request_obj.save()
        from django.contrib import messages

        messages.success(self.request, "Request created.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("requests:detail", args=[self.object.pk])


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
            .select_related("service", "request_type", "requester", "assigned_to")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["comments"] = self.object.comments.select_related("author").order_by("created_at")
        return ctx
