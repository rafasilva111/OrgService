"""Navigation registry — the single place defining sidebar groups/items.

Items resolve their URL at render time (the context processor calls
``reverse()``), so adding a surface is a one-line change here.
"""

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

# Group labels (order is significant — sidebar renders groups in this order).
G_DASHBOARD = _("Dashboard")
G_ORGANIZATIONS = _("Organizations")
G_SERVICES = _("Services")
G_WORK = _("Work")
G_WORKFLOWS = _("Workflows")
G_RESOURCES = _("Resources")
G_PEOPLE = _("People")
G_ANALYTICS = _("Analytics")
G_ADMINISTRATION = _("Administration")

NAV_GROUPS = [
    G_DASHBOARD,
    G_ORGANIZATIONS,
    G_SERVICES,
    G_WORK,
    G_WORKFLOWS,
    G_RESOURCES,
    G_PEOPLE,
    G_ANALYTICS,
    G_ADMINISTRATION,
]


@dataclass
class NavItem:
    label: str
    url_name: str
    icon: str
    group: str
    keywords: str = ""
    requires_staff: bool = False


NAV_ITEMS = [
    NavItem(_("Overview"), "dashboard", "dashboard", G_DASHBOARD, "executive home"),
    NavItem(_("Organizations"), "organizations:index", "orgs", G_ORGANIZATIONS, "hierarchy units"),
    NavItem(
        _("Memberships"),
        "organizations:memberships",
        "memberships",
        G_ORGANIZATIONS,
        "roles assignments",
    ),
    NavItem(_("Services"), "services:index", "services", G_SERVICES, "catalog capabilities"),
    NavItem(_("Requests"), "requests:index", "requests", G_WORK, "demand tickets"),
    NavItem(_("Incidents"), "incidents:index", "incidents", G_WORK, "disruptions outages"),
    NavItem(_("Problems"), "problems:index", "problems", G_WORK, "root cause"),
    NavItem(_("Changes"), "changes:index", "changes", G_WORK, "change requests approvals"),
    NavItem(_("Workflows"), "workflows:index", "workflows", G_WORKFLOWS, "definitions steps"),
    NavItem(_("Executions"), "workflows:executions", "executions", G_WORKFLOWS, "runs tasks"),
    NavItem(
        _("Resources"), "resources:index", "resources", G_RESOURCES, "assets equipment material"
    ),
    NavItem(_("People"), "people:directory", "people", G_PEOPLE, "directory staff"),
    NavItem(_("Skills"), "people:skills", "skills", G_PEOPLE, "capabilities qualifications"),
    NavItem(_("Events"), "events:timeline", "events", G_ANALYTICS, "audit stream activity"),
    NavItem(_("Metrics"), "metrics:index", "metrics", G_ANALYTICS, "measurements values"),
    NavItem(_("KPIs"), "metrics:kpis", "kpis", G_ANALYTICS, "targets slas"),
    NavItem(_("Roles & Permissions"), "access:roles", "roles", G_ADMINISTRATION, "rbac grants"),
    NavItem(_("Audit Log"), "audit:log", "audit", G_ADMINISTRATION, "immutable trail"),
]
