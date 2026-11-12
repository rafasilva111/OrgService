"""Dashboard views (apex-inspired "live pictures" over the domain data).

Three surfaces:
  * Executive overview   — platform-wide numbers scoped to the user;
  * Organization page    — everything under one organization subtree;
  * Service page         — everything attached to one service.

Every count is computed from an authz-scoped queryset (``visible_to``), so the
page always shows exactly what the user is allowed to see.
"""

import json
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from apps.changes.models import Change
from apps.common.breadcrumbs import BreadcrumbsMixin
from apps.common.mixins import PermissionRequiredMixin, visible_services
from apps.incidents.models import Incident, IncidentStatus
from apps.organizations.models import Organization
from apps.people.models import Person
from apps.problems.models import Problem
from apps.requests.models import Request, RequestStatus
from apps.resources.models import Resource
from apps.services.models import ServiceStatus
from apps.workflows.models import Workflow, WorkflowExecution

CLOSED_STATUSES = (
    RequestStatus.RESOLVED,
    RequestStatus.CLOSED,
    RequestStatus.CANCELLED,
)
INACTIVE_INCIDENTS = (IncidentStatus.RESOLVED, IncidentStatus.CLOSED)


def _list_url(url_name: str, **params) -> str:
    """A list-view URL with query params — dashboard "drill down" links so
    the number on the card matches what the target list actually shows.
    ``service`` may be a single pk or an iterable of pks (repeated param).
    """
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            pairs.extend((key, str(v)) for v in value)
        else:
            pairs.append((key, str(value)))
    query = urlencode(pairs)
    url = reverse(url_name)
    return f"{url}?{query}" if query else url


CHART_COLORS = [
    "#6366f1",
    "#0ea5e9",
    "#f59e0b",
    "#10b981",
    "#8b5cf6",
    "#ef4444",
    "#71717a",
    "#ec4899",
]


class PlatformLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True


class SetServiceContextView(LoginRequiredMixin, View):
    """Store the user's current service + role pick (session) via a POST.

    Receives ``service`` (pk) and the granting ``role`` (code); both must match
    an entry in the user's service-role list, otherwise the context is cleared
    and nothing is validated. ``next`` controls the redirect target.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        from apps.common.context_processors import (
            _visible_services_list,
            user_organization_pks,
        )

        current = request.session.get("service_context") or {}
        pair = (request.POST.get("service"), request.POST.get("role"))
        try:
            pk = int(pair[0])
        except (TypeError, ValueError):
            pk = None
        role = pair[1] or ""

        # Clicking the selected service toggles it off.
        if request.POST.get("clear") or (
            pk is not None and current.get("pk") == pk and current.get("role") == role
        ):
            request.session.pop("service_context", None)
            return redirect("/")

        org_context = request.session.get("org_context") or {}
        org_pk = org_context.get("pk")
        if org_pk is None or org_pk not in user_organization_pks(request.user):
            request.session.pop("org_context", None)
            request.session.pop("service_context", None)
            return self._redirect(request)

        match = next(
            (
                s
                for s in _visible_services_list(request.user, org_pk=org_pk)
                if s["pk"] == pk and s["role_code"] == role
            ),
            None,
        )
        if match is None:
            request.session.pop("service_context", None)
            return self._redirect(request)
        request.session["service_context"] = {"pk": pk, "role": role}
        return self._redirect(request)

    def _redirect(self, request):
        target = request.POST.get("next") or "/"
        if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
            target = "/"
        return redirect(target)


class SetOrganizationContextView(LoginRequiredMixin, View):
    """Store the user's current organization pick (session) via a POST.

    Only one organization can be selected at a time; picking a new one
    replaces the previous choice and always clears the selected service/role.
    Clicking the selected organization again clears it, returning to global.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        from apps.common.context_processors import user_organization_pks

        current = request.session.get("org_context") or {}
        try:
            pk = int(request.POST.get("organization"))
        except (TypeError, ValueError):
            pk = None

        # Clicking the selected organization toggles it (and the service) off,
        # returning to the global overview.
        if request.POST.get("clear") or (pk is not None and current.get("pk") == pk):
            request.session.pop("org_context", None)
            request.session.pop("service_context", None)
            return redirect("/")

        if pk is None or pk not in user_organization_pks(request.user):
            request.session.pop("org_context", None)
            request.session.pop("service_context", None)
            return self._redirect(request)

        request.session["org_context"] = {"pk": pk}
        # Any organization change resets the service selection.
        request.session.pop("service_context", None)
        return self._redirect(request)

    def _redirect(self, request):
        target = request.POST.get("next") or "/"
        if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
            target = "/"
        return redirect(target)


# ---------------------------------------------------------------------------
# Chart builders (plain dicts → json.dumps → ApexCharts config)
# ---------------------------------------------------------------------------


def _donut(values, labels):
    return {
        "chart": {"type": "donut", "height": 280},
        "series": values,
        "labels": labels,
        "colors": CHART_COLORS,
        "legend": {"position": "bottom"},
        "plotOptions": {"pie": {"donut": {"size": "70%"}}},
        "dataLabels": {"enabled": True},
    }


def _bar(categories, series, name):
    return {
        "chart": {"type": "bar", "height": 280, "toolbar": {"show": False}},
        "series": [{"name": str(name), "data": series}],
        "xaxis": {
            "categories": categories,
            "labels": {"rotate": -45, "rotateAlways": True, "hideOverlappingLabels": True},
        },
        "colors": CHART_COLORS[:1],
        "plotOptions": {"bar": {"borderRadius": 4, "columnWidth": "55%"}},
        "dataLabels": {"enabled": False},
    }


def _requests_by_status_chart(queryset, statuses):
    choices = list(statuses.choices)
    labels_by_code = {code: str(label) for code, label in choices}
    rows = {
        r["status"]: r["total"]
        for r in queryset.values("status").annotate(total=Count("id")).order_by("status")
    }
    series = [rows.get(code, 0) for code, _ in choices]
    return _donut(series, [labels_by_code[code] for code, _ in choices])


def _requests_trend_chart(queryset, days=30):
    since = timezone.now() - timedelta(days=days - 1)
    rows = (
        queryset.filter(created_at__gte=since)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Count("id"))
        .order_by("day")
    )
    counts = {r["day"]: r["total"] for r in rows}
    labels, series = [], []
    for i in range(days):
        day = (since + timedelta(days=i)).date()
        labels.append(day.strftime("%b %d"))
        series.append(counts.get(day, 0))
    return _bar(labels, series, str(_("Requests")))


def _visible_people(user):
    orgs = Organization.objects.visible_to(user, "organization.view")
    return Person.objects.filter(memberships__organization__in=orgs, active=True).distinct()


# ---------------------------------------------------------------------------
# Executive overview
# ---------------------------------------------------------------------------


class ExecutiveDashboard(BreadcrumbsMixin, PermissionRequiredMixin, TemplateView):
    template_name = "dashboard/index.html"
    platform_codes = ("organization.view", "service.view", "metrics.view")
    breadcrumb_title = _("Overview")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            target = self._context_target(request)
            if target is not None:
                return redirect(target)
        return super().dispatch(request, *args, **kwargs)

    def _context_target(self, request):
        """Route ``/`` to the selected service or organization dashboard.

        Stale session picks are cleared instead of redirecting to a 404/403.
        """
        from apps.common.context_processors import (
            _visible_services_list,
            user_organization_pks,
        )

        service_context = request.session.get("service_context") or {}
        service_pk = service_context.get("pk")
        if service_pk is not None:
            match = next(
                (
                    s
                    for s in _visible_services_list(request.user)
                    if s["pk"] == service_pk and s["role_code"] == service_context.get("role")
                ),
                None,
            )
            if match is not None and match.get("url"):
                return match["url"]
            request.session.pop("service_context", None)

        org_context = request.session.get("org_context") or {}
        org_pk = org_context.get("pk")
        if org_pk is not None:
            visible = (
                Organization.objects.visible_to(request.user, "organization.view")
                .filter(pk=org_pk)
                .exists()
            )
            if visible and org_pk in user_organization_pks(request.user):
                return reverse("dashboard:organization_dashboard", args=[org_pk])
            request.session.pop("org_context", None)
            request.session.pop("service_context", None)
        return None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user

        orgs = Organization.objects.visible_to(user, "organization.view")
        services = visible_services(user)
        requests = Request.objects.visible_to(user, "request.view")
        incidents = Incident.objects.visible_to(user, "incident.view")
        problems = Problem.objects.visible_to(user, "problem.view")
        changes = Change.objects.visible_to(user, "change.view")
        workflows = Workflow.objects.visible_to(user, "workflow.view")
        resources = Resource.objects.visible_to(user, "resource.view")
        executions = WorkflowExecution.objects.filter(workflow__service__in=services)

        ctx["stats"] = [
            {
                "label": _("Organizations"),
                "value": orgs.count(),
                "icon": "orgs",
                "url": reverse("organizations:index"),
            },
            {
                "label": _("Services"),
                "value": services.count(),
                "icon": "services",
                "url": reverse("services:index"),
            },
            {
                "label": _("People"),
                "value": _visible_people(user).count(),
                "icon": "people",
                "url": reverse("people:directory"),
            },
            {
                "label": _("Resources"),
                "value": resources.count(),
                "icon": "resources",
                "url": reverse("resources:index"),
            },
        ]
        # Active/Open counters. Where the work list exposes a single status
        # column filter we carry the same one into the target table so the
        # dashboard number and the list agree; aggregate "everything but done"
        # counters fall back to the plain (scoped) list.
        ctx["work_stats"] = [
            {
                "label": _("Open Requests"),
                "value": requests.exclude(status__in=CLOSED_STATUSES).count(),
                "icon": "requests",
                "url": _list_url("requests:index", status="OPEN"),
            },
            {
                "label": _("Active Incidents"),
                "value": incidents.exclude(status__in=INACTIVE_INCIDENTS).count(),
                "icon": "incidents",
                "url": reverse("incidents:index") + "?status=ACTIVE",
                "filter": "ACTIVE",
            },
            {
                "label": _("Open Problems"),
                "value": problems.exclude(status__in=("RESOLVED", "CLOSED")).count(),
                "icon": "problems",
                "url": reverse("problems:index") + "?status=ACTIVE",
                "filter": "ACTIVE",
            },
            {
                "label": _("Active Changes"),
                "value": changes.exclude(
                    status__in=("IMPLEMENTED", "VERIFIED", "REJECTED", "ROLLED_BACK")
                ).count(),
                "icon": "changes",
                "url": reverse("changes:index") + "?status=ACTIVE",
                "filter": "ACTIVE",
            },
        ]

        ctx["requests_chart"] = json.dumps(_requests_by_status_chart(requests, RequestStatus))
        ctx["trend_chart"] = json.dumps(_requests_trend_chart(requests))
        ctx["service_health"] = list(
            services.exclude(status=ServiceStatus.RETIRED)
            .values("status")
            .annotate(total=Count("id"))
            .order_by("status")
        )

        ctx["open_requests"] = (
            requests.exclude(status__in=CLOSED_STATUSES)
            .select_related("service", "request_type")
            .order_by("-created_at")[:8]
        )
        ctx["active_incidents"] = (
            incidents.exclude(status__in=INACTIVE_INCIDENTS)
            .select_related("service")
            .order_by("-created_at")[:8]
        )
        ctx["recent_executions"] = executions.select_related(
            "workflow", "workflow__service"
        ).order_by("-created_at")[:8]
        ctx["active_workflows"] = workflows.exclude(status="DEPRECATED").count()
        ctx["running_executions"] = executions.filter(status="RUNNING").count()
        return ctx


# ---------------------------------------------------------------------------
# Per-organization dashboard
# ---------------------------------------------------------------------------


class OrganizationDashboard(BreadcrumbsMixin, PermissionRequiredMixin, TemplateView):
    template_name = "dashboard/organization.html"
    platform_codes = ("organization.view",)
    breadcrumb_parent = (_("Organizations"), "organizations:index")

    def get_breadcrumb_title(self) -> str:
        return self.organization.name

    def get_context_data(self, **kwargs):
        user = self.request.user
        self.organization = get_object_or_404(
            Organization.objects.visible_to(user, "organization.view"),
            pk=kwargs["pk"],
        )
        ctx = super().get_context_data(**kwargs)

        org = self.organization
        descendants = org.get_descendants(include_self=True)
        desc_ids = descendants.values_list("pk", flat=True)
        orgs_qs = Organization.objects.visible_to(user, "organization.view").filter(pk__in=desc_ids)
        services = visible_services(user).filter(organization_id__in=desc_ids)
        service_ids = list(services.values_list("pk", flat=True))
        requests = Request.objects.visible_to(user, "request.view").filter(
            service__organization_id__in=desc_ids
        )
        incidents = Incident.objects.visible_to(user, "incident.view").filter(
            service__organization_id__in=desc_ids
        )
        problems = Problem.objects.visible_to(user, "problem.view").filter(
            service__organization_id__in=desc_ids
        )
        changes = Change.objects.visible_to(user, "change.view").filter(
            Q(service__organization_id__in=desc_ids) | Q(organization_id__in=desc_ids)
        )
        executions = WorkflowExecution.objects.filter(workflow__service_id__in=service_ids)
        people = (
            _visible_people(user)
            .filter(memberships__organization_id__in=desc_ids, memberships__active=True)
            .distinct()
        )

        ctx["organization"] = org
        ctx["children"] = org.get_children().order_by("name")
        ctx["stats"] = [
            {
                "label": _("Organizations"),
                "value": orgs_qs.count(),
                "icon": "orgs",
                "url": _list_url("organizations:index", parent=org.pk),
            },
            {
                "label": _("Services"),
                "value": services.count(),
                "icon": "services",
                "url": _list_url("services:index", organization=org.pk),
            },
            {
                "label": _("Members"),
                "value": people.count(),
                "icon": "memberships",
                "url": _list_url("people:directory", organization=org.pk),
            },
            {
                "label": _("Open Requests"),
                "value": requests.exclude(status__in=CLOSED_STATUSES).count(),
                "icon": "requests",
                "url": _list_url("requests:index", organization=org.pk, status="OPEN"),
            },
        ]
        ctx["work_stats"] = [
            {
                "label": _("Active Incidents"),
                "value": incidents.exclude(status__in=INACTIVE_INCIDENTS).count(),
                "icon": "incidents",
                "url": _list_url("incidents:index", organization=org.pk, status="ACTIVE"),
            },
            {
                "label": _("Problems"),
                "value": problems.count(),
                "icon": "problems",
                "url": _list_url("problems:index", organization=org.pk),
            },
            {
                "label": _("Changes"),
                "value": changes.count(),
                "icon": "changes",
                "url": _list_url("changes:index", organization=org.pk),
            },
            {
                "label": _("Executions"),
                "value": executions.count(),
                "icon": "executions",
                "url": _list_url("workflows:executions", organization=org.pk),
            },
        ]
        ctx["requests_chart"] = json.dumps(_requests_by_status_chart(requests, RequestStatus))
        ctx["trend_chart"] = json.dumps(_requests_trend_chart(requests))
        ctx["org_services"] = services.select_related("organization").order_by("name")[:10]
        ctx["open_requests"] = (
            requests.exclude(status__in=CLOSED_STATUSES)
            .select_related("service", "request_type")
            .order_by("-created_at")[:8]
        )
        return ctx


# ---------------------------------------------------------------------------
# Per-service dashboard
# ---------------------------------------------------------------------------


class ServiceDashboard(BreadcrumbsMixin, PermissionRequiredMixin, TemplateView):
    template_name = "dashboard/service.html"
    platform_codes = ("service.view",)
    breadcrumb_parent = (_("Services"), "services:index")

    def get_breadcrumb_title(self) -> str:
        return self.service.name

    def get_context_data(self, **kwargs):
        user = self.request.user
        self.service = get_object_or_404(visible_services(user), pk=kwargs["pk"])
        ctx = super().get_context_data(**kwargs)

        service = self.service
        requests = Request.objects.visible_to(user, "request.view").filter(service=service)
        incidents = Incident.objects.visible_to(user, "incident.view").filter(service=service)
        problems = Problem.objects.visible_to(user, "problem.view").filter(service=service)
        workflows = Workflow.objects.visible_to(user, "workflow.view").filter(service=service)
        executions = WorkflowExecution.objects.filter(workflow__in=workflows)
        tasks = list(executions.filter(status="RUNNING").values_list("pk", flat=True))

        ctx["service"] = service
        ctx["stats"] = [
            {
                "label": _("Open Requests"),
                "value": requests.exclude(status__in=CLOSED_STATUSES).count(),
                "icon": "requests",
                "url": _list_url("requests:index", service=service.pk, status="OPEN"),
            },
            {
                "label": _("Active Incidents"),
                "value": incidents.exclude(status__in=INACTIVE_INCIDENTS).count(),
                "icon": "incidents",
                "url": _list_url("incidents:index", service=service.pk, status="ACTIVE"),
            },
            {
                "label": _("Open Problems"),
                "value": problems.exclude(status__in=("RESOLVED", "CLOSED")).count(),
                "icon": "problems",
                "url": _list_url("problems:index", service=service.pk, status="ACTIVE"),
            },
            {
                "label": _("Workflows"),
                "value": workflows.filter(status="ACTIVE").count(),
                "icon": "workflows",
                "url": _list_url("workflows:index", service=service.pk, status="ACTIVE"),
            },
        ]
        ctx["health"] = [
            {
                "label": _("Active Incidents"),
                "value": incidents.exclude(status__in=INACTIVE_INCIDENTS).count(),
                "icon": "incidents",
                "url": _list_url("incidents:index", service=service.pk, status="ACTIVE"),
            },
            {
                "label": _("Incident Executions"),
                "value": len(tasks),
                "icon": "executions",
                "url": _list_url("workflows:executions", service=service.pk, status="RUNNING"),
            },
        ]
        ctx["requests_chart"] = json.dumps(_requests_by_status_chart(requests, RequestStatus))
        ctx["trend_chart"] = json.dumps(_requests_trend_chart(requests))
        ctx["open_requests"] = (
            requests.exclude(status__in=CLOSED_STATUSES)
            .select_related("request_type")
            .order_by("-created_at")[:8]
        )
        ctx["active_incidents"] = incidents.exclude(status__in=INACTIVE_INCIDENTS).order_by(
            "-created_at"
        )[:8]
        return ctx
