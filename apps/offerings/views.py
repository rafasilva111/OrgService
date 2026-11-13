"""Service Offerings: definitions, details and executions."""

import json

from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.utils.translation import npgettext
from django.views.generic import DetailView, FormView, View

from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.mixins import (
    CreatePermissionMixin,
    PermissionRequiredMixin,
    ScopedDetailView,
    selected_service_pk,
    service_filter_choices,
    user_has_permission_code,
    user_has_scoped_permission,
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
    service_step_roles,
    step_role_choices,
)
from apps.offerings.models import (
    ExecutionNote,
    ExecutionStatus,
    FormFieldType,
    Notification,
    OfferingExecution,
    OfferingStatus,
    OfferingStep,
    OfferingTask,
    PauseReason,
    ServiceOffering,
    ServiceOfferingType,
    StepType,
    TaskActivity,
    TaskStatus,
)
from apps.offerings.request_form import (
    RequestAnswersForm,
    answers_form_for,
    form_definition_from_offering,
    parse_form_definition,
    save_form_definition,
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


class OfferingProductivityView(BreadcrumbsMixin, PermissionRequiredMixin, View):
    platform_codes = ("offering.view",)
    breadcrumb_title = _("Offering productivity")
    breadcrumb_parent = (_("Service Offerings"), "offerings:index")

    def get(self, request):
        from apps.offerings.productivity import offering_detail, offering_totals, period_start

        periods = (
            ("7", _("Last 7 days")),
            ("30", _("Last 30 days")),
            ("90", _("Last 90 days")),
            ("year", _("This year")),
        )
        period = request.GET.get("period", "7")
        if period not in dict(periods):
            return render(
                request, "offerings/productivity.html",
                {"periods": periods, "period_error": _("Select a valid period."),
                 "breadcrumbs": self.get_breadcrumbs()},
                status=400,
            )
        offerings = ServiceOffering.objects.filter(
            service__in=execution_visible_services(request.user)
        ).select_related("service").order_by("service__name", "name", "-version", "-pk")
        selected = None
        if request.GET.get("offering"):
            try:
                offering_id = int(request.GET["offering"])
            except ValueError as exc:
                raise Http404 from exc
            if not 0 < offering_id <= 9223372036854775807:
                raise Http404
            selected = get_object_or_404(offerings, pk=offering_id)

        now = timezone.now()
        start = period_start(period, now)
        executions = OfferingExecution.objects.filter(
            offering__service__in=execution_visible_services(request.user)
        )
        totals = {
            (row["offering__service_id"], row["offering__name"]): row
            for row in offering_totals(executions, start, now)
        }
        rows = []
        seen = set()
        for offering in offerings:
            key = (offering.service_id, offering.name)
            if key not in seen:
                seen.add(key)
                rows.append({
                    "offering": offering,
                    "counts": totals.get(key, {
                        "started": 0, "completed": 0, "running": 0, "waiting": 0, "failed": 0,
                    }),
                })
        context = {
            "periods": periods,
            "period": period,
            "rows": rows,
            "selected": selected,
            "start": start,
            "end": now,
            "breadcrumbs": self.get_breadcrumbs(),
        }
        if selected:
            selected_executions = executions.filter(
                offering__service_id=selected.service_id, offering__name=selected.name
            )
            context["people"], context["timeline"] = offering_detail(
                selected_executions, start, now
            )
        return render(request, "offerings/productivity.html", context)


class ServiceOfferingDetailView(BreadcrumbsMixin, ScopedDetailView):
    model = ServiceOffering
    template_name = "offerings/detail.html"
    context_object_name = "offering"
    scope_action = "offering.view"

    def get_breadcrumb_title(self) -> str:
        return f"{self.object.name} v{self.object.version}"

    def get_queryset(self):
        return super().get_queryset().select_related("service", "offering_type")

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.headers.get("HX-Request") and request.GET.get("step"):
            return render(request, "core/tables/_table.html", self.get_actors_context())
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def get_actors_context(self):
        from apps.offerings.execution import eligible_people
        from apps.offerings.step_duration import MANUAL_STEPS, add_person_step_statistics

        if not execution_visible_services(self.request.user).filter(pk=self.object.service_id).exists():
            raise Http404
        try:
            step_id = int(self.request.GET.get("step", ""))
        except ValueError as exc:
            raise Http404 from exc
        if not 0 < step_id <= 9223372036854775807:
            raise Http404
        step = get_object_or_404(
            OfferingStep.objects.select_related(
                "offering__service", "service_offering__service", "responsible_role"
            ),
            offering=self.object,
            pk=step_id,
        )
        people = eligible_people(step) if step.step_type in MANUAL_STEPS else []
        people = sorted(people, key=lambda person: (person.full_name.casefold(), person.pk))
        paginator = Paginator(people, 10)
        page = paginator.get_page(self.request.GET.get("page"))
        add_person_step_statistics(page.object_list, step)
        config = TableConfig(
            key="eligible_actors",
            columns=[
                Column("full_name", _("Name")),
                Column("completed_task_count", _("Completed tasks"), template="offerings/_count_cell.html"),
                Column("average_sample_count", _("Tasks with reliable times"), template="offerings/_sample_count_cell.html"),
                Column("average_duration", _("Average active time"), template="offerings/_duration_cell.html"),
                Column("average_elapsed_duration", _("Average elapsed time"), template="offerings/_duration_cell.html"),
                Column("average_pause_duration", _("Average paused time"), template="offerings/_duration_cell.html"),
            ],
            page_size=10,
            caption=_("Eligible people") + f" · {step.name}",
            empty_headline=_("No eligible people"),
            empty_body=(
                _("Automatic steps do not have eligible executors.")
                if step.step_type not in MANUAL_STEPS
                else _("No one is currently eligible to execute this step.")
            ),
            searchable=False,
            row_url_name="people:detail",
        )
        return {
            "config": config,
            "visible_columns": config.columns,
            "object_list": page.object_list,
            "paginator": paginator,
            "page_obj": page,
            "is_paginated": paginator.num_pages > 1,
            "query_without_sort": f"step={step.pk}",
            "selected_step": step,
            "table_description": _(
                "Completed tasks are counted by recorded assignee. Averages use reliable "
                "completed tasks for this step and offering version, excluding recorded pauses "
                "from active time."
            ),
        }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["steps"] = annotate_flow(steps_queryset(self.object))
        ctx["answers_form"] = answers_form_for(self.object, disabled=True)
        ctx["can_manage"] = (
            manageable_services(self.request.user).filter(pk=self.object.service_id).exists()
        )
        ctx["versions"] = (
            ServiceOffering.objects.visible_to(self.request.user, "offering.view")
            .filter(service=self.object.service, name=self.object.name)
            .order_by("-version")
        )
        ctx["can_view_executions"] = (
            execution_visible_services(self.request.user).filter(pk=self.object.service_id).exists()
        )
        if ctx["can_view_executions"]:
            from apps.offerings.step_duration import add_step_averages

            add_step_averages(ctx["steps"], self.object)
            ctx["executions"] = self.object.executions.select_related("created_by").order_by(
                "-started_at"
            )[:8]
        if self.request.GET.get("step"):
            ctx["actors_context"] = self.get_actors_context()
            ctx["selected_step"] = ctx["actors_context"]["selected_step"]
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
        row_url_name="offerings:execution_detail",
    )
    breadcrumb_title = _("Executions")
    breadcrumb_parent = (_("Service Offerings"), "offerings:index")
    service_filter_field = "offering__service"
    organization_filter_fields = ("offering__service__organization",)

    def get_base_queryset(self):
        return OfferingExecution.objects.filter(
            offering__service__in=execution_visible_services(self.request.user)
        ).select_related("offering", "offering__service", "created_by", "current_step")


def execution_visible_services(user):
    from apps.services.models import Service

    return visible_services(user).filter(pk__in=Service.objects.visible_to(user, "offering.view"))


def task_services(user):
    from apps.services.models import Service

    return Service.objects.visible_to(user, "offering.execute") | Service.objects.visible_to(
        user, "offering.manage"
    )


def task_service_choices(view):
    return [
        (str(service.pk), service.name)
        for service in task_services(view.request.user).order_by("name")
    ]


def actionable_tasks(user):
    from apps.common.authorization import membership_service_scope
    from apps.people.models import Person
    from apps.services.models import Service, ServiceActor

    managed = Service.objects.visible_to(user, "offering.manage")
    executable = Service.objects.visible_to(user, "offering.execute")
    person = Person.objects.filter(user=user, active=True, deleted_at__isnull=True).first()

    def service_filter(services):
        return Q(step__service_offering__service__in=services) | Q(
            step__service_offering__isnull=True, step__offering__service__in=services
        )

    visible = service_filter(managed)
    if person is not None:
        executable_ids = set(executable.values_list("pk", flat=True))
        groups = set(
            ServiceActor.objects.filter(
                person=person,
                service_id__in=executable_ids,
                active=True,
                role__active=True,
                role__deleted_at__isnull=True,
            ).values_list("service_id", "role_id")
        )
        for membership in person.memberships.filter(
            active=True, role__active=True, role__deleted_at__isnull=True
        ).select_related("organization", "role"):
            groups.update(
                (service.pk, membership.role_id)
                for service in membership_service_scope(membership)
                if service.pk in executable_ids
            )
        visible |= service_filter(executable) & (
            Q(assigned_to=person)
            | Q(assigned_to__isnull=True, step__responsible_role__isnull=True)
        )
        for service_id, role_id in groups:
            visible |= (
                service_filter([service_id])
                & Q(step__responsible_role_id=role_id, assigned_to__isnull=True)
            )
    return ongoing_service_tasks(user).filter(visible)


def ongoing_service_tasks(user):
    services = task_services(user)
    service_filter = Q(step__service_offering__service__in=services) | Q(
        step__service_offering__isnull=True, step__offering__service__in=services
    )
    return OfferingTask.objects.filter(
        execution__status__in=(ExecutionStatus.RUNNING, ExecutionStatus.WAITING),
        execution__current_step_id=F("step_id"),
        status__in=(
            TaskStatus.PENDING,
            TaskStatus.ASSIGNED,
            TaskStatus.IN_PROGRESS,
            TaskStatus.PAUSED,
            TaskStatus.BLOCKED,
        ),
    ).filter(service_filter)


class OfferingTaskListView(LoginRequiredMixin, BreadcrumbsMixin, TableView):
    model = OfferingTask
    template_name = "offerings/tasks.html"
    breadcrumb_title = _("Service tasks")
    table_config = TableConfig(
        key="offering_tasks",
        columns=[
            Column(
                "title",
                _("Task"),
                sortable=True,
                searchable=True,
                template="offerings/task_link.html",
            ),
            organization_column(
                key="responsible_organization_name",
                lookups=("responsible_organization_path",),
            ),
            Column(
                "responsible_service_name",
                _("Service"),
                sortable=True,
                searchable=True,
                filter=Filter(
                    FilterKind.CHOICE,
                    param="service",
                    lookup="responsible_service_id",
                    choices_callable=task_service_choices,
                ),
            ),
            Column(
                "status",
                _("Status"),
                sortable=True,
                filter=Filter(FilterKind.CHOICE, choices=TaskStatus.choices),
                template="offerings/task_status.html",
            ),
            Column("assigned_to.full_name", _("Assigned to"), sortable=True, searchable=True),
            Column("deadline", _("Deadline"), sortable=True, filter=Filter(FilterKind.DATERANGE)),
            Column("created_at", _("Created"), sortable=True, filter=Filter(FilterKind.DATERANGE)),
        ],
        default_sort="-created_at",
        caption=_("Service tasks"),
        empty_headline=_("No active tasks in your services."),
    )
    service_filter_field = "responsible_service"

    def get_queryset(self):
        self.task_scope = (
            "service" if self.request.GET.get("scope") == "service" else "mine"
        )
        if self.task_scope == "service":
            self.use_header_scope = False
        return super().get_queryset()

    def get_base_queryset(self):
        tasks = (
            ongoing_service_tasks(self.request.user)
            if self.task_scope == "service"
            else actionable_tasks(self.request.user)
        )
        return (
            tasks
            .annotate(
                responsible_service_id=Coalesce(
                    "step__service_offering__service_id", "step__offering__service_id"
                ),
                responsible_service_name=Coalesce(
                    "step__service_offering__service__name", "step__offering__service__name"
                ),
                responsible_organization_name=Coalesce(
                    "step__service_offering__service__organization__name",
                    "step__offering__service__organization__name",
                ),
                responsible_organization_path=Coalesce(
                    "step__service_offering__service__organization__path",
                    "step__offering__service__organization__path",
                ),
            )
            .select_related("assigned_to")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.copy()
        query.pop("scope", None)
        query.pop("page", None)
        context["task_scope"] = getattr(self, "task_scope", "mine")
        context["task_scope_query"] = query.urlencode()
        return context


class ExecutionNoteForm(forms.Form):
    kind = forms.ChoiceField(
        label=_("Message type"),
        choices=ExecutionNote.Kind.choices,
    )
    body = forms.CharField(
        label=_("Note"),
        max_length=4000,
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": 4000}),
    )

    def __init__(self, *args, allow_work_notes=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not allow_work_notes:
            self.fields["kind"].choices = [
                (ExecutionNote.Kind.COMMENT, _("Comments")),
            ]


def visible_executions(user):
    from apps.requests.models import Request

    owner_access = Q(pk__in=[])
    if user_has_permission_code(user, "offering.view"):
        owner_access = Q(offering__service__in=execution_visible_services(user))
    delegated_access = Q(pk__in=actionable_tasks(user).values("execution_id"))
    requester_access = Q(
        subject_type=ContentType.objects.get_for_model(Request),
        subject_id__in=Request.objects.filter(
            requester__user=user, requester__active=True, requester__deleted_at__isnull=True
        ).values("pk"),
    )
    return OfferingExecution.objects.filter(
        owner_access | delegated_access | requester_access
    ).select_related("offering", "offering__service", "created_by", "current_step")


def execution_is_requester(execution, user):
    from apps.requests.models import Request

    return execution.subject_type_id == ContentType.objects.get_for_model(Request).pk and (
        Request.objects.filter(
            pk=execution.subject_id,
            requester__user=user,
            requester__active=True,
            requester__deleted_at__isnull=True,
        ).exists()
    )


def execution_delegated_only(execution, user):
    return not (
        user_has_permission_code(user, "offering.view")
        and execution_visible_services(user).filter(pk=execution.offering.service_id).exists()
        or execution_is_requester(execution, user)
    )


def execution_step_tasks(execution, user, delegated_only):
    from apps.people.models import Person

    actor_id = Person.objects.filter(user=user).values_list("pk", flat=True).first()
    steps = list(execution.offering.steps.order_by("order", "pk"))
    tasks = execution.tasks.select_related("step", "assigned_to").order_by(
        "step__order", "started_at", "pk"
    )
    if delegated_only:
        tasks = tasks.filter(pk__in=actionable_tasks(user).values("pk"))
        steps = [execution.current_step] if execution.current_step else []
    if execution_visible_services(user).filter(pk=execution.offering.service_id).exists():
        from apps.offerings.step_duration import add_step_averages

        add_step_averages(steps, execution.offering)
    tasks_by_step = {step.pk: [] for step in steps}
    for task in tasks:
        if task.step_id not in tasks_by_step:
            steps.append(task.step)
            tasks_by_step[task.step_id] = []
        tasks_by_step[task.step_id].append(task)
        task.history = list(task.activities.select_related("actor").all())
        from apps.offerings.execution import eligible_people

        service = task.step.effective_service
        task.can_execute = user_has_scoped_permission(
            user, "offering.execute", service=service
        ) and (not task.assigned_to_id or task.assigned_to_id == actor_id)
        task.can_manage = user_has_scoped_permission(
            user, "offering.manage", service=service
        )
        task.can_pause_resume = task.can_manage or (
            task.can_execute and task.assigned_to_id is not None
            and task.assigned_to_id == actor_id
        )
        task.eligible = (
            [
                person
                for person in eligible_people(task.step)
                if person.pk != task.assigned_to_id
            ]
            if task.can_manage
            else []
        )
        task.can_claim = (
            task.can_execute
            and task.status == TaskStatus.PENDING
            and task.step.responsible_role_id is not None
            and task.assigned_to_id is None
            and actor_id in {person.pk for person in eligible_people(task.step)}
        )
        task.is_current = execution.current_step_id == task.step_id and task.status not in (
            TaskStatus.DONE,
            TaskStatus.CANCELLED,
        )
        task.outcomes = list(task.step.outgoing.select_related("to_step").all())
    return [(step, tasks_by_step[step.pk]) for step in steps]


def execution_subject_context(user, subject, return_url, *, include_log=False):
    execution = visible_executions(user).filter(
        subject_type=ContentType.objects.get_for_model(subject), subject_id=subject.pk
    ).first()
    if execution is None:
        return {}
    delegated_only = execution_delegated_only(execution, user)
    step_tasks = execution_step_tasks(execution, user, delegated_only)
    current_task = max(
        (
            task
            for step, tasks in step_tasks
            if step.pk == execution.current_step_id
            for task in tasks
            if task.status in (
                TaskStatus.PENDING,
                TaskStatus.ASSIGNED,
                TaskStatus.IN_PROGRESS,
                TaskStatus.PAUSED,
                TaskStatus.BLOCKED,
            )
        ),
        key=lambda task: task.pk,
        default=None,
    )
    context = {
        "execution": execution,
        "delegated_only": delegated_only,
        "step_tasks": step_tasks,
        "show_step_averages": any(
            getattr(step, "show_average_duration", False) for step, _ in step_tasks
        ),
        "current_task": current_task,
        "task_return_url": return_url,
        "pause_reasons": PauseReason.choices,
    }
    if include_log:
        context["log_entries"] = execution_log_entries(execution, user, delegated_only)
        context["note_form"] = ExecutionNoteForm(
            allow_work_notes=not execution_is_requester(execution, user)
        )
    return context


def execution_log_entries(execution, user, delegated_only):
    from apps.people.models import user_display_name

    notes = execution.notes.select_related("author", "author__person", "task")
    activities = TaskActivity.objects.filter(task__execution=execution).select_related(
        "actor", "task"
    )
    if delegated_only:
        visible_tasks = actionable_tasks(user).filter(execution=execution)
        notes = notes.filter(task__in=visible_tasks)
        activities = activities.filter(task__in=visible_tasks)
    if execution_is_requester(execution, user):
        notes = notes.exclude(kind=ExecutionNote.Kind.WORK_NOTE)

    entries = [*notes, *activities]
    from apps.requests.models import Request, RequestComment

    if execution.subject_type_id == ContentType.objects.get_for_model(Request).pk:
        comments = RequestComment.objects.filter(request_id=execution.subject_id).select_related(
            "author"
        )
        for comment in comments:
            comment.message_type = ExecutionNote.Kind.COMMENT.label
            comment.author_display = comment.author.full_name if comment.author else "—"
        entries.extend(comments)

    for entry in entries:
        if isinstance(entry, ExecutionNote):
            entry.message_type = entry.get_kind_display()
            entry.author_display = user_display_name(entry.author)
    return sorted(entries, key=lambda entry: (entry.created_at, entry.pk), reverse=True)


@transaction.atomic
def add_execution_note(execution, user, body, *, kind=ExecutionNote.Kind.COMMENT, subject=None):
    OfferingExecution.objects.select_for_update().get(pk=execution.pk)
    execution = get_object_or_404(visible_executions(user), pk=execution.pk)
    if subject is not None and (
        execution.subject_type_id != ContentType.objects.get_for_model(subject).pk
        or execution.subject_id != subject.pk
    ):
        raise Http404
    if kind == ExecutionNote.Kind.WORK_NOTE and execution_is_requester(execution, user):
        raise PermissionDenied
    task = None
    if execution.status in (ExecutionStatus.RUNNING, ExecutionStatus.WAITING):
        task = (
            execution.tasks.filter(
                step_id=execution.current_step_id,
                status__in=(
                    TaskStatus.PENDING,
                    TaskStatus.ASSIGNED,
                    TaskStatus.IN_PROGRESS,
                    TaskStatus.PAUSED,
                    TaskStatus.BLOCKED,
                ),
            )
            .order_by("-pk")
            .first()
        )
    return ExecutionNote.objects.create(
        execution=execution,
        task=task,
        author=user,
        body=body,
        kind=kind,
    )


class OfferingExecutionDetailView(LoginRequiredMixin, BreadcrumbsMixin, DetailView):
    model = OfferingExecution
    template_name = "offerings/execution_detail.html"
    context_object_name = "execution"
    breadcrumb_parent = (_("Executions"), "offerings:executions")

    def get_breadcrumb_title(self):
        return _("Execution #%(number)s") % {"number": self.object.pk}

    def get_queryset(self):
        return visible_executions(self.request.user)

    def get_context_data(self, **kwargs):
        from apps.requests.models import Request
        from apps.requests.views import visible_requests

        ctx = super().get_context_data(**kwargs)
        ctx["delegated_only"] = execution_delegated_only(self.object, self.request.user)
        if self.object.subject_type_id == ContentType.objects.get_for_model(Request).pk:
            ctx["linked_request"] = visible_requests(self.request.user).filter(
                pk=self.object.subject_id
            ).first()
        ctx["step_tasks"] = execution_step_tasks(self.object, self.request.user, ctx["delegated_only"])
        ctx["show_step_averages"] = any(
            getattr(step, "show_average_duration", False) for step, _ in ctx["step_tasks"]
        )
        ctx["pause_reasons"] = PauseReason.choices
        ctx["log_entries"] = execution_log_entries(
            self.object, self.request.user, ctx["delegated_only"]
        )
        ctx["note_form"] = (
            kwargs["note_form"]
            if "note_form" in kwargs
            else ExecutionNoteForm(
                allow_work_notes=not execution_is_requester(self.object, self.request.user)
            )
        )
        return ctx

    def is_requester(self):
        return execution_is_requester(self.object, self.request.user)

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        allow_work_notes = not execution_is_requester(self.object, request.user)
        form = ExecutionNoteForm(request.POST, allow_work_notes=allow_work_notes)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(note_form=form), status=400)
        add_execution_note(
            self.object,
            request.user,
            form.cleaned_data["body"],
            kind=form.cleaned_data["kind"],
        )
        return redirect("offerings:execution_detail", pk=self.object.pk)


class OfferingTaskActionView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from apps.offerings.execution import task_action

        task = get_object_or_404(OfferingTask, pk=pk)
        try:
            task_action(
                pk,
                request.user,
                request.POST.get("action", ""),
                assignee_id=request.POST.get("assignee"),
                outcome=request.POST.get("outcome"),
                reason=request.POST.get("reason"),
                comment=request.POST.get("comment"),
            )
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
        next_url = request.POST.get("next", "")
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(next_url)
        return redirect("offerings:execution_detail", pk=task.execution_id)


class NotificationListView(LoginRequiredMixin, BreadcrumbsMixin, View):
    def get(self, request):
        notifications = Notification.objects.filter(recipient__user=request.user)
        return render(
            request,
            "offerings/notifications.html",
            {
                "notifications": notifications,
                "breadcrumbs": [(_("Notifications"), None)],
            },
        )


class NotificationReadView(LoginRequiredMixin, View):
    def post(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, recipient__user=request.user)
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at", "updated_at"])
        return redirect("offerings:notifications")


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
        offerings = list(responsible_offering_choices(self.request.user))
        services = set(manageable_services(self.request.user).values_list("pk", flat=True))
        services.update(offering.service_id for offering in offerings)
        return parse_definition(
            raw,
            roles=step_role_choices(),
            offerings=offerings,
            own_service_id=own_service_id,
            service_roles=service_step_roles(services),
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
        if form.is_bound:
            try:
                form_definition = json.loads(form.data.get("form_definition") or "[]")
            except ValueError:
                form_definition = []
        elif self.object is not None:
            form_definition = form_definition_from_offering(self.object)
        else:
            form_definition = []
        offerings = list(responsible_offering_choices(self.request.user))
        services = list(form.fields["service"].queryset)
        organizations, offering_rows, service_chains = location_options(offerings, services)
        role_services = {service.pk for service in services}
        role_services.update(offering.service_id for offering in offerings)
        service_roles = service_step_roles(role_services)
        ctx.update(
            {
                "offering": self.object,
                "editor_definition": definition,
                "editor_definition_json": json.dumps(definition),
                "editor_form": form_definition,
                "editor_form_json": json.dumps(form_definition),
                "form_field_errors": getattr(self, "form_field_errors", {}),
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
                    "serviceRoles": {
                        str(service_id): [[role.pk, role.name] for role in roles]
                        for service_id, roles in service_roles.items()
                    },
                    "offerings": offering_rows,
                    "organizations": organizations,
                    "serviceChains": service_chains,
                    "formFieldTypes": [[k, str(v)] for k, v in FormFieldType.choices],
                },
                "step_errors": getattr(self, "step_errors", {}),
                "plan_errors": getattr(self, "plan_errors", []),
            }
        )
        return ctx

    def form_valid(self, form):
        service = form.cleaned_data.get("service")
        parsed = self._parse(form.cleaned_data.get("definition"), getattr(service, "pk", None))
        form_specs, form_errors = parse_form_definition(self.request.POST.get("form_definition"))
        if not parsed.ok or form_errors:
            self.step_errors, self.plan_errors = parsed.step_errors, list(parsed.errors)
            self.plan_errors += form_errors.pop(-1, [])
            self.form_field_errors = form_errors
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
                save_form_definition(offering, form_specs)
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
        offerings = list(responsible_offering_choices(request.user))
        services = set(manageable_services(request.user).values_list("pk", flat=True))
        services.update(offering.service_id for offering in offerings)
        parsed = parse_definition(
            request.POST.get("definition"),
            roles=step_role_choices(),
            offerings=offerings,
            own_service_id=own.service_id,
            service_roles=service_step_roles(services),
        )
        own.sla_minutes = specs_sla(parsed.steps)
        return render(
            request,
            "offerings/_preview.html",
            {
                "offering": own,
                "steps": preview_steps(parsed.steps, own),
                "answers_form": RequestAnswersForm(
                    parse_form_definition(request.POST.get("form_definition"))[0], disabled=True
                ),
                "preview": True,
            },
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
