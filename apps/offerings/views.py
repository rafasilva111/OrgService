"""Service Offerings: definitions, details and executions."""

import json
from dataclasses import replace
from datetime import date, timedelta
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import (
    Avg,
    Case,
    CharField,
    DurationField,
    Exists,
    F,
    OuterRef,
    Q,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.utils.translation import npgettext
from django.views.generic import DetailView, FormView, TemplateView, View

from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.durations import humanize_minutes
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
from apps.offerings.recurrence import occurrences_between
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
            Column(
                "average_elapsed_duration",
                _("Average elapsed time"),
                sortable=True,
                template="offerings/_duration_cell.html",
            ),
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
        return (
            ServiceOffering.objects.visible_to(self.request.user, "offering.view")
            .select_related("service", "offering_type")
            .annotate(
                average_elapsed_duration=Avg(
                    F("executions__completed_at") - F("executions__created_at"),
                    filter=Q(
                        service__in=visible_services(self.request.user),
                        executions__status=ExecutionStatus.COMPLETED,
                        executions__completed_at__gte=F("executions__created_at"),
                    ),
                    output_field=DurationField(),
                )
            )
        )


class OfferingCalendarView(BreadcrumbsMixin, PermissionRequiredMixin, TemplateView):
    platform_codes = ("offering.view",)
    template_name = "offerings/calendar.html"
    breadcrumb_title = _("Scheduled service offerings")
    breadcrumb_parent = (_("Service Offerings"), "offerings:index")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = selected_service_pk(self.request.user, self.request.session)
        context["calendar_service"] = visible_services(self.request.user).filter(pk=pk).first()
        return context


class OfferingCalendarEventsView(PermissionRequiredMixin, View):
    platform_codes = ("offering.view",)

    def get(self, request):
        def date_param(key):
            value = request.GET.get(key, "")
            try:
                parsed = parse_datetime(value)
                return parsed.date() if parsed else parse_date(value)
            except ValueError:
                return None

        start, end = date_param("start"), date_param("end")
        if (
            not start
            or not end
            or start < date(1900, 1, 1)
            or end > date(9998, 12, 31)
            or not 0 < (end - start).days <= 62
        ):
            return JsonResponse(
                {"error": str(_("Select a valid calendar date range."))}, status=400
            )

        service_pk = selected_service_pk(request.user, request.session)
        if service_pk is None:
            return JsonResponse([], safe=False)
        offerings = (
            ServiceOffering.objects.visible_to(request.user, "offering.view")
            .filter(service_id=service_pk, status=OfferingStatus.ACTIVE, on_schedule=True)
            .filter(recurrence_start__lt=end)
            .order_by("name", "pk")
        )
        events = []
        for offering in offerings:
            duration_days = max(1, ((offering.sla_minutes or 0) + 1439) // 1440)
            lookback = min(duration_days - 1, (start - date.min).days)
            first_start = start - timedelta(days=lookback)
            for occurrence in occurrences_between(offering, first_start, end):
                event_end = occurrence + timedelta(
                    days=min(duration_days, (date.max - occurrence).days)
                )
                events.append(
                    {
                        "title": offering.name,
                        "start": occurrence.isoformat(),
                        "end": event_end.isoformat(),
                        "allDay": True,
                        "url": reverse("offerings:detail", args=[offering.pk]),
                        "extendedProps": {
                            "sla": humanize_minutes(offering.sla_minutes, max_parts=3)
                        },
                    }
                )
        return JsonResponse(events, safe=False)


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
            if request.GET.get("person"):
                actors = self.get_actors_context()
                return render(
                    request,
                    "offerings/_person_recent_tasks.html",
                    self.get_person_tasks_context(actors["selected_step"]),
                )
            actors = self.get_actors_context()
            if request.headers.get("HX-Target") == "eligible_actors-wrap" or request.GET.get(
                "page"
            ):
                return render(request, "core/tables/_table.html", actors)
            return render(
                request,
                "offerings/_step_insights.html",
                {
                    "actors_context": actors,
                    "pause_context": self.get_pause_context(actors["selected_step"]),
                },
            )
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def get_actors_context(self):
        from apps.offerings.execution import eligible_people
        from apps.offerings.step_duration import MANUAL_STEPS, add_person_step_statistics

        if (
            not execution_visible_services(self.request.user)
            .filter(pk=self.object.service_id)
            .exists()
        ):
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
        history_url = reverse("offerings:tasks")
        history_filters = {**step_task_filters(step), "status": TaskStatus.DONE}
        for person in page.object_list:
            person.sla_minutes = step.sla_minutes
            person.step_pk = step.pk
            person.task_history_url = (
                f"{history_url}?{urlencode({**history_filters, 'assignee': person.pk})}"
            )
        config = TableConfig(
            key="eligible_actors",
            columns=[
                Column("full_name", _("Name"), template="offerings/_eligible_person_cell.html"),
                Column(
                    "average_sample_count",
                    _("Tasks"),
                    template="offerings/_sample_count_cell.html",
                ),
                Column(
                    "average_duration",
                    _("Average active time"),
                    template="offerings/_duration_cell.html",
                ),
                Column(
                    "average_elapsed_duration",
                    _("Average elapsed time"),
                    template="offerings/_duration_cell.html",
                ),
                Column(
                    "average_pause_duration",
                    _("Average paused time"),
                    template="offerings/_duration_cell.html",
                ),
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
                "The count and averages use completed tasks with valid times, grouped by "
                "recorded assignee for this step and offering version. Active time excludes "
                "recorded pauses."
            ),
        }

    PERSON_TASK_ORDERS = ("recent", "active", "elapsed", "paused")

    def get_person_tasks_context(self, step):
        """Five tasks of one eligible person on this step and offering version.

        ``order=recent`` (default) lists the latest tasks; ``active``,
        ``elapsed`` and ``paused`` list the longest ones by that time, so a
        click on an average shows the tasks behind it.
        """
        from apps.offerings.execution import eligible_people
        from apps.offerings.step_duration import MANUAL_STEPS, pause_prefetch, reliable_sample

        try:
            person_id = int(self.request.GET.get("person", ""))
        except ValueError as exc:
            raise Http404 from exc
        order = self.request.GET.get("order") or "recent"
        if order not in self.PERSON_TASK_ORDERS:
            raise Http404
        people = eligible_people(step) if step.step_type in MANUAL_STEPS else []
        person = next((person for person in people if person.pk == person_id), None)
        if person is None:
            raise Http404
        tasks = OfferingTask.objects.filter(
            execution__offering=step.offering, step=step, assigned_to=person
        ).order_by("-created_at", "-pk")
        if order == "recent":
            tasks = tasks[:5]
        tasks = list(tasks.prefetch_related(pause_prefetch()))
        for task in tasks:
            sample = reliable_sample(task) if task.status == TaskStatus.DONE else None
            task.active_duration, task.elapsed_duration = sample or (None, None)
            task.paused_duration = (
                task.elapsed_duration - task.active_duration if sample is not None else None
            )
        if order != "recent":
            attribute = f"{order}_duration"
            measured = [task for task in tasks if getattr(task, attribute) is not None]
            tasks = sorted(measured, key=lambda task: getattr(task, attribute), reverse=True)[:5]
        filters = {**step_task_filters(step), "assignee": person.pk}
        return {
            "person": person,
            "step": step,
            "tasks": tasks,
            "order": order,
            "sla_minutes": step.sla_minutes,
            "all_tasks_url": f"{reverse('offerings:tasks')}?{urlencode(filters)}",
        }

    def get_pause_context(self, step):
        from apps.offerings.pause_statistics import step_pause_summaries

        rows = step_pause_summaries(step)
        history_url = reverse("offerings:tasks")
        for row in rows:
            filters = {**step_task_filters(step), "pause_reason": row.key}
            row.history_url = f"{history_url}?{urlencode(filters)}"
            row.ongoing_url = f"{history_url}?{urlencode({**filters, 'status': TaskStatus.PAUSED})}"
        config = TableConfig(
            key="pause_reasons",
            columns=[
                Column("reason", _("Pause reason")),
                Column("count", _("Pauses"), template="offerings/_pause_count_cell.html"),
                Column(
                    "ongoing_count",
                    _("Ongoing pauses"),
                    template="offerings/_pause_ongoing_cell.html",
                ),
                Column(
                    "total_duration",
                    _("Total paused time"),
                    template="offerings/_pause_total_cell.html",
                ),
            ],
            caption=_("Pause reasons") + f" · {step.name}",
            empty_headline=_("No pauses recorded"),
            empty_body=_("Recorded pauses for this step will appear here."),
            searchable=False,
        )
        return {
            "config": config,
            "visible_columns": config.columns,
            "object_list": rows,
            "table_description": _(
                "Includes all tasks for this step and offering version, including ongoing pauses. "
                "The average above uses completed tasks only. A dash means at least one pause "
                "duration could not be calculated."
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
            ctx["pause_context"] = self.get_pause_context(ctx["selected_step"])
            if self.request.GET.get("person"):
                ctx["person_tasks_context"] = self.get_person_tasks_context(ctx["selected_step"])
        return ctx


class OfferingExecutionListView(BreadcrumbsMixin, PermissionRequiredMixin, TableView):
    model = OfferingExecution
    template_name = "offerings/executions.html"
    platform_codes = ("offering.view",)
    table_config = TableConfig(
        key="offerings_executions",
        page_size=10,
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


TASK_SCOPES = ("mine", "service", "organization", "all")


def member_organizations(user):
    """Organizations (with their subtrees) where the user holds an active membership."""
    from apps.organizations.models import Organization, OrganizationMembership

    if getattr(user, "is_superuser", False):
        return Organization.objects.alive()
    paths = OrganizationMembership.objects.filter(
        person__user=user,
        person__active=True,
        person__deleted_at__isnull=True,
        active=True,
        organization__active=True,
        organization__deleted_at__isnull=True,
    ).values_list("organization__path", flat=True)
    subtree = Q(pk__in=[])
    for path in set(paths):
        subtree |= Q(path__descendants=path)
    return Organization.objects.alive().filter(subtree)


def pause_reason_choices():
    return [*PauseReason.choices, ("OTHER", _("Other or unspecified"))]


def step_task_filters(step):
    """Task list filters that drill down from a step page to its tasks."""
    service = step.service_offering.service if step.service_offering_id else step.offering.service
    return {
        "scope": "all",
        "activity": "all",
        "organization": service.organization_id,
        "service": service.pk,
        "step": step.pk,
    }


def task_service_choices(view):
    return [
        (str(service.pk), service.name)
        for service in task_services(view.request.user).order_by("name")
    ]


def actionable_tasks(user):
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
        visible |= service_filter(executable) & (
            Q(assigned_to=person) | Q(assigned_to__isnull=True, step__responsible_role__isnull=True)
        )
        for service_id, role_id in groups:
            visible |= service_filter([service_id]) & Q(
                step__responsible_role_id=role_id, assigned_to__isnull=True
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


def task_assignee_choices(view):
    from apps.people.models import Person

    return [
        (str(pk), name)
        for pk, name in Person.objects.filter(
            pk__in=view.get_base_queryset().values("assigned_to_id")
        )
        .order_by("full_name", "pk")
        .values_list("pk", "full_name")
    ]


def task_step_choices(view):
    """Steps of the listed tasks, labelled "<offering> v<version> · <step>".

    A delegated step is labelled with its delegated offering: the workers it is
    delegated to must not learn the parent offering's name.
    """
    steps = OfferingStep.objects.filter(
        pk__in=view.get_base_queryset().values("step_id")
    ).select_related("offering", "service_offering")
    choices = []
    for step in steps:
        offering = step.service_offering or step.offering
        label = f"{offering.name} v{step.offering.version} · {step.name}"
        choices.append((str(step.pk), label, (offering.name.casefold(), step.order, step.pk)))
    return [(pk, label) for pk, label, _key in sorted(choices, key=lambda choice: choice[2])]


def task_role_choices(view):
    from apps.access.models import Role

    return [
        (str(pk), name)
        for pk, name in Role.objects.filter(
            pk__in=view.get_base_queryset().values("step__responsible_role_id")
        )
        .order_by("name", "pk")
        .values_list("pk", "name")
    ]


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

    def get_table_config(self):
        config = super().get_table_config()
        if getattr(self, "task_scope", None) not in TASK_SCOPES:
            return config
        # "My actionable tasks" are already narrowed to what I can execute, so
        # an assignee filter only makes sense on the wider tabs.
        assignee_columns = (
            [
                Column(
                    "assigned_to_id",
                    _("Assigned to"),
                    visible=False,
                    filter=Filter(
                        FilterKind.CHOICE,
                        param="assignee",
                        choices_callable=task_assignee_choices,
                    ),
                )
            ]
            if self.task_scope != "mine"
            else []
        )
        columns = [
            *config.columns,
            *assignee_columns,
            Column(
                "step.responsible_role_id",
                _("Responsible role"),
                visible=False,
                filter=Filter(
                    FilterKind.CHOICE,
                    param="role",
                    choices_callable=task_role_choices,
                ),
            ),
            Column(
                "step_id",
                _("Step"),
                visible=False,
                filter=Filter(
                    FilterKind.CHOICE,
                    param="step",
                    choices_callable=task_step_choices,
                ),
            ),
            Column(
                "pause_reason_match",
                _("Pause reason"),
                visible=False,
                filter=Filter(
                    FilterKind.CHOICE,
                    param="pause_reason",
                    choices_callable=lambda view: pause_reason_choices(),
                ),
            ),
        ]
        return replace(config, columns=columns)

    def get_queryset(self):
        requested_scope = self.request.GET.get("scope")
        self.task_scope = requested_scope if requested_scope in TASK_SCOPES else "mine"
        self.task_activity = self.request.GET.get("activity", "active") or "active"
        if self.task_activity not in ("active", "all"):
            raise Http404
        pause_reason = self.request.GET.get("pause_reason", "")
        if pause_reason and pause_reason not in dict(pause_reason_choices()):
            raise Http404
        for key in ("assignee", "role", "step"):
            value = self.request.GET.get(key, "")
            if value:
                try:
                    if not 0 < int(value) <= 9223372036854775807:
                        raise ValueError
                except ValueError as exc:
                    raise Http404 from exc
        # The header's selection pre-fills the filter drawer (choosing "All"
        # there opts out; explicit ?service=/?organization= links override it):
        # the personal and service tabs take service and organization, the
        # organization tab only the organization, and "All tasks" neither.
        self.use_header_scope = self.task_scope != "all"
        self.use_header_service_scope = self.task_scope in ("mine", "service")
        return super().get_queryset()

    def scope_tasks(self, scope):
        """Tasks behind a tab, before the activity filter narrows them.

        Each wider tab contains the narrower ones: "All tasks" is everything I
        may view plus my service and personal tasks, and "my organization"
        narrows that to my organizations' subtrees.
        """
        user = self.request.user
        viewable = OfferingTask.objects.filter(
            execution__offering__in=ServiceOffering.objects.visible_to(user, "offering.view"),
            execution__offering__service__in=execution_visible_services(user),
        )
        if scope == "all":
            return OfferingTask.objects.filter(
                Q(pk__in=viewable)
                | Q(pk__in=self.scope_tasks("service"))
                | Q(pk__in=self.scope_tasks("mine"))
            )
        if scope == "organization":
            organizations = member_organizations(user)
            return self.scope_tasks("all").filter(
                Q(step__service_offering__service__organization__in=organizations)
                | Q(
                    step__service_offering__isnull=True,
                    step__offering__service__organization__in=organizations,
                )
            )
        tasks = ongoing_service_tasks(user) if scope == "service" else actionable_tasks(user)
        if self.task_activity == "all":
            if scope == "mine":
                history = viewable.filter(assigned_to__user=user)
            else:
                services = task_services(user)
                history = viewable.filter(
                    Q(step__service_offering__service__in=services)
                    | Q(
                        step__service_offering__isnull=True,
                        step__offering__service__in=services,
                    )
                )
            tasks = OfferingTask.objects.filter(Q(pk__in=tasks) | Q(pk__in=history))
        return tasks

    def get_base_queryset(self):
        tasks = self.scope_tasks(self.task_scope)
        # "Paused at least once for <reason>": the annotation equals the
        # requested reason on matching tasks, so the drawer's choice filter can
        # select them. "OTHER" covers pauses without a known reason.
        pause_reason = self.request.GET.get("pause_reason", "")
        if pause_reason in dict(pause_reason_choices()):
            paused = TaskActivity.objects.filter(task=OuterRef("pk"), action="paused")
            paused = (
                paused.exclude(detail__in=PauseReason.values)
                if pause_reason == "OTHER"
                else paused.filter(detail=pause_reason)
            )
            tasks = tasks.annotate(
                pause_reason_match=Case(
                    When(Exists(paused), then=Value(pause_reason)),
                    default=Value(""),
                    output_field=CharField(),
                )
            )
        else:
            tasks = tasks.annotate(pause_reason_match=Value("", output_field=CharField()))
        if self.task_activity == "active":
            tasks = tasks.filter(
                execution__status__in=(ExecutionStatus.RUNNING, ExecutionStatus.WAITING),
                execution__current_step_id=F("step_id"),
                status__in=(
                    TaskStatus.PENDING,
                    TaskStatus.ASSIGNED,
                    TaskStatus.IN_PROGRESS,
                    TaskStatus.PAUSED,
                    TaskStatus.BLOCKED,
                ),
            )
        return tasks.annotate(
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
        ).select_related("assigned_to")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.copy()
        query.pop("scope", None)
        query.pop("page", None)
        context["task_scope"] = getattr(self, "task_scope", "mine")
        context["task_activity"] = self.task_activity
        context["task_scope_query"] = query.urlencode()
        context["task_preserved_filters"] = [
            (key, self.request.GET[key])
            for key in ("scope", "activity")
            if self.request.GET.get(key)
        ]
        context["task_activity_query"] = [
            (key, value)
            for key, values in self.request.GET.lists()
            if key not in ("activity", "page")
            for value in values
        ]
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
        from apps.offerings.deadlines import complete_path_sla_minutes
        from apps.offerings.step_duration import add_step_averages

        add_step_averages(steps, execution.offering)
        end_steps = [step for step in steps if step.step_type == StepType.END]
        if end_steps:
            average = OfferingExecution.objects.filter(
                offering=execution.offering,
                status=ExecutionStatus.COMPLETED,
                started_at__isnull=False,
                completed_at__gte=F("started_at"),
            ).aggregate(
                average=Avg(F("completed_at") - F("started_at"), output_field=DurationField())
            )["average"]
            total_sla = complete_path_sla_minutes(execution.offering)
            for step in end_steps:
                step.show_execution_summary = True
                step.average_execution_elapsed = average
                step.total_sla_minutes = total_sla
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
        task.can_manage = user_has_scoped_permission(user, "offering.manage", service=service)
        task.can_pause_resume = task.can_manage or (
            task.can_execute and task.assigned_to_id is not None and task.assigned_to_id == actor_id
        )
        task.eligible = (
            [person for person in eligible_people(task.step) if person.pk != task.assigned_to_id]
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
    execution = (
        visible_executions(user)
        .filter(subject_type=ContentType.objects.get_for_model(subject), subject_id=subject.pk)
        .first()
    )
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
            if task.status
            in (
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
        from apps.offerings.deadlines import execution_final_deadline
        from apps.requests.models import Request
        from apps.requests.views import visible_requests

        ctx = super().get_context_data(**kwargs)
        ctx["delegated_only"] = execution_delegated_only(self.object, self.request.user)
        if not ctx["delegated_only"]:
            ctx["final_deadline"] = execution_final_deadline(self.object)
        if self.object.subject_type_id == ContentType.objects.get_for_model(Request).pk:
            ctx["linked_request"] = (
                visible_requests(self.request.user).filter(pk=self.object.subject_id).first()
            )
        ctx["step_tasks"] = execution_step_tasks(
            self.object, self.request.user, ctx["delegated_only"]
        )
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
