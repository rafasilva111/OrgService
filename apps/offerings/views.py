"""Service Offerings: definitions, details and executions."""

import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils.translation import npgettext
from django.views.generic import FormView, View

from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.mixins import (
    CreatePermissionMixin,
    PermissionRequiredMixin,
    ScopedDetailView,
    selected_service_pk,
    service_filter_choices,
    visible_services,
)
from apps.common.tables.config import Column, Filter, FilterKind, TableConfig
from apps.common.tables.filters import organization_column
from apps.common.tables.views import TableView
from apps.offerings.builder import (
    annotate_flow,
    definition_from_offering,
    parse_definition,
    preview_steps,
    save_definition,
    specs_sla,
    steps_queryset,
)
from apps.offerings.forms import (
    ServiceOfferingForm,
    location_options,
    manageable_services,
    responsible_offering_choices,
    step_role_choices,
)
from apps.offerings.models import (
    ExecutionStatus,
    OfferingExecution,
    OfferingStatus,
    ServiceOffering,
    ServiceOfferingType,
    StepType,
)


def offering_type_choices(view):
    return [(str(t.pk), t.name) for t in ServiceOfferingType.objects.order_by("name")]


ACTIVE_STATUS_CHOICES = tuple(
    c for c in OfferingStatus.choices if c[0] != OfferingStatus.DEPRECATED
)


class ServiceOfferingListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = ServiceOffering
    template_name = "offerings/index.html"
    platform_codes = ("offering.view",)
    table_config = TableConfig(
        key="offerings",
        columns=[
            Column("name", _("Service Offering"), sortable=True, searchable=True),
            Column(
                "offering_type.name",
                _("Type"),
                sortable=True,
                filter=Filter(
                    FilterKind.CHOICE,
                    param="type",
                    lookup="offering_type_id",
                    choices_callable=offering_type_choices,
                ),
            ),
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
            Column(
                "status",
                _("Status"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=ACTIVE_STATUS_CHOICES),
                template="partials/pill.html",
            ),
            Column("sla_minutes", _("SLA"), sortable=True, template="offerings/_sla_cell.html"),
            Column("version", _("Version"), sortable=True),
            Column("updated_at", _("Updated"), sortable=True, filter=Filter(FilterKind.DATERANGE)),
        ],
        default_sort="-version",
        caption="Service Offerings",
        empty_headline="No offerings in scope",
        empty_body="Service Offering definitions for services you can see will appear here.",
        row_url_name="offerings:detail",
    )
    breadcrumb_title = _("Service Offerings")
    service_filter_field = "service"
    organization_filter_fields = ("service__organization",)

    def get_base_queryset(self):
        return ServiceOffering.objects.visible_to(
            self.request.user, "offering.view"
        ).select_related("service", "offering_type")


class ServiceOfferingDetailView(BreadcrumbsMixin, ScopedDetailView):
    model = ServiceOffering
    template_name = "offerings/detail.html"
    context_object_name = "offering"
    scope_action = "offering.view"

    def get_breadcrumb_title(self) -> str:
        return f"{self.object.name} v{self.object.version}"

    def get_queryset(self):
        return super().get_queryset().select_related("service", "offering_type")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["steps"] = annotate_flow(steps_queryset(self.object))
        ctx["can_manage"] = (
            manageable_services(self.request.user).filter(pk=self.object.service_id).exists()
        )
        ctx["versions"] = (
            ServiceOffering.objects.visible_to(self.request.user, "offering.view")
            .filter(service=self.object.service, name=self.object.name)
            .order_by("-version")
        )
        ctx["executions"] = self.object.executions.select_related("created_by").order_by(
            "-started_at"
        )[:8]
        return ctx


class OfferingExecutionListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = OfferingExecution
    template_name = "offerings/executions.html"
    platform_codes = ("offering.view",)
    table_config = TableConfig(
        key="offerings_executions",
        columns=[
            Column("offering.name", _("Service Offering"), sortable=True, searchable=True),
            organization_column(
                key="offering.service.organization.name",
                lookups=("offering__service__organization__path",),
            ),
            Column(
                "offering.service.name",
                _("Service"),
                sortable=True,
                searchable=True,
                filter=Filter(
                    FilterKind.CHOICE,
                    param="service",
                    lookup="offering__service_id",
                    choices_callable=service_filter_choices,
                ),
            ),
            Column(
                "status",
                _("Status"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=ExecutionStatus.choices),
                template="offerings/exec_state.html",
            ),
            Column("current_step.name", _("Step"), sortable=True),
            Column("created_by.full_name", _("Started by"), sortable=True, searchable=True),
            Column("started_at", _("Started"), sortable=True, filter=Filter(FilterKind.DATERANGE)),
            Column("started_at", _("Duration"), template="offerings/exec_duration.html"),
        ],
        default_sort="-started_at",
        caption="Executions",
        empty_headline="No executions yet",
        empty_body="Runs of the offerings you can see will appear here.",
    )
    breadcrumb_title = _("Executions")
    breadcrumb_parent = (_("Service Offerings"), "offerings:index")
    service_filter_field = "offering__service"
    organization_filter_fields = ("offering__service__organization",)

    def get_base_queryset(self):
        service_ids = list(visible_services(self.request.user).values_list("id", flat=True))
        return OfferingExecution.objects.filter(
            offering__service_id__in=service_ids
        ).select_related("offering", "offering__service", "created_by", "current_step")


# --------------------------------------------------------------------- builder


class OfferingBuilderView(BreadcrumbsMixin, CreatePermissionMixin, FormView):
    """Create (no ``pk``) or edit (``pk``, drafts only) an offering and its plan.

    The form carries the offering fields plus the plan as JSON (``definition``).
    ``action=publish`` saves and then publishes in one go.
    """

    template_name = "offerings/builder.html"
    form_class = ServiceOfferingForm
    create_perm_code = "offering.manage"
    breadcrumb_parent = (_("Service Offerings"), "offerings:index")

    def dispatch(self, request, *args, **kwargs):
        self.object = None
        if request.user.is_authenticated and "pk" in kwargs:
            self.object = get_object_or_404(
                ServiceOffering.objects.filter(
                    service__in=manageable_services(request.user)
                ).select_related("service"),
                pk=kwargs["pk"],
            )
            if not self.object.is_editable:
                raise Http404("Only draft offerings can be edited.")
        return super().dispatch(request, *args, **kwargs)

    def get_breadcrumb_title(self) -> str:
        if self.object:
            return _("Edit %(name)s v%(version)s") % {
                "name": self.object.name,
                "version": self.object.version,
            }
        return _("New service offering")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["instance"] = self.object
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        if self.object is None:
            service_pk = self.request.GET.get("service") or selected_service_pk(
                self.request.user, self.request.session
            )
            if service_pk:
                initial["service"] = service_pk
        return initial

    def _parse(self, raw, own_service_id=None):
        return parse_definition(
            raw,
            roles=step_role_choices(),
            offerings=responsible_offering_choices(self.request.user),
            own_service_id=own_service_id,
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = ctx["form"]
        if form.is_bound:
            raw = form.data.get("definition") or "[]"
            try:
                definition = json.loads(raw)
            except ValueError:
                definition = []
        elif self.object is not None:
            definition = definition_from_offering(self.object)
        else:
            definition = [{"key": "step-1", "name": "", "step_type": StepType.TASK}]
        organizations, offering_rows, service_chains = location_options(
            responsible_offering_choices(self.request.user),
            form.fields["service"].queryset,
        )
        ctx.update(
            {
                "offering": self.object,
                "editor_definition": definition,
                "editor_definition_json": json.dumps(definition),
                "editor_schedule": {
                    "onRequest": bool(form["on_request"].value()),
                    "onSchedule": bool(form["on_schedule"].value()),
                    "frequency": form["recurrence_frequency"].value() or "",
                    "service": str(form["service"].value() or ""),
                    "interval": _int_or_none(form["recurrence_interval"].value()) or 1,
                },
                "editor_units": {
                    "DAILY": [
                        npgettext("recurrence unit", "day", "days", 1),
                        npgettext("recurrence unit", "day", "days", 2),
                    ],
                    "WEEKLY": [
                        npgettext("recurrence unit", "week", "weeks", 1),
                        npgettext("recurrence unit", "week", "weeks", 2),
                    ],
                    "MONTHLY": [
                        npgettext("recurrence unit", "month", "months", 1),
                        npgettext("recurrence unit", "month", "months", 2),
                    ],
                    "YEARLY": [
                        npgettext("recurrence unit", "year", "years", 1),
                        npgettext("recurrence unit", "year", "years", 2),
                    ],
                },
                "editor_options": {
                    "roles": [[r.pk, r.name] for r in step_role_choices()],
                    "offerings": offering_rows,
                    "organizations": organizations,
                    "serviceChains": service_chains,
                },
                "step_errors": getattr(self, "step_errors", {}),
                "plan_errors": getattr(self, "plan_errors", []),
            }
        )
        return ctx

    def form_valid(self, form):
        service = form.cleaned_data.get("service")
        parsed = self._parse(form.cleaned_data.get("definition"), getattr(service, "pk", None))
        if not parsed.ok:
            self.step_errors, self.plan_errors = parsed.step_errors, parsed.errors
            return self.form_invalid(form)
        publishing = self.request.POST.get("action") == "publish"
        try:
            with transaction.atomic():
                offering = form.save(commit=False)
                if offering.pk is None:
                    offering.version = 1
                    offering.status = OfferingStatus.DRAFT
                    offering.created_by = getattr(self.request.user, "person", None)
                offering.save()
                save_definition(offering, parsed.steps)
        except ValidationError as exc:
            self.plan_errors = exc.messages
            return self.form_invalid(form)
        self.object = offering
        if publishing:
            try:
                offering.publish()
            except ValidationError as exc:
                for message in exc.messages:
                    messages.error(self.request, message)
                messages.info(self.request, _("Draft saved, but it cannot be published yet."))
                return redirect("offerings:edit", pk=offering.pk)
            messages.success(self.request, _("Service offering published."))
            return redirect("offerings:detail", pk=offering.pk)
        messages.success(self.request, _("Draft saved."))
        return redirect("offerings:edit", pk=offering.pk)


class OfferingPreviewView(CreatePermissionMixin, View):
    """HTMX: render the steps card for the editor's current (unsaved) plan."""

    create_perm_code = "offering.manage"

    def post(self, request, *args, **kwargs):
        own = _offering_from_post(request)
        parsed = parse_definition(
            request.POST.get("definition"),
            roles=step_role_choices(),
            offerings=responsible_offering_choices(request.user),
            own_service_id=own.service_id,
        )
        own.sla_minutes = specs_sla(parsed.steps)
        return render(
            request,
            "offerings/_preview.html",
            {"offering": own, "steps": preview_steps(parsed.steps, own), "preview": True},
        )


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _offering_from_post(request):
    """Unsaved offering built from the editor's current fields (preview only)."""
    from django.utils.dateparse import parse_date

    post = request.POST
    own = ServiceOffering(
        name=post.get("name") or _("This offering"),
        on_request=post.get("on_request") == "on",
        on_schedule=post.get("on_schedule") == "on",
        recurrence_frequency=post.get("recurrence_frequency") or "",
        recurrence_interval=_int_or_none(post.get("recurrence_interval")) or 1,
        recurrence_weekdays=[
            d
            for d in (_int_or_none(v) for v in post.getlist("recurrence_weekdays"))
            if d is not None
        ],
        recurrence_month_day=_int_or_none(post.get("recurrence_month_day")),
        recurrence_month=_int_or_none(post.get("recurrence_month")),
        recurrence_start=parse_date(post.get("recurrence_start") or ""),
        recurrence_end=parse_date(post.get("recurrence_end") or ""),
    )
    service = manageable_services(request.user).filter(pk=_int_or_none(post.get("service")))
    own.service = service.first()
    return own


class _ManagedOfferingAction(CreatePermissionMixin, View):
    """POST-only action on an offering of a service the user manages."""

    create_perm_code = "offering.manage"

    def get_offering(self, pk):
        return get_object_or_404(
            ServiceOffering.objects.filter(service__in=manageable_services(self.request.user)),
            pk=pk,
        )


class OfferingPublishView(_ManagedOfferingAction):
    def post(self, request, pk):
        offering = self.get_offering(pk)
        if not offering.is_editable:
            raise Http404
        try:
            offering.publish()
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
            return redirect("offerings:edit", pk=offering.pk)
        messages.success(request, _("Service offering published."))
        return redirect("offerings:detail", pk=offering.pk)


class OfferingNewVersionView(_ManagedOfferingAction):
    def post(self, request, pk):
        offering = self.get_offering(pk)
        if offering.is_editable:
            return redirect("offerings:edit", pk=offering.pk)
        draft = offering.new_version(created_by=getattr(request.user, "person", None))
        messages.success(request, _("New draft version created."))
        return redirect("offerings:edit", pk=draft.pk)


class OfferingDeleteDraftView(_ManagedOfferingAction):
    def post(self, request, pk):
        offering = self.get_offering(pk)
        if not offering.is_editable or offering.requests.exists():
            raise Http404
        with transaction.atomic():
            offering.transitions.all().delete()
            offering.steps.all().delete()
            offering.delete()
        messages.success(request, _("Draft deleted."))
        return redirect(reverse("offerings:index"))
