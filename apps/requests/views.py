"""Requests: table (search/filters/bulk close), detail and create."""

from django import forms
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, FormView, View

from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.mixins import (
    CreatePermissionMixin,
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
from apps.offerings.request_form import answer_rows, answers_form_for
from apps.people.models import Person
from apps.requests.models import Priority, Request, RequestAttachment, RequestStatus

CLOSED_STATUSES = (RequestStatus.RESOLVED, RequestStatus.CLOSED, RequestStatus.CANCELLED)


def visible_requests(user):
    authorized = Request.objects.visible_to(user, "request.view")
    if not user.is_authenticated or not user.is_active:
        return authorized
    return Request.objects.filter(
        Q(pk__in=authorized)
        | Q(requester__user=user, requester__active=True, requester__deleted_at__isnull=True)
    )


def offerings_for_service(user, service_pk):
    """Active offerings of one service the user can see (empty if not visible)."""
    if not service_pk:
        return ServiceOffering.objects.none()
    from apps.services.models import Service

    return (
        ServiceOffering.objects.filter(
            service__in=visible_services(user),
            service_id=service_pk,
            status=OfferingStatus.ACTIVE,
            on_request=True,
        )
        .filter(service__in=Service.objects.visible_to(user, "request.create"))
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
        fields = ("service", "offering", "title", "description")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.services.models import Service

        self.fields["service"].queryset = (
            visible_services(user)
            .filter(pk__in=Service.objects.visible_to(user, "request.create"))
            .select_related("organization")
            .order_by("name")
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


class RequestListView(BreadcrumbsMixin, LoginRequiredMixin, TableView):
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

    def dispatch(self, request, *args, **kwargs):
        if (
            request.user.is_authenticated
            and not user_has_permission_code(request.user, "request.view")
            and not Request.objects.filter(
                requester__user=request.user,
                requester__active=True,
                requester__deleted_at__isnull=True,
            ).exists()
        ):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_base_queryset(self):
        return visible_requests(self.request.user).select_related(
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
        from django.contrib import messages
        from django.contrib.contenttypes.models import ContentType

        from apps.offerings.models import OfferingExecution

        if action != "mark_closed":
            raise PermissionDenied
        if not user_has_permission_code(self.request.user, "request.close"):
            raise PermissionDenied
        authorized = Request.objects.visible_to(self.request.user, "request.close")
        if queryset.exclude(pk__in=authorized).exists():
            raise PermissionDenied
        linked_ids = OfferingExecution.objects.filter(
            subject_type=ContentType.objects.get_for_model(Request),
            subject_id__in=queryset.values("pk"),
        ).values("subject_id")
        if queryset.filter(pk__in=linked_ids).exists():
            messages.error(
                self.request,
                _("Requests linked to executions cannot be closed independently."),
            )
            return self.redirect_to_list()
        updated = queryset.exclude(status=RequestStatus.CLOSED).update(status=RequestStatus.CLOSED)
        if updated:
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

    @staticmethod
    def _selected_offering(form):
        """The chosen offering, if it is one the form may accept."""
        pk = form["offering"].value()
        try:
            return form.fields["offering"].queryset.filter(pk=int(pk)).first() if pk else None
        except (TypeError, ValueError):
            return None

    def get_context_data(self, **kwargs):
        """Render the selected offering's steps and request form questions
        (initial value or re-display after errors)."""
        ctx = super().get_context_data(**kwargs)
        selected = self._selected_offering(ctx["form"])
        ctx["selected_offering"] = selected
        ctx["offering_steps"] = offering_steps(selected) if selected else None
        ctx["answers_form"] = getattr(self, "answers_form", None) or (
            answers_form_for(selected) if selected else None
        )
        return ctx

    def post(self, request, *args, **kwargs):
        # The request fields and the offering's own questions are validated
        # together; nothing is saved unless both are valid.
        form = self.get_form()
        selected = self._selected_offering(form)
        self.answers_form = (
            answers_form_for(selected, request.POST, request.FILES) if selected else None
        )
        answers_ok = self.answers_form is None or self.answers_form.is_valid()
        if form.is_valid() and answers_ok:
            return self.form_valid(form)
        return self.form_invalid(form)

    @transaction.atomic
    def form_valid(self, form):
        person = Person.objects.filter(user=self.request.user).first()
        if person is None:
            form.add_error(
                None,
                _("Your account must be linked to a person before creating a request."),
            )
            return self.form_invalid(form)
        request_obj = form.save(commit=False)
        request_obj.priority = form.cleaned_data["offering"].default_priority
        request_obj.requester = person
        request_obj.created_by = person
        request_obj.status = RequestStatus.NEW
        data, uploads = self.answers_form.answers() if self.answers_form else ({}, [])
        request_obj.form_data = data
        from apps.offerings.execution import start_request_execution

        try:
            with transaction.atomic():
                request_obj.save()
                start_request_execution(request_obj, person)
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)
        for key, upload in uploads:
            attachment = RequestAttachment(
                request=request_obj,
                field_key=key,
                original_name=upload.name[:255],
                size=upload.size,
                content_type=(getattr(upload, "content_type", "") or "")[:128],
            )
            attachment.file.save(upload.name, upload, save=True)
            request_obj.form_data[key] = {
                "attachment": attachment.pk,
                "name": attachment.original_name,
            }
        if uploads:
            request_obj.save(update_fields=["form_data", "updated_at"])
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
    ``?offering=<pk>`` (an active, visible offering), else a hint — plus, out of
    band, that offering's request form questions."""

    create_perm_code = "request.create"

    def get(self, request, *args, **kwargs):
        from apps.services.models import Service

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
            )
            .filter(service__in=Service.objects.visible_to(request.user, "request.create"))
            .first()
            if pk is not None
            else None
        )
        steps = offering_steps(selected) if selected is not None else None
        # Also swaps the offering's questions into the form (out of band).
        return render(
            request,
            "requests/_offering_selected.html",
            {
                "steps": steps,
                "selected_offering": selected,
                "answers_form": answers_form_for(selected) if selected else None,
            },
        )


class RequestDetailView(BreadcrumbsMixin, LoginRequiredMixin, DetailView):
    model = Request
    template_name = "requests/detail.html"
    scope_action = "request.view"
    context_object_name = "req"

    def get_breadcrumb_title(self) -> str:
        return f"#{self.object.pk} {self.object.title}"

    def get_queryset(self):
        return visible_requests(self.request.user).select_related(
            "service", "offering", "offering__offering_type", "requester", "assigned_to"
        )

    def get_context_data(self, **kwargs):
        from apps.offerings.views import execution_subject_context

        ctx = super().get_context_data(**kwargs)
        ctx["comments"] = self.object.comments.select_related("author").order_by("created_at")
        ctx["answers"] = answer_rows(self.object, include_empty=True)
        ctx.update(
            execution_subject_context(
                self.request.user, self.object, self.request.path, include_log=True
            )
        )
        if "note_form" in kwargs:
            ctx["note_form"] = kwargs["note_form"]
        return ctx

    def post(self, request, *args, **kwargs):
        from django.contrib.contenttypes.models import ContentType
        from django.http import Http404
        from django.shortcuts import redirect

        from apps.offerings.views import (
            ExecutionNoteForm,
            add_execution_note,
            execution_is_requester,
            visible_executions,
        )

        self.object = self.get_object()
        execution = visible_executions(request.user).filter(
            subject_type=ContentType.objects.get_for_model(Request), subject_id=self.object.pk
        ).first()
        if execution is None:
            raise Http404
        form = ExecutionNoteForm(
            request.POST,
            allow_work_notes=not execution_is_requester(execution, request.user),
        )
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(note_form=form), status=400)
        add_execution_note(
            execution,
            request.user,
            form.cleaned_data["body"],
            kind=form.cleaned_data["kind"],
            subject=self.object,
        )
        return redirect(f"{request.path}#execution-log")


class RequestAttachmentView(LoginRequiredMixin, View):
    """Download a request attachment — only for users who can see the request
    (404 otherwise, without revealing that it exists)."""

    platform_codes = ("request.view",)

    def get(self, request, pk):
        from django.http import FileResponse, Http404

        visible = visible_requests(request.user)
        attachment = (
            RequestAttachment.objects.filter(pk=pk, request__in=visible)
            .select_related("request")
            .first()
        )
        if attachment is None or not attachment.file:
            raise Http404
        try:
            handle = attachment.file.open("rb")
        except FileNotFoundError as exc:
            raise Http404 from exc
        return FileResponse(handle, as_attachment=True, filename=attachment.original_name)
